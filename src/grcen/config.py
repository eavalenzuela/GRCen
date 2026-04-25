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
    # the oldest session(s) are evicted to make room for the new one. Per-role
    # overrides take precedence (admins are typically capped tighter).
    SESSION_MAX_CONCURRENT: int = 5
    # Per-role overrides: -1 means "use the global default". Admin defaults to
    # a tighter cap because admin sessions are higher-value targets.
    SESSION_MAX_CONCURRENT_ADMIN: int = 3
    SESSION_MAX_CONCURRENT_AUDITOR: int = -1
    SESSION_MAX_CONCURRENT_EDITOR: int = -1
    SESSION_MAX_CONCURRENT_VIEWER: int = -1

    # Login lockout
    LOGIN_MAX_FAILED_ATTEMPTS: int = 5
    LOGIN_LOCKOUT_MINUTES: int = 15

    # Roles that MUST have TOTP enabled to log in. Comma-separated string of
    # role names ('admin', 'editor', 'viewer', 'auditor'). Empty = no
    # enforcement. SSO users are exempt — their IdP handles MFA.
    MFA_REQUIRED_FOR_ROLES: str = ""

    # Login rate limiting (per-IP spraying protection)
    LOGIN_RATE_LIMIT_SECONDS: float = 2.0

    # General API rate limit. Sliding window per (key, bucket) pair.
    # Key is the API token id, then session id, then client IP, in that order.
    # Two buckets: 'read' (GET/HEAD/OPTIONS) and 'write' (everything else).
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_READ_PER_MINUTE: int = 600
    RATE_LIMIT_WRITE_PER_MINUTE: int = 120
    # Per-route overrides for the bucket budgets. Format:
    #   "<path-prefix>:<read>:<write>,<path-prefix>:<read>:<write>"
    # E.g. "/api/imports:30:30,/api/exports:60:60" tightens import/export
    # endpoints relative to the global default. Match is by ``startswith``;
    # the longest matching prefix wins when several apply.
    RATE_LIMIT_ROUTE_OVERRIDES: str = ""

    # SSL/TLS — set both to enable direct HTTPS termination
    SSL_CERTFILE: str | None = None
    SSL_KEYFILE: str | None = None

    # Encryption at rest — base64url-encoded 32-byte keys. Empty = disabled.
    ENCRYPTION_KEY: str = ""
    ENCRYPTION_KEY_RETIRED: str = ""  # previous key, decrypt-only, for rotation

    # Public base URL used when building links in outbound emails.
    APP_BASE_URL: str = "http://localhost:8000"


settings = Settings()
