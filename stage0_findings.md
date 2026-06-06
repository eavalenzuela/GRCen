# GRCen Stage-0 Expert Pass — Findings
Instance: https://grc.eevn.io · Method: scripted web + API walkthrough of the 10 task scenarios.
Severity (Nielsen): 4 catastrophic · 3 major · 2 minor · 1 cosmetic.

## Resolution status (updated post-pass)
- **F1 [sev3] FIXED** (commit d7c2453): the inline Add-Relationship form was doubly broken (JSON
  search swapped as raw text + form-encoded POST -> silent 422). Replaced with relationship_form.js
  (live search, clickable pick, JSON POST, visible errors). Verified with a real headless-browser
  click-through: error path shows a message, search→pick sets the target, submit creates the link.
- **F4 [sev4] FIXED** (commit fe95dfe): type-consistent sort in get_risk_register + regression test.
  Re-verified live — creating a scored risk now keeps /risk-management at 200 (was 500).
- **F5 [sev3] FIXED**: seed_data.py enrich() scores all 16 risks across the heatmap (some overdue).
- **F7 [sev2] FIXED**: seed_data.py enrich() keyword-tags 46 assets (pci/gdpr/pii/crown-jewel/soc2).
- **F2 [sev3] FIXED** (commit dbdf193): relationship_type input now offers a datalist of existing
  types (suggestions only, any new type still allowed). Verified live: 47 suggestions rendered.
- **F3 [sev2] FIXED** (dbdf193): interactive create no longer rewrites owns→manages; uses the type
  as entered. Verified live (owns→Person stays "owns") + regression test. Bulk import left as-is.
- **F6 [sev2] FIXED** (dbdf193): invalid attachment kind now returns 400, not 500. Verified live + test.
- **ALL 7 FINDINGS RESOLVED. Full suite: 601 passed.**

## F1 [SEV 3] Detail-page "Add Relationship" form silently fails (422)
- Task 2. The inline form on /assets/{id} htmx-POSTs **form-urlencoded** to /api/relationships/,
  but that route requires a JSON body (RelationshipCreate). Result: HTTP 422, and htmx shows
  NO error (hx-on::after-request reloads only on success) — user clicks "Add", nothing happens.
- Verified: form-encoded POST -> 422; identical JSON POST -> 201. No `json-enc` htmx ext is
  configured; htmx loaded plain. Tests only POST JSON to the API, so the form path is untested.
- Mitigation that exists: graph drag-to-link (graph.js) posts JSON correctly and WORKS, as does
  the REST API. So linking is possible, just not via the most discoverable affordance.
- Fix options: add `hx-ext="json-enc"` to the form (+ load the ext), OR accept Form() on the
  route, OR swap the form to a small JS fetch posting JSON (like graph.js). Add a UI test for the
  form path. Also surface htmx errors to the user.

## F2 [SEV 3] relationship_type is unguided free text
- Task 2. `<input name="relationship_type" placeholder="Relationship type">` — no list/autocomplete
  of existing types. Users will coin variants ("owns" vs "owned by" vs "manages"), fragmenting the
  graph vocabulary that is the product's core value. (Design intends no imposed paradigm — fine —
  but *suggesting* existing types preserves consistency without imposing.)
- Recommendation: autocomplete from existing relationship_type values.

## F3 [SEV 2] Silent owns->manages rewrite
- Task 2. create_relationship silently rewrites relationship_type "owns" -> "manages" when target
  is a Person. A user who types "owns" sees "manages" with no explanation. Helpful intent, but
  surprising and undocumented. Recommendation: note the normalization in the UI, or surface it.

## OPEN TO CHECK: imports/index.html + alerts/notifications.html also use hx-post="/api/" — same bug?

## F4 [SEV 4] Creating a scored risk crashes the entire Risk Management page (500)
- Task 5/6. risk_service.get_risk_register sorts: `risks.sort(key=lambda r: (r.get(sort_key) or ""))`
  with default sort_key="score". Score is stored as `score or 0`; when a risk's score is 0/None the
  `or ""` coerces the key to "" (str), while scored risks yield int — mixed str/int → TypeError, 500.
