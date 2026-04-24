import asyncpg

from grcen.config import settings

pool: asyncpg.Pool | None = None

SCHEMA_SQL = """
DO $$ BEGIN
    CREATE EXTENSION IF NOT EXISTS pg_trgm;
EXCEPTION WHEN unique_violation THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE asset_type AS ENUM (
        'person', 'policy', 'product', 'system', 'device',
        'data_category', 'audit', 'requirement', 'process',
        'intellectual_property', 'risk', 'organizational_unit'
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN CREATE TYPE asset_status AS ENUM ('active', 'inactive', 'draft', 'archived');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN CREATE TYPE attachment_kind AS ENUM ('file', 'url', 'document');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN CREATE TYPE schedule_type AS ENUM ('once', 'recurring');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN CREATE TYPE user_role AS ENUM ('admin', 'editor', 'viewer', 'auditor');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS assets (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    type        asset_type  NOT NULL,
    name        VARCHAR(255) NOT NULL,
    description TEXT,
    status      asset_status NOT NULL DEFAULT 'active',
    owner       VARCHAR(255),
    metadata    JSONB DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by  UUID
);

CREATE INDEX IF NOT EXISTS ix_assets_type ON assets (type);
CREATE INDEX IF NOT EXISTS ix_assets_name_trgm ON assets USING gin (name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS ix_assets_metadata ON assets USING gin (metadata);

CREATE TABLE IF NOT EXISTS relationships (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_asset_id   UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    target_asset_id   UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    relationship_type VARCHAR(255) NOT NULL,
    description       TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_rel_source_target
    ON relationships (source_asset_id, target_asset_id);

CREATE TABLE IF NOT EXISTS attachments (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    asset_id    UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    kind        attachment_kind NOT NULL,
    name        VARCHAR(255) NOT NULL,
    url_or_path TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS alerts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    asset_id        UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    title           VARCHAR(255) NOT NULL,
    message         TEXT,
    schedule_type   schedule_type NOT NULL,
    cron_expression VARCHAR(100),
    next_fire_at    TIMESTAMPTZ,
    enabled         BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS notifications (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    alert_id   UUID NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
    title      VARCHAR(255) NOT NULL,
    message    TEXT,
    read       BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username        VARCHAR(150) UNIQUE NOT NULL,
    hashed_password VARCHAR(255) NOT NULL,
    is_active       BOOLEAN NOT NULL DEFAULT true,
    is_admin        BOOLEAN NOT NULL DEFAULT false,
    role            user_role NOT NULL DEFAULT 'viewer',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

DO $$ BEGIN
    ALTER TABLE assets ADD CONSTRAINT fk_assets_updated_by
        FOREIGN KEY (updated_by) REFERENCES users(id) ON DELETE SET NULL;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS audit_config (
    entity_type  VARCHAR(50) PRIMARY KEY,
    enabled      BOOLEAN NOT NULL DEFAULT true,
    field_level  BOOLEAN NOT NULL DEFAULT true,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit_log (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID,
    username     VARCHAR(150) NOT NULL,
    action       VARCHAR(50) NOT NULL,
    entity_type  VARCHAR(50) NOT NULL,
    entity_id    UUID,
    entity_name  VARCHAR(255),
    changes      JSONB,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_audit_log_entity_type ON audit_log (entity_type);
CREATE INDEX IF NOT EXISTS ix_audit_log_action ON audit_log (action);
CREATE INDEX IF NOT EXISTS ix_audit_log_user_id ON audit_log (user_id);
CREATE INDEX IF NOT EXISTS ix_audit_log_created_at ON audit_log (created_at DESC);

CREATE TABLE IF NOT EXISTS oidc_config (
    key         VARCHAR(50) PRIMARY KEY,
    value       TEXT NOT NULL DEFAULT '',
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

MIGRATION_SQL = """
-- Add role column to existing users tables that lack it
DO $$ BEGIN
    ALTER TABLE users ADD COLUMN role user_role NOT NULL DEFAULT 'viewer';
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;

