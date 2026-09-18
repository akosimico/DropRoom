from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)


def ensure_aware(dt: datetime) -> datetime:
    """SQLite returns naive UTC datetimes; normalize them for comparisons."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def has_expired(dt: datetime) -> bool:
    return ensure_aware(dt) <= utcnow()