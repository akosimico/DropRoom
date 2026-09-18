import os
import tempfile

_STORAGE_DIR = tempfile.mkdtemp(prefix="droproom-test-storage-")

os.environ["ENVIRONMENT"] = "test"
os.environ["AUTO_CREATE_TABLES"] = "false"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["STORAGE_BACKEND"] = "local"
os.environ["STORAGE_LOCAL_DIR"] = _STORAGE_DIR
os.environ["CLEANUP_INTERVAL_SECONDS"] = "3600"
os.environ["RATE_LIMIT_ENABLED"] = "true"
os.environ["BASE_URL"] = "http://testserver"

import httpx
import pytest

from app import main as app_module
from app.core import ratelimit
from app.db.base import Base
from app.db.session import get_engine, init_engine


@pytest.fixture
async def client(tmp_path):
    init_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    ratelimit.get_rate_limiter().reset()
    transport = httpx.ASGITransport(app=app_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c
    await get_engine().dispose()


async def csrf_jar(client: httpx.AsyncClient) -> httpx.Cookies:
    """Issue a fresh CSRF double-submit cookie and return a cookie jar.
    Clears any cookies httpx auto-persisted so identities stay isolated."""
    client.cookies.clear()
    resp = await client.get("/api/v1/csrf")
    assert resp.status_code == 200, resp.text
    jar = httpx.Cookies()
    jar.update(resp.cookies)
    return jar


async def use_jar(client: httpx.AsyncClient, jar: httpx.Cookies) -> None:
    client.cookies.clear()


def _cookie_value(jar: httpx.Cookies, name: str) -> str | None:
    value = jar.get(name)
    return value.strip('"') if value else None


def join_headers(jar: httpx.Cookies) -> dict:
    csrf = _cookie_value(jar, "dr_csrf")
    return {"X-CSRF-Token": csrf} if csrf else {}


async def create_room(
    client: httpx.AsyncClient,
    jar: httpx.Cookies | None = None,
    **body,
) -> tuple[httpx.Response, httpx.Cookies]:
    jar = jar or await csrf_jar(client)
    await use_jar(client, jar)
    default = {
        "name": "Test Room",
        "guest_upload_enabled": False,
        "guest_download_enabled": False,
    }
    default.update(body)
    resp = await client.post("/api/v1/rooms", json=default, cookies=jar, headers=join_headers(jar))
    jar.update(resp.cookies)
    return resp, jar


async def join_room(
    client: httpx.AsyncClient,
    access_token: str,
    jar: httpx.Cookies | None = None,
    **body,
) -> tuple[httpx.Response, httpx.Cookies]:
    jar = jar or await csrf_jar(client)
    await use_jar(client, jar)
    resp = await client.post(
        f"/api/v1/rooms/{access_token}/join", json=body, cookies=jar, headers=join_headers(jar)
    )
    jar.update(resp.cookies)
    return resp, jar