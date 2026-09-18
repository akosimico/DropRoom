from collections.abc import Sequence
from typing import cast

from sqlalchemy import delete, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utcnow
from app.models import Message


async def create_message(
    session: AsyncSession,
    *,
    room_id: int,
    sender_session_id: int | None,
    display_name: str | None,
    body: str,
) -> Message:
    msg = Message(
        room_id=room_id,
        sender_session_id=sender_session_id,
        display_name=display_name,
        body=body,
        created_at=utcnow(),
    )
    session.add(msg)
    await session.flush()
    return msg


async def list_room_messages(
    session: AsyncSession, room_id: int, limit: int = 200
) -> Sequence[Message]:
    """Return the latest *limit* messages (oldest first)."""
    result = await session.execute(
        select(Message)
        .where(Message.room_id == room_id)
        .order_by(Message.created_at.desc())
        .limit(limit)
    )
    return list(reversed(list(result.scalars().all())))


async def delete_message(session: AsyncSession, message_id: int, room_id: int) -> bool:
    result = await session.execute(
        delete(Message).where(Message.id == message_id, Message.room_id == room_id)
    )
    return cast(CursorResult, result).rowcount > 0


async def delete_messages_by_room(session: AsyncSession, room_id: int) -> None:
    await session.execute(delete(Message).where(Message.room_id == room_id))