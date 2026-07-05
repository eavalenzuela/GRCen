# GRCen Integrations

How GRCen connects to the systems around it — what feeds the graph, and where the
aggregated data plugs back out. This is a **living map**: it marks what exists today, what is
partial, and what is greenfield, and it cross-references the code seams by path so it stays
honest as the tool grows.

A GRC graph is only as good as the authoritative systems feeding it and the workflows it
drives. This doc answers two questions:

1. **Ingress** — which external systems should own which GRCen asset types, and by what
   mechanism (HR tools → People, CMDBs → Systems, vulnerability scanners → Findings, …).
2. **Egress** — where GRCen operationalizes what it aggregates (Slack, calendar, auditor
   exports, ticketing, SIEM, …).

## Where GRCen sits in the stack

GRCen is the **org-graph layer** of a deliberate three-tool stack:

- **[autocomply](../autocomply)** — system-of-record for control/requirement *definitions*,
  cross-framework crosswalks, and evidence/CSPM collectors (anchored on NIST SP 800-53). It
  **already feeds GRCen** via the catalog sync (see the export contract at
  [`../autocomply/GRCEN_CATALOG_EXPORT.md`](../autocomply/GRCEN_CATALOG_EXPORT.md)).
- **GRCen** — the *relationships* layer: owners, the systems a control protects, vendors,
  risks, audits, findings. It ingests a projection of the catalog and wraps it in the org
  graph.
