from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.models.enums import RoomStatus


class Room(Base, TimestampMixin):
    __tablename__ = "rooms"

    room_code: Mapped[str] = mapped_column(String(12), nullable=False, unique=True, index=True)
    access_token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    access_token_signed: Mapped[str | None] = mapped_column(String(255), nullable=True)
    owner_token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=RoomStatus.ACTIVE.value, index=True
    )
    guest_upload_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    guest_download_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    max_users: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    user_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    storage_used_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    file_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)