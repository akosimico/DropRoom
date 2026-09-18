from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import errors
from app.core.config import get_settings
from app.core.security import hash_password, hash_token, verify_password
from app.core.time import ensure_aware, has_expired, utcnow
from app.models import Role, Room, RoomStatus
from app.repositories import room_repository, session_repository
from app.schemas import RoomCreate, RoomPatch


def validate_lifetime(lifetime_seconds: int | None) -> int:
    settings = get_settings()
    if lifetime_seconds is None:
        return settings.default_room_lifetime_seconds
    if lifetime_seconds not in settings.room_lifetime_options_seconds:
        raise errors.DropRoomError(
            f"lifetime_seconds must be one of {settings.room_lifetime_options_seconds}"
        )
    if lifetime_seconds > settings.max_room_lifetime_seconds:
        raise errors.DropRoomError("lifetime exceeds the server-enforced maximum")
    return lifetime_seconds


def validate_max_users(max_users: int | None) -> int:
    settings = get_settings()
    if max_users is None:
        return settings.max_users_per_room
    if max_users < 1 or max_users > settings.max_users_per_room:
        raise errors.DropRoomError(
            f"max_users must be between 1 and {settings.max_users_per_room}"
        )
    return max_users


async def create_room(db: AsyncSession, data: RoomCreate) -> tuple[Room, dict[str, str]]:
    settings = get_settings()
    lifetime = validate_lifetime(data.lifetime_seconds)
    max_users = validate_max_users(None)
    password_hash = hash_password(data.password) if data.password else None
    room, access_token, owner_token, room_code = await room_repository.create_room(
        db,
        name=data.name,
        password_hash=password_hash,
        guest_upload_enabled=data.guest_upload_enabled,
        guest_download_enabled=data.guest_download_enabled,
        max_users=max_users,
        lifetime_seconds=lifetime,
        base_url=settings.base_url,
    )
    secrets = {
        "access_token": access_token,
        "owner_token": owner_token,
        "room_code": room_code,
    }
    return room, secrets


def _assert_joinable(room: Room) -> None:
    if room.status == RoomStatus.EXPIRED.value or room.status == RoomStatus.DELETED.value:
        raise errors.RoomExpired()
    if room.status != RoomStatus.ACTIVE.value:
        raise errors.DropRoomError("Room is not active")
    if has_expired(room.expires_at):
        raise errors.RoomExpired()


def _resolve_role(room: Room, password: str | None, owner_token: str | None) -> str:
    if owner_token:
        from app.core.security import hash_token as _ht

        if room.owner_token_hash != _ht(owner_token):
            raise errors.InvalidOwnerToken()
        return Role.OWNER.value
    if room.password_hash:
        if not password or not verify_password(password, room.password_hash):
            # Do not reveal whether the password is missing or wrong.
            raise errors.WrongPassword()
    return Role.GUEST.value


async def join_room(
    db: AsyncSession,
    room: Room,
    *,
    display_name: str | None,
    password: str | None = None,
    owner_token: str | None = None,
) -> tuple[Any, str]:
    """Register a participant atomically. Returns (SessionRecord, raw session token)."""
    from app.core.security import generate_token

    _assert_joinable(room)
    role = _resolve_role(room, password, owner_token)
    ok = await room_repository.increment_user_count_atomic(db, room.id)
    if not ok:
        raise errors.RoomFull()
    token = generate_token(32)
    sess = await session_repository.create_session(
        db,
        room_id=room.id,
        session_token_hash=hash_token(token),
        display_name=display_name,
        role=role,
    )
    await db.flush()
    return sess, token


async def get_room_by_access_or_owner_token(
    db: AsyncSession, access_token: str, owner_token: str | None
) -> Room | None:
    room = await room_repository.get_room_by_access_token(db, access_token)
    return room


async def patch_room(db: AsyncSession, room: Room, data: RoomPatch) -> Room:
    _assert_joinable(room)
    values: dict[str, Any] = {}
    if data.name is not None:
        values["name"] = data.name.strip() or None
    if data.guest_upload_enabled is not None:
        values["guest_upload_enabled"] = data.guest_upload_enabled
    if data.guest_download_enabled is not None:
        values["guest_download_enabled"] = data.guest_download_enabled
    if data.password is not None:
        if data.password == "":
            values["password_hash"] = None
        else:
            values["password_hash"] = hash_password(data.password)
    if not values:
        return room
    updated = await room_repository.update_room_fields(db, room.id, **values)
    return updated or room


async def serialize_room(room: Room) -> dict[str, Any]:
    return {
        "room_code": room.room_code,
        "name": room.name,
        "status": room.status,
        "guest_upload_enabled": room.guest_upload_enabled,
        "guest_download_enabled": room.guest_download_enabled,
        "user_count": room.user_count,
        "max_users": room.max_users,
        "storage_used_bytes": room.storage_used_bytes,
        "file_count": room.file_count,
        "created_at": room.created_at,
        "expires_at": room.expires_at,
    }


def remaining_seconds(room: Room) -> int:
    return max(0, int((ensure_aware(room.expires_at) - utcnow()).total_seconds()))