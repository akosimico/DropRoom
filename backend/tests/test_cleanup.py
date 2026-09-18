import asyncio

from sqlalchemy import func, select, update

from app.core.time import utcnow
from app.db.session import get_session_factory
from app.models import FileRecord, Message, Room, RoomStatus, SessionRecord
from app.services.cleanup_service import force_delete_room, run_cleanup_pass
from app.storage import get_storage_backend
from tests.conftest import create_room, join_headers, join_room


async def _make_room_with_file(client, filename="expire.txt", content=b"ephemeral"):
    resp, jar = await create_room(client, name="Cleanup")
    token = resp.json()["accessToken"]
    up = await client.post(
        f"/api/v1/rooms/{token}/files",
        files={"file": (filename, content, "text/plain")},
        cookies=jar,
        headers=join_headers(jar),
    )
    assert up.status_code == 201, up.text
    return token, jar


async def _expire_all_rooms() -> None:
    async with get_session_factory()() as db:
        await db.execute(
            update(Room).where(Room.access_token_hash.isnot(None)).values(expires_at=utcnow())
        )
        await db.commit()


async def test_cleanup_removes_expired_room_entirely(client):
    await _make_room_with_file(client)
    await _expire_all_rooms()

    async with get_session_factory()() as db:
        cleaned = await run_cleanup_pass(db, get_storage_backend())
        await db.commit()
    assert cleaned, "expected at least one room to be cleaned"

    async with get_session_factory()() as db:
        assert await db.scalar(select(func.count()).select_from(Room)) >= 1
        room = (
            await db.execute(select(Room).where(Room.access_token_hash.isnot(None)))
        ).scalar_one()
        assert room.status == RoomStatus.DELETED.value
        assert await db.scalar(select(func.count()).select_from(FileRecord)) == 0
        assert await db.scalar(select(func.count()).select_from(Message)) == 0
        assert await db.scalar(select(func.count()).select_from(SessionRecord)) == 0


async def test_cleanup_is_idempotent_under_concurrency(client):
    """Two workers racing on the same expired room: only one cleans, the other
    no-ops cleanly — no double-delete errors."""
    await _make_room_with_file(client)
    await _expire_all_rooms()
    storage = get_storage_backend()

    async def worker():
        async with get_session_factory()() as db:
            try:
                result = await run_cleanup_pass(db, storage)
                await db.commit()
                return len(result)
            except Exception:
                await db.rollback()
                return -1

    outcomes = await asyncio.gather(worker(), worker())
    assert outcomes.count(1) == 1, outcomes
    assert outcomes.count(0) == 1, outcomes


async def test_force_delete_active_room(client):
    token, jar = await _make_room_with_file(client)
    async with get_session_factory()() as db:
        room = (
            await db.execute(select(Room).where(Room.access_token_hash.isnot(None)))
        ).scalar_one()
        ok = await force_delete_room(db, get_storage_backend(), room)
        await db.commit()
    assert ok
    r, _ = await join_room(client, token, password="", display_name="late")
    assert r.status_code == 410