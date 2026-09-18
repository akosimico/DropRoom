import asyncio
import logging

from app.core.config import get_settings
from app.db.session import get_session_factory
from app.services.cleanup_service import run_cleanup_pass
from app.storage import get_storage_backend

logger = logging.getLogger("droproom.cleanup")


class CleanupScheduler:
    """Background `asyncio` task that periodically expires rooms and wipes content.

    This is the MVP's simple periodic loop (plan §28 Step 6); the job-queue
    worker system from §23 replaces the loop body without touching the room
    teardown logic (cleanup_service.cleanup_room is already idempotent).
    """

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def run(self) -> None:
        settings = get_settings()
        while not self._stop.is_set():
            try:
                await self._pass_once()
            except Exception:
                logger.exception("cleanup pass failed")
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=settings.cleanup_interval_seconds
                )
            except TimeoutError:
                continue

    async def _pass_once(self) -> None:
        from app.ws.manager import manager

        async with get_session_factory()() as db:
            cleaned = await run_cleanup_pass(db, get_storage_backend())
            await db.commit()
            for room in cleaned:
                from app.api.rooms import ws_channel

                await manager.broadcast(
                    ws_channel(room), {"type": "ROOM_EXPIRED", "payload": {}}
                )

    def start(self) -> None:
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except (TimeoutError, asyncio.CancelledError):
                self._task.cancel()
            self._task = None


scheduler = CleanupScheduler()