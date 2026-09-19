from datetime import timedelta
from typing import Any

from app.core.time import utcnow
from app.db.session import get_session_factory
from app.models import FileRecord, FileStatus, RoomStatus
from app.repositories import file_repository, room_repository
from app.storage import get_storage_backend

JOB_ROOM_CLEANUP = "ROOM_CLEANUP"
JOB_DELETE_FILE_OBJECT = "DELETE_FILE_OBJECT"
JOB_EXPIRE_UPLOAD_SESSION = "EXPIRE_UPLOAD_SESSION"
JOB_SEND_ROOM_EXPIRATION_EVENT = "SEND_ROOM_EXPIRATION_EVENT"
JOB_CLEANUP_ORPHANED_OBJECT = "CLEANUP_ORPHANED_OBJECT"
JOB_GENERATE_AUDIT_EVENT = "GENERATE_AUDIT_EVENT"


async def run_room_cleanup(payload: dict[str, Any]) -> None:
    """Tear down one room (EXPIRED -> DELETING -> DELETED) and wipe content.

    Idempotent: cleanup_service.cleanup_room uses atomic status transitions, so
    when two workers race on the same room only one performs the wipe.
    """
    from app.services.cleanup_service import cleanup_room
    from app.ws.manager import manager

    room_id = payload.get("room_id")
    if room_id is None:
        return
    async with get_session_factory()() as db:
        room = await room_repository.get_room_by_id(db, room_id)
        if room is None or room.status == RoomStatus.DELETED.value:
            return
        storage = get_storage_backend()
        changed = await cleanup_room(db, storage, room)
        await db.commit()
        if changed:
            await manager.broadcast(
                f"room:{room.id}", {"type": "ROOM_EXPIRED", "payload": {}}
            )


async def delete_file_object(payload: dict[str, Any]) -> None:
    storage_key = payload.get("storage_key")
    if storage_key:
        await get_storage_backend().delete(storage_key)


async def expire_upload_session(payload: dict[str, Any]) -> None:
    """Remove an abandoned PENDING upload: staged parts + metadata row.

    Idempotent: no-ops when the row is already gone or no longer PENDING.
    """
    from app.services.upload_service import cleanup_upload_parts

    file_id = payload.get("file_id")
    if file_id is None:
        return
    async with get_session_factory()() as db:
        rec = await file_repository.get_file_by_id(db, file_id)
        if rec is None or rec.status != FileStatus.PENDING.value:
            return
        storage = get_storage_backend()
        await cleanup_upload_parts(db, storage, rec)
        await storage.delete(rec.storage_key)
        await db.delete(rec)
        await db.commit()


async def send_room_expiration_event(payload: dict[str, Any]) -> None:
    from app.ws.manager import manager

    room_id = payload.get("room_id")
    if room_id is not None:
        await manager.broadcast(
            f"room:{room_id}", {"type": "ROOM_EXPIRED", "payload": {}}
        )


async def cleanup_orphaned_object(payload: dict[str, Any]) -> None:
    storage_key = payload.get("storage_key")
    if storage_key:
        await get_storage_backend().delete(storage_key)


async def generate_audit_event(payload: dict[str, Any]) -> None:
    async with get_session_factory()() as db:
        room_repository.create_audit_event(
            db,
            payload.get("room_id"),
            payload.get("event_type", ""),
            payload.get("metadata"),
        )
        await db.commit()


def register_default_handlers(queue) -> None:
    queue.register(JOB_ROOM_CLEANUP, run_room_cleanup)
    queue.register(JOB_DELETE_FILE_OBJECT, delete_file_object)
    queue.register(JOB_EXPIRE_UPLOAD_SESSION, expire_upload_session)
    queue.register(JOB_SEND_ROOM_EXPIRATION_EVENT, send_room_expiration_event)
    queue.register(JOB_CLEANUP_ORPHANED_OBJECT, cleanup_orphaned_object)
    queue.register(JOB_GENERATE_AUDIT_EVENT, generate_audit_event)