-- Migrate is_admin flag to role
UPDATE users SET role = 'admin' WHERE is_admin = true AND role = 'viewer';

-- Add updated_by to existing assets tables that lack it
DO $$ BEGIN
    ALTER TABLE assets ADD COLUMN updated_by UUID REFERENCES users(id) ON DELETE SET NULL;
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;

-- Add owner_id FK column to assets
DO $$ BEGIN
    ALTER TABLE assets ADD COLUMN owner_id UUID REFERENCES assets(id) ON DELETE SET NULL;
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;

-- Populate owner_id from owner text (best-effort name lookup)
UPDATE assets a
SET owner_id = (
    SELECT o.id FROM assets o
    WHERE o.name = a.owner
      AND o.type IN ('person', 'organizational_unit')
    ORDER BY o.created_at
    LIMIT 1
)
WHERE a.owner IS NOT NULL
  AND a.owner != ''
  AND a.owner_id IS NULL;

CREATE INDEX IF NOT EXISTS ix_assets_owner_id ON assets (owner_id);

-- OIDC: Add oidc_sub column for stable OIDC subject identifier
DO $$ BEGIN
    ALTER TABLE users ADD COLUMN oidc_sub VARCHAR(255);
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS ix_users_oidc_sub
    ON users (oidc_sub) WHERE oidc_sub IS NOT NULL;

-- OIDC: Link user account to a Person asset
DO $$ BEGIN
    ALTER TABLE users ADD COLUMN person_asset_id UUID REFERENCES assets(id) ON DELETE SET NULL;
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;

-- OIDC: Email column for user display and matching
DO $$ BEGIN
    ALTER TABLE users ADD COLUMN email VARCHAR(255);
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;

-- Seed default audit config
INSERT INTO audit_config (entity_type, enabled, field_level)
VALUES
    ('asset', true, true),
    ('relationship', true, true),
    ('attachment', true, true),
    ('alert', true, true),
    ('user', true, true)
ON CONFLICT (entity_type) DO NOTHING;

-- Seed default OIDC config
INSERT INTO oidc_config (key, value) VALUES
    ('issuer_url', ''),
    ('client_id', ''),
    ('client_secret', ''),
    ('scopes', 'openid email profile'),
    ('role_claim', 'groups'),
    ('role_mapping', '{}'),
    ('default_role', 'viewer'),
    ('display_name', 'SSO')
ON CONFLICT (key) DO NOTHING;

