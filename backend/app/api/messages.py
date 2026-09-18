from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, require_member, validate_csrf
from app.api.rooms import ws_channel
from app.models import Room, SessionRecord
from app.schemas import MessageView
from app.services import message_service

router = APIRouter(prefix="/rooms/{room_token}/messages", tags=["messages"])

Db = Annotated[AsyncSession, Depends(get_db)]


def _message_view(msg) -> MessageView:
    return MessageView(
        id=msg.id,
        display_name=msg.display_name,
        body=msg.body,
        created_at=msg.created_at,
    )


@router.get("", response_model=list[MessageView])
async def list_messages(
    room_token: str,
    auth: Annotated[tuple[SessionRecord, Room], Depends(require_member)],
    db: Db,
) -> list[MessageView]:
    _session, room = auth
    messages = await message_service.list_messages(db, room.id)
    return [_message_view(m) for m in messages]


@router.delete("/{message_id}", status_code=200)
async def delete_message(
    room_token: str,
    message_id: int,
    auth: Annotated[tuple[SessionRecord, Room], Depends(require_member)],
    db: Db,
    _: Annotated[None, Depends(validate_csrf)],
) -> dict:
    session_rec, room = auth
    ok = await message_service.delete_message(db, room, session_rec, message_id)
    await db.commit()
    if ok:
        from app.ws.manager import manager

        await manager.broadcast(
            ws_channel(room),
            {"type": "CHAT_MESSAGE_DELETED", "payload": {"messageId": message_id}},
        )
    return {"ok": ok}