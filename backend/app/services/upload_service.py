from collections.abc import AsyncIterator
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import errors
from app.core.config import get_settings
from app.core.time import utcnow
from app.file import magic
from app.models import FileRecord, FileStatus, Room, RoomStatus, SessionRecord
from app.repositories import file_repository, room_repository
from app.storage.base import StorageBackend


def part_key(storage_key: str, index: int) -> str:
    return f"{storage_key}.{index:06d}"


async def create_upload_session(
    db: AsyncSession,
    storage: StorageBackend,
    room: Room,
    actor: SessionRecord,
    *,
    filename: str | None,
    content_type: str | None,
    total_size_bytes: int,
    total_chunks: int,
) -> FileRecord:
    await _authorize_chunked_upload(room, actor)
    import uuid

    settings = get_settings()
    if total_size_bytes <= 0 or total_size_bytes > settings.max_file_size_bytes:
        raise errors.FileTooLarge(
            f"File exceeds the maximum size of {settings.max_file_size_bytes} bytes"
        )
    if total_chunks <= 0 or total_chunks > 100_000:
        raise errors.DropRoomError("total_chunks out of bounds")
    safe_name = (filename or "unnamed")[:255] or "unnamed"
    storage_key = str(uuid.uuid4())
    rec = await file_repository.create_file_record(
        db,
        room_id=room.id,
        original_filename=safe_name,
        storage_key=storage_key,
        content_type=content_type,
        size_bytes=0,
        uploaded_by=actor.id,
        expires_at=room.expires_at + timedelta(seconds=300),
    )
    rec.total_size_bytes = total_size_bytes
    await db.flush()
    return rec


async def store_chunk(
    db: AsyncSession,
    storage: StorageBackend,
    rec: FileRecord,
    index: int,
    chunk: bytes,
) -> int:
    """Persist one chunk; returns the total bytes staged so far."""
    settings = get_settings()
    if rec.status != FileStatus.PENDING.value:
        raise errors.FileNotFound("Upload session is not active")
    if not chunk:
        raise errors.DropRoomError("Empty chunk")
    if len(chunk) > settings.upload_chunk_size_bytes:
        raise errors.FileTooLarge(
            f"Chunk exceeds the maximum chunk size of {settings.upload_chunk_size_bytes} bytes"
        )
    key = part_key(rec.storage_key, index)

    async def _single() -> AsyncIterator[bytes]:
        yield chunk

    await storage.put(key, _single())
    rec.size_bytes += len(chunk)
    await db.flush()
    return rec.size_bytes


async def session_status(rec: FileRecord) -> dict:
    return {
        "uploadId": rec.id,
        "filename": rec.original_filename,
        "totalSizeBytes": rec.total_size_bytes or 0,
        "receivedBytes": rec.size_bytes,
        "status": rec.status,
    }


async def complete_upload_session(
    db: AsyncSession,
    storage: StorageBackend,
    room: Room,
    actor: SessionRecord,
    rec: FileRecord,
    *,
    expected_total_chunks: int,
) -> FileRecord:
    """Assemble staged chunks into the final object, then claim quota atomically.

    The magic-byte check happens on the PENDING -> CONFIRMED transition, reading
    the first bytes after assembly (Phase 3.5 behavior, applied to Model A).
    """
    settings = get_settings()
    if rec.room_id != room.id:
        raise errors.FileNotFound()
    await _authorize_chunked_upload(room, actor)
    if rec.status != FileStatus.PENDING.value:
        raise errors.FileNotFound("Upload session is not active")

    keys = [k for k in await storage.list_keys(rec.storage_key + ".")] if hasattr(
        storage, "list_keys"
    ) else []
    received = {int(k.rsplit(".", 1)[1]): k for k in keys if k.rsplit(".", 1)[1].isdigit()}
    missing = [i for i in range(expected_total_chunks) if i not in received]
    if missing:
        raise errors.DropRoomError(
            f"Upload incomplete: missing chunk(s) {missing[:10]}"
        )

    async def _merged() -> AsyncIterator[bytes]:
        for i in range(expected_total_chunks):
            async for chunk in storage.get_stream(received[i]):
                yield chunk

    total_expected = rec.total_size_bytes or 0
    try:
        total = await storage.put(rec.storage_key, _merged())

        async def _first() -> AsyncIterator[bytes]:
            async for chunk in storage.get_stream(rec.storage_key):
                yield chunk
                break

        first_chunk = await anext(_first(), b"")
    except Exception:
        await storage.delete(rec.storage_key)
        raise

    if total != total_expected:
        await storage.delete(rec.storage_key)
        raise errors.DropRoomError(
            f"Size mismatch: expected {total_expected} bytes, got {total}"
        )
    if total > settings.max_file_size_bytes:
        await storage.delete(rec.storage_key)
        raise errors.FileTooLarge()

    claimed = await _claim_quota(db, room.id, total)
    if not claimed:
        await storage.delete(rec.storage_key)
        for i in range(expected_total_chunks):
            await storage.delete(received[i])
        raise errors.RoomStorageQuotaExceeded("Room storage quota exceeded")

    mime, _ext = magic.normalize_content_type(first_chunk, rec.content_type)
    rec.content_type = mime
    rec.size_bytes = total
    rec.status = FileStatus.CONFIRMED.value
    await db.flush()
    await room_repository.create_audit_event(
        db,
        room.id,
        "FILE_UPLOADED",
        {"file_id": rec.id, "name": rec.original_filename, "size": total},
    )
    for i in range(expected_total_chunks):
        await storage.delete(received[i])
    return rec


async def abort_upload_session(
    db: AsyncSession, storage: StorageBackend, room: Room, rec: FileRecord
) -> None:
    if rec.room_id != room.id:
        raise errors.FileNotFound()
    await cleanup_upload_parts(db, storage, rec)
    await db.delete(rec)
    await db.flush()
    await room_repository.create_audit_event(
        db, room.id, "UPLOAD_ABORTED", {"file_id": rec.id}
    )


async def cleanup_upload_parts(
    db: AsyncSession, storage: StorageBackend, rec: FileRecord
) -> list[str]:
    prefixes = [] if rec is None else [rec.storage_key]
    keys: list[str] = []
    for prefix in prefixes:
        keys.extend(await storage.list_keys(prefix + "."))
    for key in keys:
        await storage.delete(key)
    return keys


async def _authorize_chunked_upload(room: Room, actor: SessionRecord) -> None:
    if room.status != RoomStatus.ACTIVE.value:
        raise errors.RoomExpired()
    if actor.role != "OWNER" and not room.guest_upload_enabled:
        raise errors.PermissionDenied("Guest uploads are disabled in this room")


async def _claim_quota(db: AsyncSession, room_id: int, size_bytes: int) -> bool:
    from app.core.time import utcnow as _now
    from sqlalchemy import text

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
            "now": _now(),
            "id": room_id,
            "status": RoomStatus.ACTIVE.value,
            "max_files": settings.max_files_per_room,
            "max_storage": settings.max_room_storage_bytes,
        },
    )
    return result.first() is not None