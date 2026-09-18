import base64
import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

_ph = PasswordHasher(time_cost=2, memory_cost=19 * 1024, parallelism=1)

ROOM_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O/1/I/L


def _serializer(secret: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret, salt="droproom-access-recovery")


def sign_access_token(access_token: str, secret: str, max_age_seconds: int) -> str:
    """Encrypt access tokens so join-by-code can recover the share link while the
    DB keeps only the hash for direct-lookup. Expires like the room lifecycle."""
    return _serializer(secret).dumps(access_token)


def unsign_access_token(signed: str, secret: str, max_age_seconds: int) -> str | None:
    try:
        return _serializer(secret).loads(signed, max_age=max_age_seconds)
    except (BadSignature, SignatureExpired):
        return None


def generate_token(nbytes: int = 32) -> str:
    """Cryptographically strong random token. NEVER use random.* for this."""
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> str:
    """Hash a high-entropy token at rest (SHA-256 is appropriate for this)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_room_code(length: int = 6) -> str:
    return "".join(secrets.choice(ROOM_CODE_ALPHABET) for _ in range(length))


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _ph.verify(password_hash, password)
    except VerificationError:
        return False


def _csrf_sig(payload: bytes, secret: str) -> str:
    return base64.urlsafe_b64encode(
        hmac.new(secret.encode("utf-8"), payload, digestmod=hashlib.sha256).digest()
    ).decode("ascii")


def make_csrf_token(secret: str) -> str:
    """Double-submit CSRF token: random payload + HMAC signature."""
    payload = secrets.token_urlsafe(24).encode("ascii")
    sig = _csrf_sig(payload, secret)
    return base64.urlsafe_b64encode(payload).decode("ascii") + "." + sig


def verify_csrf_token(token: str, secret: str) -> bool:
    try:
        payload_b64, sig = token.rsplit(".", 1)
        payload = base64.urlsafe_b64decode(payload_b64.encode("ascii"))
        expected = _csrf_sig(payload, secret)
        return hmac.compare_digest(expected, sig)
    except Exception:
        return False