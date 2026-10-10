# GRCen UI Improvement Plan

_Status: Phase 0 and Phase 1 shipped (see CHANGELOG). Phase 2 (navigation shell) is next: its data plumbing (org name/memberships on the user, `switch-org?next`, numeric badge, menu JS, favicon) is in place; `base.html` still has the flat 22-link nav and 20-link admin bar. 28 inline styles remain in templates, each with a CSS class now available in style.css section 8._

## Context

GRCen's backend is mature (RLS tenancy, RBAC, 600+ tests), but the UI grew one page at a
time: a 339-line stylesheet, ~520 inline `style=` attributes, a 22-link top nav plus a
20-link admin bar, no shared feedback mechanism, and feature pages that each invent their
own layout. This pass answers one question: **what is badly designed or not fully
functional as a user actually experiences it**, and what to fix, in what order.

**How the evidence was gathered.** Built the Docker image from `main`, seeded it (187
sample assets + 366 relationships, the `common-baseline` content pack, 44 alerts, answer
library + questionnaire, one account per role), crawled 122 routes with Playwright at 1440px
and 390px (no 500s on the happy path; every PDF/CSV/ZIP downloads), then ran 13 persona-based
Playwright testers (nav, dashboard, lists, CRUD, graph, risk, frameworks, questionnaires,
import/approvals, admin, roles, visual design, a11y, end-to-end practitioner journeys), then a
completeness critic dispatched 6 more (auth/MFA/SSO lifecycle, multi-org, alert delivery,
sync-to-UI, API machine clients, resilience/scale/mobile). Every one of the 457
deduplicated findings was re-checked by an independent verifier (417 confirmed, 40
partially, 0 refuted), and I re-verified the systemic ones by hand.

**Evidence in the repo:** `ui_audit_findings.md` (the 457-finding register with routes, repro steps, code pointers and verifier notes) and `tools/ui_audit/` (`crawl.py` + `grc_pw.py`, the reusable Playwright crawler/helper: `PYTHONPATH=tools/ui_audit .venv/bin/python tools/ui_audit/crawl.py admin`).

**Headline.** One security-hardening change silently broke a large share of the UI's
interactivity, and the app has no general way to tell the user that something succeeded or
failed. Those two foundations come first; everything else layers on them.

---

## Execution order (rewrite each file once)

| # | Theme | Effort | Why here |
|---|-------|--------|----------|
| 0 | Blockers: CSP-safe JS, 204 swaps, attachment route, export default, base URL, security fixes | M | Every sev-4 finding; mechanical, ships alone |
| 1 | Design tokens + shared components + a11y baseline (`style.css`, `macros.html`, partials) | L | Every later theme consumes these |
| 2 | Navigation IA, user/org menu, responsive + print shell (`base.html` rewritten once) | M | Depends on 1; page-wide |
| 3 | Session flash + htmx feedback contract + HTML error pages + auth-flow messaging | M | The channel every later write renders into |
| 4 | Jinja presentation filters (dates, enums, cron) | S | Applied while page families are being edited |
| 5 | Form validation with input preservation (asset, admin, auth) | L | Shares the re-render pattern with 3 |
| 6 | Lists, registers, tables, pagination, heatmap partial | L | Feeds 7 and 8 |
| 7 | Asset detail/edit: accessible pickers, relationships, provenance | L | Combobox reused by 9 |
| 8 | Dashboard, My Work, notifications, alert form | M | Reuses heatmap partial + flash |
| 9 | Compliance pages: frameworks/SoA/controls, approvals, questionnaires/campaigns | L | Reuses combobox, badges, table toolkit |
| 10 | Import/export flows | M | Reuses partial-rendering pattern from 3 |
| 11 | Graph and Org Views | L | Isolated JS surface; last |

---

## Phase 0 — Restore broken interactivity (blockers)

### 0.1 CSP blocks every inline handler (sev 4, reported by 12/13 testers)
`middleware.py:87` sends `script-src 'self' 'nonce-…'`. 29 `onclick/onsubmit/onchange`
attributes across 24 templates, the server-rendered owner-search fragment, and every
`hx-on::` attribute are blocked by the browser (console: *Executing inline event handler
violates CSP*). Consequences, all reproduced:
- **Destructive actions run with no confirmation**: asset Delete (`assets/detail.html`),
  user delete, tag delete, token/OAuth revoke, questionnaire delete, MFA disable, evidence
  delete, bulk Apply, org delete.
