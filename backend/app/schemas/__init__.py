from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    """Base model that serialises to camelCase JSON and validates from either
    snake_case field names or camelCase aliases, or ORM objects."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class RoomCreate(CamelModel):
    name: str | None = Field(default=None, max_length=120)
    password: str | None = Field(default=None, max_length=128)
    guest_upload_enabled: bool = False
    guest_download_enabled: bool = True
    lifetime_seconds: int | None = None

    @field_validator("name")
    @classmethod
    def strip_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        return v or None


class RoomPatch(CamelModel):
    name: str | None = Field(default=None, max_length=120)
    guest_upload_enabled: bool | None = None
    guest_download_enabled: bool | None = None
    password: str | None = Field(default=None, max_length=128)


class RoomView(CamelModel):
    room_code: str
    name: str | None
    status: str
    guest_upload_enabled: bool
    guest_download_enabled: bool
    user_count: int
    max_users: int
    storage_used_bytes: int
    file_count: int
    created_at: datetime
    expires_at: datetime


class RoomCreated(BaseModel):
    roomCode: str
    name: str | None
    expiresAt: datetime
    accessToken: str
    shareUrl: str
    ownerUrl: str
    passwordRequired: bool
    guestUploadEnabled: bool
    guestDownloadEnabled: bool


class JoinRequest(CamelModel):
    display_name: str | None = Field(default=None, max_length=60)
    password: str | None = Field(default=None, max_length=128)
    owner_token: str | None = None

    @field_validator("display_name")
    @classmethod
    def strip_display_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        return v or None


class JoinByCodeRequest(JoinRequest):
    room_code: str = Field(min_length=4, max_length=12)


class JoinResponse(BaseModel):
    accessToken: str
    room: RoomView
    role: str
    displayName: str | None
    sessionExpiresAt: datetime
    wsUrl: str

    @model_validator(mode="before")
    @classmethod
    def alias_role(cls, data):
        if isinstance(data, dict) and "display_name" in data:
            data["displayName"] = data.pop("display_name")
        return data


class MessageView(CamelModel):
    id: int
    display_name: str | None
    body: str
    created_at: datetime


class FileView(CamelModel):
    id: int
    original_filename: str
    content_type: str | None
    size_bytes: int
    status: str
    download_count: int
    uploaded_by_name: str | None
    created_at: datetime


class FileListResponse(BaseModel):
    files: list[FileView]
    storageUsedBytes: int
    fileCount: int
    maxRoomStorageBytes: int
    maxFilesPerRoom: int


class ErrorResponse(BaseModel):
    detail: str