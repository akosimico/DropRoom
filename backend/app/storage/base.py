from collections.abc import AsyncIterator
from typing import Protocol


class StorageBackend(Protocol):
    """S3-compatible object storage abstraction (thin wrapper so provider is swappable)."""

    async def put(self, key: str, data: AsyncIterator[bytes]) -> int:
        """Stream `data` into storage under `key`. Returns bytes stored."""
        ...

    def get_stream(self, key: str) -> AsyncIterator[bytes]:
        """Stream the object's bytes back (async generator)."""
        ...

    async def head(self, key: str) -> int:
        """Return object size in bytes, or -1 if it does not exist."""
        ...

    async def delete(self, key: str) -> None:
        """Idempotent delete - no error if the key is missing."""
        ...

    async def exists(self, key: str) -> bool:
        ...

    async def list_keys(self, prefix: str = "") -> list[str]:
        """Return all object keys under an optional key prefix."""
        ...


async def drain_chunks(data: AsyncIterator[bytes], chunk_cb) -> int:
    total = 0
    async for chunk in data:
        if chunk:
            await chunk_cb(chunk)
            total += len(chunk)
    return total