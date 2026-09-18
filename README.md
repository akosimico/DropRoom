# DropRoom

Temporary, private file-transfer rooms. Create a room → share the link or 6-digit
code → guests upload/download files and chat → the room expires and everything is
wiped automatically. No accounts, no tracking.

- **Backend**: Python 3.12+, FastAPI, SQLAlchemy 2 (async), PostgreSQL, WebSockets,
  Alembic migrations, S3/MinIO or local-file storage.
- **Frontend**: Next.js (App Router) + TypeScript + Tailwind CSS.
- **Infra**: Docker Compose (Postgres + MinIO + backend + frontend).

Full product/architecture spec: [`Plan.md`](Plan.md).

## Quickstart — local development (SQLite, no Docker)

### 1. Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate            # Windows  (Linux/macOS: source .venv/bin/activate)
pip install -e ".[dev]"
cp .env.example .env              # wants: DATABASE_URL, CSRF_SECRET (rest have defaults)
uvicorn app.main:app --reload     # API at http://localhost:8000, OpenAPI at /docs
```

> For a quick SQLite dev DB, set `DATABASE_URL=sqlite+aiosqlite:///./droproom.db`
> and leave `AUTO_CREATE_TABLES=true` (default) so tables are created on boot,
> or run `alembic upgrade head`.

Run the test suite: `python -m pytest tests -q` (expect 25 passing).

### 2. Frontend

```bash
cd frontend
npm install
npm run dev                       # UI at http://localhost:3000
```

The frontend proxies `/api/v1/*` to the backend (default `http://localhost:8000`,
override with `BACKEND_URL` at build time). Set `NEXT_PUBLIC_WS_URL` to the WebSocket
origin the browser should reach (default `ws://localhost:8000`).

## Quickstart — Docker Compose (Postgres + MinIO)

```bash
cd infra
cp .env.example .env              # generate a strong CSRF_SECRET
docker compose up --build
```

- Frontend: http://localhost:3000
- Backend API: http://localhost:8000 (docs at `/docs`)
- MinIO console: http://localhost:9001

The backend container runs `alembic upgrade head` before starting. Storage uses
the `s3` backend against the bundled MinIO instance; set `STORAGE_BACKEND=local`
and mount a volume to `/data/storage` if you prefer filesystem storage.

## How it works

| Concept      | Detail                                                                 |
| ------------ | ---------------------------------------------------------------------- |
| Room         | Created by an owner. Has a share link + 6-digit room code, optional password, optional guest upload/download toggles, lifetime (30m–24h). |
| Access       | Room URL contains a random access token; ownership is a separate token (owner link). Sessions are HttpOnly cookies + double-submit CSRF. |
| Files        | Streamed through the API (size-capped), validated by magic bytes, stored in S3/local storage. Downloads can be limited to the owner. |
| Realtime     | WebSocket on `room:{id}` broadcasts file/chat/settings/member events; clients receive full `ROOM_STATE` on connect. |
| Cleanup      | A background scheduler marks rooms EXPIRED → DELETING → DELETED and deletes files/rows when the lifetime passes. |
| Limits       | 10 users, 100 files, 1 GiB/file, 2 GiB/room, chat at 20 msg/min, plus fixed-window rate limits per endpoint. |

## Project layout

```
backend/    FastAPI app, tests, Alembic migrations
frontend/   Next.js app (App Router)
infra/      docker-compose.yml, Dockerfiles, .env.example
Plan.md     The implementation spec
```