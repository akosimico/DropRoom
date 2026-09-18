from fastapi import APIRouter
from sqlalchemy import text

from app.api.deps import get_db

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(db=get_db):
    async for session in db:
        await session.execute(text("SELECT 1"))
        break
    return {"status": "ready"}