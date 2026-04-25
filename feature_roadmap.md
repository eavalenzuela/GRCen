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
`/frameworks` lists every framework with a coverage bar; `/frameworks/{id}` shows requirements (with ✓ satisfied / gap status and the policies, controls, systems, or processes covering them), audits linked via `certifies`, vendors linked via `certified_by`, and all in-scope assets. Coverage logic in `services/framework_service.py` treats a requirement as satisfied if it has an outbound `satisfied_by`/`implemented_by` edge or an inbound `satisfies` edge from a control. Matching REST endpoints at `GET /api/frameworks/` and `GET /api/frameworks/{id}` for programmatic access. Remaining: per-framework gap report export (CSV/PDF), "last audited" rollups, and a control-library view that inverts the graph (controls → which requirements they cover).

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
WeasyPrint-backed reports at `GET /frameworks/{id}/report.pdf` (compliance summary with coverage, requirement gap status, audits, vendors, and in-scope assets) and `GET /assets/{id}/report.pdf` (per-asset dossier with custom fields, relationships in both directions, attachments, and alerts). Shared print stylesheet (`templates/reports/_base.html`) gives both reports a consistent header, @page footer with page counter, and pill styles. "Download PDF" buttons on the framework and asset detail pages. Remaining: asset-list bulk exports, per-audit reports, branding / cover page.

## Tier 3 — Enterprise / Production Gaps

### 12. Multi-Tenancy / Multi-Organization — **SHIPPED (foundation)**
Every data table now carries a NOT NULL `organization_id`; every read and write filters or injects from the authenticated user's org via `routers.deps.get_current_organization_id` (or directly off `user.organization_id`). The default org seeded in the migration owns all pre-multi-tenancy rows. Cross-tenant references are rejected at the service layer: `assets.owner_id` must point inside the same org, both endpoints of a `relationship` must share an org, an `attachment` can only hang off an asset/relationship in the same org, an `alert.asset_id` must be in the user's org, and `workflow_config` is per-org with a composite primary key. The nightly risk-snapshot scheduled job iterates every org via `capture_all_org_snapshots`. Users belong to exactly one org (`users.organization_id`); SSO and TOTP and saved searches and audit / access logs all derive their org from the user. Organization management is CLI-only for this iteration: `grcen createorg`, `grcen listorgs`, and `grcen createadmin` now prompt for an optional org slug. Admin UI at `/admin/organization` shows org info and counts. Remaining: in-app org creation + switcher, multi-org membership (a user belonging to several orgs with different roles), a "superadmin" role with cross-org visibility, per-org SSO/SMTP/webhook/encryption-scope configuration overrides (currently global), and a tenancy-aware row-level security policy (defense-in-depth) once the application-layer scoping has soaked.

### 13. Workflow / Approval States — **SHIPPED**
Per-type approval gating in `workflow_config` (admin UI at `/admin/workflow`) decides whether create / update / delete on each asset type requires approval. When gated, the write becomes a row in `pending_changes` (status: pending / approved / rejected / withdrawn) and the asset is left untouched. The HTML form posts and REST endpoints both detect the gate; REST returns 202 with `pending_change_id` instead of the usual 201/200/204. An approver consumes the queue at `/approvals` (page) or `GET /api/approvals/`; approval applies the recorded payload through the asset service inside a single transaction so the asset write and the queue transition cannot diverge. The audit row is written under the approver with `_workflow.submitted_by` recording who proposed it. Self-approval is blocked (the submitter must withdraw or another approver must act); the `Permission.APPROVE` permission is granted to Admin only by default. Asset detail shows a yellow "pending changes" callout when one or more proposals target that asset. Remaining: workflow on relationships and attachments, multi-step approvals, configurable approver routing per asset type / criticality, "self-approval allowed for trivial changes" override, comment threads on a pending change.

