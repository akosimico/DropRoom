import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import csrf, files, health, messages, rooms, uploads, websocket
from app.core import errors
from app.core.config import get_settings
from app.db.base import Base

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("droproom")


async def _create_tables() -> None:
    from app.db.session import get_engine

    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if settings.auto_create_tables:
        try:
            await _create_tables()
        except Exception:
            logger.exception("auto table creation failed (database may not be reachable yet)")
    from app.cleanup.scheduler import scheduler
    from app.jobs import register_default_handlers
    from app.jobs.queue import get_job_queue

    job_queue = get_job_queue()
    register_default_handlers(job_queue)
    if settings.environment != "test":
        job_queue.start()
        scheduler.start()
    try:
        yield
    finally:
        if settings.environment != "test":
            await scheduler.stop()
            await job_queue.stop()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.environment != "production" else None,
        redoc_url=None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(csrf.router, prefix=settings.api_prefix)
    app.include_router(rooms.router, prefix=settings.api_prefix)
    app.include_router(files.router, prefix=settings.api_prefix)
    app.include_router(uploads.router, prefix=settings.api_prefix)
    app.include_router(messages.router, prefix=settings.api_prefix)
    app.include_router(websocket.router)

    @app.exception_handler(errors.DropRoomError)
    async def droproom_error_handler(request: Request, exc: errors.DropRoomError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "detail": exc.detail},
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception):
        logger.exception("unhandled error on %s", request.url.path)
        return JSONResponse(
            status_code=500,
            content={"code": "INTERNAL_ERROR", "detail": "Internal server error"},
        )

    return app


app = create_app()