from collections.abc import Sequence

from sqlalchemy import delete as sa_delete
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import FileRecord, FileStatus


async def create_file_record(
    session: AsyncSession,
    *,
    room_id: int,
    original_filename: str,
    storage_key: str,
    content_type: str | None,
    size_bytes: int,
    uploaded_by: int | None,
    expires_at,
) -> FileRecord:
    rec = FileRecord(
        room_id=room_id,
        original_filename=original_filename,
        storage_key=storage_key,
        content_type=content_type,
        size_bytes=size_bytes,
        status=FileStatus.PENDING.value,
        uploaded_by=uploaded_by,
        download_count=0,
        expires_at=expires_at,
    )
    session.add(rec)
    await session.flush()
    return rec


async def get_file_by_id(session: AsyncSession, file_id: int) -> FileRecord | None:
    result = await session.execute(select(FileRecord).where(FileRecord.id == file_id))
    return result.scalar_one_or_none()


async def mark_confirmed(session: AsyncSession, file_id: int) -> None:
    await session.execute(
        update(FileRecord)
        .where(FileRecord.id == file_id)
        .values(status=FileStatus.CONFIRMED.value)
    )


async def mark_deleted(session: AsyncSession, file_id: int) -> None:
    await session.execute(
        update(FileRecord)
        .where(FileRecord.id == file_id)
        .values(status=FileStatus.DELETED.value)
    )


async def increment_download(session: AsyncSession, file_id: int) -> None:
    await session.execute(
        update(FileRecord)
        .where(FileRecord.id == file_id)
        .values(download_count=FileRecord.download_count + 1)
    )


async def list_confirmed_files(session: AsyncSession, room_id: int) -> Sequence[FileRecord]:
    result = await session.execute(
        select(FileRecord).where(
            FileRecord.room_id == room_id,
            FileRecord.status == FileStatus.CONFIRMED.value,
        )
    )
    return list(result.scalars().all())


async def list_all_storage_keys(session: AsyncSession, room_id: int) -> Sequence[str]:
    """Return all storage keys in the room (including DELETED-pending, so object
    cleanup can also sweep files whose metadata was already flipped)."""
    result = await session.execute(
        select(FileRecord.storage_key).where(FileRecord.room_id == room_id)
    )
    return list(result.scalars().all())


async def delete_file_records(session: AsyncSession, room_id: int) -> None:
    await session.execute(sa_delete(FileRecord).where(FileRecord.room_id == room_id))