# GRCen Feature Roadmap

Tracks known gaps identified during the project review. Items marked **SHIPPED** are complete; others are sorted roughly by priority within each tier.

## Tier 1 — High Value, Contained Scope

### 1. Email Notification Delivery — **SHIPPED**
Admin configures SMTP at `/admin/smtp-settings`; users opt in at `/settings`; firing alerts send to the asset owner (or admin fallback) and log every attempt to `notification_deliveries`.

### 2. Webhook Notification Delivery — **SHIPPED**
Admins manage webhooks at `/admin/webhooks`; each endpoint gets HMAC-SHA256-signed JSON posts with `X-GRCen-Event`/`X-GRCen-Signature`/`X-GRCen-Delivery` headers. `fire_alert` dispatches `alert.fired`; admins can send a `ping` event from the UI. All attempts log to `webhook_deliveries` (with a 1-based `attempt` column). Transient failures (network error / HTTP 5xx) now retry with exponential backoff up to `WEBHOOK_MAX_ATTEMPTS` (default 3; 4xx never retries). New event types `asset.created` / `asset.deleted` dispatch in the background from the single-asset REST + page routes (bulk imports deliberately excluded). Remaining work: digest/batch mode so bulk fires don't spam, more event types (e.g. `risk.review_due`), and firing asset events from workflow-approved applies.

### 3. REST API for Assets & Relationships — **SHIPPED**
(The initial review was wrong: per-asset/per-relationship CRUD + `/api/graph/{id}` + filtered list/search have existed. Bearer tokens work via `/api/tokens`. Auth + RBAC + per-token permission checks live in `routers/deps.py`.) Added in this pass: `POST /api/imports/assets/bulk` and `POST /api/imports/relationships/bulk` for JSON-body batch inserts with `dry_run=true` support, a `preview` endpoint for relationship files, and OpenAPI summaries on every route so `/docs` is self-describing.

### 4. Compliance Framework Dashboards — **SHIPPED**
`/frameworks` lists every framework with a coverage bar; `/frameworks/{id}` shows requirements (✓ satisfied / gap with satisfiers), audits, vendors, in-scope assets, and a "Last audited" column derived from `audit_log` timestamps. Gap report exports as CSV (`/frameworks/{id}/gap-report.csv`) and PDF (`/frameworks/{id}/gap-report.pdf`). Inverted view at `/controls` lists every Control with the requirements it covers. **Borrowed coverage (comply once, satisfy many):** a direct-gap requirement that cross-maps `equivalent` to a satisfied requirement in another framework is credited as *covered via crosswalk* — a third coverage tier surfaced on the detail page, gap CSV/PDF, and `GET /api/frameworks/{id}`, with an "effective coverage %" alongside direct, a framework×framework crosswalk matrix on the index, and `GET /api/frameworks/crosswalk-matrix`. Control-library export shipped: `/controls/export.csv` (one row per control with effectiveness, test dates, and the framework:requirement pairs it satisfies; recorded in the data-access log).

### 5. Relationship Bulk Import — **SHIPPED** (folded into #3)
Relationship file upload was already wired to `/api/imports/relationships/execute`. This pass added the matching `/preview` endpoint, a `dry_run` flag on both asset and relationship execute routes, and the JSON-body `/bulk` endpoints covered in #3.

