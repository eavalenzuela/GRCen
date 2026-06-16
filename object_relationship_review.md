# GRCen — Object & Relationship Management Review

A comprehensive review of GRCen *as a graph tool*: how well it lets users create,
connect, find, explore, and maintain assets (objects) and their relationships.
Scope is the core domain model and its UX — not auth/crypto/infra. Complements
`stage0_findings.md` (task-scenario walkthrough); this is the architectural pass.

Method: parallel read of the asset, relationship, graph, and search/discovery
surfaces (routers, services, templates, static JS). Two correctness bugs below
were verified directly in source.

---

## Verdict

GRCen's pitch is "a graph of assets and relationships, searchable and visualizable
from any node." The **objects** layer is genuinely strong — 17 typed asset types,
per-type custom fields, clone, bulk import/export, attachments, rich detail pages.
The **relationships** layer is functional but thin, and **the graph itself — the
product's reason for existing — is the weakest surface.** You can model the graph
well; you cannot yet *explore, search, or maintain* it well. The biggest wins are
not new features but closing the gap between "the data is in there" and "a user can
see and work with the connections."

Two findings below (G1, R3) are correctness/tenancy bugs, not UX opinions.

---

## Correctness bugs found during the review

### G1 [SEV: HIGH — cross-tenant data leak] Org Views ignore `organization_id`
`services/org_views.py` has **zero** `organization_id` filters. `get_org_chart`,
`get_business_structure`, `get_product_view`, and `list_products` don't even accept
org context (verified: signatures take only `pool` / `product_id`). They query all
`assets`/`relationships` org-wide. In a multi-tenant deployment, `/org-views` leaks
Persons, OUs, and Products across every tenant. Contrast `services/graph.py`, which
is correctly scoped on every table. **Fix:** thread the active org through all four
functions and filter, mirroring `graph.py`. Add a cross-tenant regression test.

### R3 [SEV: MEDIUM — preview/execute disagree across tenants] Import preview not org-scoped
`services/import_service.py` relationship **preview** resolves endpoints with
`SELECT 1 FROM assets WHERE name=$1 AND type=$2` (lines ~164, ~175) — no
`organization_id`. **Execute** (lines ~207-218) adds `AND organization_id=$3`. So
preview can call a row valid because the asset exists in *another* org, then execute
reports "not found." Also a minor info-leak (confirms existence of names in other
orgs). **Fix:** scope preview to the caller's org, identical to execute.

---

## Objects (assets) — strong, with browse-side blind spots

What works: clean create form, per-type custom fields (`custom_fields.py`),
type-aware detail pages, **clone (+optional relationships)** which meaningfully
offsets tedious single-entry, bulk import/export with dry-run, attachments on assets.

Gaps, prioritized:

- **O1 [HIGH] The list table is type-blind.** `/assets` shows only Name/Type/Status/
  Owner/Created (`templates/assets/list.html:107-113`). Filter to just Risks and you
  still see no severity, no review date, no criticality — the table is identical to
  "all types." You must open every asset to see anything meaningful. *Want:* per-type
  columns (or user-selectable columns) so a filtered list is actually a worklist.
- **O2 [HIGH] Can't sort or filter by custom fields or `updated_at`.** Sort is
  whitelisted to name/type/status/owner/created_at (`services/asset.py:180`). No
  "sort risks by severity," "show me what changed recently," "overdue reviews first."
  Metadata filtering is a single exact key=value pair — no ranges, no second key.
- **O3 [MED] `required` field flag is dead.** `FieldDef.required` exists but is
  enforced nowhere (form, page POST `_pages_shared.py`, or import). You can create a
  Risk with no likelihood/impact/severity and nothing objects. No way to mandate
  completeness on any type.
- **O4 [MED] No completeness signal on detail pages.** Custom fields render only when
  non-empty (`detail.html:135`), so blank/unfilled fields are invisible — you can't
  see what's missing on an asset. No "60% complete" or "needs review" cue, despite
  nearly every type carrying a `next_review_due`.
- **O5 [MED] Import foot-guns.** Step 2 makes you re-select the file (preview file A,
  execute file B is possible); unresolved owners are silently nulled; invalid enum
  metadata is accepted without validation against `choices`. Tags and criticality
  aren't importable at all.
- **O6 [LOW] Hard delete, JS-confirm only.** No soft-delete/trash/undo even though an
  `archived` status exists. Export uses freeform comma-text for types/columns with no
  checklist of valid columns; list filters don't carry into the CSV/JSON export form.
