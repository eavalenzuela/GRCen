# GRCen Feature Roadmap

Tracks known gaps identified during the project review. Items marked **SHIPPED** are complete; others are sorted roughly by priority within each tier.

## Tier 1 — High Value, Contained Scope

### 1. Email Notification Delivery — **SHIPPED**
Admin configures SMTP at `/admin/smtp-settings`; users opt in at `/settings`; firing alerts send to the asset owner (or admin fallback) and log every attempt to `notification_deliveries`.

### 2. Webhook Notification Delivery — **SHIPPED**
Admins manage webhooks at `/admin/webhooks`; each endpoint gets HMAC-SHA256-signed JSON posts with `X-GRCen-Event`/`X-GRCen-Signature`/`X-GRCen-Delivery` headers. `fire_alert` dispatches `alert.fired`; admins can send a `ping` event from the UI. All attempts log to `webhook_deliveries`. Remaining work: retry/backoff policy for failed deliveries, digest/batch mode so bulk fires don't spam, and more event types beyond `alert.fired` (e.g. `asset.created`, `risk.review_due`).

### 3. REST API for Assets & Relationships — **SHIPPED**
(The initial review was wrong: per-asset/per-relationship CRUD + `/api/graph/{id}` + filtered list/search have existed. Bearer tokens work via `/api/tokens`. Auth + RBAC + per-token permission checks live in `routers/deps.py`.) Added in this pass: `POST /api/imports/assets/bulk` and `POST /api/imports/relationships/bulk` for JSON-body batch inserts with `dry_run=true` support, a `preview` endpoint for relationship files, and OpenAPI summaries on every route so `/docs` is self-describing.

### 4. Compliance Framework Dashboards — **SHIPPED**
`/frameworks` lists every framework with a coverage bar; `/frameworks/{id}` shows requirements (✓ satisfied / gap with satisfiers), audits, vendors, in-scope assets, and a "Last audited" column derived from `audit_log` timestamps. Gap report exports as CSV (`/frameworks/{id}/gap-report.csv`) and PDF (`/frameworks/{id}/gap-report.pdf`). Inverted view at `/controls` lists every Control with the requirements it covers. Remaining: control-library export.

### 5. Relationship Bulk Import — **SHIPPED** (folded into #3)
Relationship file upload was already wired to `/api/imports/relationships/execute`. This pass added the matching `/preview` endpoint, a `dry_run` flag on both asset and relationship execute routes, and the JSON-body `/bulk` endpoints covered in #3.

## Tier 2 — Deepen Existing Features

### 6. Attachments on Relationships — **SHIPPED**
`attachments` now allows either `asset_id` OR `relationship_id` (enforced via CHECK constraint). Evidence can be attached to edges like "control satisfies requirement" from a dedicated `/relationships/{id}/evidence` page. Asset detail lists each relationship with a link showing its evidence count. Matching REST endpoints live under `/api/relationships/{id}/attachments/` mirroring the asset endpoints (list, create, upload, download, delete). Cascade-delete cleans up attachments when a relationship is removed.

### 7. Risk Management Polish — **SHIPPED**
- **Bulk actions:** checkbox column on the register + "Bulk Apply to Selected" fieldset lets editors update treatment, review date, and owner across many risks in one transaction. Each change is audit-logged.
- **Trend indicators:** `risk_snapshots` table captures daily severity counts (nightly APScheduler cron job at 00:05 UTC); the dashboard shows ▲/▼ arrows per severity card vs. the most recent prior snapshot, coloring increases red for severity bands (green for total), decreases opposite, and grey if unchanged.
- **Control effectiveness rollup:** new `get_risk_control_rollup()` service walks each risk's outbound `mitigated_by` edges, keeps only targets of type `control`, averages their `metadata.effectiveness` into a 0–1 score (effective=1, partially=0.5, not_tested=0.25, ineffective=0), and labels it strong/adequate/weak/none. Surfaced as a new "Controls" column in the register.

### 8. Cross-Cutting Tag Vocabulary — **SHIPPED**
Kept the existing `assets.tags TEXT[]` column (no schema change — the GIN index already powers efficient lookups) and added an aggregation layer on top. `/tags` lists every distinct tag with asset counts and links into the filtered asset list. Admins can rename a tag across every asset in one UPDATE (with automatic dedup if the target name already existed on an asset) or remove it entirely. Asset list + `/api/assets/` accept `?tag=X` to filter cross-type. Asset create/edit forms surface existing tags as click-to-add chips so users converge on shared names. Matching JSON API at `GET /api/tags/`.