### 4a. Compliance Content Packs — **SHIPPED**
Closes the empty-register cold start (#4 lit the dashboard but shipped no seed content). Bundled, ready-to-install compliance baselines — NIST CSF 2.0 (106 subcategories), CIS Controls v8.1 (153 safeguards), SOC 2 (61 criteria), ISO 27001:2022 Annex A (93 controls), plus a flagship `common-baseline` that ties all four together with a 40-control shared library and 115 cross-framework crosswalks — installable in one click. `services/content_packs.py` assembles JSON fragments under `src/grcen/content_packs/` and projects them through `catalog_sync` under a per-pack source tag (`grcen-pack:<id>`): coexists with autocomply syncs + hand-authored data, idempotent re-install, clean uninstall. Admin UI at `/admin/content-packs` (install / dry-run preview / uninstall); CLI `grcen list-packs` / `install-pack`. `catalog_sync` gained an additive `crosswalks[]` section → `cross_maps` requirement↔requirement edges, surfaced in a new *Cross-framework* column on the framework detail page (giving the export contract's `metadata.crosswalk` a first-class home). See `CONTENT_PACKS.md`. Installed-version tracking shipped: installs record the pack version per org (`app_settings` `content_pack_version:<org>:<pack>`), `/admin/content-packs` and `grcen list-packs` show an "update available" indicator when the bundled version moves ahead. Remaining: per-pack upgrade *content diffing* (what exactly changes on re-install).

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
Every data table carries a NOT NULL `organization_id`; reads filter and writes inject from the authenticated user's active org. Cross-tenant references are rejected at the service layer (asset.owner_id, relationship endpoints, attachment owner, alert.asset_id, workflow_config). Multi-org membership lives in `user_organizations(user_id, organization_id, role, is_default)`, populated automatically on `create_user`. The active tenant for a session is stored in `request.session['active_org_id']` and applied as a `user.organization_id` overlay in `get_current_user` — when the user is also a member, their per-org role is swapped in too. Switch UI on `/settings`, REST at `POST /switch-org`. Stale active-org ids fall back to default. Cross-org admin: `users.is_superadmin` flag + new `Permission.MANAGE_ORGS` (deliberately *not* in any role's permission set, only the flag unlocks it) drive `/admin/orgs` (cross-tenant list + create + delete) and `grcen createsuperadmin`. Per-org email branding (`organizations.email_from_name` / `email_brand_color` / `email_logo_url`) overrides the default templates at render time. Self-service org invitations shipped: admins mint single-use expiring invite links at `/admin/invitations` (role + optional email/note, SHA-256-hashed token at rest, revocable); the invitee sets credentials on the public `/invite/{token}` page and lands in the right org. **Postgres RLS — SHIPPED** (`src/grcen/tenancy.py`, `tests/test_rls.py`): a
`grcen_tenant_isolation` policy on all 30 tenant tables, so the ~212 hand-written
`organization_id` filters get a database-level backstop — a query that forgets one returns
nothing rather than another tenant's rows. Context rides asyncpg's per-acquire `setup` hook, so
none of the ~459 DB call sites changed; `SET LOCAL` was unusable because 404 of them are
single-statement `pool.fetch()` with no transaction. Enforced queries run as an auto-provisioned
`grcen_app` (NOSUPERUSER/NOBYPASSRLS) — **necessary, not belt-and-braces: the connecting role is
normally a superuser and superusers ignore RLS outright, `FORCE ROW LEVEL SECURITY`
notwithstanding.** Measured during build: policies applied to a superuser connection were
provably inert while the suite stayed green, which is why `assert_enforcing` now fails startup
instead. Cross-tenant paths (migrations, scheduler, CLI, pre-auth capability lookups) use
`system_context()`, which declines to drop privileges — a real privilege boundary rather than a
flag the policy trusts. Identity plane (`users`, `user_organizations`, `api_tokens`) is
deliberately excluded: read before a tenant exists, and multi-org membership means
`organization_id` isn't its access key. `RLS_ENABLED` (default on) applies or drops policies in
`init_schema`, so it's reversible. Backups are unaffected — `pg_dump --no-acl` was already in use.
Remaining: per-org SSO/SMTP/webhook/encryption-scope overrides (currently global); narrowing the
calendar-feed and OAuth token exchanges from whole-handler bypass to a scoped lookup, as the
vendor portal already does.

### 13. Workflow / Approval States — **SHIPPED**
Per-type approval gating in `workflow_config` (admin UI at `/admin/workflow`) covers asset create / update / delete and now also relationship create / delete (extra `pending_change_action` enum values + `require_approval_relationship_create` / `_delete` columns; the relationship REST endpoints return 202 with `pending_change_id` when gated). Multi-step approvals via `required_approvals` (each lands in `pending_change_approvals`; submitter and same-approver doubles blocked). Approver-role routing via `approver_role` — when set, only that role can act (superadmin always counts). Comment threads at `/approvals/{id}/comment`. Self-approval override shipped: `workflow_config.allow_self_approval` (per type, off by default, checkbox at `/admin/workflow`) lets the submitter approve/reject their own change — the small-team escape hatch. Remaining: workflow on attachments.

### 14. Field-Level Redaction by Role — **SHIPPED**
`FieldDef` carries a `sensitive: bool` code default; Person `email`/`phone`/`clearance_level` are sensitive out of the box. Per-org overrides live in `sensitive_field_overrides` (PK `(organization_id, asset_type, field_name)`) and per-asset overrides in `asset_sensitive_overrides` (PK `(asset_id, field_name)`). `redact_metadata_async` merges them with the code defaults — per-asset wins over per-org, per-org wins over code, explicit `sensitive=False` can declassify. Admin UI at `/admin/sensitive-fields` for the per-type sheet; collapsible per-asset section on the asset detail page.