- **Dead controls**: `/assets` Advanced filter toggle, Save this search (+ ✕), select-all
  checkboxes (assets, risk register), Owner autocomplete result pick on New/Edit Asset
  (owner cannot be set at all), graph Link Mode, token Copy, encryption profile radios,
  audit-settings toggle, tag chips on the form, file-upload "reload after success".

**Approach.** Keep the strict CSP; add one nonce-loaded `static/js/app.js` with delegated
listeners on `data-*` attributes: `data-confirm="…"` (submit/click → `confirm()`),
`data-toggle="#id"` (also sets `aria-expanded`), `data-select-all`, `data-copy="#id"`
(clipboard + "Copied" status; reuse for tokens, OAuth secrets, invite links, recovery codes,
calendar URL), `data-tag-chip`, `data-select-on-focus`, `data-action=save-search|delete-search`.
Autocomplete results become `<button type=button data-pick-owner>`. Replace every
`hx-on::after-request` with handlers in app.js. Add a test that fails if any template
contains `on[a-z]+=` or `hx-on`. Files: `base.html`, `assets/{detail,list,form}.html`,
`partials/{saved_searches,bulk_actions}.html`, `risks/index.html`, `graph/{view,overview}.html`,
`tokens/my_tokens.html`, `admin/*.html`, `tags/index.html`, `relationships/evidence.html`,
`answers/questionnaire_detail.html`, `vendor_campaigns/detail.html`, `settings.html`,
`routers/asset_pages.py` (owner-search fragment), `static/js/graph.js`.

### 0.2 htmx actions that return 204 never update the page (sev 3)
`DELETE /api/relationships/{id}`, `/api/alerts/{id}`, attachments, `POST …/notifications/{id}/read`
return 204; htmx 2.0.4 does not swap on 204, so Remove relationship, Mark read, Delete alert
and Delete attachment leave the stale row until reload. **Approach.** In app.js set
`htmx.config.responseHandling` to swap on 204 and use `hx-target="closest tr" hx-swap="outerHTML"`;
have the read endpoint return the re-rendered row (`partials/notification_row.html`) with
`HX-Trigger: notif-changed`, and make the nav badge listen (`hx-trigger="load, every 60s,
notif-changed from:body"`).

### 0.3 "Add URL attachment" always fails with a silent 422 (sev 3)
`assets/detail.html:253` posts form-encoded to the JSON-only `create_attachment`
(`routers/attachments.py:91`); the upload form shows raw JSON on success. **Approach.**
Extract `partials/attachments_table.html`; have the create/upload routes return the partial
for `HX-Request` (or add a `Form()` page route + redirect with flash); same for relationship
evidence.

### 0.4 Export page is broken by default (sev 4)
`/exports` "Download Export" with Status = All sends `status=` → `/api/exports/assets`
returns 422 JSON; types/columns are free-text comma lists and any non-exact value 500s.
**Approach.** Treat empty strings as `None` in `routers/exports.py`; checkbox lists of types
and columns in `exports/index.html`; on error redirect back with a flash. Consider folding
the page into the list-page CSV/JSON buttons that already honour filters (theme 10).

### 0.5 Alerts cannot be created or edited from the UI (sev 4)
`/alerts` lists and deletes only; the create form its empty-state copy refers to does not
exist (`alerts/list.html`; only `POST /api/alerts/`). **Approach.** `alerts/_form.html`
(title, asset picker, one-time date or recurring schedule builder that emits the cron,
enabled) on `/alerts` and an "Add alert" shortcut on asset detail; edit page; list shows the
asset name and a human schedule. (Full treatment in theme 8; the form itself is a blocker.)

### 0.6 Links minted with `APP_BASE_URL` default `http://localhost:8000` (sev 3)
Invitation links, calendar feed URL, vendor-portal links, and every link in alert/digest
emails and Slack/Teams webhooks use `config.APP_BASE_URL`. **Approach.** Build UI-minted
links from `request.base_url`; keep `APP_BASE_URL` for background jobs and warn on
`/admin/organization` when unset.