### 9. Saved / Bookmarked Searches — **SHIPPED**
New `saved_searches` table stores per-user (path + query_string) bookmarks with an optional `shared` flag. A shared `partials/saved_searches.html` include renders a "Saved searches (N)" dropdown + "Save this search" button on `/assets` and `/risk-management`; saves capture the current filters and re-running is a single click. Owners and admins can delete; private searches return 404 to non-owners (no existence leak). Matching REST endpoints: `GET/POST /api/saved-searches/`, `DELETE /api/saved-searches/{id}`, plus a `?path=X` query filter to scope a list to one page.

### 10. Drag-and-Drop Graph Relationship Creation — **SHIPPED**
Graph view "Link Mode" is now drag-to-link: press on a source node, drag across the canvas (a dashed ghost edge follows the cursor), and drop on a target node — a prompt captures the relationship type and description. Hovered targets highlight green; dropping on empty space or back on the source cancels. Mode stays active across creations so you can wire many relationships in a row.

### 11. PDF / Report Generation — **SHIPPED**
WeasyPrint-backed reports across the app: `/frameworks/{id}/report.pdf` (compliance summary), `/frameworks/{id}/gap-report.pdf` (gap-only doc), `/assets/{id}/report.pdf` (per-asset dossier), `/assets/{id}/audit-report.pdf` (audit-only — frameworks certified, incident findings, in-scope assets), and `/exports/assets.pdf` (filtered asset inventory). Every PDF carries a per-org branded cover page (logo + brand color from `organizations.email_*`) plus the shared header/footer in `templates/reports/_base.html`. Each download is recorded in the access log.

## Tier 3 — Enterprise / Production Gaps

### 12. Multi-Tenancy / Multi-Organization — **SHIPPED**
Every data table carries a NOT NULL `organization_id`; reads filter and writes inject from the authenticated user's active org. Cross-tenant references are rejected at the service layer (asset.owner_id, relationship endpoints, attachment owner, alert.asset_id, workflow_config). Multi-org membership lives in `user_organizations(user_id, organization_id, role, is_default)`, populated automatically on `create_user`. The active tenant for a session is stored in `request.session['active_org_id']` and applied as a `user.organization_id` overlay in `get_current_user` — when the user is also a member, their per-org role is swapped in too. Switch UI on `/settings`, REST at `POST /switch-org`. Stale active-org ids fall back to default. Cross-org admin: `users.is_superadmin` flag + new `Permission.MANAGE_ORGS` (deliberately *not* in any role's permission set, only the flag unlocks it) drive `/admin/orgs` (cross-tenant list + create + delete) and `grcen createsuperadmin`. Per-org email branding (`organizations.email_from_name` / `email_brand_color` / `email_logo_url`) overrides the default templates at render time. Remaining: per-org SSO/SMTP/webhook/encryption-scope overrides (currently global), tenancy-aware Postgres RLS policy (defense-in-depth), self-service org-invitation flow.

### 13. Workflow / Approval States — **SHIPPED**
Per-type approval gating in `workflow_config` (admin UI at `/admin/workflow`) covers asset create / update / delete and now also relationship create / delete (extra `pending_change_action` enum values + `require_approval_relationship_create` / `_delete` columns; the relationship REST endpoints return 202 with `pending_change_id` when gated). Multi-step approvals via `required_approvals` (each lands in `pending_change_approvals`; submitter and same-approver doubles blocked). Approver-role routing via `approver_role` — when set, only that role can act (superadmin always counts). Comment threads at `/approvals/{id}/comment`. Remaining: workflow on attachments, "self-approval for trivial changes" override.

### 14. Field-Level Redaction by Role — **SHIPPED**
`FieldDef` carries a `sensitive: bool` code default; Person `email`/`phone`/`clearance_level` are sensitive out of the box. Per-org overrides live in `sensitive_field_overrides` (PK `(organization_id, asset_type, field_name)`) and per-asset overrides in `asset_sensitive_overrides` (PK `(asset_id, field_name)`). `redact_metadata_async` merges them with the code defaults — per-asset wins over per-org, per-org wins over code, explicit `sensitive=False` can declassify. Admin UI at `/admin/sensitive-fields` for the per-type sheet; collapsible per-asset section on the asset detail page.