- TRIGGER IS THE NORMAL TASK 5 ACTION: create a risk with likelihood+impact while any unscored risk
  exists. Reproduced: created risk (score 16) -> /risk-management 500; deleted it -> /risk-management 200.
- Org-wide + persistent (everyone in the org sees 500 until the data is changed). Dashboard + /frameworks
  unaffected (they don't hit this sort).
- Fix: make the sort key type-consistent — e.g. sort numeric "score" with `(r.get("score") or 0)` and
  the text columns with `(r.get(k) or "")`; don't funnel an int field through `or ""`. Add a regression
  test with a mix of scored and unscored risks.

## F5 [SEV 3] Seed risks have no likelihood/impact -> empty heatmap + arms F4
- Tasks 5/6. 16/17 seeded risks have likelihood=None, impact=None (assets.csv carries no custom fields),
  so they're unscored (score 0), NOT positioned on the 5x5 heatmap. "Find your top risks" starts from an
  effectively unpopulated heatmap, and the first scored risk a participant adds triggers F4.
- (Corrects an earlier claim that the seed "lights up the heatmap" — the mitigated_by rollups exist, but
  the risks aren't placed.) Fix: seed risks WITH likelihood/impact (the assets_with_custom_fields.json
  variant likely has them), or extend seed_data.py to set risk scoring metadata.

## F6 [SEV 2] Invalid attachment "kind" from a form returns 500, not 400
- Task 7. relationship_pages.py:69 `AttachmentKind(str(form.get("kind","url")))` raises ValueError ->
  500 on any kind outside {url, document}. The UI <select> constrains input so real users rarely hit it,
  but bad/replayed input shouldn't 500. Fix: validate and return 400, or default invalid -> a safe kind.
- Happy path verified: kind=url -> 302, evidence attached fine.

## F7 [SEV 2] No seeded tags -> Task 9 tag-filter can't be exercised
- Task 9. GET /api/tags/ returns []. Sample assets carry no tags, so "find every asset tagged 'pci'"
  has no data. Saved-search creation itself works (POST /api/saved-searches/ -> 201) and the /assets
  filter form is a clean shareable GET. Fix: seed tags on a subset of assets (pci, gdpr, crown-jewel...).

## PASSES (functional, via web + API)
- T1 Onboarding: dashboard populated, clear cards (Assets/Asset Types/Alerts/Reviews/Answer Library/
  Heatmap/Recent), comprehensive nav. (Known onboarding friction = CLI-only first admin, documented.)
- T2 Create asset: web form works (System created, 302). Linking works via GRAPH drag-to-link (JSON) and
  REST API — but NOT the detail-page form (see F1).
- T3 Graph: /api/graph/{id} returns subgraph 200; drag-to-link posts JSON correctly. Visual feel = needs human.
- T4 Import: CSV preview (2 valid) + execute (created 2) via multipart — works cleanly.
- T5 Risk: create works, score auto-computed (likely×major=16). BUT register then 500s (F4); heatmap empty (F5).
- T6 Frameworks: detail 200, gap-report.csv valid, gap-report.pdf renders (WeasyPrint OK, 14KB).
- T7 Evidence: URL evidence attaches (302) with a valid kind. (F6 on invalid kind.)
- T8 Questionnaire: fill/map-to-library 302, export.csv + export.pdf (18KB) both render. Works.
- T9 Saved search: create 201, filter form is GET (shareable). Tag filter blocked by F7 (no tags).
- T10 Approvals: gated Policy-delete -> approve 302 -> policy actually deleted. Workflow works end-to-end.

## CROSS-CUTTING / DX
- CSRF flow: page POSTs need a session-cookie-backed csrf_token (GET first). Fine for browsers; a gotcha
  for scripted/form clients. Login itself uses the same flow.
- Login rate limit (2s/IP) returns 429 that reads like a lockout — no "slow down" messaging (noted earlier).
- htmx error feedback: forms that hx-post and fail (F1) show the user NOTHING. Consider a global htmx
  responseError handler to surface 4xx/5xx.