### 0.7 Security defects found on the way (not UI, but must land first)
Found by the API/auth gap testers and independently confirmed; each is a one-file fix:
- **Arbitrary server file read**: `POST /api/assets/{id}/attachments/` with `kind=file` and
  an absolute `url_or_path` is accepted and later served (`routers/attachments.py`). Reject
  `kind=file` from the API or restrict paths to `UPLOAD_DIR`.
- **Upload type allowlist trusts the client `Content-Type`** (`.exe` sent as `image/png`
  accepted); sniff magic bytes; filenames > 255 chars → 500 and an orphan file on disk.
- **Bulk import bypasses approval gating** that the single-asset API enforces
  (`routers/imports.py`); route gated types through `pending_changes` or refuse with 409.
- **OAuth access token can mint a never-expiring static API token** (`POST /api/tokens`
  with a client-credentials bearer); deny `MANAGE_TOKENS` to OAuth-issued tokens.
- **A bare `POST /settings/mfa/begin` disables enforced MFA**; the same TOTP code is accepted
  twice (no replay window); `GET /settings/mfa/cancel` and `GET /logout` change state (make
  them POST + CSRF).
- **Reflected flash banners**: `/tags`, `/settings`, `/admin/sessions` render `?flash=` text
  verbatim (replaced by the session flash in 3.1).
- **Viewers can read the vendor-portal capability URL** on `/vendor-campaigns/{id}`.

---

## Phase 1 — Design tokens, shared components, accessibility baseline

Verified inconsistencies across routes: five badge styles, three action-bar styles,
undefined button hierarchy (Save secondary on some admin pages, primary on others),
checkboxes stretched full-width inside `.form-group` (settings, SMTP, webhooks, tokens),
inputs in Arial and textareas in monospace, h1 squeezed beside intro text (content packs,
risk appetite), 44 distinct hex colours, emoji as the icon system, blue-underlined links on
every table cell, dates wrapping mid-value, four admin page layouts, focus ring removed on
inputs, 196/306/72 unlabeled grid inputs.

**Approach.** `style.css`: tokens (`--fs-1..6`, tone tokens `--tone-{success,warning,danger,
info,neutral}-{bg,fg,border}`, button heights), `button,input,select,textarea{font:inherit}`,
`.form-check`, `:focus-visible` outline, `.page-header` (h1 + `.actions`), `.btn` hierarchy,
`.badge--{status}` vocabulary (reuse the Review Tracker pills), `.empty-state`, `.alert`,
table rules (`td.num`, `td.date{white-space:nowrap}`, sticky `thead`, `.table-wrap{overflow-x:auto}`),
heading scale, inline SVG icon sprite. `templates/macros.html`: `status_badge`, `field_error`,
`sort_th`, `pagination`; partials `page_header.html`, `empty_state.html`, `permission_grid.html`
(labelled checkbox grid for workflow/sensitive-fields/token permissions); `admin/_layout.html`.
Then migrate inline styles template by template, worst first: `frameworks/detail.html`,
`frameworks/soa.html`, `frameworks/controls.html`, `assets/list.html`, `frameworks/index.html`,
`admin/content_packs.html`. Mobile: 16px inputs (iOS zoom), 44px touch targets.

---

## Phase 2 — Navigation IA and responsive shell

`.nav-links` forces a 1712–1728px document at 1440px (horizontal scrollbar on every page),
the search box collapses to 26px, the admin bar overflows to the unreachable left side at
≤1280px (Users/Invitations/Tokens unreachable at 1024px), and 390px is unusable; nothing
marks the current section, the active organization is shown nowhere, back links use four
styles, no favicon, no skip link, no print styles.