### 15. Data-Access Logging — **SHIPPED**
New `data_access_log` table captures reads (views, downloads, exports, PDF generations) separately from the existing `audit_log` (which stays focused on writes). Instrumented routes: asset detail view, asset PDF, framework PDF, asset export (CSV/JSON), attachment downloads. Browse at `/admin/access-log` (filters: user / entity type / action) or `GET /api/access-log/`. CSV export at `/admin/access-log/export.csv` (honors filters; export is itself recorded). Retention is configurable via `app_settings['data_access_log_retention_days']` and a daily APScheduler job at 03:00 UTC purges rows past the window. Setting it via `/admin/access-log` form. List-endpoint sampling shipped: `DATA_ACCESS_LOG_LIST_SAMPLE_RATE` = N logs roughly 1-in-N list reads (asset list page + `/api/assets/`), 0 (default) keeps lists out of the log entirely.

### 16. MFA for Local Auth — **SHIPPED (TOTP)**
Local users enroll TOTP from `/settings`: QR code, eight single-use recovery codes (SHA-256 hashed). Login redirects to `/login/mfa` when MFA is enabled and accepts either the TOTP or a recovery code. Per-role enforcement via `MFA_REQUIRED_FOR_ROLES`. TOTP secrets can now be encrypted at rest via the `totp_secrets` scope (covered by the GDPR / Full encryption profiles); flipping it on encrypts on the next enrolment, and reads transparently decrypt either form. Remaining: FIDO2 / WebAuthn.

## Tier 4 — Hardening & Operational

### 17. General API Rate Limiting — **SHIPPED**
`RateLimitMiddleware` runs in front of every non-exempt request. Sliding-window counters keyed by (caller, bucket, prefix): caller = API token → session id → client IP, bucket = `read` vs `write`, prefix = matching route override or `""`. Default budgets 600 read / 120 write per minute, configurable from the admin UI at `/admin/rate-limits` (DB-backed; cached for 30 s and propagated to all workers via the middleware refresh). Per-route overrides via `RATE_LIMIT_ROUTE_OVERRIDES` (`"<prefix>:<read>:<write>,..."`); longest matching prefix wins. 429 responses include `Retry-After` and `X-RateLimit-Limit` / `Remaining`. Remaining: shared backend (Redis) so multi-worker deployments don't undercount.

