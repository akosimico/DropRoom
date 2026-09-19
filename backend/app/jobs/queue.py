import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.core.config import get_settings

logger = logging.getLogger("droproom.jobs")

JobHandler = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass
class Job:
    """A unit of background work. `payload` is JSON-serializable by design so
    the queue can later be backed by Redis/Celery without reshaping handlers."""

    job_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    job_type: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    max_attempts: int = 3
    attempt: int = 0


class JobQueue:
    """Hand-rolled `asyncio.Queue` worker pool (plan §23).

    A fixed number of worker tasks pull jobs off a shared `asyncio.Queue` and
    run the registered handler with retries + exponential backoff. Handlers are
    responsible for being idempotent — the schema's status fields give them the
    atomic transitions to guarantee only one worker performs a destructive step.
    """

    def __init__(
        self,
        *,
        workers: int | None = None,
        max_attempts: int | None = None,
        backoff_base: float | None = None,
        backoff_max: float | None = None,
    ) -> None:
        settings = get_settings()
        self._workers_count = workers or settings.job_workers
        self.max_attempts = max_attempts or settings.job_max_attempts
        self.backoff_base = backoff_base or settings.job_backoff_base_seconds
        self.backoff_max = backoff_max or settings.job_backoff_max_seconds
        self._queue: asyncio.Queue[Job | None] = asyncio.Queue()
        self._handlers: dict[str, JobHandler] = {}
        self._worker_tasks: list[asyncio.Task[None]] = []

    def register(self, job_type: str, handler: JobHandler) -> None:
        self._handlers[job_type] = handler

    def enqueue(
        self,
        job_type: str,
        payload: dict[str, Any] | None = None,
        *,
        max_attempts: int | None = None,
    ) -> Job:
        job = Job(
            job_type=job_type,
            payload=payload or {},
            max_attempts=max_attempts or self.max_attempts,
        )
        self._queue.put_nowait(job)
        return job

    def start(self) -> None:
        if self._worker_tasks:
            return
        for _ in range(self._workers_count):
            self._worker_tasks.append(asyncio.create_task(self._worker()))

    async def stop(self) -> None:
        for _ in self._worker_tasks:
            self._queue.put_nowait(None)
        if self._worker_tasks:
            await asyncio.gather(*self._worker_tasks, return_exceptions=True)
        self._worker_tasks.clear()

    async def join(self) -> None:
        await self._queue.join()

    async def _worker(self) -> None:
        while True:
            job = await self._queue.get()
            if job is None:
                return
            try:
                await self._run_with_retries(job)
            finally:
                self._queue.task_done()

    async def _run_with_retries(self, job: Job) -> None:
        handler = self._handlers.get(job.job_type)
        if handler is None:
            logger.error("no handler registered for job type %s", job.job_type)
            return
        while job.attempt < job.max_attempts:
            job.attempt += 1
            try:
                await handler(job.payload)
                return
            except Exception:
                if job.attempt >= job.max_attempts:
                    logger.exception(
                        "job %s (%s) failed permanently after %d attempt(s)",
                        job.job_id,
                        job.job_type,
                        job.attempt,
                    )
                    return
                delay = min(self.backoff_base * (2 ** (job.attempt - 1)), self.backoff_max)
                logger.warning(
                    "job %s (%s) attempt %d failed; retrying in %.1fs",
                    job.job_id,
                    job.job_type,
                    job.attempt,
                    delay,
                )
                await asyncio.sleep(delay)


_queue: JobQueue | None = None


def get_job_queue() -> JobQueue:
    global _queue
    if _queue is None:
        _queue = JobQueue()
    return _queue


def reset_job_queue() -> None:
    global _queue
    _queue = None