**Approach.** Rebuild the `base.html` header once: brand, search (`flex:1 1 240px; min-width:180px`),
six grouped `<details class="nav-menu">` menus (Inventory: Assets, Registers, Tags, Import,
Export · Relationships: Graph, Org Views · Risk: Risk Management, Reviews, Alerts ·
Compliance: Frameworks, Controls, Evidence, Reports · Questionnaires: Answers, Incoming
Questionnaires, Vendor Campaigns · Work: My Work, Approvals, Notifications with a badge pill),
a user menu (username, active org name + switcher with `?next=`, Settings, Logout as POST),
admin as a grouped menu (Access, Integrations, Security, Organization, Logs); `aria-current`
on the active section (`request.url.path` prefix), hamburger under 1024px, skip-to-content
link, `favicon.svg`, breadcrumb partial, `@media print` hiding chrome.

### 2.1 Multi-org is invisible and half-wired
A superadmin cannot switch into an org they just created (`/admin/orgs` has no open/switch
action); no way to add an existing user to a second org; org delete has no confirmation and
500s when the org has users; `/switch-org` lands on `/?`. **Approach.** "Open" on
`/admin/orgs`, membership editor on the user edit page, confirm + guarded delete.

---

## Phase 3 — Feedback foundations

### 3.1 Session flash + inline form errors
No success feedback after create/edit/delete/clone/watch/save-settings anywhere; validation
errors on admin forms redirect and discard input; 33 hand-styled alert divs in 22 templates.
**Approach.** `flash(request, level, text)` / `get_flashes` in `routers/_pages_shared.py`
backed by `request.session["_flash"]`, rendered once in `base.html` as
`#flash-region role="status" aria-live="polite"`; delete `_decode_flash` and every
`?flash=` parameter; re-render forms with errors + submitted values (HTTP 400) instead of
redirecting.

### 3.2 htmx feedback contract
`app.js`: `htmx:responseError` / `htmx:sendError` → toast in `#flash-region`;
`htmx-indicator` on every htmx form and the import buttons (6–8s with no indicator today);
401 on htmx → `HX-Redirect: /login?expired=1&next=…`; keep a submitted form in the session
across re-login; `updated_at` hidden field on edit forms to reject stale concurrent saves.
Destructive buttons use `hx-confirm`/`data-confirm` consistently (bulk Apply, SoA N/A,
appetite band remove, vendor-portal Submit with an unanswered-question warning).

### 3.3 HTML error pages and auth-flow messaging
Reproduced: `/assets?type=foo`, `/reviews?type=bogus`, `/assets?page=0`, `/admin/audit?page=0|abc`
→ bare `Internal Server Error`; `/nope` → JSON; missing asset/framework → 19-byte text; login
and MFA 429 → bare text; 403 hides the nav; SSO callback failures → raw 500/JSON; lockout
gives no duration; recovery-code login gives no "codes remaining". **Approach.**
`errors/{404,429,500,error}.html` extending `base.html` with the real user; rewrite the
`main.py` HTTP exception handler to branch on `/api/`+`/scim/` (JSON with `WWW-Authenticate`
on 401) vs HTML; typed `Query()` params (`AssetType` enum, `page: int = Query(1, ge=1)`) in
`asset_pages.py`, `dashboard_pages.py`, `admin_pages.py`; login/MFA pages show throttle,
lockout duration, which account is being challenged, and codes remaining.

### 3.4 Notifications correctness
Marking a broadcast notification read hides it for every user (read state is per row) — add
per-user read rows; `GET /api/alerts/notifications/all` 500s once a watcher/session
notification exists; deleting an alert cascades away its notifications (keep them); an
overdue review date triggers no reminder on any channel — generate review reminders from
`review_service` automatically; "Mark all read"; link each notification to its subject.

---

## Phase 4 — Presentation filters and validation

### 4.1 Jinja filters (`routers/_pages_shared.py`)
Raw values shown to users: microsecond timestamps (dashboard alerts, asset Alerts table),
eight date formats, UTC with no indicator, cron strings, snake_case enums (approvals,
workflow, evidence kinds, campaign status, control status), `title()`-mangled free text,
`&#9660;` rendered as text on the risk register sort header (`risks/index.html:201`), UUID
export filenames. **Approach.** `fmt_dt` (one format, `<time datetime>`), `fmt_date`,
`rel_time`, `humanize` (acronym allowlist), `cron_text`, `kind_label`, `label_for(enum_field)`;
sweep the 41 `strftime` sites; literal ▲▼ in `sort_th`; name exports after the object.