### 15. Data-Access Logging — **SHIPPED**
New `data_access_log` table captures reads (views, downloads, exports, PDF generations) separately from the existing `audit_log` (which stays focused on writes). Instrumented routes: asset detail view, asset PDF, framework PDF, asset export (CSV/JSON), attachment downloads. Browse at `/admin/access-log` (filters: user / entity type / action) or `GET /api/access-log/`. CSV export at `/admin/access-log/export.csv` (honors filters; export is itself recorded). Retention is configurable via `app_settings['data_access_log_retention_days']` and a daily APScheduler job at 03:00 UTC purges rows past the window. Setting it via `/admin/access-log` form. Remaining: list-endpoint sampling (currently skipped to keep volume sane).

### 16. MFA for Local Auth — **SHIPPED (TOTP)**
Local users enroll TOTP from `/settings`: QR code, eight single-use recovery codes (SHA-256 hashed). Login redirects to `/login/mfa` when MFA is enabled and accepts either the TOTP or a recovery code. Per-role enforcement via `MFA_REQUIRED_FOR_ROLES`. TOTP secrets can now be encrypted at rest via the `totp_secrets` scope (covered by the GDPR / Full encryption profiles); flipping it on encrypts on the next enrolment, and reads transparently decrypt either form. Remaining: FIDO2 / WebAuthn.

## Tier 4 — Hardening & Operational

### 17. General API Rate Limiting — **SHIPPED**
`RateLimitMiddleware` runs in front of every non-exempt request. Sliding-window counters keyed by (caller, bucket, prefix): caller = API token → session id → client IP, bucket = `read` vs `write`, prefix = matching route override or `""`. Default budgets 600 read / 120 write per minute, configurable from the admin UI at `/admin/rate-limits` (DB-backed; cached for 30 s and propagated to all workers via the middleware refresh). Per-route overrides via `RATE_LIMIT_ROUTE_OVERRIDES` (`"<prefix>:<read>:<write>,..."`); longest matching prefix wins. 429 responses include `Retry-After` and `X-RateLimit-Limit` / `Remaining`. Remaining: shared backend (Redis) so multi-worker deployments don't undercount.

### 18. Concurrent Session Limits — **SHIPPED**
`session_service.create_session` enforces a concurrent-session cap before inserting; the post-insert count never exceeds the configured limit. Per-role overrides via `SESSION_MAX_CONCURRENT_{ADMIN,AUDITOR,EDITOR,VIEWER}` (`-1` falls through to the global `SESSION_MAX_CONCURRENT` default of 5; admins default to 3). When the cap evicts older sessions, a targeted in-app notification (rows now carry `user_id`) lands in the affected user's notification feed. `/settings` lists the user's own sessions with revoke buttons; `/admin/sessions` adds a cross-user listing for admins (org-scoped — admins can't revoke sessions in another org).

### 19. Backup Encryption & Secrets Management — **SHIPPED (backup + IP allowlist + edit UI)**
`grcen backup <out>` runs `pg_dump` and writes an AES-256-GCM-encrypted file. `grcen restore <in>` decrypts and pipes through `psql`. Token IP allowlist accepts exact-match addresses and CIDR ranges (v4 + v6). Edit UI at `/tokens` — each non-revoked token has a textarea row for the allowlist; the form validates each entry through `ipaddress.ip_network` before saving (typos surface as a session error instead of silently locking the token out). Remaining: OAuth 2.0 client-credentials flow, rotating the backup-derivation salt to invalidate old backups when a key is retired.

### 20. HTML Email Templates — **SHIPPED (with branding + digest mode)**
Outbound alert emails are `multipart/alternative` (plain-text + HTML). Templates: `_layout.html`, `alert.html`/`.txt`, `digest.html`/`.txt`. The HTML shell pulls `app_name`, `brand_color`, and `logo_url` from per-org branding when present (`organizations.email_from_name` / `email_brand_color` / `email_logo_url`), with per-field fallback to defaults. `List-Unsubscribe` + `List-Unsubscribe-Post: List-Unsubscribe=One-Click` headers point at `/settings`. **Digest mode**: users can pick `email_notification_mode = 'digest'` from `/settings`; alerts queue into `pending_email_digest` and an APScheduler hourly job (`_flush_email_digests`, `:15` past the hour) groups by user × org and sends one envelope per group. Falls back gracefully when a user opts out between queue and flush. Remaining: webhook-driven email previews in the admin UI.

---

## Known Stale / Cleanup
- `CLAUDE.md` has been refreshed but the rest of `configure_*.md` and `security_features_and_requirements.md` should be reviewed in the same pass when we make significant changes to those areas.
