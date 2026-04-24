# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

GRCen (pronounced "gurken") is a free and open-source GRC (Governance, Risk, Compliance) tool. It manages assets and their relationships as a graph — any object can be linked to any other with a described relationship, searchable from any node.

**Status:** Actively developed. Core graph model, web UI, auth, and many GRC features are implemented. See `feature_roadmap.md` for remaining work.

## Stack

- Python 3.12+, FastAPI (async), Jinja2 templates, asyncpg + PostgreSQL
- D3.js for graph visualization (otherwise minimal JS)
- Deployed via Docker / docker-compose (see `docker-compose.yml`, `docker-compose.prod.yml`, `deploy/`)
- Tests: pytest (run with `.venv/bin/pytest`)

## Code Layout

- `src/grcen/main.py` — FastAPI app entrypoint
- `src/grcen/routers/` — HTTP routes (assets, relationships, graph, imports, exports, alerts, auth, oidc, saml, tokens, org_views, attachments, pages)
- `src/grcen/models/` — data models (asset, relationship, user, alert, attachment, api_token, notification)
- `src/grcen/services/` — business logic (graph traversal, import/export, encryption, audit, risk, alerts, SSO settings, etc.)
- `src/grcen/schemas/` — Pydantic schemas
- `src/grcen/templates/`, `src/grcen/static/` — UI
- `tests/` — pytest suite
- `sample_data/` — example assets and relationships (CSV/JSON) for dev

## Asset Model

16 asset types are implemented: Person, Policy, Product, System, Device, Data Category, Audit, Requirement, Process, Intellectual Property, Risk, Organizational Unit, Vendor, Control, Incident, Framework. Any asset can link to any other via a `Relationship` with a type and free-text description. Per-type custom fields are supported (`src/grcen/custom_fields.py`). Both assets AND relationships can have evidence/document/URL attachments (enforced by a CHECK constraint that an attachment has exactly one owner).

## Implemented Features

- Asset and relationship graph (recursive SQL CTE traversal in `services/graph.py`)
- Visual node graphs (Cytoscape.js) at `/assets/{id}/graph` with a drag-to-link mode for creating relationships inline
- Bulk CSV/JSON import of assets with preview; filterable column-selectable export (CSV/JSON)
- Schedulable alerts and review reminders with three delivery channels: in-app notifications, SMTP email (admin config at `/admin/smtp-settings`, user opt-in at `/settings`, log in `notification_deliveries`), and outbound webhooks (manage at `/admin/webhooks`, HMAC-SHA256 signed, log in `webhook_deliveries`).
- RBAC with four roles: Admin, Editor, Viewer, Auditor (`permissions.py`), plus field-level redaction of fields marked `sensitive=True` in `custom_fields.py` for users without `VIEW_PII` permission
- Audit trail with optional field-level diffs, PII sanitization, encryption support
- SSO: OIDC and SAML 2.0, with admin UI config and role mapping
- Optional application-level encryption at rest (AES-256-GCM, scope-based keys, zero-downtime rotation, blind indexes)
- Risk register with 5x5 heatmap, filtering, overdue tracking, bulk-update actions (treatment/owner/review_date), daily severity snapshots + trend arrows, and a control-effectiveness rollup column that averages `mitigated_by` control edges
- Org Views (hierarchical tree views with orthogonal connectors)
- Cross-cutting tag vocabulary at `/tags` (aggregated counts, admin rename/delete), `?tag=X` filter on `/assets` and `/api/assets/`, plus `GET /api/tags/`
- Saved searches: per-user bookmarks of current filters on `/assets` and `/risk-management` with optional sharing. REST: `/api/saved-searches/`
- PDF reports: WeasyPrint-generated at `/frameworks/{id}/report.pdf` and `/assets/{id}/report.pdf` (shared print stylesheet in `templates/reports/_base.html`)
- Compliance framework dashboards at `/frameworks` and `/frameworks/{id}` with coverage bars, gap highlighting, audit + vendor panels, and matching `GET /api/frameworks/` and `GET /api/frameworks/{id}` JSON endpoints
- REST API: full CRUD for assets/relationships, `/api/graph/{id}` subgraph queries, bulk endpoints at `/api/imports/assets/bulk` and `/api/imports/relationships/bulk` (with `dry_run`), relationship preview at `/api/imports/relationships/preview`, authenticated `/docs` OpenAPI UI, Bearer token auth via `/api/tokens` with per-token permission scoping

## Known Gaps (see `feature_roadmap.md` for the full list)

- Multi-tenancy / multi-org
- PDF report generation
- Cross-cutting tag vocabulary (asset tags exist but are per-asset strings)
- MFA for local auth; field-level redaction by role; data-access (read) logging

## Design Philosophy

The system is a graph of assets and relationships. Searchability from any node and visual representation of relationships are primary concerns. The tool should not impose a paradigm — it exists to map ownership and relationships as they actually are.

## Conventions

- Run tests with `.venv/bin/pytest` (not system Python).
- Org Views trees use orthogonal (right-angle) connectors, not curves or diagonals.
- Security posture is taken seriously — see `secure_coding_requirements.md` and `security_features_and_requirements.md` before changing auth, crypto, or input-handling code.