### 18. Concurrent Session Limits — **SHIPPED**
`session_service.create_session` enforces a concurrent-session cap before inserting; the post-insert count never exceeds the configured limit. Per-role overrides via `SESSION_MAX_CONCURRENT_{ADMIN,AUDITOR,EDITOR,VIEWER}` (`-1` falls through to the global `SESSION_MAX_CONCURRENT` default of 5; admins default to 3). When the cap evicts older sessions, a targeted in-app notification (rows now carry `user_id`) lands in the affected user's notification feed. `/settings` lists the user's own sessions with revoke buttons; `/admin/sessions` adds a cross-user listing for admins (org-scoped — admins can't revoke sessions in another org).

### 19. Backup Encryption & Secrets Management — **SHIPPED (backup + IP allowlist + edit UI)**
`grcen backup <out>` runs `pg_dump` and writes an AES-256-GCM-encrypted file. `grcen restore <in>` decrypts and pipes through `psql`. Token IP allowlist accepts exact-match addresses and CIDR ranges (v4 + v6). Edit UI at `/tokens` — each non-revoked token has a textarea row for the allowlist; the form validates each entry through `ipaddress.ip_network` before saving (typos surface as a session error instead of silently locking the token out). OAuth 2.0 client-credentials shipped: register machine clients at `/admin/oauth-clients` (bcrypt-hashed secret shown once, per-client permission scope + token TTL), exchange at `POST /api/oauth/token` for a short-lived scoped `api_tokens` bearer. Backup format v2 shipped: each backup embeds a random per-file HKDF salt in the header (no two backups share a derived key); v1 fixed-salt files still restore.

### 20. HTML Email Templates — **SHIPPED (with branding + digest mode)**
Outbound alert emails are `multipart/alternative` (plain-text + HTML). Templates: `_layout.html`, `alert.html`/`.txt`, `digest.html`/`.txt`. The HTML shell pulls `app_name`, `brand_color`, and `logo_url` from per-org branding when present (`organizations.email_from_name` / `email_brand_color` / `email_logo_url`), with per-field fallback to defaults. `List-Unsubscribe` + `List-Unsubscribe-Post: List-Unsubscribe=One-Click` headers point at `/settings`. **Digest mode**: users can pick `email_notification_mode = 'digest'` from `/settings`; alerts queue into `pending_email_digest` and an APScheduler hourly job (`_flush_email_digests`, `:15` past the hour) groups by user × org and sends one envelope per group. Falls back gracefully when a user opts out between queue and flush. Email previews shipped: `/admin/smtp-settings/preview?kind=alert|digest` renders the outbound HTML with sample data and the org's branding, linked from the SMTP settings form.

## Tier 5 — Net-New Features

### 21. Security Questionnaire Answer Library (Inbound) — **SHIPPED**

Built in four phases (all on `main` once validated):
- **Phase 1** — `AssetType.ANSWER` (question=name, canonical answer=description) + custom fields; the `POSTURE_TYPES`/`ORGANIZATIONAL_TYPES` taxonomy split excluding answers from the general /assets surfaces; the `substantiated_by` relationship; the `/answers` workspace and forced-type create flow.
- **Phase 2** — freshness engine: `answer_service.list_answers` flags an answer needs-review when unsubstantiated or backed by a degraded asset (control ineffective/not_tested, asset inactive/archived, framework not_started/not_applicable); `/answers` review column + dashboard widget.
- **Phase 3** — inbound questionnaires: `questionnaires` + `questionnaire_responses` tables, CSV question import, per-question fill (map to a library answer with auto-fill, or manual), status tracking; routes under `/questionnaires`.
- **Phase 4** — export filled questionnaires to CSV + branded PDF (`reports/questionnaire.html`), recorded in the data-access log.

Design notes below were the pre-build plan; they remain accurate.

**Problem.** When a customer or prospect sends *us* a security questionnaire (SIG, CAIQ, or a bespoke spreadsheet) during a sales cycle or vendor review, someone has to answer dozens to hundreds of questions about our own posture, consistently, and re-answer them every time. A flat answer bank (Loopio-style) solves the "type it once" problem but not the "is this answer still true?" problem — it goes stale silently, and you confidently tell a prospect "MFA is enforced on all admin accounts" months after someone disabled it.

**Why it lives in GRCen, not the evidence-collection tool.** GRCen is the system of record for **control state** (Control `metadata.effectiveness`, asset status, framework coverage). The sibling tool only *collects evidence* upstream — it does not hold pass/fail state. Because the "is this answer still true" signal originates from control state that lives here, the feature is self-contained in GRCen: no cross-tool integration or webhook-in is required for freshness. (Evidence collected by the other tool already lands as attachments/assets — that is a separate, existing pathway.)

The differentiator over a flat answer bank: answers are **graph-linked to the assets that substantiate them**, so freshness is a graph traversal, not manual bookkeeping.

#### Data model

Split by lifecycle: the **reusable library** is graph-native; **transactional questionnaire instances** are dedicated tables.

- **Answer-library entries → graph (new `AssetType.ANSWER`).** Question text → asset `name`; canonical answer + answer-type (yes-no / scale / free-text) → custom fields (`custom_fields.py`); categorize with the existing tag vocabulary (#8). Modeling these as assets is deliberate: it makes substantiation a real `Relationship`, which unlocks the freshness traversal and reuses attachments (#6), audit, redaction (#14, for answers that quote sensitive detail), search, and the node graph for free.
- **Substantiation links → relationships.** New relationship type `substantiated_by` (add to `RELATIONSHIP_LABELS` in `routers/_pages_shared.py`; `relationship_type` is already a free-text VARCHAR, so no schema change). An Answer links `substantiated_by` → one or more **Control / Policy / System / Framework / Audit** assets.
- **Questionnaire documents → new tables.** `questionnaires` (an imported incoming questionnaire: name, source customer, due date, status) and `questionnaire_responses` (one row per question in that instance: question text, mapped `answer_asset_id` nullable, filled answer text, reviewer, status). These are point-in-time documents, not permanent graph nodes, so they stay out of the asset graph. Both carry `organization_id` per the multi-tenancy convention (#12).

#### Surfaces (decided)

`AssetType.ANSWER` is a full member of the data model (real relationships, freshness traversal, attachment/audit/redaction reuse) but is *not* surfaced like the 16 organizational asset types. Decision:

- **Home:** a dedicated `/answers` workspace (follows the `/risk-management` and `/controls` precedent) where import / fill / export / staleness-review live.
- **Node graph + search:** answers **do** appear — seeing which customer-facing claims a Control/Policy substantiates is the traceability payoff; depth limits keep it bounded.
- **General `/assets` list, dashboard asset counts, framework "in-scope assets" panels:** answers are **excluded by default** (answers are posture metadata, not org assets). A `?type=answer` filter on `/assets` is the escape hatch.
- **Implementation of the carve-out:** introduce one taxonomy split at the model level — `ORGANIZATIONAL_TYPES` (the existing 16) vs `POSTURE_TYPES` (Answer) — and have the general surfaces iterate `ORGANIZATIONAL_TYPES`, rather than scattering `type != 'answer'` literals. Future metadata-style types slot in cleanly. (Library volume is expected to be modest, so this is a clarity/conceptual choice, not a scale one.)

#### Freshness engine (the payoff)

A traversal walks each Answer's outbound `substantiated_by` edges (reusing `services/graph.py`) and flags the answer **needs-review** when any substantiating asset has degraded — Control `effectiveness` of `ineffective`/`not_tested`, Policy/System status `inactive`/`archived`, or a Framework with open gaps. Mirrors the existing risk control-effectiveness rollup pattern (#7). Surfaced as a column in the library list and as a dashboard widget ("N answers need review"); optionally fires an alert (#1/#2) when an answer goes stale.

#### Fill workflow

1. Import an incoming questionnaire (CSV first; XLSX later) — each row becomes a `questionnaire_responses` question.
2. Match questions against the library — manual mapping UI first (pick the library entry per question), fuzzy/semantic matching as a later enhancement.
3. Auto-fill mapped questions from their library entries; flag any answer whose source is stale before sending.
4. Export the filled questionnaire as CSV/PDF (reuse `pdf_service` + `templates/reports/_base.html`, with per-org branding). Record the export in the data-access log (#15) — who sent what to which customer matters for inbound reviews.

#### Reuse summary
Attachments (evidence on answers), tags (#8), RBAC (separate "edit library" vs "fill questionnaire" permissions), redaction (#14), PDF/CSV export (#11), access log (#15), alerts (#1/#2). New surface area is mainly: one asset type + custom fields, one relationship type (display only), two tables, the freshness traversal, and the import/fill UI.

#### Open questions
- Permission granularity: does filling a questionnaire need a distinct permission from editing the library?
- ~~Whether `AssetType.ANSWER` should appear in the general asset list/graph by default~~ **Resolved** — see "Surfaces (decided)" above: dedicated `/answers` home, present in graph+search, excluded from the general `/assets` list and asset counts via an `ORGANIZATIONAL_TYPES` / `POSTURE_TYPES` taxonomy split.
- Outbound vendor-risk questionnaires (assessing *our* vendors — the original roadmap-review interpretation) share almost no code with this and are explicitly **out of scope** for #21; track separately if wanted.

### 22. Asset Watchers — **SHIPPED**
Per-user change subscriptions: a Watch button on every asset detail page inserts into `asset_watchers`; updates and deletes (REST + page routes) send targeted in-app notifications (`notifications.user_id`) to every watcher except the actor. `services/watcher_service.py`. Remaining: optional email delivery for watch notifications, watching via the REST API.

### 23. iCal Review-Calendar Feed — **SHIPPED**
Review/due dates as a subscribable calendar: users generate a per-user capability URL from `/settings` (`users.calendar_token`, rotatable/revocable), and `/calendar/{token}/grcen.ics` serves the org's review dates (from `review_service`) as all-day VEVENTs (RFC 5545, folded lines, escaped text). `services/calendar_service.py`, `routers/calendar.py`. Remaining: per-user filtering (only my assets), alert schedules as VEVENTs.

### 24. "My Work" Page — **SHIPPED**
`/my-work` aggregates, per user: approvals waiting on an approver, the user's own pending submissions, reviews due/overdue on assets owned by their linked Person asset, open findings assigned to them, the answer-library needs-review count, and their watched assets. `routers/my_work.py`, nav link in the top bar. Remaining: dashboard widget summarizing my-work counts.

---

## Tier 6 — External System Integrations

The full ingress/egress ecosystem map lives in `INTEGRATIONS.md`; this tier tracks the connector
build. The reusable ingress seam is the `(source, source_ref)` provenance upsert.

### I0. Generic external-upsert primitive — **SHIPPED**
`services/external_sync.py`: `upsert_external_asset` / `upsert_external_edge` (idempotent on
`(organization_id, source, source_ref)`; the source owns `status`, unlike the narrower
`catalog_sync` projection which deliberately leaves human archival alone) + `prune_stale_assets`
/ `prune_stale_edges`. New `sync` permission (Admin-held, and grantable to a scoped API token).
Every future connector (HRIS, CMDB, scanner) builds on this. Tests `tests/test_external_sync.py`.
Remaining: a generic `POST /api/sync/<source>` HTTP surface (lands with the first bulk connector).

### I0b. External-identity fields — **SHIPPED**
`custom_fields.py` gained the fields an external source needs to key/populate: Person
`employee_id`/`location`/`cost_center`; System `hostname`/`ip_address`/`fqdn`/`external_id`/
`cloud_resource_id`; Device `ip_address`/`external_id`; Finding `cve_id`/`cvss_score`/
`cvss_vector`/`scanner`/`scan_id`/`affected_asset`/`first_seen`/`last_seen`. Additive (metadata
JSONB), so they surface in forms/detail/columns/export with no schema change.

### I1. IdP SCIM 2.0 (Users + Groups) — **SHIPPED**
`routers/scim.py` + `services/scim_service.py`: SCIM 2.0 `/scim/v2/Users` **and**
`/scim/v2/Groups` (create/get/list+filter/PUT/PATCH/DELETE) + `ServiceProviderConfig`,
authenticated by a `sync`-scoped API token (org-bound, so provisioning is tenant-scoped). Each
SCIM user maps to a GRCen login plus an externally-owned Person (`source='scim'`, `source_ref` =
`externalId` || `userName`, projected through I0). `active:false` / `DELETE` soft-deprovision —
the login is deactivated and the Person archived (never hard-deleted, preserving audit history
and human-authored edges) — closing the deprovisioning gap that login-time OIDC/SAML JIT leaves
open. Enterprise-extension attributes (employeeNumber, department, manager, title) map to the
I0b Person fields. **Groups → role sync:** `scim_groups` / `scim_group_members` back the Group
resource; a group's `displayName` maps to a GRCen role via a configurable map
(`app_settings['scim_group_role_map']`, edited at `/admin/scim-settings`), and each member's
login role is recomputed to the highest-privilege role among their groups (removed from every
mapped group → the SCIM default role). SCIM Group PATCH member add/remove (incl. the
`members[value eq "id"]` path form) is handled. Tests `tests/test_scim.py`. Remaining: per-org
SCIM config (currently global). See `INTEGRATIONS.md` Part C for the rest of the roadmap.

### I2. HRIS roster sync — **SHIPPED**
`services/hris_sync.py` (`sync_roster`) — the bulk counterpart to SCIM. Projects an HRIS roster
(employees + optional org units) into People and Organizational Units with `manages` /
`member_of` / `parent_of` edges, idempotent via I0. Movers re-point edges (stale ones pruned);
**leavers are archived and their linked login disabled** (default; toggle with `--keep-leavers`),
with optional owned-asset reassignment (`--reassign-to`). Delivered as CLI `grcen sync-hris
<file>` and `POST /api/sync/hris` (`routers/sync.py`, `sync`-scoped token — which also lands
I0's generic HTTP ingress surface). Roster contract in the `hris_sync` module docstring. Tests
`tests/test_hris_sync.py`.

### I3. CMDB / cloud-inventory sync — **SHIPPED**
`services/cmdb_sync.py` (`sync_inventory`): the infrastructure counterpart to the HRIS People
sync. Projects a CMDB / cloud inventory (systems + devices) into System and Device assets with
real hostnames / IPs / resource-ids, `depends_on` CI-dependency edges, and owner-by-name linkage
to a Person/OU. Each asset's `metadata.external_id` defaults to its CI id, so a uvt/scanner
finding (I4) referencing that id resolves straight onto the CMDB-synced host — the two connectors
join up with no extra plumbing. Decommissioned records (status) and CIs that drop out of the
inventory are archived (`archive_missing`, default on; a devices-only sync never archives
Systems). CLI `grcen sync-cmdb <file>` and `POST /api/sync/cmdb`. Contract in the `cmdb_sync`
module docstring. Tests `tests/test_cmdb_sync.py`.

### I4. Vuln findings via uvt (+ risk aggregation) — **SHIPPED**
`services/vuln_sync.py` (`sync_findings`): consumes the sibling **uvt** tracker's triaged
findings export → Finding assets (via I0), each linked `System --affected_by--> Finding` by
resolving the affected host (external_id → hostname → fqdn → ip_address → name; GRCen never
invents Systems — an unresolved target is a warning, the Finding is still recorded). Findings
that drop out of the export are archived as `remediated` (`resolve_missing`, default on; toggle
`--keep-resolved`). CLI `grcen sync-vulns <file>` and `POST /api/sync/vulns`. Export contract in
the `vuln_sync` module docstring. Tests `tests/test_vuln_sync.py`.

**Risk aggregation** (`services/risk_aggregation.py`, `aggregate_vuln_risks`): rolls open
Findings up into Risk assets — grouped by affected **System** (default) or by **severity** —
mapping worst-severity to the register's likelihood × impact (critical 4×5, high 3×4, …) so
they surface on the risk register + 5×5 heatmap, linking `Risk --threatens--> System` and
`Risk --relates_to--> Finding` for traceability. Idempotent via I0 under `source='vuln-agg'`:
edges to closed findings prune, and a Risk archives once its group has no open findings
(reactivating if new ones appear). CLI `grcen aggregate-risks [--group-by=system|severity]`
plus a nightly APScheduler job (00:20 UTC). Tests `tests/test_risk_aggregation.py`. The grouping
policy is configurable — `system` and `severity` ship; `cve` is a straightforward extension.

### E1. Slack / Teams native webhook delivery — **SHIPPED**
Per-webhook `format` (`grcen` | `slack` | `teams`) with a formatter branch in
`webhook_service.send_to_webhook`: `slack` posts a mrkdwn incoming-webhook message, `teams` a
MessageCard, both unsigned (the secret URL is the credential); `grcen` (default) keeps the signed
JSON envelope unchanged. Any event can now reach Slack in Slack format, configured from the
`/admin/webhooks` format selector. The event catalog is centralized in
`webhook_service.WEBHOOK_EVENTS` (single source for the admin UI + chat formatter) and gains
`asset.updated`, emitted from the REST + page asset-update handlers alongside the existing
watcher notification. Tests `tests/test_webhooks.py`. Remaining: domain events (`finding.*`,
`risk.appetite_breached`, `review.due`) need lifecycle emit points; a syslog/CEF SIEM egress.

### E2. Auditor evidence package — **SHIPPED**
`services/evidence_package.py` (`build_package`): assembles one ZIP per compliance framework —
`manifest.json` + `README.txt`, the Statement of Applicability, the framework-scoped control
library, the **per-run control test ledger** (every recorded run, not just the latest summary —
the piece GRCen couldn't export before), the **audit trail** (change history) for the framework's
assets, and the actual **evidence files** (encrypted attachments decrypted in) with an
`evidence/_index.csv`. Optional `?since=&until=` scopes the ledger + audit trail to an audit
period. `GET /frameworks/{id}/evidence-package.zip` (EXPORT-gated, access-logged) + a download
button on the framework detail page. Tests `tests/test_evidence_package.py`. Remaining: scheduled
delivery (email the package on a cadence via an APScheduler job, like the digest flush).

### E3. External ticketing (push + status sync) — **SHIPPED**
`services/ticketing_service.py`. **Outbound:** `push_finding` builds a provider-specific payload
(Jira `/rest/api/2/issue`, ServiceNow `/api/now/table/*`, or a generic JSON POST), creates the
ticket, and stamps `external_ticket` / `external_ticket_url` / `external_ticket_status` onto the
finding — `POST /api/findings/{id}/ticket` (EDIT-gated). **Inbound:** `sync_ticket_statuses`
maps an ITSM status feed (`{"tickets":[{ticket_key,status}]}`) onto `finding_status` via a
default status map — `POST /api/sync/tickets` (`sync`-scoped), closing the loop without a bespoke
webhook receiver. Provider config + encrypted API token in `itsm_settings` (new `itsm_config`
table, `itsm_secrets` encryption scope), admin page `/admin/itsm-settings`. Finding gains
`external_ticket*` custom fields. Tests `tests/test_ticketing.py`. Remaining: auto-push on finding
raise (per-severity rule); a configurable status map.

### E4. Calendar enrichments — **SHIPPED**
Enriches the per-user iCal review feed (`calendar_service` + `review_service` +
`routers/calendar.py`): **audit** end-dates now appear as events (added to `REVIEW_DATE_FIELDS`);
per-type event phrasing (audits read "Audit: …", findings "Finding remediation due: …", etc.,
policies unchanged); a **VALARM** reminder on each event via `?alarm=N` days (default 7, `0`
disables); and a `?scope=mine` filter that limits the feed to assets the subscriber owns (via
their linked Person). Options are documented on the `/settings` calendar card. Tests
`tests/test_calendar_feed.py`. Deferred: true push-to-calendar via the Google Calendar / MS
Graph API — net-new OAuth surface, tracked as a follow-up.

### CSPM misconfiguration sync (I4 second wave) — **SHIPPED**
`services/cspm_sync.py` (`sync_findings`): consumes a CSPM export (Wiz, Prisma, AWS Security Hub,
Steampipe) — cloud-posture *misconfigurations* — into Finding assets under `source='cspm:<vendor>'`,
each linked `System --affected_by--> Finding` by resolving the affected cloud resource
(`resource_id`/ARN → System `cloud_resource_id` → `external_id` → name; i.e. the keys the CMDB /
cloud-inventory sync populates, I3). Check id → `scan_id`, provider → `scanner`, framework/resource
context in the description + metadata. Same resolve-on-disappearance lifecycle as `vuln_sync`, and
because `risk_aggregation` (I4) rolls up *all* open findings, CSPM misconfigs feed the risk register
with no extra wiring. CLI `grcen sync-cspm <file>` and `POST /api/sync/cspm`. Contract in the
`cspm_sync` module docstring. Tests `tests/test_cspm_sync.py` (incl. the CSPM→risk-aggregation
interlock). Remaining: mapping a CSPM check to its GRCen Control (evidence linkage).

### I5. Generic upsert-by-source — **SHIPPED**
`services/generic_sync.py` (`sync`) + `POST /api/sync/upsert` (`sync`-scoped): the untyped
sibling of I1–I4, for callers with no matching connector — a spreadsheet export, an in-house
CMDB with its own shape, a one-off ETL. The caller declares its own `source` tag and a stable
`ref` per record; GRCen reconciles on `(organization_id, source, source_ref)` through the same
I0 primitives, so re-posting the same inventory updates in place instead of duplicating. Closes
the gap where the only programmatic ingress was `/api/imports` (blind `INSERT`, duplicates on
re-run) or the REST API (keys on GRCen's UUIDs, so the client has to maintain its own
external-id → GRCen-id mapping table).

Payload carries `assets[]` (`ref`/`type`/`name` + description, status, owner-by-name, tags,
criticality, and either an explicit `metadata` bag or flat per-type custom-field keys coerced
exactly as a CSV import column would be — so a CSV row read into a dict posts through unchanged)
and `relationships[]` (`from`/`to` external refs + `type`), resolving refs against the batch
first then earlier runs of the same source. `metadata.external_id` defaults to the record's
`ref`, so uvt/CSPM findings resolve onto generically-synced Systems for free. Lifecycle is the
body's `on_missing`: `ignore` (default — a partial/paginated upload must not archive what it
omits), `archive`, or `delete`. Staleness is scoped per asset *type* the payload carried
(cmdb_sync's per-CI-class rule), and edges prune only when the payload carried a
`relationships` key at all. `?dry_run=true` previews in a rolled-back transaction.

Guard: caller-supplied `source` tags that collide with a built-in connector's namespace
(`scim`, `hris`, `cmdb`, `cspm`, `uvt`, `autocomply`, `vuln-agg`, `grcen-pack:*`) are rejected —
otherwise `{"source": "scim", "assets": [], "on_missing": "delete"}` would drop an IdP-synced
directory.

**CSV front door:** `POST /api/sync/upsert/csv` (multipart — an `assets` file, a
`relationships` file, or both) runs the same path through `payload_from_csv`, a thin adapter
onto the payload above, so the contract, validation and lifecycle rules keep exactly one
implementation. Columns are the record keys (`ref` — or `external_id`/`ci_id` — `type`, `name`,
…, plus per-type custom-field columns); `tags` is one comma-separated cell, matching the tag
input on the asset form; a `default_type` form field covers homogeneous exports with no `type`
column. Headers are stripped/lowercased and a BOM dropped (Excel), blank and `,,,` filler lines
skipped, and a header that isn't a known field for its row's type comes back as a **warning**
rather than vanishing silently. Blank cells omit their key rather than blanking the value, so a
CSV can't clear `tags`/`criticality`/`owner` — that needs the JSON door. Sending both files in
one request keeps the sync in one transaction, which is what lets an edge resolve against an
asset created in the same call.

**Name-based linking:** each end of a relationship is addressed one of two ways, exactly one —
by external `ref` (`from`/`to`) or by `from_name` + `from_type` / `to_name` + `to_type`. The
latter reaches assets the source doesn't own: the hand-authored Org Unit that owns a synced
system, an HRIS-synced Person, a content-pack Control. Org-scoped and source-agnostic (the rule
`owner` already follows — restricting to `source IS NULL` would baffle anyone linking to an
HRIS Person). Setting both forms for one end is an *error*, not a precedence rule, and a ref
that fails to resolve never silently falls back to a name. A duplicate name+type resolves to the
oldest match **and warns**; an unmatched one warns and skips the edge — nothing is ever invented
to satisfy a link. Derived edge refs are unchanged for ref-only edges, so the feature doesn't
churn edges synced before it landed. The vocabulary deliberately differs from
`/api/imports/relationships`' `source_name`/`target_name`: `source` already means the provenance
tag on this endpoint, and overloading it exactly where both meanings are live would be a trap.

Tests `tests/test_generic_sync.py` (44). I5 is complete.

## Known Stale / Cleanup
- `CLAUDE.md` has been refreshed but the rest of `configure_*.md` and `security_features_and_requirements.md` should be reviewed in the same pass when we make significant changes to those areas.