### 4.2 Server-side validation with input preservation
Whitespace-only names accepted; names > 255 and non-integer custom fields → 500; malformed
dates stored then silently blanked; owner cannot be cleared; duplicate/self relationships
accepted; SoA N/A with empty justification; appetite warn > max; token expiry in the past;
no password policy and case-sensitive username uniqueness on admin user creation; no email
field (while `/settings` tells users to ask an admin); no change-own-password; OIDC secret
echoed into HTML; SMTP/ITSM/webhook/rate-limit/branding/SCIM forms accept invalid values
with a success flash. **Approach.** `_extract_metadata` returns `(metadata, errors)`;
`coerce_value` gains date/int guards; `field_error` macro + `aria-invalid`; shared validators
per admin form; password change on `/settings`; never echo secrets.

---

## Phase 5 — Page families

### 5.1 Lists, registers, tables (theme 6)
Default `/assets` opens on 420 content-pack requirements sorted "1.1, 1.2, 10.1"; 27 bare
page numbers (200+ at 5k assets) with no prev/next or page size; filter strings not
URL-encoded (`&`, `#`, `+` break sort/pagination); register sort field leaks across types;
"Clear" ejects users from registers; type views dump 19–26 columns (2221px wide) with
dates wrapping; two "Manager" columns on People; three risk views disagree on counts;
metric cards not clickable; `/reviews` renders 5,022 rows at scale; access log capped at 200
with no paging; tag filter case-sensitive; Save-search uses `prompt()`. **Approach.**
`sort_th` + `pagination` macros (Prev/Next, window, page-size select, "x–y of N") wired into
`asset_pages.py`, `/reviews`, audit and access logs; default sort `updated_at desc`; curated
columns by default with a column chooser; `urlencode` filter params; one counting rule for
risks; clickable metric cards; a named saved-search dialog; shared `partials/heatmap.html`.

### 5.2 Asset detail and edit (theme 7)
Owner and relationship pickers are mouse-only `<div>`s with no combobox semantics; Enter in
the owner search submits the form; pinned-type forms say "New Asset" and cancel to the wrong
list; default type "Audit"; risk inherent score editable yet overwritten; attachments show
raw server paths with no download link; relationship type is free text (casing junk, inverse
forms); no audit-history entry point; hub assets list 513 relationships flat; synced assets
give no hint they are machine-owned, so edits are overwritten and deletes undone by the next
sync. **Approach.** `static/js/combobox.js` (WAI-ARIA combobox: arrows/Enter/Escape, clear
chip, no form submit) for owner + relationship target; constrained relationship-type select
with direction hint from `suggested_relationship_types()`; type-specific summary partials
(`assets/_summary_risk.html` etc.) replacing the flat `detail-grid`; "Synced from HRIS/CMDB/uvt"
badge with read-only synced fields and an Archived badge; relationships table with type
filter + paging; audit-history link; reject self/duplicate edges in `relationship_service`.

### 5.3 Dashboard, My Work, notifications, alerts (theme 8)
"Recent Assets" is alphabetical; heatmap cells link to the unfiltered risk list; cards and
alert rows are not links; asset-types card is a bare 16-item list; My Work tells an Admin to
"ask an admin"; `/alerts` shows raw cron/timestamps with no asset name, sort or filter.
**Approach.** `list_assets(sort="updated_at")` excluding `source IS NOT NULL` rows for
Recent; shared heatmap partial linking to `/risk-management?likelihood=&impact=`; badge links;
type chips with counts; alert form from 0.5 with history and delivery links.

### 5.4 Compliance pages (theme 9)
Framework detail is a 150-row unfiltered dump (15.8k px) with lexical requirement order
(A.5.1, A.5.10, … A.5.2) and N/A rows shown as gaps; no way to map a control to a gap; SoA is
93 separate unlabeled forms with no bulk apply; control tests capture only pass/fail with no
visible history; board pack loses narrative text across its forms; approvals show raw JSON
with no diff, no reject reason, raw enums; questionnaire metadata and campaign questions
cannot be edited, CSV import gives no feedback and always reads column 0, answers cannot be
cleared, "Needs review" cannot be acknowledged. **Approach.** Table toolbar with status chips,
text filter and "gaps only"; natural sort in `framework_service`; explicit N/A branch; per-gap
"Map control…" using the combobox; SoA as one form with bulk apply and htmx row save keeping
scroll position; control test form with date/notes/evidence + history table; field-level
diff table + reason textarea on approvals; edit routes, column mapping, searchable answer
mapping, "mark reviewed" in the answer library.