-- API tokens for programmatic access
CREATE TABLE IF NOT EXISTS api_tokens (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id            UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name               VARCHAR(150) NOT NULL,
    token_hash         VARCHAR(255) NOT NULL,
    permissions        TEXT[] NOT NULL,
    expires_at         TIMESTAMPTZ,
    last_used_at       TIMESTAMPTZ,
    is_service_account BOOLEAN NOT NULL DEFAULT false,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked            BOOLEAN NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS idx_api_tokens_user ON api_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_api_tokens_hash ON api_tokens(token_hash);

-- Generic app settings (key-value)
CREATE TABLE IF NOT EXISTS app_settings (
    key        VARCHAR(100) PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- New asset types
DO $$ BEGIN ALTER TYPE asset_type ADD VALUE 'vendor';
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN ALTER TYPE asset_type ADD VALUE 'control';
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN ALTER TYPE asset_type ADD VALUE 'incident';
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN ALTER TYPE asset_type ADD VALUE 'framework';
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- Global tags and criticality columns
DO $$ BEGIN
    ALTER TABLE assets ADD COLUMN tags TEXT[] DEFAULT '{}';
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;
DO $$ BEGIN
    ALTER TABLE assets ADD COLUMN criticality VARCHAR(20);
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;
CREATE INDEX IF NOT EXISTS idx_assets_tags ON assets USING gin (tags);

-- Server-side sessions
CREATE TABLE IF NOT EXISTS sessions (
    session_id  VARCHAR(64) PRIMARY KEY,
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_active TIMESTAMPTZ NOT NULL DEFAULT now(),
    ip_address  VARCHAR(45),
    user_agent  TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

-- Login tracking and lockout columns
DO $$ BEGIN
    ALTER TABLE users ADD COLUMN last_login TIMESTAMPTZ;
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;
DO $$ BEGIN
    ALTER TABLE users ADD COLUMN failed_login_count INTEGER NOT NULL DEFAULT 0;
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;
DO $$ BEGIN
    ALTER TABLE users ADD COLUMN locked_until TIMESTAMPTZ;
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;

-- Encryption at rest configuration
CREATE TABLE IF NOT EXISTS encryption_config (
    key        VARCHAR(100) PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Blind index for encrypted email lookups
DO $$ BEGIN
    ALTER TABLE users ADD COLUMN email_blind_idx VARCHAR(64);
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;
CREATE INDEX IF NOT EXISTS idx_users_email_blind ON users(email_blind_idx);

-- Per-file encryption flag
DO $$ BEGIN
    ALTER TABLE attachments ADD COLUMN encrypted BOOLEAN NOT NULL DEFAULT false;
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;

-- Widen sessions.ip_address to hold encrypted ciphertext
ALTER TABLE sessions ALTER COLUMN ip_address TYPE TEXT;

-- SAML 2.0 configuration (key-value, mirrors oidc_config)
CREATE TABLE IF NOT EXISTS saml_config (
    key         VARCHAR(50) PRIMARY KEY,
    value       TEXT NOT NULL DEFAULT '',
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Seed default SAML config
INSERT INTO saml_config (key, value) VALUES
    ('idp_entity_id', ''),
    ('idp_sso_url', ''),
    ('idp_slo_url', ''),
    ('idp_x509_cert', ''),
    ('sp_entity_id', ''),
    ('sp_private_key', ''),
    ('sp_x509_cert', ''),
    ('name_id_format', 'urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress'),
    ('role_attribute', 'Role'),
    ('role_mapping', '{}'),
    ('default_role', 'viewer'),
    ('display_name', 'SAML SSO'),
    ('want_assertions_signed', 'true'),
    ('want_name_id_encrypted', 'false')
ON CONFLICT (key) DO NOTHING;

-- SAML: stable subject identifier on users
DO $$ BEGIN
    ALTER TABLE users ADD COLUMN saml_sub VARCHAR(255);
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;
CREATE UNIQUE INDEX IF NOT EXISTS ix_users_saml_sub
    ON users (saml_sub) WHERE saml_sub IS NOT NULL;

-- Daily risk severity snapshots for trend indicators
CREATE TABLE IF NOT EXISTS risk_snapshots (
    snapshot_date DATE PRIMARY KEY,
    total         INTEGER NOT NULL DEFAULT 0,
    critical      INTEGER NOT NULL DEFAULT 0,
    high          INTEGER NOT NULL DEFAULT 0,
    medium        INTEGER NOT NULL DEFAULT 0,
    low           INTEGER NOT NULL DEFAULT 0,
    overdue       INTEGER NOT NULL DEFAULT 0,
    no_treatment  INTEGER NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Allow attachments to hang off relationships as well as assets.
DO $$ BEGIN
    ALTER TABLE attachments ALTER COLUMN asset_id DROP NOT NULL;
EXCEPTION WHEN OTHERS THEN NULL;
END $$;
DO $$ BEGIN
    ALTER TABLE attachments ADD COLUMN relationship_id UUID
        REFERENCES relationships(id) ON DELETE CASCADE;
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;
CREATE INDEX IF NOT EXISTS idx_attachments_relationship
    ON attachments (relationship_id);
DO $$ BEGIN
    ALTER TABLE attachments ADD CONSTRAINT attachments_exactly_one_owner
        CHECK (
            (asset_id IS NOT NULL AND relationship_id IS NULL) OR
            (asset_id IS NULL AND relationship_id IS NOT NULL)
        );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- SMTP configuration (key-value, mirrors oidc_config)
CREATE TABLE IF NOT EXISTS smtp_config (
    key         VARCHAR(50) PRIMARY KEY,
    value       TEXT NOT NULL DEFAULT '',
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO smtp_config (key, value) VALUES
    ('host', ''),
    ('port', '587'),
    ('username', ''),
    ('password', ''),
    ('from_address', ''),
    ('from_name', 'GRCen'),
    ('use_starttls', 'true'),
    ('use_ssl', 'false'),
    ('enabled', 'false')
ON CONFLICT (key) DO NOTHING;

-- Per-user email notification opt-in
DO $$ BEGIN
    ALTER TABLE users ADD COLUMN email_notifications_enabled BOOLEAN NOT NULL DEFAULT false;
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;

-- Outbound webhooks
CREATE TABLE IF NOT EXISTS webhooks (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name          VARCHAR(150) NOT NULL,
    url           TEXT NOT NULL,
    secret        TEXT NOT NULL DEFAULT '',
    enabled       BOOLEAN NOT NULL DEFAULT true,
    event_filter  TEXT[] NOT NULL DEFAULT '{}',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS webhook_deliveries (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    webhook_id    UUID REFERENCES webhooks(id) ON DELETE CASCADE,
    alert_id      UUID REFERENCES alerts(id) ON DELETE SET NULL,
    event         VARCHAR(64) NOT NULL,
    url           TEXT NOT NULL,
    status_code   INTEGER,
    response_body TEXT,
    error         TEXT,
    attempted_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_webhook
    ON webhook_deliveries(webhook_id);
CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_attempted_at
    ON webhook_deliveries(attempted_at DESC);

-- Email delivery log (one row per attempted send)
CREATE TABLE IF NOT EXISTS notification_deliveries (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    alert_id      UUID REFERENCES alerts(id) ON DELETE SET NULL,
    user_id       UUID REFERENCES users(id) ON DELETE SET NULL,
    email         VARCHAR(255) NOT NULL,
    status        VARCHAR(20) NOT NULL,
    error         TEXT,
    attempted_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_notification_deliveries_alert
    ON notification_deliveries(alert_id);
CREATE INDEX IF NOT EXISTS idx_notification_deliveries_attempted_at
    ON notification_deliveries(attempted_at DESC);
"""


def _dsn() -> str:
    """Convert the config DATABASE_URL to a plain postgres:// DSN for asyncpg."""
    url = settings.DATABASE_URL
    if url.startswith("postgresql+asyncpg://"):
        url = url.replace("postgresql+asyncpg://", "postgresql://", 1)
    return url


async def init_pool() -> asyncpg.Pool:
    global pool
    pool = await asyncpg.create_pool(_dsn(), min_size=2, max_size=10)
    return pool


async def close_pool() -> None:
    global pool
    if pool:
        await pool.close()
        pool = None


async def get_pool() -> asyncpg.Pool:
    if pool is None:
        raise RuntimeError("Database pool not initialised")
    return pool


async def init_schema() -> None:
    """Create all tables and types if they don't already exist.

    Uses a PostgreSQL advisory lock to prevent deadlocks when multiple
    workers start simultaneously (e.g. Gunicorn with multiple uvicorn workers).
    """
    p = await get_pool()
    async with p.acquire() as conn:
        # Advisory lock ID 1 — only one connection runs migrations at a time
        await conn.execute("SELECT pg_advisory_lock(1)")
        try:
            await conn.execute(SCHEMA_SQL)
            await conn.execute(MIGRATION_SQL)
        finally:
            await conn.execute("SELECT pg_advisory_unlock(1)")
