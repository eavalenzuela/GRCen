# Changelog

All notable changes to GRCen are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/); dates are ISO-8601.

## [Unreleased]

### Fixed — UI audit, phase 0: interactivity the CSP had silently broken

A Playwright-driven audit of the seeded app (457 verified findings, see
`ui_audit_findings.md`) found one root cause behind every severity-4 item:
the nonce-only Content-Security-Policy blocks inline `on*=` handlers and
`hx-on::`, so delete confirmations never fired and a dozen buttons were dead.

- **Deletes ran with no confirmation** (assets, users, tags, tokens, OAuth
  clients, questionnaires, webhooks, sessions, orgs, MFA, evidence, bulk
  apply) and **dead controls** (Advanced filters, Save this search, select-all,
  the Owner picker on the asset form, graph Link Mode, token Copy, encryption
  profile radios, audit-settings toggle, tag chips). All inline handlers moved
  to `static/js/app.js`, driven by `data-*` attributes; a test now forbids
  inline handlers in templates.
- **htmx actions that return 204 left stale rows** (Remove relationship,
  Remove attachment, Delete alert, Mark read). 204 now swaps; Mark read returns
  the re-rendered row and refreshes the nav badge via `HX-Trigger`.
- **"Add URL attachment" always failed with a silent 422** (form-encoded post
  to a JSON route) — the form now posts JSON through a small `json-enc`
  extension; upload/URL forms reload on success and surface errors.
- **The export page failed by default** (`status=` → 422 JSON); comma lists
  with spaces or mixed case 500'd. Empty values mean "all"; bad values are 400.
- **Alerts could not be created or edited from the UI** — `/alerts` has a form
  (asset picker, one-time or cron schedule, validation that keeps your input)
  and an edit page; asset pages link to it. A recurring alert is seeded with
  its first cron occurrence (an alert with no `next_fire_at` never fires).
- **Links were minted with `APP_BASE_URL`'s default `http://localhost:8000`**
  (invites, calendar feed, vendor portal, email/webhook links): they now use
  the request origin unless `APP_BASE_URL` is configured.
- One-shot session flash messages (`flash()` + `#flash-region`) replace the
  `?flash=` query parameter on the pages touched here.

### Security — found by the same audit

- The attachment API accepted `kind=file` with an arbitrary `url_or_path`,
  which the download route then served: any authenticated user could read
  server files. File kinds are upload-only and downloads are confined to
  `UPLOAD_DIR`.
- Uploads trusted the browser's `Content-Type`; the first bytes are now checked
  against the declared type (PDF/PNG/JPEG/GIF/OOXML magic, UTF-8 for text
  types). Filenames over 255 characters crashed with a 500 and left an orphan
  file; they are truncated and the file is removed if the row insert fails.
- A client-credentials OAuth access token could mint a never-expiring static
  API token; `POST /api/tokens/` now refuses OAuth-issued bearers.
- A bare `POST /settings/mfa/begin` on an enrolled account reset the enrolment
  and silently turned enforced MFA off; it now refuses. A TOTP code is accepted
  at most once (`user_totp.last_used_step`). `/logout` and
  `/settings/mfa/cancel` are POST + CSRF instead of state-changing GETs.
- Viewers could read the vendor-portal capability URL on a campaign page.
- Review follow-ups on the above: the TOTP replay guard records the matched
  code's own time step (the wall-clock step let a captured code replay for up
  to 60s); the OAuth rule is enforced through a structural `api_tokens.oauth_issued`
  flag on both the REST and HTML token routes; uploads with no declared type
  are refused instead of skipping every check; downloads are confined to the
  owning object's own upload folder; the relationship-evidence form also
  refuses `kind=file`; `Content-Disposition` is RFC 6266-encoded; and
  `public_base_url` honours `X-Forwarded-Proto`/`-Host` (gunicorn now runs
  with `--forwarded-allow-ips`, default `*`, env `FORWARDED_ALLOW_IPS`).
  Deliberately not recorded as "used": the enrolment code, so "confirm, then
  sign in with the code you have" keeps working.

Not changed on purpose: bulk import (`/api/imports/*/bulk`) still bypasses
approval gating — an earlier product decision, now flagged in the register.

### Fixed — Adversarial review pass 2: concurrency & transaction boundaries

