# GRCen

GRCen (pronounced "gurken") is a free and open-source Governance, Risk, and Compliance (GRC) tool. It is free as in freedom — licensed under an open-source license — and free as in cost. No subscriptions, or per-seat pricing.

GRCen is purpose-built to map assets (organizational assets, not just physical ones), ownership, and relationships as they actually exist in your organization. It runs on a simple stack — Python, PostgreSQL, and plain HTML templates — with no heavyweight frameworks, no JavaScript build step, and minimal moving parts. Deploy it with Docker Compose and you're up in minutes.

## Key Features

- **Asset graph** — 12 built-in asset types (People, Policies, Systems, Risks, Devices, and more) linked by described relationships. Search from any node to understand how it connects to everything else.
- **Visual relationship graphs** — Interactive node graphs that show how assets relate at a glance.
- **Advanced search & filtering** — Full-text search across names, descriptions, and owners. Filter by type, status, date range, and metadata fields. Sortable columns and an advanced search toggle for progressive disclosure.
- **Risk scoring & heatmap** — Automatic risk score calculation from likelihood and impact. Visual 5×5 heatmap matrix on the dashboard with top risks ranked by severity.
- **Review workflows** — Track review dates across asset types with overdue/due-soon status indicators. Dashboard widget surfaces items needing attention.
- **Bulk import & export** — Import assets and relationships from CSV or JSON. Export filtered datasets in multiple formats.
- **Asset cloning** — Duplicate any asset with a single click, optionally including all its relationships.
- **Schedulable alerts** — Set reminders for annual reviews, audits, certifications, or any recurring process.
- **Role-based access control** — Four roles (Admin, Editor, Viewer, Auditor) with granular permissions.
- **SSO/OIDC authentication** — Integrate with any OIDC-compliant identity provider (Keycloak, Azure AD, Google, etc.). Claim-based role mapping, automatic Person asset provisioning on first login, and local password auth as a fallback.
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

## SSO/OIDC Configuration

GRCen supports any OIDC-compliant identity provider. SSO is configured entirely from the admin UI — no environment variables needed.

1. Log in as an admin and go to **Users > SSO Settings**
2. Enter your identity provider's Issuer URL, Client ID, and Client Secret
3. Optionally configure role mapping (map IdP groups to GRCen roles) and the default role for new SSO users
4. Save — the login page immediately shows a "Sign in with SSO" button

On first SSO login, a Person asset is automatically created and linked to the user account. Admins can change this link from the user edit page.
