from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, validate_csrf
from app.core import errors
from app.core.config import get_settings
from app.core.ratelimit import RateLimiterDeps
from app.core.security import hash_token, make_csrf_token
from app.core.time import ensure_aware, has_expired, utcnow
from app.models import Role, Room, SessionRecord
from app.repositories import room_repository, session_repository
from app.schemas import (
    JoinByCodeRequest,
    JoinRequest,
    JoinResponse,
    RoomCreate,
    RoomCreated,
    RoomPatch,
    RoomView,
)
from app.services import room_service

router = APIRouter(prefix="/rooms", tags=["rooms"])
limiter = RateLimiterDeps()

Db = Annotated[AsyncSession, Depends(get_db)]


def ws_channel(room: Room) -> str:
    return f"room:{room.id}"


def client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def set_auth_cookies(response: Response, session_token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        settings.session_cookie_name,
        session_token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        domain=settings.cookie_domain,
        path="/",
    )
    response.set_cookie(
        settings.csrf_cookie_name,
        make_csrf_token(settings.csrf_secret),
        max_age=settings.session_ttl_seconds,
        httponly=False,
        secure=settings.cookie_secure,
        samesite="strict",
        domain=settings.cookie_domain,
        path="/",
    )


def room_view(room: Room) -> RoomView:
    return RoomView.model_validate(room)


async def _load_session(request: Request, db: AsyncSession) -> SessionRecord:
    settings = get_settings()
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        raise errors.NotAuthenticated()
    session = await session_repository.get_by_token_hash(db, hash_token(token))
    if session is None or has_expired(session.expires_at):
        raise errors.NotAuthenticated()
    return session


async def require_room_owner(
    room_token: str,
    request: Request,
    db: Db,
) -> tuple[SessionRecord, Room]:
    """Authenticated member + owner-role check dependency."""
    session = await _load_session(request, db)
    room = await room_repository.get_room_by_access_token(db, room_token)
    if room is None:
        raise errors.RoomNotFound()
    if session.room_id != room.id:
        raise errors.PermissionDenied("You are not a member of this room")
    if session.role != Role.OWNER.value:
        raise errors.PermissionDenied("Only the room owner can do this")
    return session, room


async def _establish_session(
    db: AsyncSession,
    request: Request,
    response: Response,
    room: Room,
    join: JoinRequest,
    existing_session_token: str | None,
) -> tuple[SessionRecord, str, bool]:
    """Return (session, raw_token, created). Reuses a live cookie session for the
    same room so refreshing the page does not burn more of the 10-user capacity.
    """
    settings = get_settings()
    if existing_session_token:
        existing = await session_repository.get_by_token_hash(
            db, hash_token(existing_session_token)
        )
        if existing and existing.room_id == room.id:
            existing.expires_at = utcnow() + timedelta(seconds=settings.session_ttl_seconds)
            existing.last_seen_at = utcnow()
            await db.flush()
            return existing, existing_session_token, False
    sess, token = await room_service.join_room(
        db,
        room,
        display_name=join.display_name,
        password=join.password,
        owner_token=join.owner_token,
    )
    set_auth_cookies(response, token)
    return sess, token, True


def _join_response(room: Room, sess: SessionRecord, token: str, access_token: str) -> JoinResponse:
    return JoinResponse(
        accessToken=access_token,
        room=room_view(room),
        role=sess.role,
        displayName=sess.display_name,
        sessionExpiresAt=sess.expires_at,
        wsUrl=f"/ws/rooms/{access_token}?token={token}",
    )


async def broadcast_settings(room: Room) -> None:
    from app.ws.manager import manager

    await manager.broadcast(
        ws_channel(room),
        {"type": "ROOM_SETTINGS_CHANGED", "payload": {"room": room_view(room).model_dump(by_alias=True)}},
    )


async def broadcast_expired(room: Room) -> None:
    from app.ws.manager import manager

    await manager.broadcast(ws_channel(room), {"type": "ROOM_EXPIRED", "payload": {}})