### 14. Field-Level Redaction by Role — **SHIPPED**
`FieldDef` now carries a `sensitive: bool` flag; Person fields `email`, `phone`, and `clearance_level` are marked sensitive and can serve as a template for other asset types. New `Permission.VIEW_PII` is granted to Admin / Editor / Auditor but denied to Viewer. `services/redaction.py` returns a masked copy of metadata for users lacking VIEW_PII and is applied at every egress point: asset detail HTML page, `/api/assets/` list + search + detail, CSV/JSON exports, and PDF asset reports. Secure default: if no user is passed to the export helper, redaction still fires. Remaining: admin UI to mark fields sensitive without code change, per-asset overrides, redaction on non-Person types as more candidates get identified.

### 15. Data-Access Logging — **SHIPPED**
New `data_access_log` table captures reads (views, downloads, exports, PDF generations) separately from the existing `audit_log` (which stays focused on writes). `services/access_log_service.py.record()` is best-effort — a failed insert is logged but never blocks the user response. Instrumented routes: asset detail view, asset PDF, framework PDF, asset export (CSV/JSON), and attachment downloads (both asset- and relationship-owned). Admin + Auditor can browse `/admin/access-log` with filters (user, entity type, action) or hit `GET /api/access-log/` for programmatic queries. Remaining: list-endpoint sampling (currently skipped to keep volume sane), retention policy / TTL, and export of the access log itself.

### 16. MFA for Local Auth — **SHIPPED (TOTP)**
Local users can enroll a TOTP second factor from `/settings` — the page shows a QR code + text secret, collects a verification code to enable, and displays eight single-use recovery codes once. Recovery codes are stored SHA-256 hashed; TOTP secrets in plaintext (candidate for a future `user_totp_secrets` encryption scope). Login flow gets a `/login/mfa` step when the user has MFA enabled: password succeeds → session gets `mfa_pending_user_id` → second form accepts either the TOTP or a recovery code (consumed on match). SSO users see a note that MFA is managed by their IdP. Remaining: per-role enforcement (e.g. require MFA for admins), FIDO2 / WebAuthn, optional encryption of TOTP secrets at rest.

## Tier 4 — Hardening & Operational

### 17. General API Rate Limiting — **SHIPPED**
`RateLimitMiddleware` runs in front of every non-exempt request. Sliding-window-per-minute counters live in `rate_limit._api_window`, keyed by (caller, bucket): caller is the API token (most specific) → session id → client IP, and bucket is `read` (GET/HEAD/OPTIONS) vs `write` (everything else) — separate budgets so a write spammer can't drown out reads. Default budgets are 600 read / 120 write per minute, both configurable via `settings.RATE_LIMIT_READ_PER_MINUTE` / `_WRITE_PER_MINUTE` and switchable globally via `settings.RATE_LIMIT_ENABLED`. `/health`, `/static/*`, `/login`, `/logout` are exempt; the existing per-IP login debounce still covers `/login`. 429 responses include `Retry-After` plus `X-RateLimit-Limit` / `X-RateLimit-Remaining`. Remaining: shared backend (Redis) so multi-worker deployments don't undercount, per-route overrides for cheap vs expensive endpoints, and admin-config UI for the budgets.

### 18. Concurrent Session Limits — **SHIPPED**
`session_service.create_session` enforces `settings.SESSION_MAX_CONCURRENT` (default 5; 0 disables) by deleting the user's oldest sessions ordered by `last_active` *before* inserting the new one — the post-insert count never exceeds the cap. `/settings` lists the current user's active sessions with timestamps and user-agent string and offers a per-row "Revoke" button; revoking the current session redirects to `/login`. Foreign-session revocation is blocked by scoping the DELETE to `user_id = $current_user`. Remaining: per-role caps (e.g. admins capped lower than viewers), an admin-wide active-sessions audit page that spans all users, and notifications when a session is evicted by the cap.

### 19. Backup Encryption & Secrets Management
`security_features_and_requirements.md:44`: backup encryption at rest not implemented. Also: no OAuth 2.0 client-credentials flow or IP allowlisting for API access.

### 20. HTML Email Templates
Current alert emails are plain text. Once webhook is in, add a small HTML template layer with unsubscribe link and branded header.

---

## Known Stale / Cleanup
- `CLAUDE.md` has been refreshed but the rest of `configure_*.md` and `security_features_and_requirements.md` should be reviewed in the same pass when we make significant changes to those areas.
