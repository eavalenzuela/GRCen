from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    APP_NAME: str = "GRCen"
    DEBUG: bool = False
    SECRET_KEY: str = "change-me-to-a-random-secret-key"
    DATABASE_URL: str = "postgresql+asyncpg://grcen:grcen@localhost:5432/grcen"
    UPLOAD_DIR: str = "./uploads"

    # File upload limits
    MAX_UPLOAD_SIZE_MB: int = 50
    ALLOWED_UPLOAD_TYPES: str = (
        "application/pdf,image/png,image/jpeg,image/gif,"
        "text/plain,text/csv,application/json,application/xml,"
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,"
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )

    # Session management
    SESSION_IDLE_TIMEOUT_MINUTES: int = 30
    SESSION_ABSOLUTE_TIMEOUT_MINUTES: int = 480  # 8 hours
    # Cap concurrent sessions per user. 0 = unlimited. When the cap is hit,
    # the oldest session(s) are evicted to make room for the new one.
    SESSION_MAX_CONCURRENT: int = 5

    # Login lockout
    LOGIN_MAX_FAILED_ATTEMPTS: int = 5
    LOGIN_LOCKOUT_MINUTES: int = 15

    # Login rate limiting (per-IP spraying protection)
    LOGIN_RATE_LIMIT_SECONDS: float = 2.0

    # General API rate limit. Sliding window per (key, bucket) pair.
    # Key is the API token id, then session id, then client IP, in that order.
    # Two buckets: 'read' (GET/HEAD/OPTIONS) and 'write' (everything else).
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_READ_PER_MINUTE: int = 600
    RATE_LIMIT_WRITE_PER_MINUTE: int = 120

    # SSL/TLS — set both to enable direct HTTPS termination
    SSL_CERTFILE: str | None = None
    SSL_KEYFILE: str | None = None

    # Encryption at rest — base64url-encoded 32-byte keys. Empty = disabled.
    ENCRYPTION_KEY: str = ""
    ENCRYPTION_KEY_RETIRED: str = ""  # previous key, decrypt-only, for rotation

    # Public base URL used when building links in outbound emails.
    APP_BASE_URL: str = "http://localhost:8000"


settings = Settings()
