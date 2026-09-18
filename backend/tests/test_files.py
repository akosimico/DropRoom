
from tests.conftest import (
    create_room,
    csrf_jar,
    join_headers,
    join_room,
    use_jar,
)

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 128
GARBAGE_BYTES = b"\xde\xad\xbe\xef" * 32  # no known signature


async def _owner_jar(client):
    resp, jar = await create_room(client, name="Files")
    return jar, resp.json()["accessToken"]


async def test_upload_list_download_delete_flow(client):
    jar, token = await _owner_jar(client)
    up = await client.post(
        f"/api/v1/rooms/{token}/files",
        files={"file": ("logo.png", PNG_BYTES, "image/png")},
        cookies=jar,
        headers=join_headers(jar),
    )
    assert up.status_code == 201, up.text
    body = up.json()
    assert body["contentType"] == "image/png"  # magic bytes, not client claim
    file_id = body["id"]

    listing = await client.get(f"/api/v1/rooms/{token}/files", cookies=jar)
    assert listing.status_code == 200
    assert len(listing.json()["files"]) == 1
    assert listing.json()["fileCount"] == 1
    assert listing.json()["storageUsedBytes"] == len(PNG_BYTES)

    dl = await client.get(f"/api/v1/rooms/{token}/files/{file_id}/download", cookies=jar)
    assert dl.status_code == 200
    assert dl.content == PNG_BYTES
    assert "logo.png" in dl.headers.get("content-disposition", "")

    # Owner deletes; quota decrements in the same request
    de = await client.delete(
        f"/api/v1/rooms/{token}/files/{file_id}", cookies=jar, headers=join_headers(jar)
    )
    assert de.status_code == 200, de.text
    listing2 = await client.get(f"/api/v1/rooms/{token}/files", cookies=jar)
    assert listing2.json()["fileCount"] == 0
    assert listing2.json()["storageUsedBytes"] == 0

    gone = await client.get(f"/api/v1/rooms/{token}/files/{file_id}/download", cookies=jar)
    assert gone.status_code == 404


async def test_unknown_binary_becomes_octet_stream(client):
    jar, token = await _owner_jar(client)
    up = await client.post(
        f"/api/v1/rooms/{token}/files",
        files={"file": ("blob.bin", GARBAGE_BYTES, "application/x-custom")},
        cookies=jar,
        headers=join_headers(jar),
    )
    assert up.status_code == 201, up.text
    assert up.json()["contentType"] == "application/octet-stream"


async def test_guest_permissions_enforced(client):
    jar, token = await _owner_jar(client)  # guest upload/download OFF by default

    gjar = await csrf_jar(client)
    r, gjar = await join_room(client, token, jar=gjar, display_name="Bob")
    assert r.status_code == 200, r.text

    await use_jar(client, gjar)
    up = await client.post(
        f"/api/v1/rooms/{token}/files",
        files={"file": ("a.txt", b"hello", "text/plain")},
        cookies=gjar,
        headers=join_headers(gjar),
    )
    assert up.status_code == 403

    await use_jar(client, jar)
    own = await client.post(
        f"/api/v1/rooms/{token}/files",
        files={"file": ("a.txt", b"hello", "text/plain")},
        cookies=jar,
        headers=join_headers(jar),
    )
    assert own.status_code == 201
    file_id = own.json()["id"]

    await use_jar(client, gjar)
    dl = await client.get(
        f"/api/v1/rooms/{token}/files/{file_id}/download", cookies=gjar
    )
    assert dl.status_code == 403


async def test_guest_upload_and_download_when_enabled(client):
    resp, jar = await create_room(
        client, name="Open", guest_upload_enabled=True, guest_download_enabled=True
    )
    token = resp.json()["accessToken"]
    gjar = await csrf_jar(client)
    r, gjar = await join_room(client, token, jar=gjar, display_name="Bob")
    assert r.status_code == 200

    await use_jar(client, gjar)
    up = await client.post(
        f"/api/v1/rooms/{token}/files",
        files={"file": ("note.txt", b"guests allowed", "text/plain")},
        cookies=gjar,
        headers=join_headers(gjar),
    )
    assert up.status_code == 201, up.text

    dl = await client.get(
        f"/api/v1/rooms/{token}/files/{up.json()['id']}/download", cookies=gjar
    )
    assert dl.status_code == 200
    assert dl.content == b"guests allowed"


async def test_only_owner_can_delete_files(client):
    resp, jar = await create_room(client, guest_upload_enabled=True)
    token = resp.json()["accessToken"]
    gjar = await csrf_jar(client)
    r, gjar = await join_room(client, token, jar=gjar, display_name="Bob")
    assert r.status_code == 200

    await use_jar(client, gjar)
    up = await client.post(
        f"/api/v1/rooms/{token}/files",
        files={"file": ("n.txt", b"x", "text/plain")},
        cookies=gjar,
        headers=join_headers(gjar),
    )
    assert up.status_code == 201
    did = await client.delete(
        f"/api/v1/rooms/{token}/files/{up.json()['id']}", cookies=gjar, headers=join_headers(gjar)
    )
    assert did.status_code == 403
    await use_jar(client, jar)
    listing = await client.get(f"/api/v1/rooms/{token}/files", cookies=jar)
    assert listing.json()["fileCount"] == 1


async def test_owner_can_toggle_room_settings(client):
    jar, token = await _owner_jar(client)
    res = await client.patch(
        f"/api/v1/rooms/{token}",
        json={"guest_upload_enabled": True, "name": "Renamed"},
        cookies=jar,
        headers=join_headers(jar),
    )
    assert res.status_code == 200, res.text
    assert res.json()["guestUploadEnabled"] is True
    assert res.json()["name"] == "Renamed"