-- Enable pg_trgm for trigram search on asset names
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Asset type and status enums
CREATE TYPE asset_type AS ENUM (
    'person', 'policy', 'product', 'system', 'device',
    'data_category', 'audit', 'requirement', 'process',
    'intellectual_property', 'risk', 'organizational_unit'
);

CREATE TYPE asset_status AS ENUM ('active', 'inactive', 'draft', 'archived');
CREATE TYPE attachment_kind AS ENUM ('file', 'url', 'document');
CREATE TYPE schedule_type AS ENUM ('once', 'recurring');

-- Assets
CREATE TABLE assets (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    type        asset_type  NOT NULL,
    name        VARCHAR(255) NOT NULL,
    description TEXT,
    status      asset_status NOT NULL DEFAULT 'active',
    owner       VARCHAR(255),
    metadata    JSONB DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_assets_type ON assets (type);
CREATE INDEX ix_assets_name_trgm ON assets USING gin (name gin_trgm_ops);
CREATE INDEX ix_assets_metadata ON assets USING gin (metadata);

-- Relationships
CREATE TABLE relationships (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_asset_id   UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    target_asset_id   UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    relationship_type VARCHAR(255) NOT NULL,
    description       TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_rel_source_target ON relationships (source_asset_id, target_asset_id);

-- Attachments
CREATE TABLE attachments (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    asset_id    UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    kind        attachment_kind NOT NULL,
    name        VARCHAR(255) NOT NULL,
    url_or_path TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Alerts
CREATE TABLE alerts (
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

-- Notifications
CREATE TABLE notifications (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    alert_id   UUID NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
    title      VARCHAR(255) NOT NULL,
    message    TEXT,
    read       BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Users
CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username        VARCHAR(150) UNIQUE NOT NULL,
    hashed_password VARCHAR(255) NOT NULL,
    is_active       BOOLEAN NOT NULL DEFAULT true,
    is_admin        BOOLEAN NOT NULL DEFAULT false,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
