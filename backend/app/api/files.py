from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, require_member, validate_csrf
from app.api.rooms import ws_channel
from app.core.config import get_settings
from app.core.ratelimit import RateLimiterDeps
from app.models import Room, SessionRecord
from app.repositories import file_repository, room_repository
from app.schemas import FileListResponse, FileView
from app.services import file_service
from app.storage import get_storage_backend

router = APIRouter(prefix="/rooms/{room_token}/files", tags=["files"])
limiter = RateLimiterDeps()

Db = Annotated[AsyncSession, Depends(get_db)]


def _file_view(rec, uploaded_by_name: str | None = None) -> FileView:
    return FileView(
        id=rec.id,
        original_filename=rec.original_filename,
        content_type=rec.content_type,
        size_bytes=rec.size_bytes,
        status=rec.status,
        download_count=rec.download_count,
        uploaded_by_name=uploaded_by_name,
        created_at=rec.created_at,
    )


@router.post("", response_model=FileView, status_code=201)
async def upload(
    room_token: str,
    file: UploadFile,
    auth: Annotated[tuple[SessionRecord, Room], Depends(require_member)],
    request: Request,
    db: Db,
    _: Annotated[None, Depends(validate_csrf)],
) -> FileView:
    settings = get_settings()
    limiter.check("upload", request.client.host if request.client else "unknown", settings.upload_limit)

    async def _chunks() -> AsyncIterator[bytes]:
        while True:
            chunk = await file.read(256 * 1024)
            if not chunk:
                break
            yield chunk

    session_rec, room = auth
    rec = await file_service.upload_file(
        db,
        get_storage_backend(),
        room,
        session_rec,
        filename=file.filename,
        content_type=file.content_type,
        chunks=_chunks(),
    )
    await db.commit()
    await broadcast_file_uploaded(room, _file_view(rec, session_rec.display_name))
    return _file_view(rec, session_rec.display_name)


async def broadcast_file_uploaded(room: Room, view: FileView) -> None:
    from app.ws.manager import manager

    await manager.broadcast(
        ws_channel(room),
        {"type": "FILE_UPLOAD_COMPLETED", "payload": {"file": view.model_dump(mode="json", by_alias=True)}},
    )


async def broadcast_file_deleted(room: Room, file_id: int) -> None:
    from app.ws.manager import manager

    await manager.broadcast(
        ws_channel(room),
        {"type": "FILE_DELETED", "payload": {"fileId": file_id}},
    )


async def broadcast_file_downloaded(room: Room, file_id: int, download_count: int) -> None:
    from app.ws.manager import manager

    await manager.broadcast(
        ws_channel(room),
        {
            "type": "FILE_DOWNLOADED",
            "payload": {"fileId": file_id, "downloadCount": download_count},
        },
    )


@router.get("", response_model=FileListResponse)
async def list_files(
    room_token: str,
    auth: Annotated[tuple[SessionRecord, Room], Depends(require_member)],
    db: Db,
) -> FileListResponse:
    _session, room = auth
    records = await file_repository.list_confirmed_files(db, room.id)
    return FileListResponse(
        files=[_file_view(r) for r in records],
        storageUsedBytes=room.storage_used_bytes,
        fileCount=room.file_count,
        maxRoomStorageBytes=get_settings().max_room_storage_bytes,
        maxFilesPerRoom=get_settings().max_files_per_room,
    )


@router.get("/{file_id}/download")
async def download(
    room_token: str,
    file_id: int,
    auth: Annotated[tuple[SessionRecord, Room], Depends(require_member)],
    db: Db,
):
    from urllib.parse import quote

    session_rec, room = auth
    rec, stream, size = await file_service.download_file(
        db, get_storage_backend(), room, session_rec, file_id
    )
    await db.commit()
    await room_repository.create_audit_event(db, room.id, "FILE_DOWNLOADED", {"file_id": rec.id})
    await db.commit()
    await db.refresh(rec)
    await broadcast_file_downloaded(room, rec.id, rec.download_count)
    filename = rec.original_filename
    disposition = (
        f"attachment; filename=\"{filename.replace('\\\"', '_').replace('\\n', '')}\"; "
        f"filename*=UTF-8''{quote(filename)}"
    )
    return StreamingResponse(
        stream,
        media_type=rec.content_type or "application/octet-stream",
        headers={"Content-Disposition": disposition, "Content-Length": str(size)},
    )


@router.delete("/{file_id}", response_model=FileView, status_code=200)
async def delete(
    room_token: str,
    file_id: int,
    auth: Annotated[tuple[SessionRecord, Room], Depends(require_member)],
    db: Db,
    _: Annotated[None, Depends(validate_csrf)],
) -> FileView:
    session_rec, room = auth
    rec = await file_service.delete_file(db, get_storage_backend(), room, session_rec, file_id)
    await db.commit()
    await broadcast_file_deleted(room, rec.id)
    return _file_view(rec, session_rec.display_name)