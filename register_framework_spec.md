# Register Framework — Design Spec (v2)

*Status: proposed · grounded in `src/grcen/` on `main` · 2026-06-23*

> **v2 changelog (after adversarial review).** Four foundation fixes:
> 1. **Redaction is now a prerequisite, not a caveat.** The current `/assets` list and
>    CSV/JSON export do **not** honor per-org / per-asset sensitivity *overrides* — a
>    latent leak the framework must close before adding curated columns / `columns=all`
>    / export-from-view (§7.3).
> 2. **Canonical URL unified.** `/assets?type=X` is the one canonical register surface
>    (access-log, saved-search, PDF, audit already key on it); `/registers/{slug}` is a
>    thin redirect alias and `/registers` is the index. This collapses the risky
>    shared-template refactor (§5).
> 3. **Incident/Audit lifecycle corrected.** Neither ships a meaningful status over the
>    shared `AssetStatus`; both get computed/real signals instead (§4.2, §8).
> 4. **Smaller first cut + v1 metrics.** Re-phased so value lands sooner; type-specific
>    metric cards for the flagship registers are v1, not "later" (§8, §12).

## 1. Problem & goal

GRCen has **one** purpose-built, bulk-action register (the Risk Register at
`/risk-management`) plus thinner dedicated surfaces (`/reviews`, `/controls`, `/answers`,
`/questionnaires`, `/frameworks`). Every other organizational type — Vendor, Incident,
Policy, Audit, System, Device, Process, Person, Data Category, Requirement, Product, IP,
Org Unit — is reachable only through the **generic `/assets` list keyed by `?type=`**
(`asset_pages.py:39`). That list is a competent *typed worklist* but not *register-grade*:
no nav landing page, no metrics header, no bulk lifecycle actions, no curated columns, no
export-that-matches-the-view.

**Goal:** a **config-driven register framework** that makes each asset type a named,
navigable, register-grade surface — **without** hand-building N pages. The Risk Register
stays the reference for "register-grade"; this generalizes its pattern to the types that
lack a bespoke surface, by *enhancing the existing `/assets` handler* + a config registry
+ a `/registers` index, not by forking a parallel surface.

**Non-goals (separate roadmap items):** new entity types (Findings, Exceptions, RoPA);
true per-type *workflow* status machines; org-admin-defined custom registers (explicitly
dropped from v1 — see §12).

## 2. Design principles

1. **Reuse-first.** Reuse `asset_svc.list_assets` (multi-type, all filters, `meta.<field>`
   sort, offset pagination) and the existing `assets/list.html` table. The framework is
   *configuration + a metrics header + a curated-column resolver + a bulk partial*.
2. **One canonical surface.** `/assets?type=X` renders every register; `/registers/{slug}`
   redirects to it; `/registers` is the index. No second data path → logs, saved searches,
   PDF, and audit never fork (review finding B7).
3. **Config, not classes.** `REGISTERS: dict[AssetType, RegisterDef]` is the single source
   of truth for label, slug, columns, default sort, lifecycle signal, bulk fields, metrics,
   nav placement.
4. **Coexist, don't replace.** Types with a richer bespoke page (risk, framework, control)
   keep it; their `RegisterDef` carries `canonical_path`, so the index links to the bespoke
   page and `/registers/{slug}` redirects there.
5. **Security is a precondition.** Override-aware redaction (§7.3) ships *before* any new
   column-exposure surface.

## 3. What already exists (building blocks, verified)