- **O7 [LOW] Field-richness is uneven.** Risk ~22 fields, but Framework/IP/Answer ≤8;
  Incident has no status field. Types with bespoke UI (Risk, Framework, Audit, Person)
  are first-class; generic-only types (Device, Process, Control, Vendor, Data Category)
  live only in the lowest-common-denominator `/assets` surface and suffer O1/O2 most.

---

## Relationships — functional, but the vocabulary and editing story is incomplete

What works: four create paths (detail form, graph drag-to-link, import, REST);
direction-aware display via `RELATIONSHIP_LABELS` (45 curated types with inverse
phrasing); perspective-correct relationship table on detail pages; attachments/
evidence on relationships fully wired. F1/F2/F3 from Stage-0 are fixed.

Gaps, prioritized:

- **R1 [HIGH] No UI to edit a relationship.** `PUT /api/relationships/{id}` supports
  editing type + description (`relationships.py:129`), but **no template reaches it**.
  To fix a typo'd relationship type or amend a description, a web user must delete and
  recreate. *Want:* an inline edit, same as assets have.
- **R2 [MED] The controlled vocabulary is never offered for input.** The 45-type
  `RELATIONSHIP_LABELS` map (`_pages_shared.py:16-63`) drives *display only*. Create
  forms suggest only types already in the DB (`detail.html:199-203`), so a fresh org
  gets an empty datalist and no guidance toward canonical types. The system knows the
  good vocabulary and its inverses but won't share it at the moment of creation —
  exactly when it would prevent fragmentation ("owns" vs "owned by" vs "manages").
- **R3** — see correctness bugs (import preview tenancy).
- **R4 [MED] Inconsistent rewrite + bypassed gating on import.** Bulk import silently
  rewrites `owns`→`manages` for person targets (`import_service.py:232`) — the exact
  behavior the API path was *fixed* to stop doing (Stage-0 F3). Import also writes
  relationships directly even when relationship-create is workflow-gated for that type
  (the gate lives only in the API router). Auditors won't expect bulk import to skip
  approvals.
- **R5 [MED] Two divergent create UIs.** Polished detail-page form vs. raw browser
  `prompt()` dialogs in graph drag-to-link (`graph.js:263-268`) — no autocomplete, no
  vocabulary, no real error surfacing. Same action, very different quality.
- **R6 [LOW] Can't create incoming edges from a detail page** (current asset is always
  source); unknown incoming types render literally as `"incoming: <type>"`
  (`_pages_shared.py:71`). No self-loop prevention. The detail relationship table
  intermixes in/out edges with no grouping, direction column, filter, or pagination —
  hard to scan on a hub asset. Import resolves endpoints by ambiguous `(name, type)`
  with no ID-based path, so duplicate-named assets resolve arbitrarily.

---

## Graph visualization & traversal — the weakest surface, and it's the core promise

What works: per-asset Cytoscape subgraph, pan/zoom, click-to-navigate, depth 1-3,
drag-to-link, type colors, correctly org-scoped traversal (`services/graph.py`).

Gaps, prioritized:

- **G2 [HIGH] No incremental exploration.** You cannot expand a node's neighbors in
  place. Every click *navigates away* and rebuilds a brand-new graph re-centered on
  the clicked node (`graph.js:128-134`) — full page reload + force-layout from scratch,
  losing your position and prior context. There is no way to "walk the graph" — the
  single most important interaction for a graph product. No breadcrumb/history either,
  so multi-hop exploration is disorienting.
- **G3 [HIGH] No global / whole-org graph.** Only per-asset subgraphs exist. There's no
  "see the whole graph" entry point and **no Graph link in the nav** (`base.html`) —
  the graph is reachable *only* by first opening a specific asset and clicking "View
  Graph." For a graph-first product, that's a severe discoverability hole.
- **G4 [MED] No filtering or legend in the graph.** Colors exist but there's no
  rendered legend and no way to hide/show types ("only Risks and Controls"). Busy hubs
  become unreadable. No layout choice, no "fit to screen," no search-within-graph.
- **G5 [MED] No result-size cap in traversal.** Depth-3 on a hub asset can pull a large
  fraction of the org graph with no `LIMIT`/fan-out guard, shipped to Cytoscape which
  then runs an animated force layout client-side — a perf cliff on dense graphs. The
  service also runs the recursive CTE twice (once for nodes, once for edges).
