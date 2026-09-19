import asyncio
import os
import tempfile
from collections.abc import AsyncIterator

from app.core.config import get_settings
from app.storage.local import CHUNK_SIZE, LocalStorageBackend


class S3StorageBackend:
    """S3/MinIO-backed storage using boto3 (sync client run in a thread executor).

    Objects are staged to a local temp file then uploaded/deleted with the S3
    client, keeping the API surface identical to LocalStorageBackend.
    """

    def __init__(self) -> None:
        import boto3  # lazy import so tests without the dep still work

        settings = get_settings()
        self.bucket = settings.s3_bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
            verify=settings.s3_verify_tls,
        )
        self._loop = asyncio.get_event_loop()

    def _run(self, fn, *args):
        return self._loop.run_in_executor(None, fn, *args)

    async def _ensure_bucket(self) -> None:
        def _create() -> None:
            try:
                self._client.create_bucket(Bucket=self.bucket)
            except self._client.exceptions.BucketAlreadyOwnedByYou:
                pass
            except self._client.exceptions.BucketAlreadyExists:
                pass

        await self._run(_create)

    async def put(self, key: str, data: AsyncIterator[bytes]) -> int:
        await self._ensure_bucket()
        fd, tmp = tempfile.mkstemp(suffix=".part")
        total = 0
        try:
            with os.fdopen(fd, "wb") as fh:
                async for chunk in data:
                    fh.write(chunk)
                    total += len(chunk)

            def _upload() -> None:
                with open(tmp, "rb") as src:
                    self._client.upload_fileobj(src, self.bucket, key)

            await self._run(_upload)
            return total
        except BaseException:
            try:
                self._client.delete_object(Bucket=self.bucket, Key=key)
            except Exception:
                pass
            raise
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    async def get_stream(self, key: str) -> AsyncIterator[bytes]:
        def _fetch() -> bytes:
            obj = self._client.get_object(Bucket=self.bucket, Key=key)
            return obj["Body"].read()

        body = await self._run(_fetch)
        for i in range(0, len(body), CHUNK_SIZE):
            yield body[i : i + CHUNK_SIZE]

    async def head(self, key: str) -> int:
        def _head() -> int:
            try:
                meta = self._client.head_object(Bucket=self.bucket, Key=key)
                return int(meta["ContentLength"])
            except Exception:
                return -1

        return await self._run(_head)

    async def delete(self, key: str) -> None:
        def _delete() -> None:
            try:
                self._client.delete_object(Bucket=self.bucket, Key=key)
            except Exception:
                pass

        await self._run(_delete)

    async def exists(self, key: str) -> bool:
        return await self.head(key) != -1

    async def list_keys(self, prefix: str = "") -> list[str]:
        def _list() -> list[str]:
            keys: list[str] = []
            paginator = self._client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    keys.append(obj["Key"])
            return keys

        try:
            return await self._run(_list)
        except Exception:
            return []


def get_storage_backend():
    from app.core.config import get_settings as gs

    if gs().storage_backend == "s3":
        return S3StorageBackend()
    return LocalStorageBackend()