| Building block | Where | Reused for |
|---|---|---|
| Generic list handler (filters, sort, pagination, saved searches) | `asset_pages.py:39-142` | Enhanced in place to consult `RegisterDef` when `?type=` set |
| `list_assets` — `asset_types`, `meta.<field>` sort (regex-guarded, `:209-225`), all filters, org-scoped | `services/asset.py:90` | Register queries unchanged |
| Per-type custom fields | `custom_fields.py:20-497` (`CUSTOM_FIELDS`, `FieldDef`) | Column definitions |
| Typed cell rendering + `meta.` sort headers + auto type-columns | `templates/assets/list.html:126,144-152` | Column rendering |
| Risk bulk form + checkbox column + fieldset | `templates/risks/index.html:176-284` | Extracted to `partials/bulk_actions.html` |
| `bulk_update_risks` (per-field, returns updated ids, org-scoped) | `risk_service.py:405` | Model for `bulk_update_assets` |
| Approval gating | `workflow_service.requires_approval` (`:222`), `submit` (`:245`, **per-asset**), `asset_update_payload` (`:696`) | Gate bulk edits |
| Review/due map + classifier | `review_service.REVIEW_DATE_FIELDS` (`:10`, audit deliberately absent), `review_status()` (`:29`) | `computed.next_review` + overdue metric |
| **Override-aware redaction** | `redaction.effective_sensitive_field_names` (`:47`, per-org), `redact_metadata_async` (`:99`, per-asset) | §7.3 prerequisite |
| Asset export | `/api/exports/assets` (`exports.py:15`, honors only `types`/`status`/`columns`), `export_service.export_assets` (uses **code-only** `redact_metadata`, `:68`) | Export-from-view (needs filter-parity **and** redaction fix) |
| Nav | `templates/base.html:19-28` | Register links + index |
| Per-asset audit | `audit_svc.log_audit_event` | Bulk-edit trail |

## 4. Core concept

### 4.1 `RegisterDef` / `ColumnDef` (new module `src/grcen/registers.py`)

```python
from dataclasses import dataclass, field
from grcen.models.asset import AssetType

@dataclass(frozen=True)
class ColumnDef:
    key: str            # "name"|"status"|"owner"|"created_at"|"updated_at"  (core)
                        # or "meta.<field>"                                  (custom field)
                        # or "computed.next_review" | "computed.lifecycle" | "computed.incident_state"
    label: str
    sortable: bool = True
    numeric: bool = False   # meta column to sort numerically (§7.4)

@dataclass(frozen=True)
class RegisterDef:
    type: AssetType
    slug: str                       # pretty URL segment, e.g. "vendors"
    label: str; plural: str
    columns: list[ColumnDef]        # curated default (≤5); ?columns=all expands
    default_sort: str = "name"
    default_order: str = "asc"
    lifecycle_column: str | None = None  # the "status at a glance" column; None ⇒ no badge
    owner_field: str | None = None       # accountable-party meta field (else core asset.owner)
    bulk_fields: list[str] = field(default_factory=list)
    metrics: list[str] = field(default_factory=list)   # metric keys (§8)
    nav_primary: bool = False
    canonical_path: str | None = None    # bespoke page (risk/framework/control); index links there
```

`REGISTERS: dict[AssetType, RegisterDef]`, one entry per organizational type. Posture
types (`ANSWER`) excluded. **Import-time assert:** every column key is non-sensitive by
*code default* — necessary but **not sufficient** (org overrides handled at runtime, §7.3).

### 4.2 Initial registry (columns = real `CUSTOM_FIELDS` names)

Curated to ≤5 default columns; `?columns=all` expands to the full effective-non-sensitive
set. `owner` below = core `asset.owner` unless an `owner_field` is named. Lifecycle and
metric choices reflect the review.

