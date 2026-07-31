# Changelog

All notable changes to GRCen are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/); dates are ISO-8601.

## [Unreleased]

### Added — External System Integrations (Tier 6)

GRCen went from a self-contained graph to the **org-graph layer of a wider
stack**: it now ingests from the authoritative systems around it and pushes its
aggregated data back out. The whole surface is mapped in
[INTEGRATIONS.md](INTEGRATIONS.md); the connectors below all build on one
reusable primitive and interlock by shared identifiers — an HRIS employee owns a
CMDB system, a uvt/CSPM finding resolves onto that system by resource id,
aggregates into a Risk on the heatmap, pushes a ticket, and the ticket's closure
flows back to the finding.

**Foundation**

- **External-upsert primitive** (`services/external_sync.py`) — the substrate
  every connector reuses: `upsert_external_asset` / `upsert_external_edge`,
  idempotent on `(organization_id, source, source_ref)`, plus source-scoped
  `prune_stale_*`. New `sync` permission; authenticated `POST /api/sync/<source>`
  HTTP ingress alongside the CLIs.
- **External-identity fields** on the asset types a connector needs to key and
  populate: Person `employee_id`/`location`/`cost_center`; System
  `hostname`/`ip_address`/`fqdn`/`external_id`/`cloud_resource_id`; Device
  `ip_address`/`external_id`; Finding `cve_id`/`cvss_score`/`cvss_vector`/
  `scanner`/`scan_id`/`affected_asset`/`first_seen`/`last_seen`, and (for
  ticketing) `external_ticket`/`_url`/`_status`.

**Ingress — external systems → the asset graph**

- **SCIM 2.0 provisioning** (`routers/scim.py`, `services/scim_service.py`) —
  `/scim/v2/Users` **and** `/scim/v2/Groups`, `sync`-scoped and org-bound. Each
  user maps to a login **and** an externally-owned Person; `active:false`/`DELETE`
  soft-deprovision (login disabled, Person archived), closing the login-time-JIT
  deprovisioning gap. **Groups → role sync:** a group's `displayName` maps to a
  role (configurable at `/admin/scim-settings`); a member's role becomes the
  highest-privilege role among their groups.
- **HRIS roster sync** (`services/hris_sync.py`, `grcen sync-hris` /
  `POST /api/sync/hris`) — People + Org Units with `manages`/`member_of`/
  `parent_of` edges; movers re-point edges; **leavers are archived and their
  login disabled**, with optional owned-asset reassignment.
- **CMDB / cloud-inventory sync** (`services/cmdb_sync.py`, `grcen sync-cmdb` /
  `POST /api/sync/cmdb`) — Systems + Devices with real hostnames/IPs/resource-ids,
  `depends_on` CI edges, and owner-by-name linkage; decommissioned/dropped-out
  CIs archived. Sets each asset's `external_id` = CI id so findings resolve onto
  it.
- **uvt vulnerability findings** (`services/vuln_sync.py`, `grcen sync-vulns` /
  `POST /api/sync/vulns`) — the sibling tracker's triaged findings → Finding
  assets linked `System --affected_by--> Finding` (resolved by host id);
  dropped-out findings archive as remediated.
- **Vulnerability-risk aggregation** (`services/risk_aggregation.py`,
  `grcen aggregate-risks` + a nightly job) — rolls open findings into Risk assets
  by affected System or by severity, mapping worst-severity to likelihood ×
  impact so they land on the register + 5×5 heatmap; edges prune and the Risk
  archives/reactivates as findings change.
- **CSPM misconfiguration sync** (`services/cspm_sync.py`, `grcen sync-cspm` /
  `POST /api/sync/cspm`) — cloud posture misconfigs (Wiz/Prisma/Security Hub/
  Steampipe) → Finding assets resolved onto their cloud System by resource id;
  feeds the risk register through the aggregation above with no extra wiring.
- **ITSM ticket-status sync** (`POST /api/sync/tickets`) — an external status
  feed maps ticket status back onto `finding_status`.
