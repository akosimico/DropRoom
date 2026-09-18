from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import errors
from app.core.config import get_settings
from app.models import Role, Room, SessionRecord
from app.repositories import message_repository


def validate_body(body: str) -> str:
    settings = get_settings()
    body = body.strip()
    if not body:
        raise errors.DropRoomError("Message must not be empty")
    if len(body) > settings.max_message_length:
        raise errors.DropRoomError(
            f"Message exceeds the maximum length of {settings.max_message_length} characters"
        )
    return body


async def send_message(
    db: AsyncSession, room: Room, actor: SessionRecord, body: str
) -> Any:
    clean = validate_body(body)
    msg = await message_repository.create_message(
        db,
        room_id=room.id,
        sender_session_id=actor.id,
        display_name=actor.display_name,
        body=clean,
    )
    await db.flush()
    return msg


async def list_messages(
    db: AsyncSession, room_id: int, limit: int | None = None
) -> Sequence[Any]:
    settings = get_settings()
    return await message_repository.list_room_messages(
        db, room_id, limit or settings.chat_scrollback
    )


async def delete_message(
    db: AsyncSession, room: Room, actor: SessionRecord, message_id: int
) -> bool:
    if actor.role != Role.OWNER.value:
        raise errors.PermissionDenied("Only the room owner can delete chat messages")
    return await message_repository.delete_message(db, message_id, room.id)