### 5.5 Import and export (theme 10)
Preview/result rendered as raw JSON; file chosen twice; duplicates and unresolved owners
silent; only five columns documented; export page is a second, worse export surface.
**Approach.** `imports/_preview.html` rendered by `routers/imports.py` for `HX-Request`
(summary, sample table, errors, duplicate/owner warnings, Commit button with a server-side
upload token); documented columns + template download; reject self-edges; fold `/exports`
into the list-page export buttons.

### 5.6 Graph and Org Views (theme 11)
Whole-org graph picks the "500 most recent" assets so one import replaces everyone's graph;
174 overlapping labels at 0.46 zoom; static 17-type legend (missing Answer); depth change and
link creation destroy the graph; depth 3 on a hub silently truncates to the same 501 nodes;
no zoom/fit/search/export, no keyboard access or text alternative; org chart renders ~3px
labels in a mostly empty 680px canvas with no fit control. **Approach.** Legend built from
loaded nodes with counts and `aria-pressed`; `cy.fit` after filtering; label ellipsis +
`min-zoomed-font-size`; `graph/_toolbar.html` (zoom ±, Fit, Reset, Centre, find box, PNG
export); in-place depth change; truncation notice; a text "Neighbours" list under the canvas
for keyboard/screen readers; org tree auto-fit with readable labels and tab semantics.

---

## Role-appropriate controls (cross-cutting, lands with Phase 3)
Viewer/auditor see Export CSV (SoA), Audit Report PDF, alert Delete, access-log retention
form and "+ add" evidence links that end in 403; the 403 page drops the nav. Gate each with
`has_perm` in the templates (the pattern already used for New/Edit/Delete); decide whether
viewers may export lists (per-asset PDFs yes today, lists no).

## Out of scope for this UI plan (backend/product, tracked in the findings register)
Per-user notification read model (schema), login throttle keyed per IP, PDF export filter/cap,
sample-data enrichment (duplicate SOC 2 / ISO frameworks), connector metadata merge instead
of replace, HRIS/SCIM person matching, ticket sync-back honouring the CAPA gate, token
IP-allowlist behind the nginx proxy (`X-Forwarded-For`), OpenAPI schema hygiene, natural-sort
and deterministic ordering inside services, `owner_id` vs `owns`-edge data model unification.

---

## Verification

1. **Automated (pytest)**: template grep forbids `on[a-z]+=`/`hx-on`; HTML error pages for
   404/403/422/429/500 on page routes and JSON on `/api/*`; `GET /assets?type=foo` → 400
   page not 500; `/api/exports/assets?status=` → 200; attachment create via the page path;
   notification read is per-user; flash rendered after create/edit; `&#9660;` absent from
   `/risk-management`; `kind=file` attachment with an absolute path rejected; bulk import of a
   gated type yields a pending change; `POST /settings/mfa/begin` refused when MFA is enabled.
2. **Browser (Playwright, headless Chromium at `~/.cache/ms-playwright/chromium-1208`)**:
   re-run `crawl.py admin` and assert `scroll_w == inner_w` at 1440px and no horizontal
   overflow at 390px; re-run the role crawls; a scripted pass of the 10 usability-plan tasks
   (`usability_testing_plan.md` §5) asserting that delete prompts a confirm, Advanced
   toggles, Save search works, owner can be picked by keyboard, Link Mode works, Remove
   relationship updates in place, Add URL attachment succeeds, Export downloads by default,
   an alert can be created from the UI, and every write shows a flash.
3. **Manual**: screenshot diff of the 30 key routes before/after Phase 1–2; keyboard-only
   walkthrough of create-asset → add-relationship → delete; 1280px laptop and 390px phone
   check of nav, admin menu, and the risk register.