- **Generic upsert-by-source** (`services/generic_sync.py`,
  `POST /api/sync/upsert`) — the untyped connector, for sources with no
  purpose-built one: the caller declares its own `source` tag and a stable `ref`
  per record, and GRCen reconciles on `(organization_id, source, source_ref)`
  like every typed connector does. Re-post the same inventory and it updates in
  place — no client-side external-id → GRCen-id mapping table, and none of
  `/api/imports`' duplicate-on-re-run behaviour. Carries assets (incl.
  owner-by-name, tags, criticality, and flat CSV-style custom-field columns) and
  relationships by external ref. Lifecycle for dropped records is `on_missing`
  (`ignore` default / `archive` / `delete`), scoped per asset type; `?dry_run`
  previews. Source tags colliding with a built-in connector's namespace are
  rejected so a generic caller can't archive an IdP-synced directory.
  `POST /api/sync/upsert/csv` puts the same path behind a multipart envelope —
  attach an `assets` and/or `relationships` CSV and the columns map straight
  onto the contract (`tags` as one comma-separated cell, a `default_type` field
  for exports with no `type` column). Headers are normalized and de-BOM'd,
  blank lines skipped, and an unrecognized header is reported as a warning
  rather than silently dropped. Relationship ends address either an external
  `ref` or a `name` + `type` pair, so a synced asset can link to one the source
  doesn't own — a hand-authored Org Unit, an HRIS-synced Person, a content-pack
  Control. Ambiguous names resolve to the oldest match and warn; unmatched ones
  warn and skip the edge rather than inventing an asset.

**Egress — the asset graph → downstream tools**

- **Native Slack / Teams delivery** — a per-webhook `format` (`grcen`/`slack`/
  `teams`) with a formatter branch, so any event reaches Slack as a chat message
  (the signed GRCen envelope is unchanged). Event catalog centralized in
  `webhook_service.WEBHOOK_EVENTS` and gains `asset.updated`.
- **Auditor evidence package** (`services/evidence_package.py`,
  `GET /frameworks/{id}/evidence-package.zip`) — one ZIP per framework: SoA,
  control library, the **per-run** control test ledger, the **audit trail**, and
  the actual evidence files, with a manifest; optional `?since=&until=` period
  scoping.
- **External ticketing push** (`services/ticketing_service.py`,
  `POST /api/findings/{id}/ticket`) — create a Jira/ServiceNow/generic ticket
  from a finding and stamp the ticket ref back onto it. Config +
  encrypted API token at `/admin/itsm-settings`.
- **Calendar enrichments** — the iCal review feed gained audit dates, per-type
  event phrasing, **VALARM reminders** (`?alarm=N`), and a per-user
  **`?scope=mine`** filter.

**Admin & configuration**

- New admin pages: `/admin/itsm-settings` (ticketing) and `/admin/scim-settings`
  (SCIM default role + group→role map), with nav links.
- New encryption scope `itsm_secrets` (added to the minimal/GDPR/full profiles).
- New nightly scheduler jobs: compliance/risk snapshots already existed; added
  **risk aggregation** (00:20 UTC).

### Added — Tenant isolation enforced by Postgres

- **Row-level security** (`src/grcen/tenancy.py`) on all 30 tenant tables. The
  ~212 hand-written `organization_id` filters now have a database backstop: a
  query that forgets one returns nothing instead of another tenant's rows — the
  `services/org_views.py` class of bug becomes a visible failure rather than a
  silent leak. Tenant context rides asyncpg's per-acquire `setup` hook, so none
  of the ~459 database call sites changed.
- Enforced queries run as an auto-provisioned **`grcen_app`** role
  (NOSUPERUSER/NOBYPASSRLS). This is load-bearing rather than defensive: the
  connecting role is normally a superuser, and superusers ignore RLS entirely
  even with `FORCE ROW LEVEL SECURITY`. Policies applied without it are inert
  while looking correct in `pg_policy` — `tenancy.assert_enforcing` now fails
  startup rather than allowing that state.
- Cross-tenant paths (migrations, scheduler jobs, CLI, and the pre-auth
  capability-token lookups for invites, OAuth, the vendor portal and the
  calendar feed) run in `tenancy.system_context()`, which declines to drop
  privileges — a privilege boundary, not a flag the policy trusts. The vendor
  portal narrows to the campaign's org straight after the token lookup.
- The identity plane (`users`, `user_organizations`, `api_tokens`) is
  deliberately outside RLS: it's read before a tenant is known, and multi-org
  membership means `organization_id` isn't its access key.
- `RLS_ENABLED` (default on) applies or drops the policies in `init_schema`, so
  the change is reversible. Backups are unaffected — `pg_dump --no-acl` already.

### Notes

- The test suite grew from 820 to **931** over this work; every connector ships
  with its own tests, and cross-connector interlocks (uvt↔CMDB, CSPM→risk
  aggregation) are covered explicitly.
- Deferred follow-ups are tracked in `feature_roadmap.md` and `INTEGRATIONS.md`
  Part C (MDM/EDR → Device, inbound TPRM, data-catalog, SIEM syslog/CEF,
  push-to-calendar, scheduled evidence delivery, per-org connector config).