| Type | slug | Default cols (beyond Name) | lifecycle_column | owner_field | bulk_fields | nav_primary | canonical |
|---|---|---|---|---|---|---|---|
| vendor | `vendors` | tier, assessment_result, next_assessment_due, contract_end | `meta.assessment_result` | security_contact | status, owner, tags, tier, next_assessment_due | ✅ | — |
| incident | `incidents` | severity, incident_type, detected_at, **computed.incident_state** | `computed.incident_state` ‡ | — | status, owner, tags, severity | ✅ | — |
| policy | `policies` | **computed.lifecycle**, policy_type, computed.next_review, approver | core `status` | approver | status, owner, tags, review_date | ✅ | — |
| audit | `audits` | audit_type, result, end_date, open_findings | core `status` § | auditor | status, owner, tags | ✅ | — |
| system | `systems` | environment, criticality, hosting, computed.next_review | core `status` | — | status, owner, tags, criticality | — | — |
| device | `devices` | device_type, location, computed.next_review | core `status` | — | status, owner, tags | — | — |
| process | `processes` | frequency, next_execution, automation_level | core `status` | responsible_role | status, owner, tags, next_execution | — | — |
| person | `people` | title, department, computed.next_review | core `status` | — | status, owner, tags | — | — |
| data_category | `data-categories` | classification, **pii**, computed.next_review | `meta.classification` | — | status, owner, tags | — | — |
| requirement | `requirements` | framework, compliance_status, due_date, priority | `meta.compliance_status` | — | status, owner, tags, due_date | — | — |
| product | `products` | lifecycle_stage, type, computed.next_review | `meta.lifecycle_stage` | developer | status, owner, tags | — | — |
| intellectual_property | `intellectual-property` | ip_type, expiry_date, confidentiality | core `status` | — | status, owner, tags, expiry_date | — | — |
| organizational_unit | `organizational-units` | unit_type, headcount, ou_lead | core `status` | ou_lead | status, owner, tags | — | — |
| risk | `risks` | — | — | — | — | (link) | `/risk-management` |
| control | `controls` | effectiveness, frequency, next_test_due | `meta.effectiveness` | — | status, owner, tags, next_test_due | (link) | `/controls` |
| framework | `frameworks` | — | — | — | — | (link) | `/frameworks` |

‡ **Incident has no status field** (`custom_fields.py:434-456`: only `detected_at`/
`resolved_at`). v1 ships `computed.incident_state` = open/closed from `resolved_at IS NULL`,
plus age-since-`detected_at`; it does **not** badge the shared `AssetStatus`. A real
lifecycle (`open→triaged→contained→closed`) is the forward-compatible prereq: add an
`incident_status` enum to `CUSTOM_FIELDS` and repoint `lifecycle_column` — no framework change.

§ **Audit** `result` (pending/pass/pass_with_exceptions/fail, `:179`) is an *outcome*, blank
for in-flight engagements — so it is **not** the lifecycle. Lifecycle = core `status`
(engagement active vs archived); `result` stays a normal column; `open_findings` is the
headline metric. Audit gets **no** overdue/next-review metric (intentionally absent from
`REVIEW_DATE_FIELDS`).

> Bulk-field note (review C3): evidence-backed outcomes (`assessment_result`,
> `compliance_status`, `effectiveness`) are **omitted from `bulk_fields`** — mass-setting an
> approval/compliance verdict invites mis-stating posture. The universal bulk set is
> status / owner / tags / date fields.

## 5. Routing (canonical = `/assets?type=`)

```
GET  /assets?type=X[&columns=all]   → CANONICAL register surface (enhanced existing handler)
GET  /registers                     → index: one card per RegisterDef (count + link)
GET  /registers/{slug}              → 302 → /assets?type=X&sort=<def>&order=<def>  (pretty alias)
                                       (canonical_path types → 302 to the bespoke page)
POST /assets/bulk-update?type=X      → bulk lifecycle edit (Permission.EDIT, §6)
GET  /assets/export?...              → 302 → /api/exports/assets carrying the view's filters (§9)
```

