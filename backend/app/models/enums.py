from enum import StrEnum


class RoomStatus(StrEnum):
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    DELETING = "DELETING"
    DELETED = "DELETED"


class FileStatus(StrEnum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    DELETED = "DELETED"


class Role(StrEnum):
    OWNER = "OWNER"
    GUEST = "GUEST"