from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration, overridable through environment variables."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    # --- Application ---
    app_name: str = "DropRoom API"
    environment: str = "development"
    api_prefix: str = "/api/v1"
    debug: bool = False
    auto_create_tables: bool = True
    base_url: str = "http://localhost:8000"

    # --- Database ---
    database_url: str = "postgresql+asyncpg://droproom:droproom@localhost:5432/droproom"

    # Session cookie
    session_cookie_name: str = "dr_session"
    csrf_cookie_name: str = "dr_csrf"
    cookie_secure: bool = False
    cookie_domain: str | None = None
    session_ttl_seconds: int = 60 * 60 * 12
    cleanup_interval_seconds: int = 60
    csrf_secret: str = "change-me-csrf-secret"

    # --- Rooms ---
    room_code_length: int = 6
    max_room_lifetime_seconds: int = 24 * 60 * 60
    room_lifetime_options_seconds: list[int] = [1800, 3600, 21600, 43200, 86400]
    default_room_lifetime_seconds: int = 3600
    room_expiring_threshold_seconds: int = 300

    # --- Limits ---
    max_users_per_room: int = 10
    max_file_size_bytes: int = 1024 * 1024 * 1024
    max_room_storage_bytes: int = 2 * 1024 * 1024 * 1024
    max_files_per_room: int = 100
    max_message_length: int = 1000
    chat_scrollback: int = 200

    # --- Rate limiting (in-memory fixed window for MVP; Redis later) ---
    rate_limit_enabled: bool = True
    rate_limit_window_seconds: int = 60
    create_room_limit: int = 5
    generic_limit: int = 120
    join_by_code_limit: int = 6
    chat_limit: int = 20
    upload_limit: int = 30

    # --- Storage ---
    storage_backend: str = "local"  # "local" | "s3"
    storage_local_dir: str = "/tmp/droproom-storage"
    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket: str = "droproom"
    s3_region: str = "us-east-1"
    s3_verify_tls: bool = True

    # --- CORS ---
    cors_origins: list[str] = ["http://localhost:3000"]


@lru_cache
def get_settings() -> Settings:
    return Settings()