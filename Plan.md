# DropRoom — Temporary Private File Transfer
Revised plan — v3 (Python stack)

## 1. Product Summary

DropRoom is a temporary, private file-transfer web application where a user creates a room, receives a shareable link and room code, and invites other people to upload or download files.

The main idea is:

```
Create a room → share the link/code → transfer files → room expires → files are deleted.
```

The application is designed for short-lived file sharing rather than permanent cloud storage.

### Core characteristics
- No account required for the MVP.
- Every room has a unique access link and human-friendly room code.
- An optional room password can be enabled by the owner.
- Maximum of 10 people per room.
- Guests can upload/download only when the owner enables the corresponding permissions.
- Files are stored temporarily and are deleted when the room is deleted.
- The owner chooses the room lifetime from allowed options.
- File size and room storage limits are enforced to keep the free deployment sustainable.
- Real-time room updates are delivered through WebSockets.

## 2. Product Decisions

### 2.1 Storage model

Use temporary server-side object storage instead of pure browser-to-browser transfer.

```
Browser A
   |
   | upload
   v
FastAPI API
   |
   v
Temporary Object Storage
   |
   | download
   v
Browser B
```

The files are not permanent. Their lifetime is tied to the room.

**Why this model**
- Users do not need to stay online at the same time.
- A guest can join later and still download a file while the room is active.
- Multiple guests can download the same file.
- Upload/download failures can be handled more reliably.
- It is easier to build a good UX than with pure WebRTC.
- It gives the project real backend storage, cleanup, security, and concurrency problems to solve.

Use an S3-compatible object storage abstraction so the storage provider can be changed without rewriting the application (a thin wrapper around `boto3`/`aioboto3`).

For local development, use MinIO.

For deployment, use a cloud object-storage provider with a free/low-cost tier and verify its current limits before deployment.

### 2.1.1 Proxy uploads vs. direct-to-storage uploads (decide before Step 4)

Two viable models exist, and they lead to meaningfully different code:

