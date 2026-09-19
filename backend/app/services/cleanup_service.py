from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utcnow
from app.models import AuditEvent, FileRecord, FileStatus, Room
from app.repositories import (
    file_repository,
    message_repository,
    room_repository,
    session_repository,
)
from app.storage.base import StorageBackend


async def sweep_deleted_objects(db: AsyncSession, storage: StorageBackend) -> None:
    """Idempotent retry for single-file deletions whose object removal failed."""
    result = await db.execute(
        select(FileRecord).where(FileRecord.status == FileStatus.DELETED.value).limit(500)
    )
    for rec in result.scalars():
        await storage.delete(rec.storage_key)


async def sweep_stale_pending(db: AsyncSession, storage: StorageBackend) -> None:
    """Remove upload sessions abandoned mid-stream (PENDING, old) to free quota."""
    cutoff = utcnow() - timedelta(hours=24)
    result = await db.execute(
        select(FileRecord).where(
            FileRecord.status == FileStatus.PENDING.value,
            FileRecord.created_at < cutoff,
        )
    )
    for rec in result.scalars():
        await storage.delete(rec.storage_key)
        await db.delete(rec)


async def cleanup_room(db: AsyncSession, storage: StorageBackend, room: Room) -> bool:
    """Idempotent room teardown. Returns True if this invocation did the wiping.

    Two workers racing on the same room: only one wins the ACTIVE/EXPIRED ->
    DELETING transition; the loser sees `False` and no-ops.
    """
    if room.status == "ACTIVE":
        changed = await room_repository.mark_room_expired(db, room.id)
        await db.flush()
        if changed:
            room.status = "EXPIRED"
    if not await room_repository.mark_room_deleting(db, room.id):
        return False  # already being deleted by another worker
    room.status = "DELETING"

    await _delete_content(db, storage, room.id)

    await room_repository.mark_room_deleted(db, room.id)
    room.status = "DELETED"
    await db.flush()
    return True


async def _delete_content(db: AsyncSession, storage: StorageBackend, room_id: int) -> None:
    keys = await file_repository.list_room_storage_keys(db, room_id)
    for key in keys:
        await storage.delete(key)
    await file_repository.delete_file_records(db, room_id)
    await message_repository.delete_messages_by_room(db, room_id)
    await session_repository.delete_by_room(db, room_id)
    await db.execute(delete(AuditEvent).where(AuditEvent.room_id == room_id))
    await db.flush()


async def force_delete_room(db: AsyncSession, storage: StorageBackend, room: Room) -> bool:
    """Owner-initiated immediate deletion (active room). Same idempotent path."""
    return await cleanup_room(db, storage, room)


async def run_cleanup_pass(db: AsyncSession, storage: StorageBackend) -> list[Room]:
    """One full cleanup pass. Returns the room rows fully torn down this pass."""
    await sweep_deleted_objects(db, storage)
    await sweep_stale_pending(db, storage)
    rooms = await room_repository.list_rooms_pending_cleanup(db)
    cleaned: list[Room] = []
    for room in rooms:
        try:
            if await cleanup_room(db, storage, room):
                cleaned.append(room)
        except Exception:
            await db.rollback()
            continue
    return cleaned