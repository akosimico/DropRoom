import asyncio

from tests.conftest import (
    create_room,
    csrf_jar,
    join_headers,
    join_room,
)


async def test_create_room_returns_links(client):
    resp, jar = await create_room(client)
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["roomCode"]
    assert data["accessToken"]
    assert data["shareUrl"].endswith(f"/r/{data['accessToken']}")
    assert "owner=" in data["ownerUrl"]
    assert jar.get("dr_session")
    assert jar.get("dr_csrf")


async def test_get_room_public_info(client):
    resp, _ = await create_room(client)
    token = resp.json()["accessToken"]
    info = await client.get(f"/api/v1/rooms/{token}")
    assert info.status_code == 200
    body = info.json()
    assert body["canJoin"] is True
    assert body["passwordRequired"] is False


async def test_create_room_accepts_camel_case_permissions(client):
    """The frontend sends camelCase JSON; the room must be created with the
    checked guest permissions honored instead of silently defaulting to off."""
    jar = await csrf_jar(client)
    resp = await client.post(
        "/api/v1/rooms",
        json={
            "name": "Camel",
            "guestUploadEnabled": True,
            "guestDownloadEnabled": True,
            "lifetimeSeconds": 3600,
        },
        cookies=jar,
        headers=join_headers(jar),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["guestUploadEnabled"] is True
    assert body["guestDownloadEnabled"] is True
    token = body["accessToken"]
    info = await client.get(f"/api/v1/rooms/{token}")
    assert info.json()["guestUploadEnabled"] is True
    assert info.json()["guestDownloadEnabled"] is True


async def test_create_room_defaults_guest_download_on(client):
    """Guest download permission defaults to enabled for new rooms."""
    jar = await csrf_jar(client)
    resp = await client.post(
        "/api/v1/rooms",
        json={"name": "Defaults"},
        cookies=jar,
        headers=join_headers(jar),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["guestDownloadEnabled"] is True


async def test_owner_can_patch_name_permissions_and_password(client):
    resp, jar = await create_room(client, password="oldpass")
    token = resp.json()["accessToken"]
    owner_token = resp.json()["ownerUrl"].split("owner=")[1]
    r, ojar = await join_room(client, token, owner_token=owner_token)
    assert r.status_code == 200
    assert r.json()["role"] == "OWNER"

    patched = await client.patch(
        f"/api/v1/rooms/{token}",
        json={
            "name": "Renamed",
            "guestUploadEnabled": True,
            "guestDownloadEnabled": True,
            "password": "newpass",
        },
        cookies=ojar,
        headers=join_headers(ojar),
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["name"] == "Renamed"
    assert patched.json()["guestUploadEnabled"] is True
    assert patched.json()["guestDownloadEnabled"] is True

    # Old password rejected, new password accepted
    gjar = await csrf_jar(client)
    old = await client.post(
        f"/api/v1/rooms/{token}/join",
        json={"password": "oldpass"},
        cookies=gjar,
        headers=join_headers(gjar),
    )
    assert old.status_code == 403
    gjar2 = await csrf_jar(client)
    new_join = await client.post(
        f"/api/v1/rooms/{token}/join",
        json={"password": "newpass"},
        cookies=gjar2,
        headers=join_headers(gjar2),
    )
    assert new_join.status_code == 200, new_join.text


async def test_room_not_found(client):
    resp = await client.get("/api/v1/rooms/does-not-exist")
    assert resp.status_code == 404


async def test_guest_join_requires_password(client):
    resp, jar = await create_room(client, password="secret")
    token = resp.json()["accessToken"]
    # No password -> rejected
    r1, _ = await join_room(client, token)
    assert r1.status_code == 403
    # Wrong password -> rejected
    r2, _ = await join_room(client, token, password="nope")
    assert r2.status_code == 403
    # Correct password -> accepted as guest
    r3, gjar = await join_room(client, token, password="secret", display_name="Alice")
    assert r3.status_code == 200, r3.text
    assert r3.json()["role"] == "GUEST"
    assert r3.json()["displayName"] == "Alice"
    jar = None  # noqa


async def test_owner_token_gives_owner_role(client):
    resp, _ = await create_room(client)
    token = resp.json()["accessToken"]
    owner_token = resp.json()["ownerUrl"].split("owner=")[1]
    r, ogjar = await join_room(client, token, owner_token=owner_token)
    assert r.status_code == 200
    assert r.json()["role"] == "OWNER"
    assert ogjar.get("dr_session")


async def test_join_by_code_flow(client):
    resp, _ = await create_room(client)
    room_code = resp.json()["roomCode"]
    jar = await csrf_jar(client)
    # Wrong code -> generic failure (no enumeration signal)
    bad = await client.post(
        "/api/v1/rooms/join-by-code",
        json={"room_code": "ZZZZZZ"},
        cookies=jar,
        headers=join_headers(jar),
    )
    assert bad.status_code == 403
    assert bad.json()["code"] == "JOIN_FAILED"
    # Correct code
    good = await client.post(
        "/api/v1/rooms/join-by-code",
        json={"room_code": room_code, "display_name": "ByCode"},
        cookies=jar,
        headers=join_headers(jar),
    )
    assert good.status_code == 201, good.text
    body = good.json()
    assert body["role"] == "GUEST"
    assert body["accessToken"]
    assert body["wsUrl"].startswith("/ws/rooms/")


async def test_join_by_code_rate_limited(client):
    jar = await csrf_jar(client)
    # 6 allowed per minute; the 7th must be throttled
    for _ in range(6):
        await client.post(
            "/api/v1/rooms/join-by-code",
            json={"room_code": "ZZZZZZ"},
            cookies=jar,
            headers=join_headers(jar),
        )
    seventh = await client.post(
        "/api/v1/rooms/join-by-code",
        json={"room_code": "ZZZZZZ"},
        cookies=jar,
        headers=join_headers(jar),
    )
    assert seventh.status_code == 429


async def test_expired_room_rejects_join(client):
    resp, _ = await create_room(client)
    token = resp.json()["accessToken"]

    from sqlalchemy import update

    from app.core.time import utcnow
    from app.db.session import get_session_factory
    from app.models import Room

    async with get_session_factory()() as db:
        await db.execute(
            update(Room).where(Room.access_token_hash.isnot(None)).values(expires_at=utcnow())
        )
        await db.commit()

    r, _ = await join_room(client, token)
    assert r.status_code == 410


async def test_eleven_concurrent_joins_caps_at_ten(client):
    """Owner already occupies slot 1; 10 guest attempts must fill at most 9 more.

    The atomic capacity check must never let user_count exceed max_users even
    under concurrency.
    """
    resp, _ = await create_room(client)
    token = resp.json()["accessToken"]

    attempts = 10

    async def attempt(n):
        jar = await csrf_jar(client)
        for _try in range(6):
            r = await client.post(
                f"/api/v1/rooms/{token}/join",
                json={"display_name": f"guest-{n}"},
                cookies=jar,
                headers=join_headers(jar),
            )
            if r.status_code == 500:  # SQLite lock contention with no real DB
                await asyncio.sleep(0.05)
                continue
            return r
        return r

    responses = await asyncio.gather(*(attempt(i) for i in range(attempts)))
    statuses = [r.status_code for r in responses]
    successes = sum(1 for s in statuses if s == 200)
    full = sum(1 for s in statuses if s == 409)
    assert successes == 9, f"expected 9 successful joins, got {statuses}"
    assert full == 1, f"expected exactly one RoomFull rejection, got {statuses}"

    info = await client.get(f"/api/v1/rooms/{token}")
    assert info.json()["userCount"] == 10

    # The 11th user is clearly rejected with a friendly message
    jar_11 = await csrf_jar(client)
    eleventh = await client.post(
        f"/api/v1/rooms/{token}/join",
        json={"display_name": "too-late"},
        cookies=jar_11,
        headers=join_headers(jar_11),
    )
    assert eleventh.status_code == 409
    assert "full" in eleventh.json()["detail"].lower()


async def test_csrf_tokens_are_enforced(client):
    # Without a CSRF cookie/header, state-changing calls are rejected
    resp = await client.post("/api/v1/rooms", json={"name": "x"})
    assert resp.status_code in (403, 422)
    # With a valid token, creation works
    r2, _ = await create_room(client)
    assert r2.status_code == 201


async def test_rejoin_same_browser_reuses_session(client):
    resp, jar = await create_room(client)
    token = resp.json()["accessToken"]
    r, jar = await join_room(client, token, jar=jar, password="", display_name="Again")
    assert r.status_code == 200, r.text
    info = await client.get(f"/api/v1/rooms/{token}")
    assert info.json()["userCount"] == 1  # owner only - page refresh did not add a user