**Model A — Proxy through the API** (as diagrammed above). Every byte of every upload/download passes through the FastAPI process (streamed via `async` request/response bodies, not buffered fully into memory). Simple to reason about and easy to add validation (magic-byte checks, quota checks) inline. The cost: a 1 GB upload holds open a connection/worker for the full transfer duration, which limits how many concurrent large transfers a single instance can sustain (this matters more with a sync WSGI-style worker; with `async def` endpoints and streaming reads it's less severe, but still a real constraint under Uvicorn/Gunicorn worker limits) and complicates timeout/backpressure handling.

**Model B — Presigned URLs**, client uploads/downloads directly to/from object storage. The API only issues a short-lived presigned PUT/GET URL (via `boto3.generate_presigned_url` or the MinIO SDK equivalent) after checking permissions and quota; the browser then talks to the storage provider directly. The API is freed from streaming large payloads, which scales better and is closer to how production file-transfer products actually work. The cost: validation (file-signature/magic-byte checks) must happen either client-side (weaker) or as a follow-up step after upload completes, and you need a `PENDING → CONFIRMED` file-metadata state so an uploaded-but-never-confirmed object doesn't silently count against quota forever.

**Recommendation:** Build the MVP with Model A for simplicity, but treat the switch to Model B as an explicit "Phase 3.5" milestone rather than an afterthought — it solves a real scaling limitation (not a manufactured one) and is a stronger resume line than resumable uploads alone. Note this decision in code comments/ADR before starting Step 4 (§28) so the file-metadata schema is designed to support both from day one (i.e., include a `status` field on files: `PENDING`, `CONFIRMED`, `DELETED`).

## 3. Recommended Technology Stack

### Backend
- Python 3.12+
- FastAPI
- Pydantic v2 (request/response validation, replaces Bean Validation)
- SQLAlchemy 2.0 (async ORM)
- asyncpg (PostgreSQL async driver)
- Alembic (migrations, replaces Flyway)
- Starlette WebSockets (native, underlies FastAPI's WebSocket support)
- passlib + argon2-cffi (Argon2id password hashing) / bcrypt as fallback
- python-jose or itsdangerous (signed cookie/session tokens, if not using pure DB-backed sessions)
- slowapi (rate limiting, wraps the `limits` library) or a custom Redis-based limiter
- Uvicorn (ASGI server), Gunicorn+Uvicorn workers for production
- Poetry or uv (dependency/project management, replaces Maven)
- pytest, pytest-asyncio, httpx (`AsyncClient`) — replaces JUnit 5
- unittest.mock / pytest-mock — replaces Mockito
- testcontainers-python — replaces Testcontainers

### Temporary state / performance
- Redis

Potential uses:
- room presence
- rate limiting
- short-lived access state
- WebSocket presence
- upload coordination
- temporary locks
- cross-instance pub/sub for WebSocket broadcast (see §11.1)
- Celery broker/result backend (see §23)

Redis is optional for the earliest MVP and can be introduced after the core application works.

### Object storage
- S3-compatible storage
- MinIO for local development
- `boto3` / `aioboto3` as the Python client

### Frontend
- React
- Next.js
- TypeScript
- Tailwind CSS
- Native WebSocket API with a plain JSON message protocol (see note below)

### Deployment / DevOps
- Docker
- Docker Compose for local development
- GitHub Actions
- Environment-based configuration (`pydantic-settings`)

### Optional later additions
- Prometheus (via `prometheus-fastapi-instrumentator` or `prometheus_client`)
- Grafana
- OpenTelemetry (`opentelemetry-instrumentation-fastapi`)

**Note on WebSocket protocol:** the original plan referenced STOMP, which is a Spring-ecosystem convention layered on top of WebSockets. FastAPI has no equivalent built-in subprotocol, so this plan uses a plain JSON-over-WebSocket protocol instead — each message is a JSON object with a `type` field (e.g. `{"type": "FILE_UPLOAD_COMPLETED", "payload": {...}}`), dispatched server-side to handler functions per room. This is simpler to implement and is what you'd actually reach for with FastAPI; there is no functional loss versus STOMP for this project's scope.

## 4. User Roles

The MVP does not need traditional accounts.

### Room Owner

The person who creates the room.

Capabilities:
- create room
- configure room settings
- upload files
- download files
- enable/disable guest uploads
- enable/disable guest downloads
- delete files
- delete the room
- view room information
- see connected users

The owner receives a private management capability/token that is stronger than ordinary guest access.

**Known limitation** — document this explicitly rather than leaving it implicit: possession of the owner token is ownership. If the owner closes their tab or clears storage and did not save the management link, there is no recovery path in the MVP. This is an acceptable tradeoff for an anonymous, account-free product, but it should be surfaced in the UI ("save this link — it's the only way to manage this room") rather than discovered as a bug report.

### Guest

A person who accesses an active room through the share link/code.

Capabilities depend on room permissions.

Example:
```
Guest uploads:   ON
Guest downloads: ON
```
or:
```
Guest uploads:   OFF
Guest downloads: ON
```

## 5. Room Access Design

A room should have two identifiers:

**Human-friendly room code**

Example:
```
X7K9P2
```
Used for manual entry.

**Strong secret access token**

Example conceptually:
```
/r/<long-random-token>
```

The URL token must be cryptographically random (generated with Python's `secrets` module, e.g. `secrets.token_urlsafe(32)`) and sufficiently long so rooms cannot be realistically guessed. Never use `random` for this.

### Important security rule

Do not treat a short room code as a sufficient security secret by itself.

The short code is primarily for convenience.

The long random room token is the real access secret.

### Room-code brute-force protection

A 6-character alphanumeric code has on the order of ~2 billion combinations — trivial to enumerate against an unthrottled endpoint. The join-by-code endpoint needs its own, stricter rate limit, separate from general API rate limiting:

- Limit code-lookup attempts per IP (e.g., 5–10 per minute) independent of other endpoint limits (`slowapi`, keyed on client IP, or a custom Redis token-bucket/fixed-window counter).
- Consider a short exponential lockout per IP after repeated failures.
- Do not leak whether a code exists vs. exists-but-expired vs. wrong password in the response — return the same generic failure for all three so enumeration doesn't get a signal to iterate on.

### Session / authentication delivery

This must be decided before writing `auth/` — it shapes the whole package.

Recommended approach:
- The room's long access token lives in the URL path (`/r/<token>`), as already specified — this is what makes the link shareable.
- On first visit/join, the server issues a short-lived session token (a random opaque value, stored server-side — either as a `sessions` DB row or a Redis key — never a self-contained JWT holding role/permission claims, since those can't be revoked without a blocklist) bound to that room and role (owner or guest), delivered as an `HttpOnly`, `Secure`, `SameSite=Strict` cookie scoped to the API path. FastAPI's `Response.set_cookie(...)` handles this directly.
- Subsequent requests (upload, download, settings changes) authenticate via that cookie, checked in a FastAPI dependency (`Depends(get_current_session)`), not by re-sending the room token on every call.
- Because cookies enable CSRF, add a CSRF token (double-submit cookie or synchronizer token — `fastapi-csrf-protect` or a small hand-rolled implementation) on all state-changing (POST/PATCH/DELETE) requests.

This avoids storing a powerful bearer token in `localStorage` (XSS-exposed) while keeping the plain shareable link as the entry point. Plain file-download links can still work unauthenticated-by-header, since the browser attaches the cookie automatically.

### Optional password

If the owner enables a password:

```
Room link
     +
Room password
     =
Access
```

Passwords must never be stored as plaintext.

Store only a password hash generated with **Argon2id** (via `argon2-cffi`, wrapped through `passlib.hash.argon2`) — preferred for new projects — or bcrypt (`passlib.hash.bcrypt`) as a fallback. Do not use a general-purpose hash like SHA-256 for this.

## 6. Room Configuration

When creating a room, show settings such as:

```
Room name:             Optional
Password:              Optional
Guest uploads:         ON / OFF
Guest downloads:       ON / OFF
Maximum users:         10
Room expiration:       Selectable
```

### Expiration options

Because the application is initially intended for a free/low-cost deployment, use bounded choices rather than unlimited lifetime.

Recommended default choices:
- 30 minutes
- 1 hour
- 6 hours
- 12 hours
- 24 hours

The owner may choose the lifetime.

The server must still enforce a maximum allowed room lifetime regardless of client input (validated in the Pydantic request schema **and** re-checked server-side in the service layer — never trust client-side enum validation alone).

Recommended initial maximum: **24 hours**.

This can later become configurable by deployment environment or plan.

## 7. Room Capacity

Maximum concurrent/active users per room: **10 people**.

The room owner counts toward the limit.

Example:
```
Owner + 9 guests = 10 users
```

Once the limit is reached, new users receive a clear message:
```
This room is full.
Maximum capacity: 10 users.
```

Implement the capacity check atomically so two users joining at nearly the same moment cannot both bypass the limit. With SQLAlchemy async, this means either:
- a single `SELECT ... FOR UPDATE` on the room row inside a transaction, or
- an atomic conditional `UPDATE`: `UPDATE rooms SET user_count = user_count + 1 WHERE id = :id AND user_count < max_users RETURNING id`, checking that a row was actually returned/updated,

rather than a read-then-write from application code (an `await` between the read and the write is exactly where the race window opens).

## 8. File Limits

The exact limits should be configurable (via `pydantic-settings`, loaded from environment variables) because free hosting/storage limits can change.

Recommended starting defaults:
```
Maximum file size:       1 GB
Maximum room storage:    2 GB
Maximum files per room:  100
Maximum users per room:  10
```

These are application limits, not guarantees of the hosting provider.

Before production deployment, verify the selected hosting provider's:
- request size limits
- execution time limits
- storage capacity
- bandwidth limits
- database limits
- WebSocket support
- maximum object size

If the deployment platform has smaller limits, configure DropRoom below those limits.

## 9. File Lifecycle

The file lifecycle should be tied directly to the room lifecycle.

```
UPLOAD
  |
  v
TEMPORARY STORAGE
  |
  +------ room remains active ------+
  |                                  |
  v                                  v
DOWNLOADS                         ROOM EXPIRES
                                      |
                                      v
                                DELETE ROOM
                                      |
                                      v
                               DELETE FILES
```

Files should not remain in object storage after the room is deleted.

Cleanup must therefore be idempotent: running cleanup twice should not cause an error or corrupt state.

Room storage-quota accounting must account for deletions, not just uploads: when a file is deleted (by the owner, or by cleanup), the room's used-storage counter and file count must both be decremented in the same database transaction as the metadata deletion (a single `async with session.begin():` block). Otherwise a room can get permanently stuck at its 100-file or 2 GB ceiling after uploads and deletes.

## 10. Room Expiration and Cleanup

A background cleanup process should periodically find expired rooms.

Example flow:

```
Scheduled cleanup job
        |
        v
Find expired rooms
        |
        v
Mark room as EXPIRED
        |
        v
Delete file objects
        |
        v
Delete file metadata
        |
        v
Invalidate room access
        |
        v
Delete room record
```

This is one of the best places to apply your Python concurrency knowledge — see §23.

Later, the cleanup workflow can be implemented as background jobs (via your own worker pool, or Celery — see §23) rather than running entirely inside a request or a single `asyncio` scheduled task.

## 11. Realtime Behavior

Use WebSockets so users do not need to refresh the room. FastAPI's native `WebSocket` endpoints handle this directly — no extra framework needed.

Events can include:
```
USER_JOINED
USER_LEFT
FILE_UPLOAD_STARTED
FILE_UPLOAD_COMPLETED
FILE_DELETED
ROOM_SETTINGS_CHANGED
ROOM_EXPIRING
ROOM_EXPIRED
CHAT_MESSAGE_SENT
CHAT_MESSAGE_DELETED
USER_TYPING
```

Example:
```
User A uploads project.zip
            |
            v
        FastAPI
            |
            v
   WebSocket broadcast
        /        \
       v          v
   User B       User C
```

Both users immediately see the new file.

### WebSocket authentication

State this explicitly rather than leaving the handshake unauthenticated: pass the session token either as a query parameter at connection time or as a custom header during the initial HTTP upgrade request, and validate it (via the same session-lookup dependency used for REST requests) inside the WebSocket endpoint **before** accepting the connection (`await websocket.accept()`) and before allowing it to subscribe to a room's channel. An unauthenticated WebSocket channel that broadcasts file events would leak room activity to anyone who can guess or intercept a room's channel name.

A simple in-process `ConnectionManager` class (a dict of `room_token -> set[WebSocket]`) is enough for a single instance; see §11.1 for the multi-instance version.

In-room text chat is built on this same WebSocket channel — see §11.2 for the full design (message model, persistence, and moderation).

### 11.1 Multi-instance note (relevant once you scale past one API instance)

In-process WebSocket broadcast (iterating a locally-held set of connected `WebSocket` objects and calling `.send_json(...)` on each) only reaches users connected to that instance. If DropRoom ever runs more than one API instance behind a load balancer, User A's upload event won't reach User B if they're connected to a different instance. The fix is Redis pub/sub (`redis.asyncio`'s `pubsub()`, with each instance subscribing to room channels and re-broadcasting to its own locally-connected sockets) so events propagate across all instances. Not needed for a single-instance MVP, but worth having noted here before it becomes a confusing bug later.

### 11.2 In-Room Chat

Since people are already gathered in a room to move files, a lightweight text channel is a natural and low-cost addition — it reuses the same WebSocket infrastructure and doesn't need its own transport.

**Scope for the MVP-plus version**
- Plain-text messages only (no file attachments in chat — files already have their own upload flow).
- Ephemeral: messages live and die with the room, exactly like files. No cross-room or persistent chat history.
- Sent by owner or guests, subject to the same room-membership check as everything else (no separate "chat permission" toggle needed for MVP — if you're in the room, you can talk; this can become a third `guest_chat_enabled` toggle later if abuse warrants it).
- A short in-memory or DB-backed scrollback (last N messages, e.g. 200) shown to a user who joins the room after chat has started, so late joiners aren't lost mid-conversation.
- Typing indicator (`USER_TYPING`) is optional polish, not required for the first cut.

**Message flow**
```
User A types message
        |
        v
  WebSocket message: {"type": "CHAT_SEND", "payload": {...}}
        |
        v
   FastAPI: validate membership + rate limit + length
        |
        v
   Persist message (or hold in-memory, see below)
        |
        v
   Broadcast {"type": "CHAT_MESSAGE_SENT", ...} to room channel
        |
   +----+----+----+
   |    |    |    |
   v    v    v    v
  Owner  B   C    D
```

**Persistence: DB vs. in-memory/Redis**

Two reasonable choices, in increasing order of durability:

1. **In-memory only** (per-instance list, capped at N messages, e.g. a `collections.deque(maxlen=200)` per room). Simplest. Lost if the instance restarts, and doesn't survive a multi-instance deployment without also being pushed through Redis. Fine for a single-instance MVP demo.
2. **Redis list**, capped and expiring with the room's TTL (`RPUSH` + `LTRIM`, with `EXPIRE` set to match the room's `expires_at`). Natural fit since Redis is already in the stack (Phase 5) and its own expiration matches the room lifecycle well.
3. **PostgreSQL table** (below), deleted alongside the room on cleanup. Most consistent with how files are already modeled (a durable record cleaned up idempotently by the same cleanup job), and it's the simplest to reason about correctness for. **Recommended default** — treat Redis as an optimization to add later if chat volume/latency demands it, not a prerequisite for shipping chat.

**Database addition**

```
## messages

id
room_id
sender_session_id
display_name
body
created_at
```

- `body` should have a hard length cap (e.g. 1000 characters) enforced both in the Pydantic schema and as a DB column constraint.
- No edit history — messages are immutable once sent; deletion (by the owner, as a light moderation tool) is a hard delete, not a soft one, since there's no reason to retain removed content beyond the room's own lifetime.
- Deleted alongside files and the room record in the same cleanup transaction (§10) — chat data gets exactly the same lifecycle guarantee as file data, which is also the privacy story you're already telling users in §18.

**API / WebSocket surface**
```
GET    /api/v1/rooms/{roomToken}/messages     (scrollback for a newly joined user)
WS     /ws/rooms/{roomToken}                   (send: {"type": "CHAT_SEND", ...})
                                                (receive: {"type": "CHAT_MESSAGE_SENT", ...})
```

**Security and abuse considerations**
- Rate limit sends per session (e.g. 5–10 messages per 10 seconds, tracked in Redis or an in-memory sliding window) — chat is a new, cheap way to spam a room full of strangers, and the existing rate-limit infrastructure (§16) extends naturally to this.
- Sanitize/escape message content before it's rendered on the frontend — this is the one place in the product that displays arbitrary user-typed text to other users, so it's the most realistic XSS surface in the whole app. Treat it as HTML-unsafe by default: render as plain text on the React side (React escapes text content by default when not using `dangerouslySetInnerHTML` — never use that here).
- No message content in server logs beyond what's needed for abuse investigation, consistent with the minimal-data stance already taken for `audit_events` (§14).
- Owner can delete an individual message (moderation), same permission tier as deleting a file.

**Where this fits in the phases**

Chat belongs in Phase 2 (Realtime Experience, §21) — it rides on the same WebSocket work as presence and file events, so building it alongside rather than as a separate later phase avoids doing the connection/session-auth plumbing twice.

## 12. File Upload UX

The room should support:
- drag and drop
- file picker
- upload progress
- multiple files
- upload cancellation
- upload retry
- clear upload errors
- file size display
- upload speed display
- estimated remaining time

Example UI:
```
Uploading project.zip
██████████████████░░  87%

1.74 GB / 2.00 GB
18.4 MB/s
ETA: 14s
```

## 13. Resumable Large-File Uploads

This should be an advanced feature after the MVP.

Instead of sending a large file as one request:
```
2 GB file
   |
   +-- chunk 1
   +-- chunk 2
   +-- chunk 3
   +-- chunk 4
   +-- ...
```

If chunk 37 fails, the client retries chunk 37 rather than restarting the entire file.

Advanced workflow:
```
Create upload session
        |
        v
Upload chunks
        |
        v
Verify all chunks
        |
        v
Complete multipart upload
        |
        v
Create file metadata record
        |
        v
Broadcast FILE_UPLOAD_COMPLETED
```

This is an excellent resume feature because it demonstrates handling of large files and failure recovery.

If Model B (§2.1.1, presigned direct-to-storage uploads) has been adopted by this point, this maps naturally onto S3-style multipart upload (`boto3`'s `create_multipart_upload` / presigned URLs per part / `complete_multipart_upload`) rather than routing chunks through the API.

## 14. Database Design

### rooms

Suggested fields:
```
id
room_code
access_token_hash
owner_token_hash
password_hash
name
status
guest_upload_enabled
guest_download_enabled
max_users
user_count
storage_used_bytes
file_count
created_at
expires_at
updated_at
```

Possible statuses:
```
ACTIVE
EXPIRED
DELETING
DELETED
```

(Modeled as a Python `enum.Enum` mapped to a Postgres native enum or a constrained string column via SQLAlchemy.)

### files

Suggested fields:
```
id
room_id
original_filename
storage_key
content_type
size_bytes
status
uploaded_by
download_count
created_at
expires_at
```

`storage_key` must be a server-generated opaque identifier (e.g. a UUID via `uuid.uuid4()`), never derived from `original_filename` — this avoids path-traversal issues, collisions, and using the filename as an implicit authorization check.

`status` supports `PENDING` (upload initiated, not yet confirmed), `CONFIRMED` (available for download), `DELETED` (soft-deleted, pending object cleanup). This matters even for Model A (proxy uploads) to represent partially-completed multipart uploads.

### sessions / participants

Suggested fields:
```
id
room_id
session_token_hash
display_name
role
connected_at
last_seen_at
```

Do not store unnecessary personal information.

### messages

See §11.2 for full design and rationale.
```
id
room_id
sender_session_id
display_name
body
created_at
```

Deleted alongside files and the room record during cleanup (§10) — same lifecycle guarantee as everything else in the room.

### audit_events

Added to support the `GENERATE_AUDIT_EVENT` job type referenced in §23 — the original schema had no table for it to write to.
```
id
room_id
event_type
metadata_json
created_at
```

Keep entries minimal and non-identifying (event type, room ID, timestamp — not IP addresses or filenames unless genuinely needed for abuse investigation, and if so, treat that data with the same care as any other PII and expire it alongside the room).

## 15. API Design

Prefix all routes with a version segment (`/api/v1/...`) from day one — costs nothing now (a single FastAPI `APIRouter(prefix="/api/v1")`), avoids a breaking migration later.

Example endpoint structure:

**Rooms**
```
POST   /api/v1/rooms
GET    /api/v1/rooms/{roomToken}
PATCH  /api/v1/rooms/{roomToken}
DELETE /api/v1/rooms/{roomToken}
POST   /api/v1/rooms/{roomToken}/join
POST   /api/v1/rooms/join-by-code   (separate, stricter rate limit — see §5)
```

**Files**
```
POST   /api/v1/rooms/{roomToken}/files
GET    /api/v1/rooms/{roomToken}/files
GET    /api/v1/rooms/{roomToken}/files/{fileId}
DELETE /api/v1/rooms/{roomToken}/files/{fileId}
```

**Upload sessions for resumable uploads**
```
POST   /api/v1/rooms/{roomToken}/uploads
PATCH  /api/v1/uploads/{uploadId}/chunks/{chunkNumber}
POST   /api/v1/uploads/{uploadId}/complete
DELETE /api/v1/uploads/{uploadId}
```

**Chat** (see §11.2)
```
GET    /api/v1/rooms/{roomToken}/messages
DELETE /api/v1/rooms/{roomToken}/messages/{messageId}
```
Sending is done over the WebSocket channel (`/ws/rooms/{roomToken}`), not REST — see §11.2.

**Abuse reporting** (see §16)
```
POST   /api/v1/rooms/{roomToken}/report
```

Do not expose internal storage keys directly.

## 16. Security Requirements

Security is a major part of this project.

### Room access
- Use cryptographically secure random tokens (`secrets.token_urlsafe`).
- Never generate access tokens with predictable sequences, and never use Python's `random` module for anything security-sensitive.
- Hash sensitive management/access secrets at rest where appropriate.
- Never put room passwords in plaintext in the database — hash with Argon2id (preferred) or bcrypt via `passlib`.
- Do not expose storage credentials to the browser.
- Do not expose raw storage bucket paths as authorization.
- Deliver session state via `HttpOnly`, `Secure`, `SameSite=Strict` cookies, not `localStorage` bearer tokens (see §5, "Session / authentication delivery").
- Apply CSRF protection on all state-changing endpoints once cookie-based sessions are in place.

### File authorization

Every file operation must verify:
- Is the room active?
- Is the requester a member of the room?
- Does the requester have permission?
- Does the file belong to this room?
- Has the file expired?

Do not rely on the filename or object storage path for authorization. Implement this as a reusable FastAPI dependency chain rather than duplicating checks in each route handler.

### Rate limiting

Protect endpoints such as:
- Create room
- Join room
- Join by room code (stricter limit — brute-force target, see §5)
- Password verification
- Upload initialization
- Download requests
- Chat message send (spam target, see §11.2)

Use `slowapi` for per-route limits, or a custom Redis-backed sliding-window/token-bucket implementation once Redis (Phase 5) is in place. This is important because public anonymous services can be abused.

### Basic abuse protection

Consider:
- per-IP rate limiting
- room creation limits
- upload size limits (enforced via `Content-Length` pre-check and streamed size accounting, not just after the fact)
- maximum room storage
- connection limits
- expiration of inactive upload sessions
- file extension/content-type validation where appropriate
- a lightweight report-room endpoint plus a manual admin takedown path, given the service is anonymous and public-facing (see §15)

Do not assume a browser-provided MIME type is trustworthy.

Chat is the app's main XSS surface — it's the one place arbitrary user-typed text is shown to other users. Escape/sanitize on render (§11.2); never interpret message content as HTML.

## 17. File Validation

The MVP can accept many file types, but the server must still validate:
- file size
- room quota
- upload completion
- storage consistency

For advanced security, inspect file signatures/magic bytes for selected formats instead of trusting only the `Content-Type` header (the `python-magic` library, a binding over `libmagic`, is the standard tool for this).

Do not execute uploaded files on the server.

**Note:** if Model B (presigned direct uploads, §2.1.1) is adopted, magic-byte validation must happen as a post-upload verification step (object is fetched/streamed once server-side, e.g. via a ranged `GET` for just the first few KB, for inspection before being marked `CONFIRMED`) rather than inline during upload, since the API never sees the bytes as they're written.

## 18. Privacy Model

The project should clearly communicate:

> Files are temporarily stored only for the lifetime of the room and are automatically deleted when the room expires or is deleted.

Also make the deletion behavior visible in the UI.

Example:
```
This room expires in 11h 42m.
Files in this room will be deleted when the room expires.
```

Do not advertise the service as having guaranteed perfect deletion unless the actual storage provider and lifecycle behavior support that claim.

## 19. Free-Plan Strategy

Since the first deployment is intended to run on a free plan, design the application around strict quotas.

Recommended initial policy:
```
Max users per room:       10
Max file size:             1 GB
Max room storage:          2 GB
Max room lifetime:         24 hours
Max files per room:        100
```

Also consider a global anti-abuse policy, such as a per-IP room creation/upload rate limit.

**Important:** The free deployment should not depend on having unlimited storage or bandwidth.

Before deployment, verify the currently available limits for the selected platform and object-storage provider. Treat all provider limits as configuration (`pydantic-settings` environment variables), not hard-coded assumptions.

## 20. MVP Scope

The first version should be intentionally smaller.

### MVP features
- [ ] Create room
- [ ] Generate secure room access token
- [ ] Generate human-friendly room code
- [ ] Optional room password (Argon2id/bcrypt hashed)
- [ ] Owner/guest roles
- [ ] Cookie-based session issuance + CSRF protection
- [ ] Guest upload permission
- [ ] Guest download permission
- [ ] Maximum 10 users (atomic capacity check)
- [ ] Configurable room expiration
- [ ] Temporary file storage (proxy model, Model A)
- [ ] Upload file
- [ ] Download file
- [ ] Delete file (owner) — with quota decrement
- [ ] Delete room (owner)
- [ ] Room countdown
- [ ] Automatic room cleanup
- [ ] Basic rate limiting (including stricter join-by-code limit)
- [ ] Basic validation
- [ ] React room UI
- [ ] Dockerized local environment

Do not start with microservices, Kafka, or complicated distributed architecture.

## 21. Phase 2 — Realtime Experience

After the MVP works:
- [ ] WebSocket connection (with authenticated handshake, §11)
- [ ] User presence
- [ ] Real-time file notifications
- [ ] User joined/left events
- [ ] Upload status events
- [ ] In-room text chat (§11.2)
- [ ] Room-expiration warning
- [ ] Real-time room state synchronization

## 22. Phase 3 — Better File Transfer
- [ ] Upload progress
- [ ] Multiple concurrent uploads
- [ ] Upload cancellation
- [ ] Upload retry
- [ ] Resumable uploads
- [ ] Chunked/multipart uploads
- [ ] Large-file handling
- [ ] Download progress where supported

### Phase 3.5 — Direct-to-storage uploads (optional but recommended)
- [ ] Switch upload path to presigned PUT URLs (Model B, §2.1.1)
- [ ] Add post-upload verification step (magic-byte check on `PENDING` → `CONFIRMED` transition)
- [ ] Switch downloads to presigned GET URLs

## 23. Phase 4 — Python Concurrency & Job Processing

This is where the project can connect directly to your Python concurrency learning goal.

Create a background job system for tasks such as:
```
ROOM_CLEANUP
DELETE_FILE_OBJECT
EXPIRE_UPLOAD_SESSION
SEND_ROOM_EXPIRATION_EVENT
CLEANUP_ORPHANED_OBJECT
GENERATE_AUDIT_EVENT
```

Two reasonable paths, in increasing order of production-readiness:

1. **Hand-rolled worker pool** (recommended for the learning goal): build it yourself using
   - `asyncio.Queue` as the job queue
   - a fixed set of `asyncio` worker tasks (`asyncio.create_task`) pulling from the queue, for I/O-bound jobs (network calls to storage, DB writes)
   - `concurrent.futures.ThreadPoolExecutor` (via `loop.run_in_executor`) for anything CPU-bound or using a blocking library
   - retries with exponential backoff (hand-written, or via `tenacity`)
   - idempotency (checked against the `status` fields already in the schema)
   - graceful shutdown (`asyncio` task cancellation + `await asyncio.gather(..., return_exceptions=True)` on shutdown, wired into FastAPI's lifespan events)

2. **Celery + Redis** (the production-grade alternative): Celery workers as separate processes, Redis as broker/result backend. Less to build yourself, but a good "how would a real team do this" comparison point once you've built your own version — consider implementing both and writing up the tradeoffs for the resume.

Example:
```
Expired room detected
        |
        v
Create cleanup job
        |
        v
Job queue (asyncio.Queue or Celery)
        |
   +----+----+
   |         |
   v         v
Worker 1   Worker 2
   |         |
   +----+----+
        |
        v
Delete file objects
        |
        v
Mark cleanup complete
```

This makes DropRoom both a file-transfer project and a practical demonstration of Python concurrency/background processing.

## 24. Phase 5 — Redis

Introduce Redis when the basic application is stable.

Potential uses:
- rate limiting
- presence tracking
- short-lived room/session data
- distributed locks where necessary (`redis.asyncio.lock.Lock`, or a simple `SET key value NX EX` pattern)
- temporary job state
- WebSocket presence information
- cross-instance pub/sub for WebSocket broadcast (§11.1), if running multiple API instances
- Celery broker/result backend, if that path was chosen in §23

Do not add Redis merely for the resume. Each Redis use should solve an actual problem.

## 25. Phase 6 — Observability

Add:
- structured logging (`structlog` or stdlib `logging` with a JSON formatter)
- request IDs (via ASGI middleware, e.g. `asgi-correlation-id`)
- job IDs
- upload IDs
- metrics (`prometheus_client` / `prometheus-fastapi-instrumentator`)
- error monitoring (e.g. Sentry's `sentry-sdk`)
- health checks (`GET /healthz`, `GET /readyz`)

Useful metrics:
```
active_rooms
active_users
uploads_started
uploads_completed
uploads_failed
bytes_uploaded
bytes_downloaded
expired_rooms
cleanup_jobs_completed
cleanup_jobs_failed
join_by_code_attempts
join_by_code_failures
```

## 26. Testing Strategy

### Unit tests

Test (with `pytest`):
- room creation
- expiration calculation
- permission logic
- capacity checks
- file quota logic (including decrement-on-delete)
- token generation/validation
- session/CSRF handling
- cleanup logic
- retry behavior

Use `pytest-mock` (or plain `unittest.mock`) to isolate units from the DB/storage.

### Integration tests

Test (with `pytest-asyncio` + `httpx.AsyncClient` against the FastAPI app, and `testcontainers-python` spinning up real PostgreSQL/MinIO/Redis containers):
- PostgreSQL persistence
- storage integration
- room lifecycle
- upload/download authorization
- expiration cleanup
- WebSocket handshake authentication (via `httpx`'s WebSocket support or Starlette's `TestClient` WebSocket context manager)

### Concurrency tests

Specifically test:
- 11 users joining a 10-user room simultaneously (fire concurrent `asyncio` coroutines / `httpx` requests via `asyncio.gather`)
- multiple users uploading simultaneously (storage-quota race)
- two cleanup workers processing the same room
- duplicate upload-completion requests
- repeated delete requests
- rapid join-by-code attempts (rate-limit enforcement)

The goal is to prove that the system remains correct under concurrent activity.

## 27. Suggested Project Structure

```
backend/
├── app/
│   ├── main.py                 # FastAPI app factory, lifespan events
│   ├── core/                   # settings (pydantic-settings), config
│   ├── db/                     # SQLAlchemy session/engine setup
│   ├── room/
│   │   ├── router.py
│   │   ├── service.py
│   │   ├── repository.py
│   │   ├── models.py           # SQLAlchemy models
│   │   └── schemas.py          # Pydantic schemas
│   ├── file/
│   │   ├── router.py
│   │   ├── service.py
│   │   ├── repository.py
│   │   ├── models.py
│   │   └── schemas.py
│   ├── upload/
│   ├── websocket/
│   ├── auth/
│   ├── security/
│   ├── cleanup/
│   ├── jobs/                    # hand-rolled worker pool and/or Celery tasks
│   ├── storage/                 # S3/MinIO abstraction (boto3/aioboto3)
│   ├── audit/
│   └── common/
├── alembic/                      # migrations (replaces Flyway)
├── tests/
├── pyproject.toml                # Poetry/uv project + dependencies
└── Dockerfile

frontend/
├── src/
│   ├── app/                      # Next.js app router pages
│   ├── components/
│   ├── hooks/
│   ├── services/
│   ├── websocket/
│   └── types/

infra/
├── docker-compose.yml
└── nginx/
```

Keep the domain logic separated from storage and transport details.

## 28. Development Order

### Step 1 — Core Python concurrency experiment

Before FastAPI, build a tiny console prototype:
```
Producer
   ↓
asyncio.Queue (or queue.Queue with threads)
   ↓
Worker Pool
   ↓
Job Handler
```

Learn:
- `async`/`await`, coroutines
- `asyncio.create_task`, `asyncio.gather`
- `asyncio.Queue`
- `concurrent.futures.ThreadPoolExecutor` (and when threads still matter despite `asyncio`, e.g. blocking I/O or CPU-bound work)
- locks/synchronization (`asyncio.Lock`, `threading.Lock`)
- graceful shutdown (cancellation, `try`/`finally`, signal handlers)

### Step 2 — FastAPI foundation

Build:
- project setup (Poetry/uv, `pyproject.toml`)
- PostgreSQL + SQLAlchemy async engine/session
- Alembic migrations
- FastAPI routers
- services
- repositories
- Pydantic schemas
- validation
- error handling (custom exception handlers)

### Step 3 — Room system

Build room creation, access, password, permissions, user limit, session/cookie issuance, and expiration.

### Step 3.5 — Decide upload model

Before writing any storage integration code, decide Model A vs. Model B (§2.1.1) and design the files table `status` field accordingly. Revisiting this after Step 4 is much more expensive than deciding it now.

### Step 4 — File system

Integrate S3-compatible storage (`boto3`/`aioboto3` + MinIO) and file metadata.

### Step 5 — Frontend room experience

Build drag-and-drop uploads, file listing, downloads, and room settings (Next.js + React + Tailwind).

### Step 6 — Automatic cleanup

Add scheduled expiration and deletion (an `asyncio` periodic task wired into FastAPI's lifespan, or APScheduler, as the simplest first pass before Step 9's real job system).

### Step 7 — WebSockets

Add real-time presence and file events, with authenticated handshake.

### Step 8 — Resumable uploads

Add chunked/multipart upload support.

### Step 9 — Background job processing

Move cleanup and other non-request work to your own Python worker system (or Celery) from §23.

### Step 10 — Production hardening

Add rate limiting, tests, metrics, logging, Docker, CI/CD, and deployment configuration.

## 29. Production Architecture

Target architecture:

```
                        Internet
                           |
                           v
                    ┌─────────────┐
                    │  Next.js    │
                    │  Frontend   │
                    └──────┬──────┘
                           |
                           v
                  ┌─────────────────┐
                  │   FastAPI API   │
                  └──────┬──────┬───┘
                         |      |
              ┌──────────┘      └───────────┐
              v                              v
       ┌──────────────┐              ┌──────────────┐
       │  PostgreSQL  │              │    Redis     │
       │ room + files │              │ state/cache  │
       └──────────────┘              └──────────────┘
              |
              v
       ┌──────────────────┐
       │ Object Storage   │
       │ temporary files  │
       └──────────────────┘
              ^
              |
       ┌──────┴─────────┐
       │ Python Workers │
       │ cleanup/jobs   │
       └────────────────┘

                WebSocket (Redis pub/sub if multi-instance)
                    |
                    v
             Active room users
```

For the first deployment, keep the actual infrastructure as simple as possible. You can still use this logical architecture without immediately splitting services into separate microservices.

## 30. What NOT to Build Initially

Avoid these until the main product works:
- user accounts
- social features
- public file search
- permanent file storage
- multi-region architecture
- Kubernetes
- Kafka
- microservices
- complex billing
- enterprise organizations
- advanced antivirus pipelines

These can become future experiments, but they will slow down the core project.

## 31. Resume Positioning

Possible project title:

> DropRoom — Ephemeral Private File Transfer Platform

Resume description:

> Built a temporary private file-transfer platform using Python, FastAPI, PostgreSQL, WebSockets, and S3-compatible object storage. Implemented secure cookie-based room sessions, optional passwords (Argon2id), guest permissions, 10-user room capacity with atomic concurrency control, automatic expiration and file cleanup, upload/download quotas, and real-time file notifications.

For the advanced version:

> Added resumable large-file uploads, direct-to-storage presigned uploads, Redis-backed room state and rate limiting, concurrent background cleanup workers (custom asyncio worker pool and/or Celery), retry and idempotency handling, Dockerized deployment, automated integration tests, and observability metrics.

## 32. Portfolio Demo Flow

The project demo should tell a story rather than only showing code.

Demo
1. Create a room
2. Choose 24-hour expiration
3. Enable guest uploads
4. Enable guest downloads
5. Enable optional password
6. Share room link/code
7. Open the room in another browser
8. Show the 2nd user joining in real time
9. Upload a large file
10. Show progress
11. Show the file appearing instantly on the other browser
12. Download the file
13. Show room countdown
14. Delete the room
15. Show that files are removed

This demonstrates the entire product lifecycle in a few minutes.

## 33. Final MVP Definition

DropRoom is considered MVP-complete when all of the following work reliably:

- [x] Anonymous room creation
- [x] Secure room access token
- [x] Human-readable room code
- [x] Optional password (properly hashed)
- [x] Cookie-based session + CSRF protection
- [x] Maximum 10 users (atomic check)
- [x] Guest upload permission
- [x] Guest download permission
- [x] Owner controls
- [x] Temporary object storage
- [x] File upload
- [x] File download
- [x] File deletion (with quota decrement)
- [x] Room expiration
- [x] Automatic file cleanup
- [x] File and room quotas
- [x] Rate limiting (including join-by-code)
- [x] Basic WebSocket updates (authenticated handshake)
- [x] Docker local environment
- [x] Automated tests

The project becomes a strong Python/backend resume project when the advanced pieces are added:

- [+] Resumable uploads
- [+] Direct-to-storage presigned uploads
- [+] Redis
- [+] Custom Python worker pool (and/or Celery)
- [+] Job queue
- [+] Retries + exponential backoff
- [+] Idempotent cleanup
- [+] Metrics + observability
- [+] CI/CD
- [+] Production deployment

## 34. Core Engineering Goals

This project should deliberately teach and demonstrate:

### Python
- typing (type hints, `mypy`)
- dataclasses/Pydantic models
- collections
- concurrency (`asyncio`, threads)
- `asyncio.Queue`, `asyncio.create_task`, `asyncio.gather`
- `concurrent.futures` worker pools
- scheduled jobs
- exception handling
- clean architecture

### Backend
- REST API design (FastAPI)
- authentication/authorization concepts (session cookies, CSRF)
- validation (Pydantic)
- transactions (SQLAlchemy async sessions)
- database modeling
- file streaming
- object storage
- WebSockets
- background processing
- rate limiting

### Distributed/system concepts
- idempotency
- race conditions
- concurrency control
- retries
- backoff
- eventual cleanup
- temporary state
- quotas
- failure recovery

### DevOps
- Docker
- environment configuration
- CI/CD
- logging
- health checks
- metrics
- deployment constraints

## 35. Project Principle

Keep the product simple for the user:
```
Create room → Share → Transfer → Expire → Delete
```

Keep the engineering interesting underneath:
```
Secure tokens
      +
Sessions & CSRF
      +
Permissions
      +
Temporary storage
      +
WebSockets
      +
Concurrency
      +
Background jobs
      +
Retries
      +
Cleanup
      +
Quotas
      +
Observability
```

That combination makes DropRoom a practical project for learning Python while still being substantial enough to discuss in software-engineering interviews.

## 36. Project Milestones

Effort-based, not date-based — the point is a sequence of demoable, working states, each strictly building on the last, so the project is never more than one milestone away from something you could show someone. Each milestone lists what must be true to call it done and what you should be able to demo at that point.

**M0 — Concurrency warm-up**
Maps to: Step 1 (§28)
Done when: the console producer/worker-pool prototype runs (using `asyncio` and/or `concurrent.futures`), handles graceful shutdown, and you can explain what `asyncio.Queue`, tasks, and thread pools are doing without looking anything up.
Demo: a CLI run showing jobs being produced, queued, and processed by multiple workers.

**M1 — Backend skeleton**
Maps to: Step 2 (§28)
Done when: FastAPI boots against PostgreSQL via Alembic migrations, with a health-check endpoint and one real CRUD path end-to-end (even a trivial one) including validation and error handling.
Demo: `docker compose up`, hit an endpoint, see a row land in Postgres.

**M2 — Rooms exist**
Maps to: Step 3 (§28), §5–§7
Done when: a room can be created, has a working access token and human code, an optional hashed password, owner/guest roles are distinguishable, sessions are issued as cookies with CSRF protection, and the 10-user cap is enforced atomically.
Demo: create a room via API/Postman, join it from two "sessions," get rejected on an 11th.

**M3 — Decide the upload model**
Maps to: Step 3.5 (§2.1.1, §28)
Done when: you've written down (even briefly) Model A vs. Model B and which one M4 will build, with the `files.status` field designed to support either.
Demo: none — this is a design checkpoint, not a build checkpoint. Don't skip it; revisiting after M4 is expensive.

**M4 — Files move**
Maps to: Step 4 (§28), §8–§9, §14, §17
Done when: a file can be uploaded to object storage, downloaded again, deleted (with quota decrement), and every operation checks room/file/permission state per §16. Magic-byte validation is in place for at least one format.
Demo: upload a file via API, download it back, confirm it disappears from quota after delete.

**M5 — MVP complete**
Maps to: Step 5–6 (§28), full §20 checklist
Done when: every box in §20's MVP checklist is checked, there's a working Next.js/React UI (not just API calls), and the scheduled cleanup job actually deletes expired rooms and their files end-to-end.
Demo: the full portfolio demo flow (§32) minus real-time updates — i.e. steps 1–6 and 9–15, with a page refresh standing in for live sync.

This is the first milestone worth showing someone outside the project.

**M6 — It's alive: realtime + chat**
Maps to: Step 7 (§28), §11, §11.2, §21
Done when: WebSocket connections authenticate at handshake, presence and file events broadcast live, and in-room chat works (send, scrollback for late joiners, owner can delete a message, rate-limited, content escaped on render).
Demo: the full portfolio demo flow (§32) with two real browser windows — second user's join, uploads, and chat messages all appear live in the first window, no refresh.

**M7 — Big files behave**
Maps to: Step 8 (§28), §13, §22, Phase 3.5
Done when: large-file uploads survive a killed connection mid-transfer (chunk retry works), and — if you took on Phase 3.5 — uploads/downloads go direct-to-storage via presigned URLs with post-upload verification.
Demo: start uploading a large file, kill your wifi mid-upload, reconnect, watch it resume instead of restarting.

**M8 — Background jobs are real jobs**
Maps to: Step 9 (§28), §23–§24
Done when: cleanup and other async work run through your own `asyncio`/thread-pool worker system (or Celery) rather than inline in a scheduled function, with retries, exponential backoff, and idempotency proven by the concurrency tests in §26 (two workers racing on the same room's cleanup produces no double-delete errors).
Demo: trigger two cleanup runs for the same expired room concurrently, show logs proving only one actually deletes, the other no-ops cleanly.

**M9 — Production-shaped**
Maps to: Step 10 (§28), §25–§26, §29
Done when: rate limiting, structured logging, metrics, and the full test suite (unit + integration + concurrency, §26) are in place, the app is Dockerized end-to-end, and CI runs the test suite on push.
Demo: a green CI run, a Grafana/metrics dashboard (even a basic one) showing `active_rooms`, `uploads_completed`, etc. updating live.

**M10 — Deployed**
Maps to: §19, §29
Done when: the app runs on real (free-tier) infrastructure, with verified provider limits (§8, §19) configured below the platform's actual ceilings, and the resume description (§31) is factually true of what's actually deployed — not aspirational.
Demo: a live public URL. Send someone the link, have them actually use it.

## Appendix: Change log

### From v1 to v2
- Added §2.1.1: proxy vs. direct-to-storage upload tradeoff, with a recommendation to decide before Step 4.
- Added session/auth delivery design to §5 (HttpOnly cookie + CSRF, vs. bearer token).
- Added room-code brute-force protection details to §5.
- Specified Argon2id/bcrypt explicitly for password hashing (§5, §16, §20, §33).
- Added atomic capacity-check implementation guidance to §7.
- Added storage-quota decrement-on-delete requirement to §9, §20, §33.
- Added WebSocket handshake authentication to §11, plus a multi-instance Redis pub/sub note (§11.1, §24).
- Added `storage_key` opaque-identifier requirement and `status` field to files schema (§14).
- Added `audit_events` table to §14 (previously referenced by a job type with no backing table).
- Added `user_count`, `storage_used_bytes`, `file_count` to rooms schema for atomic quota tracking.
- Added API versioning (`/api/v1`) to §15.
- Added abuse-reporting endpoint to §15/§16.
- Added owner-token-loss limitation as an explicit, documented tradeoff (§4).
- Added Phase 3.5 (direct-to-storage uploads) to §22.
- Expanded testing (§26) and metrics (§25) to cover the above additions.

### v3 additions (chat)
- Added §11.2: in-room text chat design (message model, persistence choice, moderation, rate limiting), wired into the WebSocket events list (§11), messages schema (§14), API surface (§15), and security section (§16) as the app's main XSS surface. Placed in Phase 2 (§21), not the MVP.
- Added §36: effort-based project milestones (M0–M10), each mapped to an existing development step/phase, with an explicit done-condition and a demoable outcome per milestone — so progress has visible checkpoints beyond the phase checklists.

### v3 stack migration (Java → Python)
- Replaced the entire backend stack (§3): Java/Spring Boot/Hibernate/Flyway/Maven/JUnit/Mockito/Testcontainers → Python/FastAPI/SQLAlchemy(async)/Alembic/Poetry-or-uv/pytest/pytest-asyncio/unittest.mock/testcontainers-python.
- Replaced STOMP with a plain JSON-over-WebSocket protocol throughout (§3, §11, §11.2, §15), since STOMP was a Spring-specific convention with no natural FastAPI equivalent.
- Replaced `ExecutorService`/`BlockingQueue`/worker-pool language throughout (§1 intro reference, §10, §23, §26, §28, §29, §34, §36) with `asyncio.Queue`/`asyncio` tasks/`concurrent.futures.ThreadPoolExecutor`, and added Celery+Redis as a named production-grade alternative in §23.
- Replaced Bean Validation with Pydantic v2 throughout (§3, §6, §34).
- Replaced bcrypt/Argon2id library references from Spring Security's crypto module to `passlib`/`argon2-cffi` (§5, §16).
- Replaced the Java package-based project structure (§27) with a Python-package (FastAPI app) layout; frontend unchanged apart from adding Next.js explicitly (§3, §27).
- Updated the production architecture diagram (§29) and resume positioning (§31) to reference Python/FastAPI/asyncio instead of Java/Spring Boot/ExecutorService.
- Updated Core Engineering Goals (§34) from Java-specific concepts (OOP/interfaces/polymorphism, `ExecutorService`) to Python-specific equivalents (typing, `asyncio`, `concurrent.futures`).
- All schema, security model, room/file/session lifecycle, quotas, phases, and milestone structure are otherwise unchanged from v3's chat-inclusive design — only implementation-language/library references moved.