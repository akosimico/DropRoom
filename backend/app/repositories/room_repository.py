from collections.abc import Sequence
from datetime import timedelta
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import generate_room_code, generate_token, hash_token
from app.core.time import utcnow
from app.models import Room, RoomStatus
from app.models.audit_event import AuditEvent


async def create_room(
    session: AsyncSession,
    *,
    name: str | None,
    password_hash: str | None,
    guest_upload_enabled: bool,
    guest_download_enabled: bool,
    max_users: int,
    lifetime_seconds: int,
    base_url: str,
) -> tuple[Room, str, str, str]:
    """Return (room, access_token, owner_token, room_code)."""
    settings = get_settings()
    access_token = generate_token(32)
    owner_token = generate_token(32)
    room_code = generate_room_code(settings.room_code_length)
    from app.core.security import sign_access_token

    now = utcnow()
    room = Room(
        room_code=room_code,
        access_token_hash=hash_token(access_token),
        access_token_signed=sign_access_token(
            access_token, settings.csrf_secret, settings.max_room_lifetime_seconds + 3600
        ),
        owner_token_hash=hash_token(owner_token),
        password_hash=password_hash,
        name=name,
        status=RoomStatus.ACTIVE.value,
        guest_upload_enabled=guest_upload_enabled,
        guest_download_enabled=guest_download_enabled,
        max_users=max_users,
        user_count=0,
        storage_used_bytes=0,
        file_count=0,
        created_at=now,
        expires_at=now + timedelta(seconds=lifetime_seconds),
        updated_at=now,
    )
    session.add(room)
    await session.flush()
    event = AuditEvent(room_id=room.id, event_type="ROOM_CREATED")
    session.add(event)
    await session.flush()
    return room, access_token, owner_token, room_code


async def get_room_by_access_token(session: AsyncSession, access_token: str) -> Room | None:
    h = hash_token(access_token)
    result = await session.execute(select(Room).where(Room.access_token_hash == h))
    return result.scalar_one_or_none()


async def get_room_by_owner_token(session: AsyncSession, owner_token: str) -> Room | None:
    h = hash_token(owner_token)
    result = await session.execute(select(Room).where(Room.owner_token_hash == h))
    return result.scalar_one_or_none()


async def get_room_by_code(session: AsyncSession, room_code: str) -> Room | None:
    result = await session.execute(
        select(Room).where(Room.room_code == room_code.upper().strip())
    )
    return result.scalar_one_or_none()


def recover_access_token(room: Room | None) -> str | None:
    """Recover the shareable access token from its signed form (used by
    join-by-code so the client can build the room URL)."""
    from app.core.config import get_settings
    from app.core.security import unsign_access_token

    if room is None or not room.access_token_signed:
        return None
    settings = get_settings()
    return unsign_access_token(
        room.access_token_signed, settings.csrf_secret, settings.max_room_lifetime_seconds + 3600
    )


async def get_room_by_id(session: AsyncSession, room_id: int) -> Room | None:
    result = await session.execute(select(Room).where(Room.id == room_id))
    return result.scalar_one_or_none()


async def update_room_fields(
    session: AsyncSession, room_id: int, **kwargs: Any
) -> Room | None:
    await session.execute(
        update(Room).where(Room.id == room_id).values(**kwargs, updated_at=utcnow())
    )
    return await get_room_by_id(session, room_id)


async def increment_user_count_atomic(session: AsyncSession, room_id: int) -> bool:
    """Attempt atomic increment if under limit. Returns True on success, False if full."""
    result = await session.execute(
        update(Room)
        .where(Room.id == room_id, Room.user_count < Room.max_users)
        .values(user_count=Room.user_count + 1, updated_at=utcnow())
    )
    return cast(CursorResult, result).rowcount > 0


async def decrement_user_count(session: AsyncSession, room_id: int) -> None:
    await session.execute(
        update(Room)
        .where(Room.id == room_id, Room.user_count > 0)
        .values(user_count=Room.user_count - 1, updated_at=utcnow())
    )


async def update_storage_and_file_count(
    session: AsyncSession, room_id: int, size_delta: int, file_delta: int
) -> None:
    await session.execute(
        update(Room)
        .where(Room.id == room_id)
        .values(
            storage_used_bytes=Room.storage_used_bytes + size_delta,
            file_count=Room.file_count + file_delta,
            updated_at=utcnow(),
        )
    )


async def mark_room_expired(session: AsyncSession, room_id: int) -> bool:
    """Mark ACTIVE -> EXPIRED. Returns True if transitioned (idempotent)."""
    result = await session.execute(
        update(Room)
        .where(Room.id == room_id, Room.status == RoomStatus.ACTIVE.value)
        .values(status=RoomStatus.EXPIRED.value, updated_at=utcnow())
    )
    return cast(CursorResult, result).rowcount > 0


async def mark_room_deleting(session: AsyncSession, room_id: int) -> bool:
    result = await session.execute(
        update(Room)
        .where(Room.id == room_id, Room.status == RoomStatus.EXPIRED.value)
        .values(status=RoomStatus.DELETING.value, updated_at=utcnow())
    )
    return cast(CursorResult, result).rowcount > 0


async def mark_room_deleted(session: AsyncSession, room_id: int) -> bool:
    result = await session.execute(
        update(Room)
        .where(Room.id == room_id, Room.status == RoomStatus.DELETING.value)
        .values(status=RoomStatus.DELETED.value, updated_at=utcnow())
    )
    return cast(CursorResult, result).rowcount > 0


async def list_rooms_pending_cleanup(
    session: AsyncSession, limit: int = 100
) -> Sequence[Room]:
    now = utcnow()
    result = await session.execute(
        select(Room)
        .where(
            Room.status.in_([RoomStatus.ACTIVE.value, RoomStatus.EXPIRED.value]),
            Room.expires_at <= now,
        )
        .limit(limit)
    )
    return list(result.scalars().all())


async def create_audit_event(
    session: AsyncSession,
    room_id: int | None,
    event_type: str,
    metadata: dict[str, Any] | None = None,
) -> AuditEvent:
    event = AuditEvent(room_id=room_id, event_type=event_type, metadata_json=metadata)
    session.add(event)
    return event