@router.post("", response_model=RoomCreated, status_code=201)
async def create_room(
    body: RoomCreate,
    request: Request,
    response: Response,
    db: Db,
    _: Annotated[None, Depends(validate_csrf)],
) -> RoomCreated:
    settings = get_settings()
    limiter.check("create_room", client_ip(request), settings.create_room_limit)
    room, secrets = await room_service.create_room(db, body)
    # Owner auto-joins on creation so the first UI visit is already authorized.
    _sess, token = await room_service.join_room(
        db, room, display_name="Owner", owner_token=secrets["owner_token"]
    )
    await db.commit()
    set_auth_cookies(response, token)
    base = settings.base_url.rstrip("/")
    return RoomCreated(
        roomCode=secrets["room_code"],
        name=room.name,
        expiresAt=room.expires_at,
        accessToken=secrets["access_token"],
        shareUrl=f"{base}/r/{secrets['access_token']}",
        ownerUrl=f"{base}/r/{secrets['access_token']}?owner={secrets['owner_token']}",
        passwordRequired=bool(room.password_hash),
        guestUploadEnabled=room.guest_upload_enabled,
        guestDownloadEnabled=room.guest_download_enabled,
    )


@router.get("/{room_token}")
async def get_room(
    room_token: str,
    db: Db,
) -> dict:
    room = await room_repository.get_room_by_access_token(db, room_token)
    if room is None:
        raise errors.RoomNotFound()
    view = room_view(room).model_dump(by_alias=True)
    view["passwordRequired"] = bool(room.password_hash)
    view["remainingSeconds"] = max(0, int((ensure_aware(room.expires_at) - utcnow()).total_seconds()))
    view["canJoin"] = room.status == "ACTIVE" and room.user_count < room.max_users
    return view


@router.post("/{room_token}/join", response_model=JoinResponse)
async def join_room(
    room_token: str,
    body: JoinRequest,
    request: Request,
    response: Response,
    db: Db,
    _: Annotated[None, Depends(validate_csrf)],
) -> JoinResponse:
    settings = get_settings()
    limiter.check("join", client_ip(request), settings.generic_limit)
    room = await room_repository.get_room_by_access_token(db, room_token)
    if room is None:
        raise errors.RoomNotFound()
    existing_token = request.cookies.get(settings.session_cookie_name)
    sess, token, _created = await _establish_session(
        db, request, response, room, body, existing_token
    )
    await db.commit()
    return _join_response(room, sess, token, room_token)


@router.post("/join-by-code", response_model=JoinResponse, status_code=201)
async def join_by_code(
    body: JoinByCodeRequest,
    request: Request,
    response: Response,
    db: Db,
    _: Annotated[None, Depends(validate_csrf)],
) -> JoinResponse:
    settings = get_settings()
    # Stricter, independent limit: this endpoint is the room-code brute-force target.
    limiter.check("join_by_code", client_ip(request), settings.join_by_code_limit)
    room = await room_repository.get_room_by_code(db, body.room_code)
    try:
        if room is None:
            raise errors.JoinFailed("Room not found or has expired")
        existing_token = request.cookies.get(settings.session_cookie_name)
        sess, token, _created = await _establish_session(
            db, request, response, room, body, existing_token
        )
    except errors.DropRoomError as exc:
        if isinstance(exc, errors.RoomFull):
            raise
        # Generic failure: do not leak code existence / expiry / wrong password.
        raise errors.JoinFailed("Room not found or has expired")
    await db.commit()
    access_token = room_repository.recover_access_token(room) or room.room_code
    return _join_response(room, sess, token, access_token)


@router.patch("/{room_token}", response_model=RoomView)
async def patch_room(
    room_token: str,
    body: RoomPatch,
    auth: Annotated[tuple[SessionRecord, Room], Depends(require_room_owner)],
    db: Db,
    _: Annotated[None, Depends(validate_csrf)],
) -> RoomView:
    _session, room = auth
    updated = await room_service.patch_room(db, room, body)
    await db.commit()
    await broadcast_settings(updated)
    return room_view(updated)


@router.delete("/{room_token}", status_code=200)
async def delete_room(
    room_token: str,
    auth: Annotated[tuple[SessionRecord, Room], Depends(require_room_owner)],
    db: Db,
    _: Annotated[None, Depends(validate_csrf)],
) -> dict:
    from app.services import cleanup_service
    from app.storage import get_storage_backend

    _session, room = auth
    await cleanup_service.force_delete_room(db, get_storage_backend(), room)
    await db.commit()
    await broadcast_expired(room)
    return {"ok": True}