- **G6 [LOW] Inconsistencies.** Two divergent `TYPE_COLORS` tables (`graph.js` vs
  `org_tree.js`) disagree on vendor/control/incident/framework colors, so a node is a
  different color in the graph vs. org views. Documented URL is wrong: CLAUDE.md says
  `/assets/{id}/graph`, actual route is `/graph/{id}` (`asset_pages.py:517`).

---

## Search & discovery — buried, shallow, and missing the graph's signature query

What works: strong *filtering* on `/assets` (type/status/owner/date/tag/metadata),
clickable type cells, tags page with counts, saved searches on 2 pages.

Gaps, prioritized:

- **S1 [HIGH] No global search box.** Nothing in the nav (`base.html`). Search exists
  only embedded in the `/assets` list. There's no app-wide "jump to any asset" — the
  most basic affordance for a tool whose value is "searchable from any node."
- **S2 [HIGH] No way to find orphaned / unlinked assets.** No filter, no query, nothing
  surfaces assets with zero relationships. For a tool whose purpose is mapping
  connections, the inability to find *the gaps* (the disconnected nodes a GRC user
  most needs to wire up) is a core completeness hole. *Want:* an "unlinked" filter and
  a dashboard count.
- **S3 [MED] Relationship descriptions and custom fields are unsearchable.** Free-text
  search (`q`) covers name/description/owner only. Relationship descriptions — a core
  part of the graph model — are searchable nowhere. Metadata is exact key=value only.
- **S4 [MED] `/api/assets/search` underdelivers vs. its contract.** Docstring says
  "names and descriptions"; implementation is name-only `ILIKE` (`asset.py:371,381`).
  This backs the Add-Relationship target picker, so you can't find a link target by its
  description.
- **S5 [LOW] No full-text/fuzzy.** Everything is leading-wildcard `ILIKE` (no
  `tsvector`, no `pg_trgm`) — no ranking, no typo tolerance, non-indexable, degrades at
  scale. No breadcrumbs anywhere. Tag badges on detail pages aren't clickable links.
  No criticality filter/sort. Saved searches can't be renamed and exist on only 2 pages.

---

## What users want to do but can't (jobs-to-be-done)

These are the recurring real-world tasks the current model blocks or makes painful:

1. **"Show me everything connected to this system, and let me explore outward."**
   Blocked by G2 (no expand-in-place) / G3 (no persistent exploration).
2. **"Find every asset that isn't linked to anything."** Impossible (S2).
3. **"List my overdue risks / controls due for review, sorted by severity."** Blocked
   by O1/O2 (no type columns, no custom-field sort) outside the bespoke Risk register.
4. **"Search for the asset where we wrote 'handles PCI cardholder data' in the notes."**
   Blocked by S3 (descriptions/metadata unsearchable).
5. **"Fix this relationship — I meant 'processed_by', not 'process_by'."** Blocked by R1
   (no edit UI; must delete + recreate).
6. **"See the whole org map on one screen."** Blocked by G3.
7. **"Require that every Risk has a severity before it's saved."** Blocked by O3.
8. **"Jump straight to any asset from anywhere."** Blocked by S1.
9. **"Connect this asset to one that already exists, picking by description."** Weak —
   S4 (name-only target search).
10. **"Trust that my org's data and another org's don't mix."** Violated by G1/R3.

---

## Recommended sequencing

**Fix first (correctness):** G1 (tenant leak), R3 (preview scoping). Small, high-risk.

**Highest UX leverage (the graph promise):**
1. G3 + S1 — add a top-nav **Graph** entry + **global search box**. Cheap, huge
   discoverability payoff.
2. G2 — node **expand-in-place** (Cytoscape supports incremental add; stop reloading
   on every click). The single biggest exploration upgrade.
3. S2 — **"unlinked assets"** filter + dashboard count. Directly serves the core JTBD.
4. O1/O2 — **per-type / selectable columns** and **custom-field sort** on `/assets`.

**Consistency & completeness (cheap, high polish-per-line):**
5. R1 — relationship **edit UI** (backend already exists).
6. R2/R5 — offer `RELATIONSHIP_LABELS` as suggestions in *both* create paths; replace
   graph `prompt()` with the real form.
7. R4 — make import respect the no-rewrite rule and workflow gating.
8. G4/G6 — graph type-filter + legend; unify the two `TYPE_COLORS` tables; fix the
   documented graph URL.
9. O3/O4 — enforce `required`; show a completeness indicator on detail pages.

**Deeper investments:** S5 (Postgres FTS / `pg_trgm`), G5 (traversal size caps +
single-CTE), O5/O6 (import re-upload, soft-delete).
