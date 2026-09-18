import os
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

from app.core.config import get_settings

CHUNK_SIZE = 1024 * 256


class LocalStorageBackend:
    """Filesystem-backed storage for local development and tests (no MinIO needed).

    Staging happens via an atomic temp-file + os.replace so a partially written
    object never appears under its final key.
    """

    def __init__(self, root: str | None = None) -> None:
        self.root = Path(root or get_settings().storage_local_dir)

    def _path(self, key: str) -> Path:
        return self.root / key

    async def put(self, key: str, data: AsyncIterator[bytes]) -> int:
        self.root.mkdir(parents=True, exist_ok=True)
        dest = self._path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path_str = tempfile.mkstemp(dir=str(dest.parent), suffix=".part")
        total = 0
        try:
            with os.fdopen(fd, "wb") as fh:
                async for chunk in data:
                    fh.write(chunk)
                    total += len(chunk)
            os.replace(tmp_path_str, dest)
            return total
        except BaseException:
            try:
                os.unlink(tmp_path_str)
            except OSError:
                pass
            raise

    async def get_stream(self, key: str) -> AsyncIterator[bytes]:
        path = self._path(key)
        if not path.exists():
            return
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(CHUNK_SIZE)
                if not chunk:
                    break
                yield chunk

    async def head(self, key: str) -> int:
        path = self._path(key)
        if not path.exists():
            return -1
        return path.stat().st_size

    async def delete(self, key: str) -> None:
        path = self._path(key)
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    async def exists(self, key: str) -> bool:
        return self._path(key).exists()