The existing `asset_list` handler (`asset_pages.py:39`) is **enhanced, not refactored away**:
when `?type=` resolves to a `RegisterDef`, it (a) defaults `sort`/`order` from the def,
(b) resolves curated vs `all` columns (§7.2), (c) builds the metrics header (§8). With no
type (or a type lacking a def) it behaves exactly as today. Because everything stays under
`/assets`, the template's hard-coded `/assets?sort=` (`list.html:114`) and `/assets?page=`
(`:163`) links remain correct — **no shared-base-path surgery, no `/assets` HTML churn**
(this retires v1's risky "zero behavior change" claim by avoiding the refactor that broke it).

## 6. Bulk actions (generalize the Risk pattern)

**Template:** extract `risks/index.html:252-282` → `partials/bulk_actions.html`, driven by
`register.bulk_fields`. Field kinds render as: `status`→status select, `owner`→owner select
(reuse the `bulk_owners` query), `tags`→"add tags" input, `meta.<enum>`→select from
`FieldDef.choices`, `meta.<date>`→date input. Checkbox column + select-all reuse risk markup
(`name="asset_ids"`).

**Service:** `asset_svc.bulk_update_assets(pool, asset_ids, *, asset_type, status=None,
owner_id=None, add_tags=None, metadata_set=None, updated_by, organization_id) -> list[UUID]`,
modeled on `bulk_update_risks:405`. Org-scoped **and** `type`-pinned `WHERE` so a vendor bulk
can never touch another type/org. Validates enum values against `FieldDef.choices`.

**Endpoint** `POST /assets/bulk-update?type=X` (`Permission.EDIT`):
1. Parse `asset_ids` + submitted fields; validate against `bulk_fields`.
2. **Gating contract:** if `requires_approval(pool, type, "update", organization_id=...)` →
   for each id, `workflow_service.submit(action="update", payload=asset_update_payload(updates))`
   — note `submit` is **per-asset**, so a K-row bulk creates **K** `pending_changes`
   (and the submitter cannot self-approve them). **Cap K at 200** when gated; flash
   "*K changes submitted for approval*" and redirect to `/approvals`. Else apply directly via
   `bulk_update_assets`.
3. `audit_svc.log_audit_event` per updated id (mirror `dashboard_pages.py:244-258`).
4. Redirect back preserving `request.url.query`.

> **Consistency fix (review B4):** the existing `/risk-management/bulk-update`
> (`dashboard_pages.py:216`) is **ungated**. Back-port the same gating wrapper to it (small)
> so the "gold-standard" register and the generic endpoint behave identically — or document
> in `/admin/workflow` that risk bulk is intentionally ungated. Pick one; don't ship divergent.

## 7. Columns

### 7.1 Kinds
- **core** — `name/status/owner/created_at/updated_at`, sortable via `allowed_sorts`
  (`asset.py:210-217`).
- **meta** — `meta.<field>`, sortable today (text), typed cells (`list.html:144-152`).
- **computed** — framework-rendered:
  - `computed.next_review` — `REVIEW_DATE_FIELDS[type]` value + overdue/due_soon badge via
    `review_status()`.
  - `computed.lifecycle` — render `lifecycle_column` (core status badge or a meta enum).
  - `computed.incident_state` — open/closed from `resolved_at IS NULL` + age since `detected_at`.

### 7.2 Curation & `columns` mode
`resolve_columns(register, mode, effective_sensitive)`: default → `register.columns`;
`?columns=all` → every **effective-non-sensitive** custom field (§7.3). **This intentionally
*reduces* the default column count vs. today** (a `?type=system` list currently dumps ~10
fields). `columns=all` is the new escape hatch that reproduces today's full view.
`columns` is persisted into saved searches (§11).

### 7.3 Redaction — PREREQUISITE (review A1, a latent bug today)
The list path renders metadata cells **raw** and excludes only *code-default* sensitive
columns (`asset_pages.py:88-89`); `export_service.py:68` redacts with the **code-only** sync
`redact_metadata`. So a field promoted to sensitive via `sensitive_field_overrides` (per-org)
or `asset_sensitive_overrides` (per-asset) — both honored only by `redact_metadata_async`
(`redaction.py:99-128`) — **leaks on the list and in exports today.** Before shipping curated
columns / `columns=all` / export-from-view:
1. **Columns:** compute `effective_sensitive = await effective_sensitive_field_names(pool,
   type, org_id)` and exclude those from both curated and `all` column sets (replaces the
   static `not f.sensitive` filter).
2. **Cells:** mask per-row using per-asset overrides. Avoid N+1 by batch-fetching
   `asset_sensitive_overrides` for the page's `asset_id`s in one query, then masking in
   Python (or call `redact_metadata_async` per row — ≤25/page).
3. **Export:** switch `export_service.export_assets` to the async override-aware path (pool +
   `effective_sensitive_field_names`, batched per-asset overrides). Fixes the existing export
   leak as a side effect.
4. Keep the import-time non-sensitive assert, labelled *necessary-not-sufficient*.

### 7.4 Numeric meta sort (defer to slice 3; per-value, not per-column)
`meta.<field>` sorts as text (`asset.py:224`), so `headcount`/`open_findings` sort "10<2".
When implemented, cast **per value** so one dirty row can't abort the whole `ORDER BY`:
`CASE WHEN a.metadata->>'k' ~ '^-?[0-9.]+$' THEN (a.metadata->>'k')::numeric ELSE NULL END`
+ `NULLS LAST` (keep the existing regex guard on the key). A per-column allowlist alone does
**not** prevent the abort. Low value; behind slice 3.

## 8. Metrics header

`build_metrics(pool, register, user)` → small stat cards above the table (reuse risk-summary
styling). **Generic `by_status` over active/inactive/draft/archived is non-actionable for most
registers — it is demoted to a secondary stat, not a headline.** Ship type-specific cards for
the four `nav_primary` registers **in v1**:

| Register | v1 headline metrics |
|---|---|
| vendor | overdue assessments (via `REVIEW_DATE_FIELDS['vendor']`), by_tier, count not_approved/conditionally_approved |
| incident | open count (`resolved_at IS NULL`), by_severity, mean age since detected |
| policy | overdue reviews, draft count |
| audit | Σ open_findings, count fail / pass_with_exceptions |

Other registers: `total` + `overdue_reviews` (only when the type has a `REVIEW_DATE_FIELDS`
entry) + secondary `by_status`. Each metric is a bounded `GROUP BY` aggregate; the `/registers`
index derives all counts from **one** `GROUP BY type` round-trip (review C5).

## 9. Export-from-view (slice 3)

`/api/exports/assets` currently honors only `types`/`status`/`columns` (`exports.py:15-26`).
Extend `export_assets` + the route to accept the **same filter set as `list_assets`**
(q/owner/tag/meta/created range/sort), apply the §7.3 redaction fix, and add
`GET /assets/export` that 302s to `/api/exports/assets` with `type` + `request.url.query`
(carrying `columns` when `=all`). Add CSV/JSON buttons beside the existing
`exports/assets.pdf?{{ request.url.query }}` button (`list.html:8`). Exports are already
access-logged (`exports.py:31`).

## 10. Navigation

`base.html`: one **`Registers`** link → `/registers` index (cards grouped: *Third parties*
(vendor); *Operations* (incident, process, system, device); *Compliance* (requirement,
framework, control — co-locating the triad, review C4); *Governance* (policy, audit);
*Inventory* (person, data_category, product, IP, org_unit)). Plus the four `nav_primary`
links (Vendors, Incidents, Policies, Audits). Existing bespoke links stay (Risk Management,
Frameworks, Controls, Reviews, Answers). `canonical_path` entries link to the bespoke page.

## 11. Permissions, tenancy & saved searches

- View `Permission.VIEW`; bulk `Permission.EDIT`. All queries org-scoped via `list_assets`/
  `bulk_update_assets`; bulk additionally pins `type`. Bulk flows through `workflow_service`
  gating + self-approval blocks. CSRF: the new `POST` inherits the router's global
  `_csrf_check` dependency (`asset_pages.py:37`) — keep the `{% include "partials/csrf.html" %}`.
- **Saved searches (review B6):** because the canonical path is `/assets`, saved searches
  already key correctly — but `current_query`/`filter_params` must be extended to include
  `columns` (and the def-defaulted `sort`/`order`) so a saved `columns=all` register view
  reloads with its column mode intact. Add a test for this.

## 12. Phasing (re-cut smaller — review B8)

**Slice 1 — Index + register-ified `/assets` (Effort M).** `registers.py` (defs + registry);
`/registers` index + nav; `/registers/{slug}` redirect alias; enhance `asset_list` to apply
`RegisterDef` (curated columns, default sort, metrics header, overdue-review badge) when
`?type=` set; **§7.3 redaction prerequisite**. *Outcome:* every type gets a named, navigable,
metric-topped register at a pretty URL — and the latent redaction leak is closed.

**Slice 2 — Bulk actions (Effort M).** `partials/bulk_actions.html`; `bulk_update_assets`;
`POST /assets/bulk-update` with the gating contract (§6); back-port gating to risk bulk.

**Slice 3 — Polish (Effort M).** `computed.next_review` everywhere it's configured; full
v1 type-specific metric cards; export-from-view filter parity + redaction (§9); numeric meta
sort (§7.4).

**Dropped from v1:** admin-defined registers (`register_definitions` DB overlay) — defer
until an org asks; it doubles the config story (code dict + DB + merge precedence) against
CLAUDE.md's "don't impose a paradigm."

## 13. Data-model impact

**None** for slices 1–3 (pure reuse). Optional later: an `incident_status` enum field
(prereq for a real Incident lifecycle, §4.2) is a one-line `CUSTOM_FIELDS` addition, no
migration. New entity types (Findings/Exceptions/RoPA) and per-type *workflow* status are
out of scope.

## 14. File-by-file change list

| File | Change |
|---|---|
| `src/grcen/registers.py` | **new** — `RegisterDef`/`ColumnDef`/`REGISTERS`/`resolve_columns`/slug↔type map + non-sensitive assert |
| `src/grcen/routers/register_pages.py` | **new** — `/registers` index + `/registers/{slug}` redirect |
| `src/grcen/routers/asset_pages.py` | enhance `asset_list` (apply `RegisterDef`); add `POST /assets/bulk-update`, `GET /assets/export`; wire override-aware redaction into the list path |
| `src/grcen/services/asset.py` | add `bulk_update_assets`; (slice 3) per-value numeric meta-sort cast |
| `src/grcen/services/export_service.py` + `routers/exports.py` | full filter parity + async override-aware redaction |
| `src/grcen/templates/assets/list.html` | iterate `columns` + `render_cell` macro; metrics-header include; bulk-actions include; CSV/JSON buttons |
| `src/grcen/templates/partials/bulk_actions.html` | **new** — generalized from `risks/index.html` |
| `src/grcen/templates/registers/index.html` | **new** — grouped register cards |
| `src/grcen/templates/base.html` | `Registers` link + `nav_primary` links |
| `src/grcen/main.py` | mount `register_pages.router` |
| `tests/` | new module (§15) |

## 15. Testing

- `/registers` index renders; `/registers/{slug}` 302s to the right `/assets?type=...` (or
  bespoke page for `canonical_path`); unknown/posture slug → 404.
- Register-ified `/assets?type=X`: curated columns by default; `?columns=all` expands to the
  effective-non-sensitive set; default sort from the def.
- **Redaction:** a field promoted via `sensitive_field_overrides` is dropped as a column
  **and** masked in cells **and** masked in export, with and without `columns=all`; per-asset
  override likewise. (Add a regression test that fails on today's code.)
- Bulk: ungated path mutates + audit-logs; gated path creates K `pending_changes` + redirects;
  rejects wrong-type / cross-org ids; enum values validated; K capped at 200.
- Export-from-view CSV/JSON matches the filtered+sorted+`columns` view.
- Saved search captures `columns`/`sort`; reload preserves column mode; path stays `/assets`.
- Tenancy: org A cannot see/bulk-edit org B rows. Permissions: Viewer read-only; Editor bulk.
- `/registers` index issues one `GROUP BY type` count query (query-count assertion).

## 16. Resolved decisions & remaining open questions

**Resolved by review:** canonical URL = `/assets?type=` (§5); curated columns are the new
default with `columns=all` escape hatch (§7.2); incident/audit lifecycle (§4.2); redaction is
a precondition (§7.3); type-specific metrics in v1 (§8); Phase 4 dropped (§12).

**Open:**
1. Is collapsing today's full typed `?type=` columns to ≤5 acceptable, or should ad-hoc
   `/assets?type=` keep showing all fields and only the *register alias* curate? (Default
   chosen: curate everywhere, `columns=all` to expand.)
2. Back-port gating to the risk bulk endpoint, or document it as intentionally ungated? (§6)
3. Add `incident_status` now (real lifecycle) vs. ship `computed.incident_state` first? (§4.2)
4. Nav: dropdown vs. flat `nav_primary` links for the four flagship registers. (§10)
