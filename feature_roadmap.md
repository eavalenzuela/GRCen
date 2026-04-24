# GRCen Feature Roadmap

Tracks known gaps identified during the project review. Items marked **SHIPPED** are complete; others are sorted roughly by priority within each tier.

## Tier 1 — High Value, Contained Scope

### 1. Email Notification Delivery — **SHIPPED**
Admin configures SMTP at `/admin/smtp-settings`; users opt in at `/settings`; firing alerts send to the asset owner (or admin fallback) and log every attempt to `notification_deliveries`.

### 2. Webhook Notification Delivery
Alerts can't post to Slack, PagerDuty, Teams, or custom endpoints. Add a `webhook_config` table (url, secret, event filters), HMAC-sign outgoing payloads, and extend `fire_alert` alongside the existing email path. Also needed: digest/batch mode so bulk fires don't spam.

### 3. REST API for Assets & Relationships
Token management exists at `/api/tokens`, but there are no authenticated REST endpoints for the core graph. Integrations (ticketing, SIEM, CI/CD) currently have to scrape HTML. Models and services already exist — mostly needs routers + OpenAPI annotations. Endpoints: `GET/POST/PATCH/DELETE /api/assets`, `/api/relationships`, `/api/assets/{id}/graph`, plus bulk variants.

### 4. Compliance Framework Dashboards
Sample data (`sample_data/relationships.csv:367-387`) already models SOC2 / PCI DSS / GDPR / ISO27001 → requirements → audits. The UI is missing. Add `/frameworks` index and `/frameworks/{id}` detail pages showing: requirements, coverage (requirements with satisfying controls vs. unsatisfied), in-scope assets, audits, and gap highlights.

### 5. Relationship Bulk Import
Asset CSV/JSON import is complete. Relationship bulk import isn't surfaced through the import router. Extend `services/import_service.py` with a relationships CSV flow (source_id, target_id, type, description) and add a dry-run / preview mode to both asset and relationship imports.

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
