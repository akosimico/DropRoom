import asyncio
import json

from starlette.websockets import WebSocket

from app.api.websocket import room_ws
from tests.conftest import create_room, csrf_jar, join_headers, join_room

_META_TYPES = {"websocket.accept", "websocket.send", "websocket.close", "websocket.disconnect"}


def scope_path(token: str) -> str:
    return f"/ws/rooms/{token}"


def make_scope(token: str, query: str, cookies: str = "") -> dict:

    headers = []
    if cookies:
        headers.append(
            (b"cookie", cookies.encode("latin-1"))
        )
    return {
        "type": "websocket",
        "path": scope_path(token),
        "raw_path": scope_path(token).encode(),
        "root_path": "",
        "headers": headers,
        "query_string": query.encode(),
        "client": ("testclient", 12345),
        "server": ("testserver", 80),
        "scheme": "ws",
        "subprotocols": [],
        "state": {},
        "path_params": {"room_token": token},
    }


async def run_ws(token: str, query: str, messages: list[dict] | None = None, timeout: float = 3.0):
    """Drive room_ws with a real WebSocket, feeding `messages` and collecting sends."""
    received: asyncio.Queue = asyncio.Queue()
    sent: list[dict] = []

    async def receive():
        return await received.get()

    async def send(msg: dict) -> None:
        if msg.get("type") == "websocket.send" and "text" in msg:
            sent.append(json.loads(msg["text"]))

    ws = WebSocket(make_scope(token, query), receive=receive, send=send)
    task = asyncio.create_task(room_ws(ws))
    try:
        received.put_nowait(
            {
                "type": "websocket.connect",
                "path": scope_path(token),
                "headers": [],
                "subprotocols": [],
            }
        )
        for m in messages or []:
            received.put_nowait({"type": "websocket.receive", "text": json.dumps(m)})
        await asyncio.sleep(timeout)
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, RuntimeError):
            pass
    return sent


async def _session_token(jar) -> str:

    value = jar.get("dr_session")
    return value.strip('"') if value else ""


async def test_websocket_handshake_requires_auth(client):
    resp, jar = await create_room(client)
    token = resp.json()["accessToken"]
    # No token provided -> connection refused
    sent = await run_ws(token, "")
    # room_ws closes with 4401 before accepting; nothing broadcast or sent
    assert sent == []


async def test_websocket_chat_and_presence(client):
    resp, owner_jar = await create_room(client)
    token = resp.json()["accessToken"]
    owner_session = await _session_token(owner_jar)

    # Guest joins over HTTP
    gjar = await csrf_jar(client)
    r, gjar = await join_room(client, token, jar=gjar, display_name="Bob")
    assert r.status_code == 200, r.text
    guest_session = await _session_token(gjar)

    # Guest connects first (needs session token in query)
    guest_sent = await run_ws(
        token,
        f"token={guest_session}",
        [
            {"type": "PING", "payload": {}},
            {"type": "CHAT_SEND", "payload": {"body": "hello from bob"}},
        ],
        timeout=1.0,
    )
    types = [s.get("type") for s in guest_sent]
    assert "ROOM_STATE" in types
    assert "PONG" in types
    chat = [s for s in guest_sent if s["type"] == "CHAT_MESSAGE_SENT"]
    assert chat and chat[-1]["payload"]["message"]["body"] == "hello from bob"
    state = next(s for s in guest_sent if s["type"] == "ROOM_STATE")
    assert state["payload"]["role"] == "GUEST"
    assert state["payload"]["room"]["userCount"] == 2

    # Owner connects; should see USER_JOINED? No - owner connects after guest,
    # so owner receives ROOM_STATE with both members.
    owner_sent = await run_ws(
        token,
        f"token={owner_session}",
        [
            {"type": "PING", "payload": {}},
            {"type": "CHAT_SEND", "payload": {"body": "welcome"}},
        ],
        timeout=1.0,
    )
    otypes = [s.get("type") for s in owner_sent]
    state_owner = next(s for s in owner_sent if s["type"] == "ROOM_STATE")
    assert state_owner["payload"]["role"] == "OWNER"
    # scrollback delivered to late joiner
    bodies = [m["body"] for m in state_owner["payload"]["messages"]]
    assert "hello from bob" in bodies
    assert "ROOM_EXPIRING" not in otypes  # room lives > 1h


