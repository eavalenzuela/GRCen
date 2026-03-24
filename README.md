# GRCen

GRCen (pronounced "gurken") is a free and open-source Governance, Risk, and Compliance (GRC) tool. It is free as in freedom — licensed under an open-source license — and free as in cost. No subscriptions, or per-seat pricing.

GRCen is purpose-built to map assets (organizational assets, not just physical ones), ownership, and relationships as they actually exist in your organization. It runs on a simple stack — Python, PostgreSQL, and plain HTML templates — with no heavyweight frameworks, no JavaScript build step, and minimal moving parts. Deploy it with Docker Compose and you're up in minutes.

## Key Features

| Dashboard | Asset Detail | Org Views |
|:-:|:-:|:-:|
| ![Dashboard](images/dashboard.png) | ![Asset Example](images/asset_example.png) | ![Org Views](images/org_views_product.png) |

- **Asset graph** — 16 built-in asset types (People, Policies, Systems, Risks, Devices, and more) linked by described relationships. Search from any node to understand how it connects to everything else.
- **Visual relationship graphs** — Interactive node graphs that show how assets relate at a glance.
- **Advanced search & filtering** — Full-text search across names, descriptions, and owners. Filter by type, status, date range, and metadata fields. Sortable columns and an advanced search toggle for progressive disclosure.
- **Risk scoring & heatmap** — Automatic risk score calculation from likelihood and impact. Visual 5×5 heatmap matrix on the dashboard with top risks ranked by severity.
- **Review workflows** — Track review dates across asset types with overdue/due-soon status indicators. Dashboard widget surfaces items needing attention.
- **Bulk import & export** — Import assets and relationships from CSV or JSON. Export filtered datasets in multiple formats.
- **Asset cloning** — Duplicate any asset with a single click, optionally including all its relationships.
- **Schedulable alerts** — Set reminders for annual reviews, audits, certifications, or any recurring process.
- **Role-based access control** — Four roles (Admin, Editor, Viewer, Auditor) with granular permissions.
- **SSO authentication (OIDC & SAML 2.0)** — Integrate with any OIDC-compliant identity provider (Keycloak, Azure AD, Google, etc.) or any SAML 2.0 IdP. Claim/attribute-based role mapping, automatic Person asset provisioning on first login, and local password auth as a fallback. Both protocols are configured entirely from the admin UI.
- **Encryption at rest** — Optional application-level field encryption (AES-256-GCM) for sensitive data. Choose a compliance profile (Minimal, GDPR, Full) or select individual scopes. Covers SSO secrets, user PII, session metadata, audit snapshots, asset custom fields, and uploaded files. BYO encryption key via environment variable with zero-downtime key rotation. See **[configure_encryption.md](configure_encryption.md)**.
- **Configurable audit trail** — Track who changed what and when. Admins choose which entity types are logged and whether to capture field-level diffs.
- **Custom fields** — Extend asset types with additional metadata fields without changing the schema.

## Quick Start

### Docker Compose (recommended)

```bash
docker compose up --build
```

This starts PostgreSQL and GRCen on `http://localhost:8000`. Data persists across restarts via a Docker volume.

Create your first admin user:

```bash
docker compose exec app grcen createadmin
```

If you use containerd instead of Docker, substitute `nerdctl` for `docker`.

### Local Development

Requirements: Python 3.12+, a running PostgreSQL instance.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # edit with your PostgreSQL credentials
grcen createadmin
grcen runserver
```

The app will be available at `http://localhost:8000`. Database tables are created automatically on startup.

## Configuring HTTPS

GRCen supports HTTPS via an nginx reverse proxy (recommended for production) or direct TLS termination. See **[configure_https.md](configure_https.md)** for detailed setup instructions.

## Encryption at Rest

GRCen supports optional application-level encryption of sensitive database fields and uploaded files. Encryption uses AES-256-GCM with per-scope key derivation from a single master key you provide. See **[configure_encryption.md](configure_encryption.md)** for detailed setup instructions.

Quick start: generate a key with `grcen generate-key`, set the `ENCRYPTION_KEY` environment variable, restart, and select a profile from **Admin > Encryption Settings**.

## SSO Configuration (OIDC & SAML 2.0)

GRCen supports both OIDC and SAML 2.0 identity providers. Both are configured entirely from the admin UI — no environment variables needed.

### OIDC

1. Log in as an admin and go to **Users > SSO Settings**
2. Enter your identity provider's Issuer URL, Client ID, and Client Secret
3. Optionally configure role mapping (map IdP groups to GRCen roles) and the default role for new SSO users
4. Save — the login page immediately shows a "Sign in with SSO" button

### SAML 2.0

1. Log in as an admin and go to **Users > SAML Settings**
2. Enter your IdP's Entity ID, SSO URL, and X.509 certificate
3. Optionally configure SP signing certificates, role attribute mapping, and the default role
4. Save — the login page shows a "Sign in with SAML SSO" button
5. Register GRCen with your IdP using the SP metadata URL shown on the settings page (`/auth/saml/metadata`)

Both protocols support automatic user provisioning and Person asset creation on first login, role synchronization on each login, and can be enabled simultaneously. Admins can manage linked user accounts from the user edit page.
