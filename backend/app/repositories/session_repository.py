from collections.abc import Sequence
from datetime import timedelta
from typing import cast

from sqlalchemy import delete, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.time import utcnow
from app.models import SessionRecord


async def create_session(
    session: AsyncSession,
    *,
    room_id: int,
    session_token_hash: str,
    display_name: str | None,
    role: str,
) -> SessionRecord:
    now = utcnow()
    settings = get_settings()
    sess = SessionRecord(
        room_id=room_id,
        session_token_hash=session_token_hash,
        display_name=display_name or "Guest",
        role=role,
        connected_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(seconds=settings.session_ttl_seconds),
    )
    session.add(sess)
    await session.flush()
    return sess


async def get_by_token_hash(session: AsyncSession, token_hash: str) -> SessionRecord | None:
    result = await session.execute(
        select(SessionRecord).where(
            SessionRecord.session_token_hash == token_hash,
            SessionRecord.expires_at > utcnow(),
        )
    )
    return result.scalar_one_or_none()


async def get_active_sessions(session: AsyncSession, room_id: int) -> Sequence[SessionRecord]:
    result = await session.execute(
        select(SessionRecord).where(
            SessionRecord.room_id == room_id,
            SessionRecord.expires_at > utcnow(),
        )
    )
    return list(result.scalars().all())


async def delete_expired_sessions(session: AsyncSession) -> int:
    """Delete all expired session rows and return count (cleanup should use atomic user_count update)."""
    result = await session.execute(
        delete(SessionRecord).where(SessionRecord.expires_at <= utcnow())
    )
    return cast(CursorResult, result).rowcount


async def delete_by_room(session: AsyncSession, room_id: int) -> int:
    result = await session.execute(
        delete(SessionRecord).where(SessionRecord.room_id == room_id)
    )
    return cast(CursorResult, result).rowcount


async def touch_session(session: AsyncSession, sess: SessionRecord) -> None:
    """Update last_seen_at to refresh session activity."""
    await session.execute(
        update(SessionRecord)
        .where(SessionRecord.id == sess.id)
        .values(last_seen_at=utcnow())
    )


async def expire_session(session: AsyncSession, session_id: int) -> None:
    """Immediately expire a session (e.g. when its last socket leaves)."""
    await session.execute(
        update(SessionRecord)
        .where(SessionRecord.id == session_id)
        .values(expires_at=utcnow(), last_seen_at=utcnow())
    )