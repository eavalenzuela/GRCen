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

### 6. Attachments on Relationships
Evidence/documents currently attach only to assets. GRC often needs "proof that this control satisfies that requirement" — add a nullable `relationship_id` to `attachments` (alongside the existing `asset_id`) and extend the UI.

### 7. Risk Management Polish
Register, heatmap, and filters are in. Still missing:
- Bulk actions: bulk-update treatment, reassign owner, set review dates across selected risks
- Trend indicators: count per severity band vs. last review cycle
- Risk → control effectiveness scoring beyond raw links

### 8. Cross-Cutting Tag Vocabulary
Asset tags exist as free-text strings per asset. A proper vocabulary would allow filtered views across types (e.g., "all SOC2-in-scope assets"). Add a `tags` table with a many-to-many to assets, an admin tag manager, and a tag-filter widget in search.

### 9. Saved / Bookmarked Searches
Advanced filtering is implemented, but nothing persists. Add a `saved_searches` table scoped per user + optional sharing.

### 10. Drag-and-Drop Graph Relationship Creation
The graph already supports click-to-link and bulk CSV/JSON import. Drag-and-drop creation directly in the D3 view would be a meaningful ergonomics upgrade.

### 11. PDF / Report Generation
Current exports are CSV/JSON only. Auditors often need a formatted compliance report for a given Audit or Requirement. Use WeasyPrint or ReportLab; template the scope-summary report first, then extend.

## Tier 3 — Enterprise / Production Gaps

### 12. Multi-Tenancy / Multi-Organization
Single-org only. Precludes SaaS / MSP deployment. Touches every model (add `organization_id`) and every query (tenant-scope all reads/writes). Largest change in the roadmap — plan carefully before starting.

### 13. Workflow / Approval States
Assets are created and edited in place — no draft → pending → approved lifecycle, no audit sign-offs. Add per-type workflow configuration, pending-state persistence, and approval events with audit trail.

### 14. Field-Level Redaction by Role
All four roles currently see all asset fields. Define sensitivity per field (or per custom field) and mask/blur for roles without view permission on that sensitivity level.

### 15. Data-Access Logging
`audit_log` captures *changes*. Compliance frameworks (HIPAA, several SOC2 CCs) also require *read* logs: who viewed which asset, who exported which dataset, when. Add a lightweight access-log table + middleware.

### 16. MFA for Local Auth
OIDC/SAML are shipped, but local-auth users have password-only. Add TOTP (initially) and optional FIDO2/WebAuthn; enforceable per-role.

## Tier 4 — Hardening & Operational

### 17. General API Rate Limiting
Login spray protection exists (~1 req / 2s). No general per-endpoint or per-token throttle. Unauthenticated bulk ops can hammer the DB.

### 18. Concurrent Session Limits
Noted in `security_features_and_requirements.md:89` as unimplemented. An admin can have unbounded parallel sessions.

### 19. Backup Encryption & Secrets Management
`security_features_and_requirements.md:44`: backup encryption at rest not implemented. Also: no OAuth 2.0 client-credentials flow or IP allowlisting for API access.

### 20. HTML Email Templates
Current alert emails are plain text. Once webhook is in, add a small HTML template layer with unsubscribe link and branded header.

---

## Known Stale / Cleanup
- `CLAUDE.md` has been refreshed but the rest of `configure_*.md` and `security_features_and_requirements.md` should be reviewed in the same pass when we make significant changes to those areas.