A second review round, scoped to what happens when things run at the same
time. The premise it worked from: production is gunicorn with 2+ worker
processes, each with its own event loop, pool and module state. 8 defects
fixed, 16 regression tests added (1032 → 1048).

- **Creating a user could take over an existing one.** `create_user` upserted
  on username — globally unique — setting password and role but *not*
  organization_id. So "create a user" named after someone in another tenant
  reset their credentials, left them in their own org, and handed the caller a
  working login while locking the victim out. Needs no race; the review only
  found the racy variant. Verified end to end before fixing.
- **Every scheduled job runs in every worker**, and the two that are work
  queues didn't claim their work: the alert ticker and the digest flush each
  delivered once per worker — duplicate notifications, emails and webhook
  POSTs, none recallable. Both now claim with `FOR UPDATE SKIP LOCKED` before
  any side effect. The other four jobs upsert on a natural key and are already
  idempotent, so no leader election was needed.
- **One-shot alerts never stopped firing.** Nothing advanced `next_fire_at` for
  `schedule_type='once'`, so they stayed due and re-fired every minute, forever,
  in every worker.
- **RLS had a default-deny window on every boot.** `apply_policies` dropped and
  recreated each policy in separate autocommit statements, so tables sat
  RLS-enabled with no policy in between — while a sibling worker served live
  traffic. Reads returned nothing; writes failed.
- **Config caches never expired.** After an admin enabled encryption, workers
  that didn't serve that request kept writing PII, TOTP secrets and session IPs
  in plaintext into columns their sibling was writing ciphertext to.
- **Check-then-act and read-modify-write:** duplicate Jira/ServiceNow tickets
  from concurrent pushes (and a 15-second window where any concurrent metadata
  edit was reverted); bulk risk updates reverting keys they never touched; MFA
  recovery codes resurrecting each other so a consumed code worked again;
  `withdraw`/`reject` 500ing instead of 400 on an already-decided change.

### Fixed — Adversarial review pass 1

A multi-agent review across four areas (auth/web security, tenant isolation,
integrations, domain correctness), with every finding independently verified
before it was acted on. 25 defects fixed and 46 regression tests added (986 →
1032), with the load-bearing ones checked by reverting the fix and confirming
the new test goes red.

**Availability**

- **Login was broken in production.** The RLS rollout left the auth routes
  without a tenant context, so the closing audit write was rejected by the
  policy and every local and SSO login 500ed before the session cookie was
  emitted. The test suite couldn't see it: `conftest` enables a process-wide
  bypass and only `get_current_user` cleared it, which the auth routes never
  call. `TenantContextMiddleware` now resets every request to
  enforced-and-tenant-less, so the suite exercises what production runs.
- **OIDC login authenticated nobody** — the callback parked a bare
  `session['user_id']` and created no server-side session record. Its success
  path had no test at all.
- **`RLS_ENABLED=false` didn't boot** — the acquire hook dropped to a role
  that only exists when RLS is on.

**Tenant isolation.** `users` and `api_tokens` sit outside RLS by design, so
nothing backstops a missing filter there: an admin of any org could reset the
password of an admin of any other and take over that tenant, `/api/tokens/all`
spanned every org, admin-created users landed in the `default` org, SSO
first-login Person assets landed in the wrong tenant, and alert emails were
sent to every other tenant's admins.

**Sync connectors.** An empty HRIS roster was read as "everyone left" and
archived every Person plus their logins; an empty vuln/CSPM export closed the
entire findings backlog. Both now need explicit `allow_empty`. A devices-only
CMDB sync deleted every `depends_on` edge between Systems. SCIM PATCH
`replace` on members was add-only, so an offboarded admin kept admin.

**Authorization.** PII redaction ignored API-token scope, and the REST and PDF
paths ignored admin-configured sensitivity promotions. A read-only token could
mint a full-role token. `/login/mfa` had no throttle of any kind and its
failures fed no lockout. The rate-limit bucket was keyed by the raw, unvalidated
`Authorization` header — a free budget per request, plus unbounded memory growth.

**Wrong numbers.** Every diligently-reviewed risk read as permanently overdue
(overdue fell back to "Last Reviewed"); closed and risk-accepted findings still
counted as outstanding; `/my-work` disagreed with `findings_service` about what
"done" means in both directions; re-mapping a questionnaire answer kept the
previous answer's text under the new source; concurrent approvers could apply
the same pending change twice.

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
