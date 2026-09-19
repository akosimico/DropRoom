import asyncio
import logging
from datetime import timedelta

from app.core.config import get_settings
from app.core.time import utcnow
from app.db.session import get_session_factory
from app.jobs.handlers import (
    JOB_CLEANUP_ORPHANED_OBJECT,
    JOB_DELETE_FILE_OBJECT,
    JOB_EXPIRE_UPLOAD_SESSION,
    JOB_ROOM_CLEANUP,
)
from app.jobs.queue import get_job_queue
from app.repositories import file_repository, room_repository
from app.storage import get_storage_backend

logger = logging.getLogger("droproom.cleanup")


class CleanupScheduler:
    """Background `asyncio` task that discovers cleanup work and enqueues jobs.

    The scheduler is only a *producer*: it scans for expired rooms, soft-deleted
    objects, stale upload sessions, and orphaned storage keys, then hands each
    item to the job queue (plan §23). Workers run ROOM_CLEANUP etc. with retries
    + exponential backoff; room teardown stays idempotent via the atomic
    ACTIVE/EXPIRED -> DELETING -> DELETED transitions.
    """

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def run(self) -> None:
        settings = get_settings()
        while not self._stop.is_set():
            try:
                await self._scan_once()
            except Exception:
                logger.exception("cleanup scan failed")
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=settings.cleanup_interval_seconds
                )
            except TimeoutError:
                continue

    async def _scan_once(self) -> None:
        queue = get_job_queue()
        settings = get_settings()
        storage = get_storage_backend()
        stale_pending_cutoff = utcnow() - timedelta(hours=24)

        try:
            known: set[str] = set()
            async with get_session_factory()() as db:
                # 1. Rooms past their lifetime -> full teardown job.
                rooms = await room_repository.list_rooms_pending_cleanup(db)
                for room in rooms:
                    queue.enqueue(JOB_ROOM_CLEANUP, {"room_id": room.id})

                # 2. Soft-deleted files whose object removal is still pending.
                for rec in await file_repository.list_deleted_records(db):
                    if await storage.exists(rec.storage_key):
                        queue.enqueue(
                            JOB_DELETE_FILE_OBJECT, {"storage_key": rec.storage_key}
                        )

                # 3. Upload sessions abandoned mid-stream.
                for rec in await file_repository.list_stale_pending(
                    db, stale_pending_cutoff
                ):
                    queue.enqueue(JOB_EXPIRE_UPLOAD_SESSION, {"file_id": rec.id})

                # 4. Orphaned storage objects (no matching metadata row).
                known = await file_repository.list_all_storage_keys(db)

            keys = await storage.list_keys()
            orphans = sorted(set(keys) - known)[: settings.job_orphan_sweep_limit]
            for key in orphans:
                queue.enqueue(JOB_CLEANUP_ORPHANED_OBJECT, {"storage_key": key})
        except Exception:
            logger.exception("cleanup scan failed")

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