- **uvt** (Universal Vulnerability Tracker) — the vuln system-of-record. In this map it is the
  natural **producer of vulnerability Findings** — the designated home for "Qualys-likes"
  (see the [directional note](#a-note-on-qualys-likes) below).

Framework/requirement/control content that isn't coming from autocomply is seeded by bundled
**[content packs](CONTENT_PACKS.md)** so a fresh org isn't stranded on an empty register.

---

## The reusable integration seams

Every integration below hangs off a pattern GRCen already has. Naming them once:

- **Ingress "connector" pattern — `source` / `source_ref` provenance + idempotent upsert.**
  `assets` and `relationships` each carry `source VARCHAR(64)` + `source_ref VARCHAR(255)`
  with a partial unique index on `(organization_id, source, source_ref) WHERE source IS NOT
  NULL` (`src/grcen/database.py`). `source IS NULL` = human-authored; non-null = externally
  owned. Today the **only** writer is `services/catalog_sync.py` (`sync_catalog`, with
  `_ASSET_UPSERT` / `_EDGE_UPSERT` and a source-scoped stale prune), driven by
  `grcen sync-catalog` and reused by content packs under a `grcen-pack:<id>` source tag. **A
  new source reuses this substrate**: a per-source `source` tag, a stable `source_ref` = the
  source system's own primary key, upsert-in-place, prune-what's-gone.

  > **Caveat.** The REST and bulk-import paths are **append-only and never set `source`**
  > (`services/asset.py:create_asset`, `services/import_service.py`), so a connector cannot
  > ride the public API as-is — it needs a generic external-upsert primitive (roadmap **I0**).

- **Per-type fields = JSON, zero-migration.** Every asset is one row in `assets`; type-specific
  fields live in the `metadata` JSONB blob, defined by `FieldDef`s in
  `src/grcen/custom_fields.py`. Adding a field an external system needs (`employee_id`,
  `ip_address`, `cve_id`, …) is a one-line `FieldDef`, no schema change.

- **Egress event bus.** `services/webhook_service.py` (`dispatch_background`, HMAC-SHA256,
  retry with backoff) fans events to operator-configured webhooks. **The event catalog today is
  exactly four**: `alert.fired`, `asset.created`, `asset.deleted`, `ping` — emitted as ad-hoc
  string literals at the write sites. There is **no `asset.updated`** and no finding/risk/
  control events yet.

- **Notification fan-out.** `services/alert_service.py:fire_alert()` fans to three channels via
  `_deliver_email` / `_deliver_webhooks` plus an in-app insert. A new channel is a new
  `_deliver_<x>()`.

- **Calendar.** `services/calendar_service.py` + `routers/calendar.py` serve a per-user
  **subscribe-only** iCal feed (`/calendar/{token}/grcen.ics`) from
  `services/review_service.py:REVIEW_DATE_FIELDS` — already carrying finding `due_date`, risk
  `review_date`, control `next_test_due`, vendor `next_assessment_due`, and framework
  `certification_expiry`.

- **Exports.** WeasyPrint PDFs (`services/pdf_service.py`) plus inline `csv.writer` routes, each
  recorded via `access_log_service`. Executive board pack in `services/board_service.py`.

- **Scheduler.** `APScheduler` in `src/grcen/main.py` (alert tick, nightly snapshots, hourly
  digest flush) — the hook for scheduled syncs and scheduled report delivery.

---

## Part A — Ingress: authoritative systems → GRCen asset types

Legend: **✅ exists** · **◐ partial** · **⬜ greenfield**

| External system (examples) | Feeds asset type(s) | `source` tag | `source_ref` (external PK) | Fields to add first (`custom_fields.py`) | Status |
|---|---|---|---|---|---|
| **Control/compliance SoR** — autocomply (also Vanta/Drata-shaped) | Framework, Requirement, Control, crosswalks | `autocomply` | `framework:` / `requirement:` / `control:<ref>` | — (contract-defined) | ✅ `catalog_sync` |
| **Content packs** (cold-start baselines) | Framework, Requirement, Control | `grcen-pack:<id>` | same | — | ✅ `content_packs` |
| **IdP / Directory** — Entra ID, Okta, Google Workspace | User + Person, group → role | `scim` (Person) / *(users table)* | `oidc_sub` / `saml_sub` / SCIM `externalId` | Groups → role sync | ◐ SSO JIT + **SCIM Users** |
| **HRIS** — Workday, BambooHR, HiBob, Rippling, ADP | Person, Organizational Unit | `hris:<vendor>` | `employee_id` / `worker_id` | — (identity fields + edges + leaver handling shipped) | ✅ `hris_sync` (I2) |
| **CMDB / IT asset inventory** — ServiceNow CMDB, Device42, Snipe-IT | System, Device (+ `depends_on`, owner) | `cmdb:<vendor>` | `system:`/`device:<ci_id>` | — (fields shipped in I0b) | ✅ `cmdb_sync` (I3) |
| **Cloud inventory** — AWS Config, Azure Resource Graph, GCP Asset Inventory | System / Device (resources) | `cloud:<provider>` | ARN / resource id | — (uses the same `cmdb_sync` contract) | ✅ via `cmdb_sync` (I3); dedicated pullers TBD |
| **Vuln scanners / "Qualys-likes"** — Qualys, Tenable/Nessus, Rapid7, **uvt** | Finding (`affected_by` ← System) | `uvt` or `qualys` / `tenable` | `finding:<finding_id>` | — (fields shipped in I0b) | ◐ `vuln_sync` (I4); Risk aggregation deferred |
| **CSPM** — Wiz, Prisma, Security Hub, Steampipe | Finding (misconfig) → System; Control evidence | `cspm:<vendor>` | check id + resource | Finding fields above | ⬜ (autocomply's simulated AWS collector feeds control evidence) |
| **MDM / EDR** — Intune, Jamf, Kandji, CrowdStrike, SentinelOne | Device (+ device-compliance Finding) | `mdm:<vendor>` / `edr:<vendor>` | device id | Device `ip_address`, `external_id`, `compliance_state` | ⬜ |
| **ITSM / ticketing** — Jira, ServiceNow, Linear | Finding **status sync (inbound)** | — | ticket key | — (`external_ticket*` shipped) | ✅ `ticketing_service` (E3) via `POST /api/sync/tickets` |
| **Procurement / TPRM** — OneTrust, Whistic, SecurityScorecard | Vendor (+ risk rating) | `tprm:<vendor>` | vendor id | Vendor `risk_rating`, `external_id` | ⬜ (outbound questionnaire campaigns exist ✅) |
| **Data catalog / DSPM** — BigID, Collibra, Macie | Data Category (+ data-flow edges) | `dspm:<vendor>` | dataset id | — | ⬜ |
| **Evidence stores** — autocomply evidence, GDrive/Confluence/S3 | Attachments on Control / Requirement | — | — | freshness fields exist (`collected_at`, `valid_until`) | ◐ lands as attachments |

### A note on "Qualys-likes"

GRCen should consume **aggregated, triaged** findings from **uvt** — which already normalizes
Qualys / Nessus / Trivy output and de-duplicates it — rather than wiring a connector per
scanner. This mirrors how GRCen consumes controls from autocomply instead of re-implementing a
control catalog: uvt becomes the vuln system-of-record, and GRCen adds the ownership and risk
context by linking each Finding `affects` a System and `mitigated_by` a Control. Direct
per-scanner connectors remain an option for deployments not running uvt.

### What's confirmed absent today

The external connectors that exist today are autocomply's catalog sync, **SCIM Users** (I1),
**HRIS roster sync** (I2), **CMDB / cloud-inventory sync** (I3), and **vuln findings from uvt**
(I4) — the last four built on the reusable `external_sync` primitive (I0). People are
provisioned and **deprovisioned** from both an IdP (SCIM) and an HRIS roster (leaver → Person
archived + login disabled); Systems/Devices come from the CMDB with `depends_on` + ownership;
and scanner findings link onto those Systems by CI id (resolved on remediation). Findings push
to an ITSM (Jira/ServiceNow) and sync ticket status back (E3). CSPM, MDM/EDR, inbound-TPRM, and
SIEM connectors remain greenfield.

---

## Part B — Egress: where GRCen operationalizes the data

| Channel | What GRCen pushes | Seam / where it plugs in | Status |
|---|---|---|---|
| **In-app notifications** | alerts, watched-asset changes, session evictions | `alert_service` + `watcher_service` | ✅ |
| **Email (SMTP)** | alert emails, hourly digests, per-org branding, `List-Unsubscribe` | `email_service` + `digest_service` | ✅ (watcher-email + scheduled-report email are gaps) |
| **Calendar (subscribe)** | review / finding-due / risk-review / control-test / vendor / framework-cert dates as iCal VEVENTs | `calendar_service` + `review_service.REVIEW_DATE_FIELDS` | ✅ (audit dates = 1 line; per-user filter + VALARM = gaps) |
| **Calendar (push)** | same, pushed into Google / Outlook | Google Calendar / MS Graph API | ⬜ net-new |
| **Slack / MS Teams (native)** | any event, as a chat message | per-webhook `format=slack\|teams` → formatter branch in `send_to_webhook` | ✅ (E1) |
| **Webhooks / SIEM / SOAR** | asset + alert events | `webhook_service` (HMAC, retry) | ◐ 5 events (`asset.updated` added, E1); catalog centralized; still no finding/risk events or syslog/CEF |
| **External ticketing push** | create Jira / ServiceNow / generic ticket from a finding; store ticket ref | `ticketing_service.push_finding` → `POST /api/findings/{id}/ticket` | ✅ (E3) |
| **Scheduled report delivery** | email board pack / auditor bundle on a cadence | APScheduler job (mirror `_flush_email_digests`) | ⬜ (all reports are pull-only today) |
| **PDF reports** | framework, gap, asset dossier, audit dossier | `pdf_service` | ✅ |
| **CSV exports** | SoA, control-library summary, gap, access-log, questionnaire, asset export | inline `csv.writer` routes | ✅ |
| **Executive board pack** | risk / coverage / incidents / findings exec summary | `board_service` | ✅ |
| **Auditor evidence package** | ZIP of SoA + control library + per-run test ledger + evidence attachments + audit-trail for a framework/period | `services/evidence_package.py` → `GET /frameworks/{id}/evidence-package.zip` | ✅ (E2); scheduled delivery TBD |
| **Vendor questionnaire campaigns** | push a questionnaire to a third party via a login-less portal token | `vendor_campaign_service` + `portal_router` | ✅ (auto-email link + reminders = gap) |
| **My Work** | per-user aggregation of approvals / reviews / findings / watches | `routers/my_work.py` | ✅ |

### The named probes, directly

- **Slack notifications** — greenfield *native*. The webhook seam exists but sends the GRCen
  JSON envelope, not Slack Block Kit. Cheapest path: a `format` branch in `send_to_webhook`.
- **Calendar events** — largely **already shipped** as a subscribe-only iCal feed that already
  carries finding / risk / control / vendor / framework dates. Gaps are per-user filtering,
  alarms, and true push-to-calendar.
- **Auditor exports** — SoA, control-library, gap reports, per-audit dossier PDF, board pack,
  and the read-access log **all exist**. The real gap is a **packaged evidence bundle** (ZIP)
  plus **audit-trail** and **per-run control-test-ledger** exports, and **scheduled delivery**.

---

## Part C — Roadmap: Tier 6, External System Integrations

**Foundation (unlocks all ingress — do first):**

- **I0. Generic external-upsert primitive.** ✅ **Shipped** — `services/external_sync.py`:
  `upsert_external_asset` / `upsert_external_edge` (idempotent on `(organization_id, source,
  source_ref)`; unlike the narrower `catalog_sync` projection, the source owns `status` too, so
  a connector can archive on deprovision) + `prune_stale_assets` / `prune_stale_edges`. New
  `sync` permission (`permissions.py`, held by Admin). Tests: `tests/test_external_sync.py`. The
  authenticated HTTP ingress surface now exists at `POST /api/sync/<source>` (`routers/sync.py`;
  `/hris` is the first, added with I2), alongside the CLI syncs.
- **I0b. External-identity fields.** ✅ **Shipped** — added to `custom_fields.py`: Person
  `employee_id` / `location` / `cost_center`; System `hostname` / `ip_address` / `fqdn` /
  `external_id` / `cloud_resource_id`; Device `ip_address` / `external_id`; Finding `cve_id` /
  `cvss_score` / `cvss_vector` / `scanner` / `scan_id` / `affected_asset` / `first_seen` /
  `last_seen`.

**Highest-value ingress:**

- **I1. IdP SCIM 2.0 — Users.** ◐ **Shipped (Users)** — `routers/scim.py` +
  `services/scim_service.py`: SCIM 2.0 `/scim/v2/Users` (create / get / list+filter / PUT /
  PATCH / DELETE) + `ServiceProviderConfig`, authenticated by a `sync`-scoped API token
  (org-bound). Each SCIM user maps to a GRCen login **and** an externally-owned Person
  (`source='scim'`, `source_ref` = `externalId`||`userName`, via I0). `active:false` / DELETE
  soft-deprovision — login disabled, Person archived — closing the deprovisioning gap that
  login-time OIDC/SAML JIT leaves open. Tests: `tests/test_scim.py`. *Remaining:* Groups → role
  sync, per-org SCIM config UI.
- **I2. HRIS sync.** ✅ **Shipped** — `services/hris_sync.py` (`sync_roster`): the bulk
  counterpart to SCIM. Projects a roster → People (+ Org Units), `manages` / `member_of` /
  `parent_of` edges, all idempotent via I0. Movers re-point edges (stale ones pruned); **leavers
  are archived and their linked login disabled** (`deactivate_leavers`, default on), with
  optional owned-asset reassignment. Delivered as CLI `grcen sync-hris <file>` **and**
  `POST /api/sync/hris` (`routers/sync.py`, `sync`-scoped token — this also lands I0's remaining
  generic HTTP ingress surface). Roster contract in the `hris_sync` module docstring. Tests:
  `tests/test_hris_sync.py`.
- **I3. CMDB / cloud-inventory sync.** ✅ **Shipped** — `services/cmdb_sync.py`
  (`sync_inventory`): projects a CMDB/cloud inventory → System + Device assets with real
  hostnames / IPs / resource-ids, `depends_on` CI edges, and owner-by-name linkage; sets each
  asset's `external_id` = CI id so uvt findings (I4) resolve straight onto it. Decommissioned or
  dropped-out CIs are archived (`archive_missing`, default on; a devices-only sync never touches
  Systems). CLI `grcen sync-cmdb <file>` and `POST /api/sync/cmdb`. Contract in the `cmdb_sync`
  module docstring. Tests: `tests/test_cmdb_sync.py` (incl. the cross-connector uvt↔CMDB join).
- **I4. Vuln findings via uvt.** ◐ **Shipped (Findings)** — `services/vuln_sync.py`
  (`sync_findings`): consumes uvt's triaged findings export → Finding assets (via I0), each
  linked `System --affected_by--> Finding` by resolving the affected host (external_id →
  hostname → fqdn → ip_address → name; GRCen never invents Systems, an unresolved target is a
  warning). Findings that drop out of the export are archived as `remediated`
  (`resolve_missing`, default on). CLI `grcen sync-vulns <file>` and `POST /api/sync/vulns`.
  Contract in the `vuln_sync` module docstring. Tests: `tests/test_vuln_sync.py`. *Remaining:*
  aggregating findings into rolled-up Risk assets (the grouping policy is a design choice).

**Highest-value egress:**

- **E1. Slack / Teams native.** ✅ **Shipped** — a per-webhook `format` (`grcen` | `slack` |
  `teams`) with a formatter branch in `webhook_service.send_to_webhook`: `slack` posts a mrkdwn
  incoming-webhook message, `teams` a MessageCard, both unsigned (the secret URL is the
  credential); `grcen` keeps the signed JSON envelope byte-for-byte. Any event routes to Slack
  in Slack format. The event catalog is centralized in `webhook_service.WEBHOOK_EVENTS` (single
  source for the admin UI + formatter) and gains **`asset.updated`** (emitted from the REST +
  page update handlers). Admin format selector at `/admin/webhooks`. Tests: `tests/test_webhooks.py`.
  *Remaining:* domain events (`finding.*` / `risk.appetite_breached` / `review.due`) need
  lifecycle emit points — a follow-up.
- **E2. Auditor evidence package.** ✅ **Shipped** — `services/evidence_package.py`
  (`build_package`): one ZIP per framework with `manifest.json` + `README.txt`, the SoA, the
  framework-scoped control library, the **per-run control test ledger** (new — only a summary
  existed), the **audit trail** for the framework's assets (new — no export existed), and the
  actual **evidence files** (encrypted ones decrypted in) with an `evidence/_index.csv`. Optional
  `?since=&until=` period scoping. `GET /frameworks/{id}/evidence-package.zip` (EXPORT-gated,
  access-logged) + a button on the framework detail page. Tests: `tests/test_evidence_package.py`.
  *Remaining:* scheduled delivery (email the package on a cadence via an APScheduler job).
- **E3. External ticketing.** ✅ **Shipped** — `services/ticketing_service.py`: **outbound**
  `push_finding` creates a Jira / ServiceNow / generic ticket from a finding and stamps
  `external_ticket` + URL onto it (`POST /api/findings/{id}/ticket`, EDIT-gated); **inbound**
  `sync_ticket_statuses` maps an ITSM status feed onto `finding_status`
  (`POST /api/sync/tickets`, `sync`-scoped — closing the loop without a bespoke webhook
  receiver). Provider config + encrypted API token in `itsm_settings` (`itsm_secrets` scope),
  admin page at `/admin/itsm-settings`. Finding fields `external_ticket*` added. Tests:
  `tests/test_ticketing.py`. *Remaining:* auto-push on finding raise (per-severity rule);
  richer status-map config.
- **E4. Calendar enrichments** (audit dates, per-user "my assets" filter, VALARM reminders) +
  optional push-to-calendar.

**Second wave:** MDM/EDR → Device; CSPM misconfig → Finding; inbound TPRM vendor-risk; data
catalog → Data Category; SIEM syslog/CEF forwarder; auto-email + reminders for vendor
campaigns.

---

*Related docs: [CONTENT_PACKS.md](CONTENT_PACKS.md) · [feature_roadmap.md](feature_roadmap.md)
· autocomply catalog contract at `../autocomply/GRCEN_CATALOG_EXPORT.md`.*
