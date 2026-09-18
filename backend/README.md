# DropRoom Backend

FastAPI backend for DropRoom — a temporary, private file-transfer platform.

## Development

Requires Python ≥ 3.12.

```bash
python -m venv .venv && .venv\Scripts\activate   # Windows
# or: python -m venv .venv && source .venv/bin/activate   # Linux/macOS

pip install -e ".[dev]"
```

### Running locally (SQLite)

```bash
cp .env.example .env   # set DATABASE_URL=sqlite+aiosqlite:///./droproom.db
uvicorn app.main:app --reload
```

### Running tests

```bash
python -m pytest tests -q
```

### Lint & type-check

```bash
ruff check app tests
mypy app
```

### Migrations (Alembic)

```bash
# generate
alembic revision --autogenerate -m "description"

# apply
alembic upgrade head
```
