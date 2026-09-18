import uuid
from collections.abc import AsyncIterator
from datetime import timedelta

from sqlalchemy import delete as sa_delete
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import errors
from app.core.config import get_settings
from app.core.time import utcnow
from app.file import magic
from app.models import FileRecord, Role, Room, RoomStatus, SessionRecord
from app.repositories import file_repository, room_repository
from app.storage.base import StorageBackend


async def _authorize_upload(room: Room, actor: SessionRecord) -> None:
    if room.status != RoomStatus.ACTIVE.value:
        raise errors.RoomExpired()
    if actor.role != Role.OWNER.value and not room.guest_upload_enabled:
        raise errors.PermissionDenied("Guest uploads are disabled in this room")


async def _authorize_download(room: Room, actor: SessionRecord) -> None:
    if room.status != RoomStatus.ACTIVE.value:
        raise errors.RoomExpired()
    if actor.role != Role.OWNER.value and not room.guest_download_enabled:
        raise errors.PermissionDenied("Guest downloads are disabled in this room")


async def _claim_quota(db: AsyncSession, room_id: int, size_bytes: int) -> bool:
    """Atomically reserve quota: increment counters only if limits still hold.

    Returns False when a concurrent upload already consumed the room's ceiling.
    """
    settings = get_settings()
    result = await db.execute(
        text(
            """
            UPDATE rooms
            SET storage_used_bytes = storage_used_bytes + :size,
                file_count = file_count + 1,
                updated_at = :now
            WHERE id = :id
              AND status = :status
              AND file_count < :max_files
              AND storage_used_bytes + :size <= :max_storage
            RETURNING id
            """
        ),
        {
            "size": int(size_bytes),
            "now": utcnow(),
            "id": room_id,
            "status": RoomStatus.ACTIVE.value,
            "max_files": settings.max_files_per_room,
            "max_storage": settings.max_room_storage_bytes,
        },
    )
    return result.first() is not None


async def upload_file(
    db: AsyncSession,
    storage: StorageBackend,
    room: Room,
    actor: SessionRecord,
    *,
    filename: str | None,
    content_type: str | None,
    chunks: AsyncIterator[bytes],
) -> FileRecord:
    """Stream an upload through the API (Model A), with magic-byte validation.

    The file row is created PENDING, streamed to storage, then confirmed with an
    atomic quota claim. Any failure cleans up the partial object + PENDING row.
    """
    settings = get_settings()
    await _authorize_upload(room, actor)
    safe_name = (filename or "unnamed")[:255] or "unnamed"
    storage_key = str(uuid.uuid4())
    expires_at = room.expires_at + timedelta(seconds=300)

    rec = await file_repository.create_file_record(
        db,
        room_id=room.id,
        original_filename=safe_name,
        storage_key=storage_key,
        content_type=content_type,
        size_bytes=0,
        uploaded_by=actor.id,
        expires_at=expires_at,
    )
    await db.flush()

    first_chunk = b""
    total = 0

    def _check_size() -> None:
        nonlocal total
        if total > settings.max_file_size_bytes:
            raise errors.FileTooLarge(
                f"File exceeds the maximum size of {settings.max_file_size_bytes} bytes"
            )

    async def _source() -> AsyncIterator[bytes]:
        nonlocal first_chunk, total
        if first_chunk:
            yield first_chunk
        async for chunk in chunks:
            total += len(chunk)
            _check_size()
            yield chunk

    try:
        first_chunk = await anext(chunks, b"")
        total = len(first_chunk)
        _check_size()
        await storage.put(storage_key, _source())
    except errors.DropRoomError:
        await _cleanup_failed_upload(db, storage, rec.id, storage_key)
        raise
    except Exception:
        await _cleanup_failed_upload(db, storage, rec.id, storage_key)
        raise errors.DropRoomError("Upload failed while writing to storage")

    _check_size()
    claimed = await _claim_quota(db, room.id, total)
    if not claimed:
        await _cleanup_failed_upload(db, storage, rec.id, storage_key)
        raise errors.RoomStorageQuotaExceeded("Room storage quota exceeded")

    mime, _ext = magic.normalize_content_type(first_chunk, content_type)
    rec.content_type = mime
    rec.size_bytes = total
    rec.status = "CONFIRMED"
    await db.flush()
    await room_repository.create_audit_event(
        db, room.id, "FILE_UPLOADED", {"file_id": rec.id, "name": safe_name, "size": total}
    )
    return rec


async def _cleanup_failed_upload(
    db: AsyncSession,
    storage: StorageBackend,
    file_id: int,
    storage_key: str,
) -> None:
    await storage.delete(storage_key)
    await db.execute(sa_delete(FileRecord).where(FileRecord.id == file_id))
    await db.flush()


async def get_file(db: AsyncSession, room: Room, file_id: int) -> FileRecord:
    rec = await file_repository.get_file_by_id(db, file_id)
    if rec is None or rec.room_id != room.id or rec.status != "CONFIRMED":
        raise errors.FileNotFound()
    return rec


async def download_file(
    db: AsyncSession,
    storage: StorageBackend,
    room: Room,
    actor: SessionRecord,
    file_id: int,
) -> tuple[FileRecord, AsyncIterator[bytes], int]:
    await _authorize_download(room, actor)
    rec = await get_file(db, room, file_id)
    await file_repository.increment_download(db, rec.id)
    await db.flush()
    stream = storage.get_stream(rec.storage_key)
    return rec, stream, rec.size_bytes


async def delete_file(
    db: AsyncSession, storage: StorageBackend, room: Room, actor: SessionRecord, file_id: int
) -> FileRecord:
    if actor.role != Role.OWNER.value:
        raise errors.PermissionDenied("Only the room owner can delete files")
    rec = await get_file(db, room, file_id)
    # Decrement quota counters in the same transaction as metadata deletion.
    await file_repository.mark_deleted(db, rec.id)
    await room_repository.update_storage_and_file_count(db, room.id, -rec.size_bytes, -1)
    await db.flush()
    await room_repository.create_audit_event(
        db, room.id, "FILE_DELETED", {"file_id": rec.id}
    )
    await storage.delete(rec.storage_key)  # idempotent; orphan sweep doubles as retry
    return rec