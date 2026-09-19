from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, require_member, validate_csrf
from app.api.rooms import client_ip, ws_channel
from app.core import errors
from app.core.config import get_settings
from app.core.ratelimit import RateLimiterDeps
from app.models import FileRecord, Room, SessionRecord
from app.repositories import file_repository, room_repository
from app.schemas import (
    FileView,
    UploadSessionComplete,
    UploadSessionCreate,
    UploadSessionCreated,
    UploadSessionStatus,
)
from app.services import upload_service
from app.storage import get_storage_backend

router = APIRouter(prefix="/rooms/{room_token}/uploads", tags=["uploads"])
limiter = RateLimiterDeps()

Db = Annotated[AsyncSession, Depends(get_db)]


def _file_view(rec: FileRecord, uploaded_by_name: str | None = None) -> FileView:
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


async def _broadcast_progress(room: Room, rec: FileRecord) -> None:
    from app.ws.manager import manager

    total = rec.total_size_bytes or 0
    if total and rec.status == "PENDING":
        await manager.broadcast(
            ws_channel(room),
            {
                "type": "FILE_UPLOAD_PROGRESS",
                "payload": {
                    "fileId": rec.id,
                    "receivedBytes": rec.size_bytes,
                    "totalSizeBytes": total,
                    "filename": rec.original_filename,
                },
            },
        )


@router.post("", response_model=UploadSessionCreated, status_code=201)
async def create_upload_session(
    room_token: str,
    body: UploadSessionCreate,
    auth: Annotated[tuple[SessionRecord, Room], Depends(require_member)],
    request: Request,
    db: Db,
    response: Response,
    _: Annotated[None, Depends(validate_csrf)],
) -> UploadSessionCreated:
    settings = get_settings()
    limiter.check("upload", client_ip(request), settings.upload_limit)
    session_rec, room = auth
    rec = await upload_service.create_upload_session(
        db,
        get_storage_backend(),
        room,
        session_rec,
        filename=body.filename,
        content_type=body.content_type,
        total_size_bytes=body.total_size_bytes,
        total_chunks=body.total_chunks,
    )
    await db.commit()
    return UploadSessionCreated(
        uploadId=rec.id,
        filename=rec.original_filename,
        totalSizeBytes=rec.total_size_bytes or 0,
        totalChunks=body.total_chunks,
        chunkSizeBytes=settings.upload_chunk_size_bytes,
    )


@router.get("/{upload_id}", response_model=UploadSessionStatus)
async def get_upload_status(
    room_token: str,
    upload_id: int,
    auth: Annotated[tuple[SessionRecord, Room], Depends(require_member)],
    db: Db,
) -> UploadSessionStatus:
    session_rec, room = auth
    rec = await _pending_session(db, room, upload_id)
    status = await upload_service.session_status(rec)
    return UploadSessionStatus(**status)


async def _pending_session(db: AsyncSession, room: Room, upload_id: int) -> FileRecord:
    rec = await file_repository.get_file_by_id(db, upload_id)
    if rec is None or rec.room_id != room.id:
        raise errors.FileNotFound()
    return rec


@router.put("/{upload_id}/chunks/{index}", response_model=UploadSessionStatus)
async def upload_chunk(
    room_token: str,
    upload_id: int,
    index: int,
    auth: Annotated[tuple[SessionRecord, Room], Depends(require_member)],
    request: Request,
    db: Db,
):
    settings = get_settings()
    limiter.check("upload", client_ip(request), settings.upload_limit)
    session_rec, room = auth
    rec = await _pending_session(db, room, upload_id)
    chunk = await request.body()
    if len(chunk) == 0:
        raise errors.DropRoomError("Empty chunk")
    if index < 0 or index >= 1_000_000:
        raise errors.DropRoomError("Chunk index out of bounds")
    received = await upload_service.store_chunk(db, get_storage_backend(), rec, index, chunk)
    await room_repository.create_audit_event(
        db, room.id, "UPLOAD_CHUNK", {"file_id": rec.id, "chunk": index, "bytes": len(chunk)}
    )
    await db.commit()
    await _broadcast_progress(room, rec)
    status = await upload_service.session_status(rec)
    return UploadSessionStatus(**status)


@router.post("/{upload_id}/complete", response_model=FileView)
async def complete_upload_session(
    room_token: str,
    upload_id: int,
    body: UploadSessionComplete,
    auth: Annotated[tuple[SessionRecord, Room], Depends(require_member)],
    db: Db,
    _: Annotated[None, Depends(validate_csrf)],
) -> FileView:
    session_rec, room = auth
    rec = await _pending_session(db, room, upload_id)
    confirmed = await upload_service.complete_upload_session(
        db,
        get_storage_backend(),
        room,
        session_rec,
        rec,
        expected_total_chunks=body.totalChunks,
    )
    await db.commit()
    from app.api.files import broadcast_file_uploaded

    await broadcast_file_uploaded(room, _file_view(confirmed, session_rec.display_name))
    return _file_view(confirmed, session_rec.display_name)


@router.delete("/{upload_id}", status_code=200)
async def abort_upload_session(
    room_token: str,
    upload_id: int,
    auth: Annotated[tuple[SessionRecord, Room], Depends(require_member)],
    db: Db,
    _: Annotated[None, Depends(validate_csrf)],
) -> dict:
    session_rec, room = auth
    rec = await _pending_session(db, room, upload_id)
    await upload_service.abort_upload_session(db, get_storage_backend(), room, rec)
    await db.commit()
    return {"ok": True}
