from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core import errors
from app.core.config import get_settings
from app.core.ratelimit import RateLimiterDeps
from app.core.security import hash_token
from app.core.time import ensure_aware, has_expired, utcnow
from app.db.session import get_session
from app.models import Room, SessionRecord
from app.repositories import file_repository, room_repository, session_repository
from app.schemas import FileView, MessageView
from app.services import message_service
from app.ws.manager import manager

router = APIRouter()
limiter = RateLimiterDeps()


async def _ws_auth(websocket: WebSocket, db) -> tuple[SessionRecord, Room] | None:
    settings = get_settings()
    token = websocket.query_params.get("token")
    if not token:
        token = websocket.cookies.get(settings.session_cookie_name)
    if not token:
        return None
    session = await session_repository.get_by_token_hash(db, hash_token(token))
    if session is None or has_expired(session.expires_at):
        return None
    room_token = websocket.path_params.get("room_token", "")
    if not room_token:
        return None
    room = await room_repository.get_room_by_access_token(db, room_token)
    if room is None or room.id != session.room_id:
        return None
    if room.status != "ACTIVE":
        return None
    return session, room


def _file_view(rec) -> FileView:
    return FileView(
        id=rec.id,
        original_filename=rec.original_filename,
        content_type=rec.content_type,
        size_bytes=rec.size_bytes,
        status=rec.status,
        download_count=rec.download_count,
        uploaded_by_name=None,
        created_at=rec.created_at,
    )


def _message_view(msg) -> MessageView:
    return MessageView(
        id=msg.id,
        display_name=msg.display_name,
        body=msg.body,
        created_at=msg.created_at,
    )


async def _send_state(websocket: WebSocket, db, session: SessionRecord, room: Room) -> None:
    files = await file_repository.list_confirmed_files(db, room.id)
    sessions = await session_repository.get_active_sessions(db, room.id)
    messages = await message_service.list_messages(db, room.id)
    now = utcnow()
    remaining = max(0, int((ensure_aware(room.expires_at) - now).total_seconds()))
    from app.api.rooms import ws_channel

    channel = ws_channel(room)
    # People = currently-connected sessions only, so stale/expired rows that
    # joined but left never linger in the list.
    connected = [s for s in sessions if manager.is_session_connected(channel, s.id)]
    await websocket.send_json(
        {
            "type": "ROOM_STATE",
            "payload": {
                "room": {
                    "roomCode": room.room_code,
                    "name": room.name,
                    "guestUploadEnabled": room.guest_upload_enabled,
                    "guestDownloadEnabled": room.guest_download_enabled,
                    "userCount": room.user_count,
                    "maxUsers": room.max_users,
                    "storageUsedBytes": room.storage_used_bytes,
                    "fileCount": room.file_count,
                    "expiresAt": room.expires_at.isoformat(),
                    "remainingSeconds": remaining,
                },
                "role": session.role,
                "me": {"id": session.id, "displayName": session.display_name},
                "members": [
                    {"sessionId": s.id, "displayName": s.display_name, "role": s.role}
                    for s in connected
                ],
                "files": [_file_view(f).model_dump(mode="json", by_alias=True) for f in files],
                "messages": [_message_view(m).model_dump(mode="json", by_alias=True) for m in messages],
            },
        }
    )
    if remaining < get_settings().room_expiring_threshold_seconds:
        await websocket.send_json(
            {
                "type": "ROOM_EXPIRING",
                "payload": {"remainingSeconds": remaining},
            }
        )


@router.websocket("/ws/rooms/{room_token}")
async def room_ws(websocket: WebSocket):
    db_gen = get_session()
    db = await db_gen.__anext__()
    session, room = None, None
    try:
        auth = await _ws_auth(websocket, db)
        if auth is None:
            await websocket.close(code=4401)
            return
        session, room = auth
        from app.api.rooms import ws_channel

        channel = ws_channel(room)
        await manager.connect(channel, session.id, websocket)
        await _send_state(websocket, db, session, room)
        await manager.broadcast(
            channel,
            {
                "type": "USER_JOINED",
                "payload": {
                    "sessionId": session.id,
                    "displayName": session.display_name,
                    "role": session.role,
                },
            },
            exclude_session_ids={session.id},
        )
        while True:
            data = await websocket.receive_json()
            if not isinstance(data, dict) or "type" not in data:
                continue
            await _handle_message(websocket, db, channel, session, room, data)
    except WebSocketDisconnect:
        pass
    except Exception:
        try:
            await websocket.close(code=1011)
        except Exception:
            pass
    finally:
        if session is not None and room is not None:
            from app.api.rooms import ws_channel

            channel = ws_channel(room)
            manager.disconnect(channel, session.id, websocket)
            if not manager.is_session_connected(channel, session.id):
                await manager.broadcast(
                    channel,
                    {
                        "type": "USER_LEFT",
                        "payload": {"sessionId": session.id, "displayName": session.display_name},
                    },
                    exclude_session_ids={session.id},
                )
                # Free a capacity slot and stop showing the leaver in People.
                await room_repository.decrement_user_count(db, room.id)
                await session_repository.expire_session(db, session.id)
                await db.commit()
        try:
            await db_gen.aclose()
        except Exception:
            pass


async def _send_error(websocket: WebSocket, code: str, detail: str) -> None:
    try:
        await websocket.send_json({"type": "ERROR", "payload": {"code": code, "detail": detail}})
    except Exception:
        pass


async def _handle_message(
    websocket: WebSocket,
    db,
    channel: str,
    session: SessionRecord,
    room: Room,
    data: dict[str, Any],
) -> None:
    msg_type = data.get("type")
    payload = data.get("payload") or {}
    settings = get_settings()

    if msg_type == "PING":
        await websocket.send_json({"type": "PONG"})
        return

    if msg_type == "CHAT_SEND":
        try:
            limiter.check(f"chat:{room.id}", f"s{session.id}", settings.chat_limit)
        except errors.RateLimited:
            await _send_error(websocket, "CHAT_RATE_LIMITED", "Too many messages; slow down")
            return
        try:
            msg = await message_service.send_message(
                db, room, session, str(payload.get("body", ""))
            )
            await db.commit()
            await room_repository.create_audit_event(db, room.id, "MESSAGE_SENT")
            await db.commit()
        except errors.DropRoomError as exc:
            await _send_error(websocket, exc.code, exc.detail)
            return
        await manager.broadcast(
            channel,
            {"type": "CHAT_MESSAGE_SENT", "payload": {"message": _message_view(msg).model_dump(mode="json", by_alias=True)}},
        )
        return

    if msg_type == "CHAT_DELETE":
        try:
            message_id = int(payload.get("messageId", 0))
            ok = await message_service.delete_message(db, room, session, message_id)
            await db.commit()
        except errors.DropRoomError as exc:
            await _send_error(websocket, exc.code, exc.detail)
            return
        if ok:
            await manager.broadcast(
                channel, {"type": "CHAT_MESSAGE_DELETED", "payload": {"messageId": message_id}}
            )
        return

    await _send_error(websocket, "UNKNOWN_MESSAGE_TYPE", msg_type or "")