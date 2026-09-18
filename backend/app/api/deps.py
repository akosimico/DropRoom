from typing import Annotated

from fastapi import Depends, Header, Request, WebSocket
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import errors
from app.core.config import get_settings
from app.core.security import hash_token, verify_csrf_token
from app.core.time import has_expired
from app.db.session import get_session
from app.models import Room, SessionRecord
from app.repositories import room_repository, session_repository

__all__ = ["get_db", "require_session", "validate_csrf"]


async def get_db() -> AsyncSession:  # type: ignore[misc]
    async for sess in get_session():
        yield sess


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def _session_from_cookies(
    request: Request, db: AsyncSession
) -> SessionRecord | None:
    settings = get_settings()
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        return None
    return await session_repository.get_by_token_hash(db, hash_token(token))


async def _session_from_ws(
    websocket: WebSocket, db: AsyncSession
) -> SessionRecord | None:
    token = websocket.query_params.get("token")
    if token:
        return await session_repository.get_by_token_hash(db, hash_token(token))
    return None


async def require_session(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SessionRecord:
    session = await _session_from_cookies(request, db)
    if session is None or has_expired(session.expires_at):
        raise errors.NotAuthenticated()
    return session


def validate_csrf(
    request: Request,
    x_csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> None:
    """Require a signed double-submit CSRF token on mutating requests.

    State-changing methods (POST/PATCH/DELETE) are called via a frontend that
    reads the cookie (not HttpOnly) and echoes it in a header. The signature
    prevents a sibling-subdomain from crafting a valid header.
    """
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    settings = get_settings()
    cookie_value = request.cookies.get(settings.csrf_cookie_name)
    if not cookie_value or not x_csrf_token:
        raise errors.CSRFValidationFailed("CSRF token missing")
    if not verify_csrf_token(cookie_value, settings.csrf_secret):
        raise errors.CSRFValidationFailed("CSRF cookie is not valid")
    if cookie_value != x_csrf_token:
        raise errors.CSRFValidationFailed("CSRF header does not match cookie")


async def get_room_by_token(
    room_token: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Room:
    room = await room_repository.get_room_by_access_token(db, room_token)
    if room is None:
        raise errors.RoomNotFound()
    return room


async def require_room_owner(
    session: Annotated[SessionRecord, Depends(require_session)],
    room: Annotated[Room, Depends(get_room_by_token)],
) -> tuple[SessionRecord, Room]:
    from app.models import Role

    if session.role != Role.OWNER.value:
        raise errors.PermissionDenied()
    return session, room


async def require_member(
    room_token: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> tuple[SessionRecord, Room]:
    """Authenticated member of the room (owner or guest)."""
    session = await require_session(request, db)
    room = await get_room_by_token(room_token, db)
    if session.room_id != room.id:
        raise errors.PermissionDenied("You are not a member of this room")
    return session, room