async def test_chat_message_length_capped(client):
    resp, jar = await create_room(client)
    token = resp.json()["accessToken"]
    sess = await _session_token(jar)
    sent = await run_ws(
        token,
        f"token={sess}",
        [{"type": "CHAT_SEND", "payload": {"body": "x" * 5000}}],
        timeout=1.0,
    )
    errors = [s for s in sent if s["type"] == "ERROR"]
    assert errors, "expected an error for over-length message"


async def test_chat_rate_limit(client):
    resp, jar = await create_room(client)
    token = resp.json()["accessToken"]
    sess = await _session_token(jar)
    msgs = [
        {"type": "CHAT_SEND", "payload": {"body": f"m{i}"}}
        for i in range(25)  # chat_limit is 20/min
    ]
    sent = await run_ws(token, f"token={sess}", msgs, timeout=2.0)
    errors = [s for s in sent if s["type"] == "ERROR"]
    assert any(e["payload"]["code"] == "CHAT_RATE_LIMITED" for e in errors)


async def test_owner_can_delete_message(client):
    resp, jar = await create_room(client)
    token = resp.json()["accessToken"]
    sess = await _session_token(jar)
    sent = await run_ws(
        token,
        f"token={sess}",
        [
            {"type": "CHAT_SEND", "payload": {"body": "to be removed"}},
        ],
        timeout=1.0,
    )
    chat = [s for s in sent if s["type"] == "CHAT_MESSAGE_SENT"]
    assert chat
    message_id = chat[-1]["payload"]["message"]["id"]

    # The first socket's disconnect expired the session; the real client
    # re-joins over REST (which issues a fresh session) before reconnecting.
    r, jar = await join_room(
        client, token, jar=jar, owner_token=resp.json()["ownerUrl"].split("owner=")[1]
    )
    assert r.status_code == 200, r.text
    sess = await _session_token(jar)

    del_sent = await run_ws(
        token,
        f"token={sess}",
        [{"type": "CHAT_DELETE", "payload": {"messageId": message_id}}],
        timeout=1.0,
    )
    deleted = [s for s in del_sent if s["type"] == "CHAT_MESSAGE_DELETED"]
    assert deleted and deleted[-1]["payload"]["messageId"] == message_id


async def test_leaver_is_removed_from_people_and_capacity(client):
    """A session that disconnects must disappear from People and free a slot."""
    resp, owner_jar = await create_room(client)
    token = resp.json()["accessToken"]
    owner_session = await _session_token(owner_jar)

    gjar = await csrf_jar(client)
    r, gjar = await join_room(client, token, jar=gjar, display_name="Bob")
    assert r.status_code == 200, r.text
    guest_session = await _session_token(gjar)

    guest_sent = await run_ws(token, f"token={guest_session}", timeout=0.4)
    gstate = next(s for s in guest_sent if s["type"] == "ROOM_STATE")
    assert gstate["payload"]["room"]["userCount"] == 2

    # Guest socket task is cancelled -> server expires the session and frees a slot.
    owner_sent = await run_ws(token, f"token={owner_session}", timeout=0.4)
    ostate = next(s for s in owner_sent if s["type"] == "ROOM_STATE")
    assert ostate["payload"]["room"]["userCount"] == 1
    names = [m["displayName"] for m in ostate["payload"]["members"]]
    assert names == ["Owner"]


async def test_ws_payloads_serialise_camel_case(client):
    resp, jar = await create_room(client, guest_upload_enabled=True)
    token = resp.json()["accessToken"]
    sess = await _session_token(jar)

    up = await client.post(
        f"/api/v1/rooms/{token}/files",
        files={"file": ("report.txt", b"hello world", "text/plain")},
        cookies=jar,
        headers=join_headers(jar),
    )
    assert up.status_code == 201, up.text

    sent = await run_ws(
        token,
        f"token={sess}",
        [{"type": "CHAT_SEND", "payload": {"body": "hi"}}],
        timeout=1.0,
    )
    state = next(s for s in sent if s["type"] == "ROOM_STATE")
    assert state["payload"]["files"], "expected the uploaded file in ROOM_STATE"
    f = state["payload"]["files"][0]
    assert f["originalFilename"] == "report.txt"
    assert f["sizeBytes"] == len(b"hello world")
    assert "downloadCount" in f
    assert "uploadedByName" in f
    assert "createdAt" in f

    chat = [s for s in sent if s["type"] == "CHAT_MESSAGE_SENT"]
    assert chat
    msg = chat[-1]["payload"]["message"]
    assert msg["displayName"] == "Owner"
    assert msg["createdAt"]