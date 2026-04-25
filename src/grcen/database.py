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

-- TOTP second factor for local auth
CREATE TABLE IF NOT EXISTS user_totp (
    user_id         UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    secret          VARCHAR(64) NOT NULL,
    recovery_codes  TEXT[] NOT NULL DEFAULT '{}',
    enabled         BOOLEAN NOT NULL DEFAULT false,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Data access log (reads, not writes — audit_log covers writes)
CREATE TABLE IF NOT EXISTS data_access_log (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID REFERENCES users(id) ON DELETE SET NULL,
    username     VARCHAR(150) NOT NULL,
    action       VARCHAR(32) NOT NULL,
    entity_type  VARCHAR(32) NOT NULL,
    entity_id    UUID,
    entity_name  VARCHAR(255),
    path         VARCHAR(400),
    ip_address   VARCHAR(64),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_data_access_log_created ON data_access_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_data_access_log_user ON data_access_log(user_id);
CREATE INDEX IF NOT EXISTS idx_data_access_log_entity ON data_access_log(entity_type, entity_id);

-- Saved searches (per-user, with optional sharing)
CREATE TABLE IF NOT EXISTS saved_searches (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name         VARCHAR(150) NOT NULL,
    path         VARCHAR(200) NOT NULL,
    query_string TEXT NOT NULL DEFAULT '',
    shared       BOOLEAN NOT NULL DEFAULT false,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_saved_searches_user ON saved_searches(user_id);
CREATE INDEX IF NOT EXISTS idx_saved_searches_shared ON saved_searches(shared)
    WHERE shared = true;

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

-- Workflow / approval gating per asset type
CREATE TABLE IF NOT EXISTS workflow_config (
    asset_type               VARCHAR(64) PRIMARY KEY,
    require_approval_create  BOOLEAN NOT NULL DEFAULT false,
    require_approval_update  BOOLEAN NOT NULL DEFAULT false,
    require_approval_delete  BOOLEAN NOT NULL DEFAULT false,
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Pending changes (proposed creates / updates / deletes awaiting approval)
DO $$ BEGIN
    CREATE TYPE pending_change_status AS ENUM ('pending', 'approved', 'rejected', 'withdrawn');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE pending_change_action AS ENUM ('create', 'update', 'delete');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- Relationship-action variants for workflow #39.
DO $$ BEGIN ALTER TYPE pending_change_action ADD VALUE 'relationship_create';
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN ALTER TYPE pending_change_action ADD VALUE 'relationship_delete';
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS pending_changes (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    action          pending_change_action NOT NULL,
    asset_type      VARCHAR(64) NOT NULL,
    target_asset_id UUID REFERENCES assets(id) ON DELETE SET NULL,
    title           VARCHAR(255) NOT NULL,
    payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
    status          pending_change_status NOT NULL DEFAULT 'pending',
    submitted_by    UUID REFERENCES users(id) ON DELETE SET NULL,
    submitted_by_username VARCHAR(150) NOT NULL,
    submitted_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_by      UUID REFERENCES users(id) ON DELETE SET NULL,
    decided_by_username VARCHAR(150),
    decided_at      TIMESTAMPTZ,
    decision_note   TEXT
);
CREATE INDEX IF NOT EXISTS idx_pending_changes_status
    ON pending_changes(status);
CREATE INDEX IF NOT EXISTS idx_pending_changes_target
    ON pending_changes(target_asset_id) WHERE target_asset_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_pending_changes_submitted
    ON pending_changes(submitted_at DESC);

-- If an older table created the FK as ON DELETE CASCADE, swap it for SET NULL
-- so that an approved DELETE preserves the historical pending_change row.
DO $$ BEGIN
    ALTER TABLE pending_changes DROP CONSTRAINT pending_changes_target_asset_id_fkey;
    ALTER TABLE pending_changes
        ADD CONSTRAINT pending_changes_target_asset_id_fkey
        FOREIGN KEY (target_asset_id) REFERENCES assets(id) ON DELETE SET NULL;
EXCEPTION WHEN undefined_object THEN NULL; WHEN duplicate_object THEN NULL;
END $$;

-- Multi-tenancy: organizations and tenant scoping ----------------------
CREATE TABLE IF NOT EXISTS organizations (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug        VARCHAR(64) UNIQUE NOT NULL,
    name        VARCHAR(255) NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO organizations (slug, name)
VALUES ('default', 'Default Organization')
ON CONFLICT (slug) DO NOTHING;

-- Add organization_id to every data table. Backfill existing rows to the
-- default org, then enforce NOT NULL.
DO $$
DECLARE
    default_org UUID;
    tbl TEXT;
BEGIN
    SELECT id INTO default_org FROM organizations WHERE slug = 'default';
    FOR tbl IN SELECT unnest(ARRAY[
        'users', 'assets', 'relationships', 'attachments', 'alerts',
        'notifications', 'audit_log', 'data_access_log', 'saved_searches',
        'api_tokens', 'pending_changes', 'webhooks', 'webhook_deliveries',
        'notification_deliveries', 'risk_snapshots'
    ])
    LOOP
        EXECUTE format(
            'ALTER TABLE %I ADD COLUMN IF NOT EXISTS organization_id UUID',
            tbl
        );
        EXECUTE format(
            'UPDATE %I SET organization_id = $1 WHERE organization_id IS NULL',
            tbl
        ) USING default_org;
        EXECUTE format(
            'ALTER TABLE %I ALTER COLUMN organization_id SET NOT NULL',
            tbl
        );
        EXECUTE format(
            'CREATE INDEX IF NOT EXISTS ix_%I_org ON %I (organization_id)',
            tbl, tbl
        );
    END LOOP;
END $$;

-- FKs (added after the column exists). Use SET NULL on logs / audit so an
-- org delete doesn't drop the historical evidence; CASCADE on user-owned data.
DO $$ BEGIN
    ALTER TABLE users
        ADD CONSTRAINT fk_users_org FOREIGN KEY (organization_id)
        REFERENCES organizations(id) ON DELETE RESTRICT;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    ALTER TABLE assets
        ADD CONSTRAINT fk_assets_org FOREIGN KEY (organization_id)
        REFERENCES organizations(id) ON DELETE CASCADE;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- Workflow config becomes per-org (composite PK).
ALTER TABLE workflow_config
    ADD COLUMN IF NOT EXISTS organization_id UUID;
DO $$
DECLARE default_org UUID;
BEGIN
    SELECT id INTO default_org FROM organizations WHERE slug = 'default';
    UPDATE workflow_config SET organization_id = default_org
        WHERE organization_id IS NULL;
END $$;
ALTER TABLE workflow_config ALTER COLUMN organization_id SET NOT NULL;

DO $$ BEGIN
    ALTER TABLE workflow_config DROP CONSTRAINT workflow_config_pkey;
EXCEPTION WHEN undefined_object THEN NULL; END $$;

DO $$ BEGIN
    ALTER TABLE workflow_config
        ADD CONSTRAINT workflow_config_pkey PRIMARY KEY (organization_id, asset_type);
EXCEPTION WHEN invalid_table_definition THEN NULL; WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE workflow_config
        ADD CONSTRAINT fk_workflow_config_org FOREIGN KEY (organization_id)
        REFERENCES organizations(id) ON DELETE CASCADE;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- Comment threads attached to a pending workflow change. A reviewer can ask
-- a question without acting, the submitter can respond, and the audit row
-- captures the back-and-forth.
CREATE TABLE IF NOT EXISTS pending_change_comments (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pending_change_id  UUID NOT NULL REFERENCES pending_changes(id) ON DELETE CASCADE,
    author_id          UUID REFERENCES users(id) ON DELETE SET NULL,
    author_username    VARCHAR(150) NOT NULL,
    body               TEXT NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_pending_change_comments_change
    ON pending_change_comments(pending_change_id);

-- Multi-step approvals. ``workflow_config.required_approvals`` (default 1)
-- says how many distinct approvers must say yes before the change applies.
DO $$ BEGIN
    ALTER TABLE workflow_config
        ADD COLUMN required_approvals INTEGER NOT NULL DEFAULT 1;
EXCEPTION WHEN duplicate_column THEN NULL; END $$;

-- Relationship-action gating lives on the source asset's workflow_config row.
DO $$ BEGIN
    ALTER TABLE workflow_config
        ADD COLUMN require_approval_relationship_create BOOLEAN NOT NULL DEFAULT false;
EXCEPTION WHEN duplicate_column THEN NULL; END $$;
DO $$ BEGIN
    ALTER TABLE workflow_config
        ADD COLUMN require_approval_relationship_delete BOOLEAN NOT NULL DEFAULT false;
EXCEPTION WHEN duplicate_column THEN NULL; END $$;

-- Approver routing: when set, only users with this exact role may approve
-- pending changes for this asset type. NULL means "anyone with APPROVE
-- permission" (the prior behaviour).
DO $$ BEGIN
    ALTER TABLE workflow_config ADD COLUMN approver_role user_role;
EXCEPTION WHEN duplicate_column THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS pending_change_approvals (
    pending_change_id  UUID NOT NULL REFERENCES pending_changes(id) ON DELETE CASCADE,
    approver_id        UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    approver_username  VARCHAR(150) NOT NULL,
    note               TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (pending_change_id, approver_id)
);

-- Allow system-generated notifications (no alert_id) so the session-cap eviction
-- and similar UX events have somewhere to land.
DO $$ BEGIN
    ALTER TABLE notifications ALTER COLUMN alert_id DROP NOT NULL;
EXCEPTION WHEN OTHERS THEN NULL;
END $$;

-- Targeted notifications (NULL user_id = org-wide, e.g. alert-fired).
DO $$ BEGIN
    ALTER TABLE notifications
        ADD COLUMN user_id UUID REFERENCES users(id) ON DELETE CASCADE;
EXCEPTION WHEN duplicate_column THEN NULL; END $$;
CREATE INDEX IF NOT EXISTS idx_notifications_user
    ON notifications(user_id) WHERE user_id IS NOT NULL;

-- Widen user_totp.secret so the encrypted-at-rest form fits.
DO $$ BEGIN
    ALTER TABLE user_totp ALTER COLUMN secret TYPE TEXT;
EXCEPTION WHEN OTHERS THEN NULL;
END $$;

-- Per-asset overrides for the sensitive flag. Lets an admin mark a field
-- sensitive on one specific asset (e.g. one HR record) without affecting
-- the rest of the type. Layered on top of the per-org overrides table.
CREATE TABLE IF NOT EXISTS asset_sensitive_overrides (
    asset_id    UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    field_name  VARCHAR(120) NOT NULL,
    sensitive   BOOLEAN NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (asset_id, field_name)
);

-- Per-org overrides for the code-default `sensitive` flag on custom fields.
-- Lets admins mark a field sensitive at runtime without touching custom_fields.py.
CREATE TABLE IF NOT EXISTS sensitive_field_overrides (
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    asset_type      VARCHAR(64) NOT NULL,
    field_name      VARCHAR(120) NOT NULL,
    sensitive       BOOLEAN NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, asset_type, field_name)
);

-- Email digest mode: 'immediate' (per-event) or 'digest' (queued + batched
-- by an hourly job). Per-user toggle that overlays the existing
-- email_notifications_enabled flag — disabled trumps either mode.
DO $$ BEGIN
    ALTER TABLE users
        ADD COLUMN email_notification_mode VARCHAR(16) NOT NULL DEFAULT 'immediate';
EXCEPTION WHEN duplicate_column THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS pending_email_digest (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    alert_id        UUID REFERENCES alerts(id) ON DELETE SET NULL,
    asset_id        UUID,
    asset_name      VARCHAR(255),
    title           VARCHAR(255) NOT NULL,
    message         TEXT,
    link            TEXT,
    queued_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at         TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_digest_pending_user
    ON pending_email_digest(user_id) WHERE sent_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_digest_queued_at
    ON pending_email_digest(queued_at) WHERE sent_at IS NULL;

-- Per-org email branding. Empty string means fall back to defaults at render
-- time; we don't NULL these because the templates can't conditionally elide
-- a missing column without an extra branch.
DO $$ BEGIN
    ALTER TABLE organizations ADD COLUMN email_from_name VARCHAR(120) NOT NULL DEFAULT '';
EXCEPTION WHEN duplicate_column THEN NULL; END $$;
DO $$ BEGIN
    ALTER TABLE organizations ADD COLUMN email_brand_color VARCHAR(20) NOT NULL DEFAULT '';
EXCEPTION WHEN duplicate_column THEN NULL; END $$;
DO $$ BEGIN
    ALTER TABLE organizations ADD COLUMN email_logo_url VARCHAR(500) NOT NULL DEFAULT '';
EXCEPTION WHEN duplicate_column THEN NULL; END $$;

-- Multi-org membership: a user can belong to several orgs with a per-org role.
-- users.organization_id remains the *default* org (used at create time and for
-- audit-derived events); the active-tenant resolution happens at request time
-- against this table.
CREATE TABLE IF NOT EXISTS user_organizations (
    user_id          UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    organization_id  UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    role             user_role NOT NULL DEFAULT 'viewer',
    is_default       BOOLEAN NOT NULL DEFAULT false,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, organization_id)
);
CREATE INDEX IF NOT EXISTS idx_user_orgs_user ON user_organizations(user_id);
CREATE INDEX IF NOT EXISTS idx_user_orgs_org ON user_organizations(organization_id);

-- Backfill: every existing user gets a membership row for their current org.
INSERT INTO user_organizations (user_id, organization_id, role, is_default)
SELECT u.id, u.organization_id, u.role, true
FROM users u
ON CONFLICT (user_id, organization_id) DO NOTHING;

-- Superadmin flag — cross-org admin (org CRUD, viewing every org's data).
-- Distinct from is_admin (which is per-org).
DO $$ BEGIN
    ALTER TABLE users ADD COLUMN is_superadmin BOOLEAN NOT NULL DEFAULT false;
EXCEPTION WHEN duplicate_column THEN NULL; END $$;

-- IP allowlist on API tokens. Empty array = no restriction.
DO $$ BEGIN
    ALTER TABLE api_tokens ADD COLUMN allowed_ips TEXT[] NOT NULL DEFAULT '{}';
EXCEPTION WHEN duplicate_column THEN NULL; END $$;

-- risk_snapshots: switch to a (organization_id, snapshot_date) composite PK
-- so each tenant has its own daily history.
DO $$ BEGIN
    ALTER TABLE risk_snapshots DROP CONSTRAINT risk_snapshots_pkey;
EXCEPTION WHEN undefined_object THEN NULL; END $$;
DO $$ BEGIN
    ALTER TABLE risk_snapshots
        ADD CONSTRAINT risk_snapshots_pkey PRIMARY KEY (organization_id, snapshot_date);
EXCEPTION WHEN invalid_table_definition THEN NULL; WHEN duplicate_object THEN NULL;
END $$;
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
