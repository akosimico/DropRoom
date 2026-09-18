class DropRoomError(Exception):
    status_code = 400
    code = "ERROR"

    def __init__(self, detail: str = "") -> None:
        self.detail = detail or self.__class__.__name__
        super().__init__(self.detail)


class RoomNotFound(DropRoomError):
    status_code = 404
    code = "ROOM_NOT_FOUND"


class RoomExpired(DropRoomError):
    status_code = 410
    code = "ROOM_EXPIRED"


class RoomFull(DropRoomError):
    status_code = 409
    code = "ROOM_FULL"
    detail = "This room is full. Maximum capacity: 10 users."


class WrongPassword(DropRoomError):
    status_code = 403
    code = "WRONG_PASSWORD"


class InvalidRoomCode(DropRoomError):
    status_code = 404
    code = "INVALID_ROOM_CODE"


class JoinFailed(DropRoomError):
    """Generic failure for join-by-code so code existence cannot be enumerated."""

    status_code = 403
    code = "JOIN_FAILED"


class InvalidOwnerToken(DropRoomError):
    status_code = 403
    code = "INVALID_OWNER_TOKEN"


class PermissionDenied(DropRoomError):
    status_code = 403
    code = "PERMISSION_DENIED"


class NotAuthenticated(DropRoomError):
    status_code = 401
    code = "NOT_AUTHENTICATED"


class CSRFValidationFailed(DropRoomError):
    status_code = 403
    code = "CSRF_FAILED"


class RateLimited(DropRoomError):
    status_code = 429
    code = "RATE_LIMITED"


class FileTooLarge(DropRoomError):
    status_code = 413
    code = "FILE_TOO_LARGE"


class RoomStorageQuotaExceeded(DropRoomError):
    status_code = 413
    code = "ROOM_STORAGE_QUOTA_EXCEEDED"


class RoomFileLimitExceeded(DropRoomError):
    status_code = 413
    code = "ROOM_FILE_LIMIT_EXCEEDED"


class FileNotFound(DropRoomError):
    status_code = 404
    code = "FILE_NOT_FOUND"


class InvalidFileType(DropRoomError):
    status_code = 415
    code = "INVALID_FILE_TYPE"


class ChatRateLimited(RateLimited):
    code = "CHAT_RATE_LIMITED"