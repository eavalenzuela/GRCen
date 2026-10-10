# GRCen UI audit — verified findings register

457 findings, each independently verified. Severity = verifier's severity.

## F298 [sev 4] [not-functional] CSP nonce policy silently kills every inline onclick/onsubmit handler (Advanced filters, Save search, select-all, tag quick-add, Link Mode, confirm() guards)
- Route: `/assets (also /risk-management, /graph/{id}, /assets/new, /assets/{id})` · area: a11y-keyboard
- Actual: The middleware emits script-src 'self' 'nonce-<random>' with no 'unsafe-hashes'/'unsafe-inline', so the browser refuses every inline event-handler attribute. 29 inline handlers across 24 templates are dead; nothing is shown to the user. Confirmed via console messages in probe5.json and csp_check output (advanced_after_mouse_click = 'none', risk_select_all.checked_rows = 0, save_search.dialogs = [], link_mode.text unchanged, tag_quickadd_mouse.tags = '').
- Expected: Each control performs its action by mouse and keyboard; the CSP and the markup agree.
- Code: src/grcen/middleware.py:84-93 (CSP build); inline handlers in src/grcen/templates/assets/list.html:28,153 (toggleAdvanced, bulk-select-all), partials/saved_searches.html:21,32, assets/form.html:77, risks/index.html:187, graph/view.html:18, partials/bulk_actions.html:30 and 17 other templates (grep o
- Fix: Move every inline handler into nonce'd <script> blocks or the static JS files using addEventListener (data-action attributes + one delegated listener in a small ui.js), then keep the strict CSP. As an interim unblock add 'unsafe-hashes' plus per-handler sha256 hashes, but the long-term fix is removing inline handlers. Add a Playwright smoke test that fails on any 'violates the following Content Se
- Verifier (confirmed): Verified read-only (plan mode blocked writing repro scripts / logging in), but the mechanism is deterministic and does not need a browser to adjudicate:

1. Live header from the running app (`curl -sk -D - https://localhost:8443/login`): `content-security-policy: default-src 'self'; script-src 'self' 'nonce-Z9CwS9...'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connec

## F299 [sev 4] [bug] Delete asset and Bulk Apply execute immediately with no confirmation because the inline confirm() guard is blocked by CSP
- Route: `/assets/{id} (Delete button); /assets?type=… register bulk form; /risk-management bulk form` · area: a11y-keyboard
- Actual: onsubmit="return confirm('Delete this asset?')" never runs; the form posts immediately. probe5.json delete_asset: dialogs = [], landed = /assets, still_exists = [].
- Expected: A destructive, irreversible action asks for confirmation (WCAG 3.3.4 Error Prevention) before the form submits.
- Code: src/grcen/templates/assets/detail.html:33-37 (onsubmit confirm); src/grcen/templates/partials/bulk_actions.html:29-30 (onclick confirm); src/grcen/middleware.py:84-93
- Fix: Replace the inline guards with a delegated listener (e.g. form[data-confirm] -> submit event -> confirm(form.dataset.confirm)) in a nonce'd script, or convert Delete to hx-post with hx-confirm, or render a proper accessible modal <dialog>. Add a server-side 'confirm=1' field check as defence in depth so deletes without the confirmation token are rejected.
- Verifier (confirmed): Plan mode restricted me to read-only actions, so I did not create/delete an asset myself; the verdict rests on (a) the live response header, (b) the CSP spec, (c) code inspection, and (d) the prior tester's recorded evidence, which together make the behaviour deterministic rather than environmental.

1. Live server header (curl -sk -D - https://localhost:8443/login): `Content-Security-Policy: defa

## F300 [sev 4] [not-functional] Owner/Manager cannot be set on New/Edit Asset: autocomplete result rows are dead (inline onclick blocked) and not keyboard operable
- Route: `/assets/new and /assets/{id}/edit` · area: a11y-keyboard
- Actual: selectOwner() is wired via onclick in server-rendered HTML and is blocked by CSP; the list has no combobox/listbox semantics, no aria-expanded, no keyboard handling, no Escape to close. probe2.json owner_results.tabbable=0, comboboxAttrs all null; probe5.json owner_pick_mouse.owner_id=''.
- Expected: Owner is pickable with mouse or keyboard (combobox pattern: ArrowDown/Enter, Escape closes) and the choice is announced.
- Code: src/grcen/routers/asset_pages.py:795-801 (renders <div class=autocomplete-item onclick=selectOwner(...)>); src/grcen/templates/assets/form.html:44-57 and 150-160 (owner_search / selectOwner); src/grcen/static/css/style.css .autocomplete-results
- Fix: Render results as <ul role=listbox><li role=option id=…> (or <button type=button>) and bind selection with a delegated listener in the page's nonce'd script (data-id/data-name are already there). Give the input role=combobox, aria-autocomplete=list, aria-expanded, aria-controls=owner-results, aria-activedescendant; support ArrowUp/Down, Enter, Escape; close the list on blur/Escape; announce 'N res
- Verifier (confirmed): Root cause is deterministic in the code, so this cannot be a flaky observation. The owner autocomplete fragment is server-rendered by src/grcen/routers/asset_pages.py (owner_search, ~L779-806) as `<div class="autocomplete-item" data-id=.. data-name=.. onclick="selectOwner(this.dataset.id, this.dataset.name)">`. The CSP set in src/grcen/middleware.py L85-93 is `script-src 'self' 'nonce-<per-request

## F212 [sev 4] [bug] CSP blocks every inline confirm() so all destructive admin actions execute with no confirmation
- Route: `/admin/users (also /admin/tokens, /tokens, /admin/oauth-clients, /admin/webhooks, /admin/sessions, /admin/content-packs, /settings MFA disable)` · area: admin-settings
- Actual: onsubmit="return confirm(...)" is an inline handler, which the nonce-only CSP forbids; the browser logs an error and submits the form unconditionally. Dialog listener recorded zero dialogs across all scripts.
- Expected: A confirmation dialog before any delete/revoke/uninstall; the action aborts on Cancel.
- Code: src/grcen/middleware.py:87 (CSP script-src nonce only); src/grcen/templates/admin/users.html:44, tokens.html:75, oauth_clients.html:50, webhooks.html:37, sessions.html:33, content_packs.html:84, orgs.html:29, settings.html:67, tokens/my_tokens.html:98
- Fix: Remove all inline onsubmit/onclick/onchange attributes; add one nonce'd script in base.html that delegates on data-confirm="..." (document.addEventListener('submit', ...)). Alternatively add 'unsafe-hashes' + hashes, but delegation is cleaner. Consider a server-rendered confirm page for pack uninstall.
- Verifier (confirmed): Reproduced independently as admin04 (read-only probe; no destructive action was performed). Live header on https://localhost:8443 is `script-src 'self' 'nonce-...'` with no 'unsafe-inline'/'unsafe-hashes', and there is no delegation script in base.html or src/grcen/static/js/ (only htmx's own hx-confirm works). In Chromium, for every `form[onsubmit]` on /admin/users (23 forms), /admin/content-pack

## F164 [sev 4] [bug] Deleting an Answer from its detail page has no confirmation (same CSP cause)
- Route: `/assets/{id} (Answer asset)` · area: answers-questionnaires
- Actual: Deleted instantly with dialogs dismissed; GET /assets/{id} -> 404 afterwards. Redirect lands on /assets, not /answers.
- Expected: Confirmation before deleting a library answer; cancel keeps it.
- Code: src/grcen/templates/assets/detail.html:36 (onsubmit confirm); src/grcen/routers/asset_pages.py delete handler redirects to /assets regardless of type.
- Fix: Same global data-confirm handler as above; after deleting an Answer-type asset redirect to /answers.
- Verifier (confirmed): Plan mode was active for this session, so I did not perform a fresh click-through (it would create and delete an asset); the confirmation rests on the live header, the source, and a re-read of the original tester's script and its logic. (1) Live CSP header on https://localhost:8443/login via curl: `script-src 'self' 'nonce-…'` with no 'unsafe-inline' and no 'unsafe-hashes'. Per the CSP spec, nonce

## F060 [sev 4] [bug] Asset Delete happens with no confirmation — the confirm() is blocked by CSP
- Route: `/assets/{id}` · area: asset-crud
- Actual: The onsubmit="return confirm(...)" inline handler is blocked by the nonce-only CSP, so the form submits immediately. In the test, a dialog handler set to 'dismiss' still resulted in the asset being deleted (landed on /assets). Same defect on the relationship evidence page Delete buttons (onsubmit confirm, no dialog shown, 302 to /evidence).
- Expected: A confirmation dialog ('Delete this asset?') that can be cancelled before a destructive, cascading delete (relationships, attachments, watchers).
- Code: src/grcen/templates/assets/detail.html:34-35 (onsubmit confirm), src/grcen/templates/relationships/evidence.html:44-46, CSP in src/grcen/middleware.py:87
- Fix: Remove inline onsubmit handlers; use hx-confirm (which already works on the Remove buttons) or a nonce'd <script> that attaches a submit listener to .btn-danger forms. Longer term, replace window.confirm with a styled confirm that names the asset and what cascades.
- Verifier (confirmed): Reproduced the mechanism independently without mutating state (this session was placed in plan mode, so I did not run a Playwright script that would log in and delete an asset; everything below is from read-only observation).

1. Live header: `curl -sk -D - https://localhost:8443/login` returns `content-security-policy: default-src 'self'; script-src 'self' 'nonce-…'; style-src 'self' 'unsafe-inli

## F061 [sev 4] [not-functional] Owner autocomplete result click does nothing (inline onclick blocked by CSP) — owner cannot be set
- Route: `/assets/new, /assets/{id}/edit` · area: asset-crud
- Actual: The /api/owner-search fragment uses onclick="selectOwner(...)" which the nonce-only CSP blocks. Reproduced on both create and edit forms. The only way an owner got set in testing was by scripting the hidden field.
- Expected: Clicking a result fills the field with the person's name, sets the hidden owner_id, closes the list, and the asset saves with that owner.
- Code: src/grcen/routers/asset_pages.py:797-801 (onclick in fragment), src/grcen/templates/assets/form.html:176-185 (selectOwner, never invoked)
- Fix: Drop the onclick attribute from the fragment and delegate in the nonce'd script: document.getElementById('owner-results').addEventListener('click', e => { const it = e.target.closest('.autocomplete-item[data-id]'); if (it) selectOwner(it.dataset.id, it.dataset.name); }). Same fix for the tag chips below.
- Verifier (confirmed): Plan mode was active for this run, so I could not drive the browser (login/asset creation are writes). I verified the finding through read-only means instead, and the mechanism is deterministic enough that no browser can behave differently:

1. Live CSP header (curl GET https://localhost:8443/login, 200): `content-security-policy: default-src 'self'; script-src 'self' 'nonce-…'; style-src 'self' '

## F035 [sev 4] [not-functional] CSP blocks every inline onclick/onsubmit handler: Advanced toggle, select-all, Save search, saved-search delete, bulk confirm, tag-delete confirm are all dead
- Route: `/assets (also /registers/{slug}, /tags)` · area: assets-list
- Actual: Every control implemented as an inline onclick/onsubmit attribute silently does nothing; the response CSP is script-src 'self' 'nonce-...' which forbids inline event handlers (hashes/nonces don't cover attributes). toggleAdvanced is defined but never invoked. Users can only reach advanced filters by crafting URLs.
- Expected: Advanced panel toggles, select-all ticks all rows, Save this search prompts for a name, confirm dialogs guard destructive buttons.
- Code: src/grcen/middleware.py:87 (CSP script-src); src/grcen/templates/assets/list.html:40 (onclick=toggleAdvanced), :168 (select-all onclick); src/grcen/templates/partials/saved_searches.html:23,34; src/grcen/templates/partials/bulk_actions.html:32; src/grcen/templates/tags/index.html:47
- Fix: Remove all inline handler attributes and bind them in the existing nonce'd <script> block (addEventListener on data-* hooks), or in static/js. Add a Playwright/console-error test that fails on any CSP violation so this cannot regress.
- Verifier (confirmed): Plan mode was active for this agent, so I did not run a new Playwright session (it would write files and create a server session). I verified the defect by read-only means, which is sufficient here because the mechanism is deterministic browser behaviour, not something timing- or data-dependent:

1. Live header, observed directly: `curl -sk -D - https://localhost:8443/login` returns `content-secur

## F036 [sev 4] [bug] Tag Delete executes immediately with no confirmation (confirm is CSP-blocked) - deleted the seeded 'crown-jewel' tag from 20 assets in one click
- Route: `/tags` · area: assets-list
- Actual: The only guard is onsubmit="return confirm(...)" which the CSP blocks, so the POST fires on first click. During testing this removed 'crown-jewel' from 20 seeded assets; the audit row records only a count, so there is no way to recover from the UI.
- Expected: A confirmation step (ideally a server-rendered confirm page or an undo), since this strips the tag from every asset in the org.
- Code: src/grcen/templates/tags/index.html:46-50; src/grcen/routers/tag_pages.py:77-96
- Fix: Make delete a two-step server flow (GET /tags/{name}/delete confirm page listing affected assets, then POST), and record the affected asset ids in the audit event so a delete can be reverted.
- Verifier (confirmed): Reproduced as admin04 on /tags with the delete POST intercepted and aborted client-side (page.route -> abort) so no tag was actually removed. Clicking the first red Delete button (gdpr) produced no dialog (dialog list empty), immediately issued POST https://localhost:8443/tags/gdpr/delete, and the browser console logged: "Executing inline event handler violates the following Content Security Polic

## G5-1 [sev 4] [bug] Any authenticated user can read arbitrary server files via a kind=file attachment with an absolute path
- Route: `POST /api/assets/{id}/attachments/ then GET /api/assets/{id}/attachments/{att_id}/download` · area: gap-api-machine-client
- Actual: The download endpoint does os.path.isfile(att.url_or_path) and opens the path directly with no containment check, and the JSON create endpoint accepts any url_or_path for kind=file. Live result: downloaded /etc/passwd ('root:x:0:0:root:/root:/bin/bash...'). This is an arbitrary-file-read/LFI: any viewer can read app secrets, other tenants' uploaded evidence, or OS files.
- Expected: A 'file' attachment should only ever reference a file the server itself wrote under UPLOAD_DIR; download must refuse to stream a path outside that directory, and the create API should reject an absolute/outside path.
- Code: src/grcen/routers/attachments.py:149 download_file (no path-containment check) and :90 create_attachment (accepts arbitrary url_or_path for kind=file)
- Fix: In attachments.py download_file, resolve realpath and require it to start with realpath(UPLOAD_DIR)+os.sep (the same check _write_upload already does for uploads). In create_attachment, reject kind=file where url_or_path is not a server-managed upload path; let only /upload set a file path.
- Verifier (confirmed): Plan mode was active for this session, so I was restricted to read-only actions and could not create the malicious attachment on the live server or write a new verify script; the judgement rests on (a) the source and (b) the prior tester's raw capture. The source is unambiguous and deterministic: the only gates before `open()` are tenant/owner match, kind==file, and isfile(); nothing compares the 

## G5-2 [sev 4] [bug] Bulk import bypasses the approval-gating workflow that the direct create/update API enforces
- Route: `POST /api/imports/assets/bulk (and /api/imports/assets/execute)` · area: gap-api-machine-client
- Actual: execute_asset_import INSERTs rows directly and never calls workflow_service.requires_approval, so the entire approval control is bypassed by anyone with 'import' permission. Confirmed live: bulk-created Vendor appeared with no pending change while direct POST was correctly gated.
- Expected: A write to a gated asset type should be queued as a pending_change regardless of which endpoint performs it; bulk import of a gated type should either 202/queue each row or be refused.
- Code: src/grcen/services/import_service.py:67 execute_asset_import (direct INSERT, no gating) vs src/grcen/routers/assets.py create_asset which checks workflow_service.requires_approval
- Fix: Route bulk/execute imports through the same requires_approval/submit path per row (or reject imports for currently-gated types with a clear error) so gating cannot be side-stepped via /api/imports.
- Verifier (confirmed): Plan mode was active for this session, so I did not execute a live write against the app (creating vendors would mutate the instance DB). Verification is by static inspection of the read-only repo plus the prior tester's scripts/evidence, and the mechanism is deterministic enough that a live run would not change the result.

What the code shows: every write endpoint in src/grcen/routers/imports.py

## G4-1 [sev 4] [bug] Every connector re-sync overwrites the whole metadata blob, wiping the Jira ticket link, root cause, CAPA and hand-set statuses
- Route: `/assets/{finding_id} after POST /api/sync/vulns (also /assets/{person_id} after POST /api/sync/hris)` · area: gap-ingress-sync-to-ui
- Actual: After the re-sync root_cause and corrective_action_plan are None, finding_status reverted from risk_accepted to in_remediation, and the Jira link (external_ticket SEC-101, external_ticket_url, external_ticket_status 'Done') is gone entirely, so the finding is silently unlinked from its remediation ticket. Same for People: a hand-edited title and description on an HRIS-synced Person reverted to the roster values. No warning, no audit entry.
- Expected: GRCen-owned workflow fields (root_cause, corrective_action_plan, verified_by, external_ticket*, finding_status set by a human or by the ticketing sync-back) survive a scanner re-sync; at most the scanner-owned fields (severity, cvss, last_seen) are refreshed, and the user is warned on the Edit form 
- Code: src/grcen/services/external_sync.py:47-58 (_ASSET_UPSERT, 'metadata = EXCLUDED.metadata'); src/grcen/services/vuln_sync.py:216-221; src/grcen/services/hris_sync.py:256-261; src/grcen/services/ticketing_service.py:216-225 stamps fields that the upsert then discards
- Fix: In external_sync._ASSET_UPSERT merge server-side (metadata = coalesce(assets.metadata,'{}') || EXCLUDED.metadata) and have each connector pass only the keys it owns; additionally protect a GRCen-owned allowlist (external_ticket*, root_cause, corrective_action_plan, verified_*, and a human-set finding_status unless the source reports a terminal status). Show a 'Synced from <source> — fields X will 
- Verifier (confirmed): Plan mode was active for this session, so I could not re-POST to /api/sync/* or drive the Edit form myself; verification is by static code analysis plus the prior tester's artifacts, which are internally consistent with the code. Evidence: (1) the SQL above is unconditional — on conflict the entire `metadata` jsonb is replaced by the connector payload, and neither `vuln_sync` nor `hris_sync` reads

## F191 [sev 4] [bug] Asset Delete button deletes immediately with no confirmation because CSP blocks the inline onsubmit confirm()
- Route: `/assets/{id} (Delete form) and ~20 other templates using inline onsubmit/onclick confirm()` · area: import-export-approvals
- Actual: The onsubmit="return confirm('Delete this asset?')" handler is blocked by the nonce-based CSP, so the form submits unguarded. The same pattern guards user delete, org delete ('Delete {{slug}} and all its data?'), webhook delete, tag delete, content-pack uninstall, token/session revoke, questionnaire delete, MFA disable, bulk 'Apply to all selected', etc., so none of those confirmations fire either.
- Expected: A confirmation prompt before an irreversible delete (and ideally an in-app modal rather than window.confirm).
- Code: src/grcen/templates/assets/detail.html:34-35; src/grcen/middleware.py:87 (script-src 'self' 'nonce-...'); other templates: admin/users.html:44, admin/orgs.html:29, admin/webhooks.html:37, tags/index.html:47, admin/content_packs.html:84, partials/bulk_actions.html:32, risks/index.html:289, settings.h
- Fix: Replace all inline handlers with a single nonce'd script (or a static js file) that attaches to forms carrying data-confirm="..." (document.addEventListener('submit', ...)); add a test that greps templates for on*= attributes so this cannot regress.
- Verifier (confirmed): Reproduced as admin15 at https://localhost:8443 without actually deleting anything (I aborted any */delete POST via a Playwright route and preventDefault'ed submit at document level). Live response header: `content-security-policy: default-src 'self'; script-src 'self' 'nonce-...'; style-src 'self' 'unsafe-inline'; ...` -- no 'unsafe-inline' and no 'unsafe-hashes' on script-src, so per the CSP spe

## F320 [sev 4] [bug] Delete asset has no confirmation: inline onsubmit confirm() is blocked by CSP, asset is deleted on a single click
- Route: `/assets/{id}` · area: practitioner-journeys
- Actual: The form submits immediately; the clone (f2668fa9...) was gone (404) with zero dialogs captured by the dialog listener.
- Expected: A confirmation dialog ('Delete this asset?') before an irreversible delete.
- Code: src/grcen/middleware.py:85-93 (script-src 'self' 'nonce-...' with no 'unsafe-hashes'/'unsafe-inline') + src/grcen/templates/assets/detail.html:34-35 (onsubmit="return confirm(...)"). Same root cause hits every inline handler in 24 templates.
- Fix: Remove all inline on* handlers and move them into nonce'd <script> blocks or static JS with addEventListener (e.g. a data-confirm attribute handled by one delegated listener in static/js). Add a test that greps templates for 'on[a-z]+=' so this cannot regress. Same fix resolves findings 2-8 below.
- Verifier (confirmed): Reproduced the mechanism independently without destroying data (session was in plan/read-only mode, so I did not clone+delete an asset; the behaviour is deterministic given the two facts below). (1) Live server header on https://localhost:8443: `Content-Security-Policy: default-src 'self'; script-src 'self' 'nonce-...'; style-src 'self' 'unsafe-inline'; ...` -- no 'unsafe-inline' / 'unsafe-hashes'

## F109 [sev 4] [bug] Delete on asset detail page runs with no confirmation (CSP blocks the inline onsubmit confirm)
- Route: `/assets/{id} (risk and finding detail)` · area: risk
- Actual: Browser blocks the inline onsubmit handler because the CSP is script-src 'self' 'nonce-...' with no 'unsafe-inline'/'unsafe-hashes'; the form submits unconditionally. Playwright dialog listener recorded no dialog; asset removed.
- Expected: A confirmation ('Delete this asset?') before an irreversible delete.
- Code: src/grcen/templates/assets/detail.html:35 (onsubmit="return confirm(...)"); src/grcen/middleware.py:87 (CSP script-src)
- Fix: Remove all inline event handlers; bind confirm/select-all/save-search behaviour from a nonce'd <script> (or static JS) via data-confirm attributes. Add a test that greps templates for on*= attributes.
- Verifier (confirmed): Verified against the live server and source (plan mode prevented me from executing the delete in a browser, so I did not create/destroy an asset; the finding does not depend on that step). Live header from https://localhost:8443/login: `script-src 'self' 'nonce-...'` with no 'unsafe-inline' and no 'unsafe-hashes'. Per CSP Level 3, nonces whitelist only <script> elements; inline event-handler attri

## F244 [sev 4] [bug] CSP blocks every inline onsubmit/onclick, so destructive actions run with NO confirmation (asset delete, questionnaire delete, bulk apply, tag delete, token revoke, MFA disable)
- Route: `/assets/{id} (Delete), /questionnaires/{id} (Delete questionnaire), /risk-management (Bulk Apply)` · area: roles
- Actual: The inline handler is silently blocked by the per-request-nonce CSP (no 'unsafe-inline'/'unsafe-hashes'), so the form submits straight away. The clone of a Person was deleted with zero prompts; a questionnaire was deleted with zero prompts.
- Expected: A confirmation dialog before each destructive action (the template clearly intends one).
- Code: src/grcen/middleware.py:84-93 (CSP script-src 'self' 'nonce-…'); src/grcen/templates/assets/detail.html:35; templates/answers/questionnaire_detail.html:55; templates/partials/bulk_actions.html:32; templates/risks/index.html:289; templates/tags/index.html:47; templates/tokens/my_tokens.html:98; templ
- Fix: Remove all inline on* attributes and attach handlers from a static JS file (e.g. data-confirm="…" + a delegated submit listener in /static/js/app.js), or use htmx hx-confirm for forms. Alternatively add 'unsafe-hashes' with hashes, but moving to data-attributes is cleaner and keeps the strict CSP.
- Verifier (confirmed): Reproduced the mechanism directly (read-only; plan mode was active so I did not perform the actual deletes). Live header on https://localhost:8443/login: `content-security-policy: default-src 'self'; script-src 'self' 'nonce-…'; style-src 'self' 'unsafe-inline'; …` — no 'unsafe-inline' or 'unsafe-hashes' in script-src. Per CSP3, nonces never authorize inline event-handler attributes, so every on* 

## F267 [sev 4] [bug] CSP blocks every inline onsubmit confirm(): assets, users, tags, MFA, tokens delete with NO confirmation
- Route: `/assets/{id} (also /admin/users, /tags, /settings MFA disable, /tokens, /admin/webhooks, /admin/sessions, /admin/oauth-clients, /relationships/{id}/evidence)` · area: visual-design
- Actual: The response CSP is script-src 'self' 'nonce-...' with no 'unsafe-hashes'/'unsafe-inline', so all 18 onsubmit="return confirm(...)" handlers (12 templates) are silently blocked; the form submits straight through and the asset was deleted with no prompt.
- Expected: A confirmation ('Delete this asset?') before any destructive action, as the template intends.
- Code: src/grcen/middleware.py:87 (CSP script-src); src/grcen/templates/assets/detail.html:35; admin/users.html, tags/index.html:47, settings.html:67, tokens/my_tokens.html, admin/tokens.html, admin/sessions.html, admin/webhooks.html, admin/oauth_clients.html, admin/orgs.html, relationships/evidence.html, 
- Fix: Remove all inline handlers: add a nonce'd app.js that delegates on data-confirm="..." (and data-toggle, data-select-all) attributes, and ship a single <dialog>-based confirm component instead of window.confirm. Add a test that greps templates for on[a-z]+= to keep it out.
- Verifier (confirmed): Reproduced independently as admin11 against https://localhost:8443, non-destructively: opened an existing asset detail page (/assets/2a65f0ed-9036-4f77-a582-30438b7f835b), registered dialog + console listeners, intercepted and aborted all POSTs via page.route so nothing was actually deleted, then clicked the red Delete button. Result: dialogs=[] (no confirm() ever appeared), the browser issued POS

## F301 [sev 3] [a11y] Add-relationship target search is mouse-only: result rows are unfocusable <div>s with no combobox semantics
- Route: `/assets/{id} (Add Relationship form)` · area: a11y-keyboard
- Actual: relationship_form.js creates <div class=autocomplete-item> with only a click listener; no tabindex, role, aria-activedescendant or key handling. probe3.json rel_results.tabbable=0, rel_after_enter_status shows the pick-a-target error.
- Expected: A keyboard user can move into the results, choose one with Enter, and hear how many results there are (WCAG 2.1.1, 4.1.2, 1.3.1).
- Code: src/grcen/static/js/relationship_form.js:30-45; src/grcen/templates/assets/detail.html:197-212
- Fix: Same combobox/listbox implementation as the owner picker (shared module): role=option items, ArrowUp/Down + Enter selection, Escape to close, aria-live result count, and keep the first result selectable by Enter when only one matches.
- Verifier (confirmed): Verified by reading the shipped code (plan mode was active in this session, so I did not launch a Playwright run or write scripts/screenshots; the mechanism is unambiguous from source and matches the reporter's probe3.json evidence). /home/t3rmit3/gits/GRCen/src/grcen/static/js/relationship_form.js lines 29-41 build each result as `document.createElement('div')` with class `autocomplete-item`, inl

## F302 [sev 3] [a11y] No 'skip to content' link: keyboard users must Tab through 22 nav links + 20 admin-bar links on every page before reaching content
- Route: `/ (all pages via base.html)` · area: a11y-keyboard
- Actual: probe1.json skip=false on all 16 routes, mainId=''; probe2.json new_tab_order_first14 is entirely nav links; new_initial_focus is BODY.
- Expected: A visible-on-focus 'Skip to main content' link as the first focusable element (WCAG 2.4.1 Bypass Blocks) and <main id=main tabindex=-1>.
- Code: src/grcen/templates/base.html:10-12 (body/nav) and :95 (<main class=container>); src/grcen/static/css/style.css (no .skip-link / sr-only utility)
- Fix: Add <a class="skip-link" href="#main">Skip to main content</a> as the first child of <body>, give <main id="main" tabindex="-1">, add a .skip-link rule that is visually hidden until :focus. Also mark the current page link with aria-current=page and consider aria-label="Primary"/"Admin" on the two <nav>s.
- Verifier (confirmed): Verified from the rendered template source and the prior tester's probe output/screenshot (plan mode forbade writing a new verify script, but this finding is fully determined by base.html, which every one of the 61 templates extends). src/grcen/templates/base.html has no skip link anywhere; the first element inside <body> is <nav class="navbar"> (line 12) and the content wrapper is <main class="co

## F303 [sev 3] [bug] htmx actions that return 204 never swap: Remove relationship, Mark Read and Delete alert leave the stale row on screen with focus on a button for a deleted item
- Route: `/assets/{id} (Remove relationship); /notifications (Mark Read); /alerts (Delete)` · area: a11y-keyboard
- Actual: htmx does not swap on HTTP 204 No Content by default, so hx-target="closest tr" hx-swap="outerHTML" never fires; the UI is stale, nothing is announced, and focus remains on the orphan button. probe5.json remove_responses=[204], remove_rows_dom=1, remove_rows_after_reload=0; probe6 markread: 204, row_class_after_dom='unread'.
- Expected: The row is removed/updated immediately, a status message ('Relationship removed') is announced via aria-live, and focus moves to a sensible place (next row's button or the section heading).
- Code: src/grcen/routers/relationships.py:177 (status_code=204); src/grcen/routers/alerts.py:109 and :157 (204); src/grcen/templates/assets/detail.html:176-181 (hx-delete/hx-swap outerHTML); src/grcen/templates/alerts/notifications.html:20-22
- Fix: Either return 200 with an empty body (or the re-rendered row) for htmx requests (check HX-Request header), or set hx-swap="delete" and configure htmx.config.responseHandling to treat 204 as swap. Add a global <div id="status" aria-live="polite"> in base.html and send HX-Trigger / hx-swap-oob messages ('Relationship removed'); move focus with hx-on::after-request to the next actionable element.
- Verifier (confirmed): Mechanism verified from source plus the reporter's captured evidence (this session was placed in plan/read-only mode partway through, so I did not re-run a mutating browser repro; the existing probe output and screenshot were inspected directly).

Code: all three actions use `hx-target="closest tr" hx-swap="outerHTML"` (src/grcen/templates/assets/detail.html:186-189, alerts/notifications.html:20-2

## F304 [sev 3] [ux] Rate-limited login and 404 return bare unstyled text with no title, heading, navigation or way back
- Route: `/login (POST, 429); /assets/{unknown-id} (404)` · area: a11y-keyboard
- Actual: The generic HTTPException handler returns HTMLResponse(exc.detail) for every status except 401/403. probe6 login_429_page: has_form=0, text='Too many login attempts. Please wait.'; notfound: h1=[], links_back=0.
- Expected: Error states render inside the normal layout with a titled page (WCAG 2.4.2), an <h1>, the message in a role=alert, the login form preserved (or a 'try again in N s' link), and a link back to the assets list / dashboard.
- Code: src/grcen/main.py:276-286 (http_exception_handler -> HTMLResponse(content=exc.detail)); src/grcen/rate_limit.py:35 (429 raised from the login dependency before login_submit can render auth/login.html); src/grcen/templates/errors/ (only 403.html exists)
- Fix: Add errors/404.html, errors/429.html and a generic errors/error.html extending base.html; in the handler branch on 'text/html' in Accept for all statuses. For the login 429, catch it in login_submit (or render the login template from the dependency) so the form, a role=alert message and the Retry-After seconds are shown.
- Verifier (confirmed): Verification was done read-only (plan mode was active in this session, so no new Playwright scripts or screenshots could be written to work/verify/): I traced the code paths and inspected the prior tester's screenshots p6-login-429.png and p6-404.png, both of which show exactly the described state — a white page with a single line of unstyled Times text ('Too many login attempts. Please wait.' / '

## F305 [sev 3] [a11y] Heatmap cells are nameless/number-only links; risk names are tooltip-only; empty cells are 10 invisible links; dashboard cells all link to the same page
- Route: `/risk-management and / (Risk Heatmap)` · area: a11y-keyboard
- Actual: risks/index.html emits <a class=heatmap-empty-link></a> for empty cells and <a>{{ count }}</a> for populated ones; names are in td title only. probe3.json heatmap_links: 10 entries with text '' and label null; populated links w≈9.8px; heatmap_selected_cell.ariaCurrent=null.
- Expected: Each cell link has an accessible name such as 'Likely / Major: 4 risks (Ransomware..., ...)' (WCAG 2.4.4, 1.1.1 for tooltip-only content), the whole cell is the hit/focus target (2.5.5/2.5.8), empty cells are either not links or say '0 risks', and the active filter is exposed with aria-current.
- Code: src/grcen/templates/risks/index.html:66-81 (heatmap td/a); src/grcen/templates/dashboard.html:64-80; src/grcen/static/css/style.css .heatmap-cell / .heatmap-empty-link / .heatmap-selected
- Fix: Add aria-label="{{ likelihood }} likelihood, {{ impact }} impact: {{ n }} risk(s)" to every cell link, make the <a> display:block with full cell padding so the focus ring and click target are the cell, render empty cells as plain text '0' or a link named '... : no risks', put the risk names in a visually-hidden <span> or a <details> list rather than title, add aria-current="true" to the selected c
- Verifier (confirmed): Reproduced as admin01 against https://localhost:8443. /risk-management (200): the heatmap renders 25 <a> elements — 10 are class heatmap-empty-link with empty text, no aria-label, no title on the <td> (first Tab stop lands on one: tag A, text '', 80x28px, screenshot F305_focus_empty.png shows only a bare rectangle inside the cell). The 15 populated links have text '1'/'2'/'4' only, aria-label null

## F306 [sev 3] [a11y] Graph page is canvas-only: nodes cannot be reached or selected by keyboard, no text alternative, status text not announced
- Route: `/graph/{id} and /graph` · area: a11y-keyboard
- Actual: probe3.json graph_canvas: role/label null, tabindex -1, tableAlt=false; graph_tab_sequence shows focus never enters #cy; graph_legend.pressed=null; graph_panel_focus_after_select stays on a nav link.
- Expected: A keyboard/screen-reader user can reach the same information and actions: a DOM list/table of nodes and relationships that can be focused and expanded, canvas with role=img + aria-label summarising node/edge counts, legend toggles with aria-pressed, and status updates in an aria-live region (WCAG 1.
- Code: src/grcen/templates/graph/view.html:24-30; src/grcen/static/js/graph.js:209-232 (renderNodePanel), :251-279 (renderLegend), :131 and :199 (animate:true)
- Fix: Render an accessible companion list (<table> or <ul>) of visible nodes with per-row Expand/Open buttons that call expandNode()/renderNodePanel(), kept in sync with the Cytoscape instance; set role="img" aria-label="Relationship graph: N assets, M relationships" on #cy; add aria-pressed to legend buttons; add aria-live="polite" to #graph-status; move focus into the node panel when it opens; honour 
- Verifier (confirmed): Reproduced live as admin02 on both /graph/{id} (5 nodes / 7 edges) and /graph (500 nodes / 772 edges), status 200. Observed: #cy has role=null, aria-label=null, tabIndex=-1; it contains 3 <canvas> elements with no role and empty fallback content; #cy innerText is empty and there are zero <li>/<table>/<a>/<button> descendants, so the DOM carries no node or edge information at all. Tab sequence from

## F307 [sev 3] [a11y] Org Views tabs lack tab semantics/arrow-key navigation and the three canvases have no text alternative or keyboard access to nodes
- Route: `/org-views` · area: a11y-keyboard
- Actual: probe3.json org_tabs: roles/selected/controls all null; org_tab_arrowright unchanged; org_canvas: role/label null, tabindex -1, fallback ''.
- Expected: role=tablist/tab/tabpanel with aria-selected, aria-controls, roving tabindex and arrow keys (WAI-ARIA tabs pattern); canvas with role=img + aria-label and an accessible DOM tree (nested <ul> of links) as the alternative (WCAG 1.1.1, 2.1.1, 4.1.2).
- Code: src/grcen/templates/org_views.html:6-33 and the inline tab script :41-56; src/grcen/static/js/org_tree.js:266-277 (click-to-navigate hit test)
- Fix: Add role="tablist" to .org-tabs, role="tab" aria-selected aria-controls on buttons, role="tabpanel" aria-labelledby on panels, handle ArrowLeft/Right/Home/End; render the same tree data (already fetched as JSON) as a nested <ul> with links inside each panel (visually hidden or as a toggleable 'List view'), and give each canvas role="img" with a summary aria-label.
- Verifier (confirmed): Verified from source plus the prior tester's artifacts (plan mode was active for this session, so I did not launch a fresh browser; the existing probe3.json org_* results and p3-orgviews.png were inspected directly and match the code exactly).

Tabs: src/grcen/templates/org_views.html:6-10 renders three plain <button class="org-tab" data-tab=...> inside a plain <div class="org-tabs">; panels are p

## F315 [sev 3] [a11y] At 720px (200% zoom) the Risk Management heatmap is clipped to 348px losing the Moderate/Major/Catastrophic columns, and the register/dashboard tables overflow with no scroll affordance
- Route: `/risk-management (also / Top Risks table)` · area: a11y-keyboard
- Actual: probe6 zoom_risk_top_row: heatW=348, filtW=300, wrap='nowrap'; probe4.json zoom720 /risk-management mainW=1297, / mainW=771.
- Expected: Content reflows to a single column at 320 CSS px equivalent (WCAG 1.4.10 Reflow): filters stack under the heatmap, the heatmap scrolls or shrinks within its card, data tables get overflow-x:auto wrappers.
- Code: src/grcen/static/css/style.css:284-288 (.risk-top-row flex, .risk-filters-card width:300px), :195 (table width:100% with no overflow wrapper); src/grcen/templates/risks/index.html:58-61
- Fix: Add flex-wrap:wrap to .risk-top-row and let .risk-filters-card go to width:100% under a @media (max-width: 900px) rule; wrap the heatmap table in a div with overflow-x:auto; wrap data tables in <div class="table-scroll" style="overflow-x:auto"> (and the nav fix already tracked in the baseline).
- Verifier (confirmed): Reproduced independently as admin11 in a 720px-wide Chromium viewport (equivalent to 200% zoom of 1440). Measured via DOM geometry on /risk-management: .risk-top-row computed flex-wrap='nowrap', .risk-heatmap-card width=348px (clientWidth 346, scrollWidth 615, overflow visible), .heatmap-table width=591px, .risk-filters-card width=300px at left=396. Heatmap header cells: Minor ends at x=360, Moder

## F213 [sev 3] [not-functional] Token 'Copy' button does nothing (inline onclick blocked by CSP)
- Route: `/tokens` · area: admin-settings
- Actual: navigator.clipboard.writeText never runs; clipboard read back empty (matches: False).
- Expected: Secret copied to clipboard with a 'Copied' acknowledgement.
- Code: src/grcen/templates/tokens/my_tokens.html:16
- Fix: Move the copy logic into a nonce'd script bound by id/data attribute and show inline 'Copied' feedback.
- Verifier (confirmed): Confirmed from the live header plus the template source (a Playwright click-through was not run because this session is in plan mode, which forbids the token-creating write; the mechanism is deterministic so the header + source are sufficient). Live GET https://localhost:8443/login returns `content-security-policy: default-src 'self'; script-src 'self' 'nonce-...'; style-src 'self' 'unsafe-inline'

## F214 [sev 3] [not-functional] Encryption profile radios no longer drive the scope checkboxes (inline onchange blocked)
- Route: `/admin/encryption` · area: admin-settings
- Actual: onProfileChange() only runs at DOMContentLoaded (nonce'd script); the radio onchange handlers are CSP-blocked, so the admin cannot see what a profile will enable or pick custom scopes.
- Expected: Choosing a named profile ticks the scopes it bundles; 'Custom' enables manual selection, as the page copy promises.
- Code: src/grcen/templates/admin/encryption.html:52,61,130-163
- Fix: Bind change listeners inside the existing nonce'd script (querySelectorAll('input[name=profile]').forEach(r=>r.addEventListener('change', onProfileChange))).
- Verifier (confirmed): Reproduced independently as admin06 (headless Chromium via the pw helper; no files written because plan mode is active, so observation was printed DOM state only, no screenshot/script file). On /admin/encryption with no active profile, all 10 scope checkboxes start unchecked+disabled. Clicking the GDPR radio, then Custom, then Minimal left every checkbox unchecked and disabled each time. The brows

## F217 [sev 3] [bug] Invitation links (and calendar feed URLs) are minted with http://localhost:8000 and do not work
- Route: `/admin/invitations (also /settings calendar feed)` · area: admin-settings
- Actual: Hard-coded settings.APP_BASE_URL default 'http://localhost:8000' is used verbatim; the link is dead on this deployment.
- Expected: Links use the host the admin is actually on (request.base_url / X-Forwarded-*), or the page warns that APP_BASE_URL is unset.
- Code: src/grcen/routers/admin_pages.py:1058,1099; src/grcen/config.py:88; src/grcen/routers/settings_pages.py:124-133
- Fix: Fall back to str(request.base_url) when APP_BASE_URL is the default, and surface a warning banner on these pages when the configured base URL does not match request host.
- Verifier (confirmed): Verified by code + runtime-configuration inspection (plan mode was active in this session, so I did not mint a new invitation or write Playwright scripts/screenshots; the mechanism is deterministic and does not depend on request state). Findings: (1) src/grcen/routers/admin_pages.py:1099 builds the one-time invite link as f"{settings.APP_BASE_URL.rstrip('/')}/invite/{raw_token}" and :1058 passes t

## F219 [sev 3] [not-functional] Admin cannot set a user's email anywhere, yet /settings tells users to ask an admin
- Route: `/admin/users/new and /admin/users/{id}/edit` · area: admin-settings
- Actual: No UI anywhere to set email, so email notifications and SMTP 'Send Test' ('current user has no email address') are unreachable for local accounts.
- Expected: An Email field on the user form (or self-service on /settings).
- Code: src/grcen/templates/admin/user_form.html; src/grcen/routers/admin_pages.py:74-123,155-214; src/grcen/templates/settings.html:24
- Fix: Add an email input to user_form.html (create + edit) and persist it; optionally let users edit their own email on /settings.
- Verifier (confirmed): Verified by code inspection (plan mode was active for this subagent, so no browser session was created; the app answered 200 on https://localhost:8443/login). Findings: (1) src/grcen/templates/admin/user_form.html renders only username, password, role and (edit only) person_asset_id — there is no email input on create or edit. (2) The POST handlers admin_user_create_submit (admin_pages.py ~73-123)

## F220 [sev 3] [bug] No password policy on admin user creation: a 1-character password is accepted
- Route: `/admin/users/new` · area: admin-settings
- Actual: Only non-empty is checked; the invitee page and the admin page disagree on policy.
- Expected: Reject with a minimum length (the invite page enforces 8) and show the rule in the form.
- Code: src/grcen/routers/admin_pages.py:80-83,175-182; src/grcen/templates/auth/invite.html (minlength=8)
- Fix: Centralise a password-policy check in auth_service (min length, maybe breach list) and apply it on create, edit, and invite; add minlength on the inputs.
- Verifier (confirmed): Verified against the live app and the source. Plan mode was active for this session (read-only), so I did not submit the create form and mint an "admin12-weak" user; I confirmed the defect by inspecting the rendered form and the exact code path instead, which leaves no room for a server-side policy to exist.

Live form (logged in as admin12, GET /admin/users/new): the password input renders as `<i

## F222 [sev 3] [ux] No way for a signed-in user to change their own password
- Route: `/settings` · area: admin-settings
- Actual: Only an admin can reset a password via /admin/users/{id}/edit; users are dependent on admins for routine hygiene.
- Expected: A 'Change password' form (current + new + confirm) on My Settings.
- Code: src/grcen/templates/settings.html; src/grcen/routers/settings_pages.py
- Fix: Add POST /settings/password requiring the current password, with the same policy as creation.
- Verifier (confirmed): Verified by code inspection (plan mode prevented running a browser script; server at https://localhost:8443 is up, GET /settings unauthenticated returns 401). The described state exists exactly as reported: /home/t3rmit3/gits/GRCen/src/grcen/templates/settings.html has an Account card (lines 22-25) that only prints username/email/role with no form, and the page contains zero type="password" inputs

## F223 [sev 3] [bug] OIDC/SAML settings save invalid role-mapping JSON and garbage certificates silently
- Route: `/admin/oidc-settings and /admin/saml-settings` · area: admin-settings
- Actual: Values are stored as-is; the admin only discovers the problem when SSO login fails.
- Expected: Reject non-object JSON and non-PEM certificates with a field-level error; confirm on success.
- Code: src/grcen/routers/admin_pages.py:570-592,619-649
- Fix: Validate json.loads(mapping) is a dict with values in UserRole, try to parse the cert, and re-render the form with errors (as SCIM does); add a success flash.
- Verifier (confirmed): Verified by static analysis of the live code plus the prior tester's screenshot (I did not re-run the POST: this session is in plan/read-only mode and the SSO config is a global setting the task says not to toggle; the server at https://localhost:8443 is up, GET /admin/oidc-settings -> 401 unauthenticated).

Code confirms the description exactly:
- /home/t3rmit3/gits/GRCen/src/grcen/routers/admin_

## F163 [sev 3] [bug] Delete questionnaire runs with no confirmation (inline onsubmit confirm blocked by CSP)
- Route: `/questionnaires/{id}` · area: answers-questionnaires
- Actual: The onsubmit="return confirm(...)" attribute never executes because the CSP has no 'unsafe-inline', so the form submits straight away. Playwright dismissed every dialog and the record was still deleted; zero dialogs were observed.
- Expected: A confirmation prompt before an irreversible delete of the questionnaire and all its responses; cancelling keeps the data.
- Code: src/grcen/templates/answers/questionnaire_detail.html:54-55 (onsubmit confirm); src/grcen/middleware.py:87 (script-src 'self' 'nonce-…', no unsafe-inline). 22 templates use inline onsubmit/onclick handlers.
- Fix: Replace inline handlers with a nonce'd global script that handles data-confirm attributes (or use htmx hx-confirm), and add a test that fails if any template contains onsubmit=/onclick=.
- Verifier (confirmed): Verified read-only (plan mode is active in this session, so I did not run the Playwright create/delete repro or write scripts; no questionnaire was created or deleted). Evidence: (1) live header from the running app — `curl -sk -D - https://localhost:8443/login` returns `content-security-policy: default-src 'self'; script-src 'self' 'nonce-...'; style-src 'self' 'unsafe-inline'; ...` — script-src 

## F165 [sev 3] [bug] Vendor portal 'Submit' locks the questionnaire with no confirmation and no unanswered-question warning
- Route: `/vendor-portal/{token}` · area: answers-questionnaires
- Actual: onclick confirm is blocked by CSP; vendor is locked out after an accidental click. Admin then sees status 'submitted' with Progress 2/4.
- Expected: A confirmation (as the template intends) and ideally a warning that N questions are unanswered before the irreversible submit.
- Code: src/grcen/templates/vendor_portal.html:58-59 (onclick confirm); src/grcen/routers/vendor_campaigns.py:217-233 (portal_submit).
- Fix: Move the confirm into a nonce'd <script> on the portal page, include the unanswered count in the message, and/or add a server-side 'are you sure' step when unanswered > 0.
- Verifier (confirmed): Plan mode was active for this session, so I did not create a new campaign or drive the mutating submit flow; I verified the mechanism with read-only checks instead. (1) Live GET of the existing portal https://localhost:8443/vendor-portal/Fe3MRBnn38VXT9XQXJlI314iS6yvfAnrLg_rnb6GnXw returns 200 with header `content-security-policy: default-src 'self'; script-src 'self' 'nonce-...'; ...` — a nonce ne

## F166 [sev 3] [bug] Generated vendor portal link points at APP_BASE_URL (http://localhost:8000), not the host the app is served on
- Route: `/vendor-campaigns/{id}` · area: answers-questionnaires
- Actual: net::ERR_CONNECTION_REFUSED; the link the admin is told to share with a vendor is dead. Nothing in the UI indicates it depends on a server setting.
- Expected: A link that resolves to the running instance (https://localhost:8443/...) or an admin-visible warning that APP_BASE_URL is unset/misconfigured.
- Code: src/grcen/routers/vendor_campaigns.py:44-45 (_portal_url uses settings.APP_BASE_URL); src/grcen/config.py:88 default 'http://localhost:8000'.
- Fix: Fall back to request.base_url when APP_BASE_URL is the default, and surface a warning on the campaign page / Organization admin page when the configured base URL differs from the request host.
- Verifier (confirmed): Confirmed from code plus the running deployment, without creating objects (plan mode restricted me to read-only actions, so I did not drive the browser through campaign creation; the observation does not depend on request state). `_portal_url` in src/grcen/routers/vendor_campaigns.py:44-45 is a pure function of `settings.APP_BASE_URL`, and the template src/grcen/templates/vendor_campaigns/detail.h

## F168 [sev 3] [ux] CSV import gives no feedback at all: wrong column, .txt files and empty files 'succeed' silently; expected format undocumented
- Route: `/questionnaires/{id}` · area: answers-questionnaires
- Actual: Every outcome is a silent redirect to the same page; the only format hint is the label 'Question column (0-based)'. The vendor-campaign import at least shows 'Imported N question(s)'.
- Expected: An 'Imported N questions' (or 'Nothing imported — column 7 does not exist / file is empty') flash, a preview before commit, duplicate detection on re-import, and a short description/example of the expected CSV shape next to the form.
- Code: src/grcen/routers/answer_pages.py:125-153 (questionnaire_import redirects without a message); src/grcen/templates/answers/questionnaire_detail.html:26-41 (import card).
- Fix: Return ?flash=ok:Imported N / fail:… like vendor_campaigns does, add a one-line format hint ('one question per row; pick the column that holds the question text; tick header if row 1 is a title row'), and optionally a preview step as /import already has for assets.
- Verifier (confirmed): Live browser repro was NOT executed: this session is in plan mode, which forbids non-read-only actions (the Playwright script would log in and insert rows). The verdict rests on static analysis of the exact code path, which is unconditional and deterministically produces every outcome the reporter listed:

- Route src/grcen/routers/answer_pages.py:125-153 (questionnaire_import) reads the upload, c

## F169 [sev 3] [bug] An answer's text can never be cleared: emptying the textarea sets status Unanswered but the old text is kept and exported
- Route: `/questionnaires/{id} (POST .../responses/{rid})` · area: answers-questionnaires
- Actual: The server coalesces NULL to the previous value, so the row is in a contradictory state and the stale text goes out in customer-facing exports.
- Expected: Saving an empty answer blanks the stored text (and status becomes Unanswered), or the UI refuses with a message.
- Code: src/grcen/services/questionnaire_service.py set_response: UPDATE ... filled_answer = coalesce($2, filled_answer); router answer_pages.py:173-174 converts empty text to None.
- Fix: Distinguish 'field absent' from 'field empty': when the form posts filled_answer='' and no mapping, store '' (not NULL) so the answer is actually cleared.
- Verifier (confirmed): Plan mode was active for this session, so I could not run a Playwright script that POSTs to the live app (non-read-only); verification is a deterministic code trace plus independent inspection of the prior tester's persisted artifacts. Trace: (1) src/grcen/routers/answer_pages.py:173-174 maps an empty/whitespace textarea to filled_answer=None (`str(manual) if manual is not None and str(manual).str

## F170 [sev 3] [not-functional] Vendor campaign CSV import always reads column 0 and has no column selector, so an ID column becomes the questions
- Route: `/vendor-campaigns/{id} (draft)` · area: answers-questionnaires
- Actual: Hard-coded column=0; the user sends vendors a questionnaire whose questions read 'Q1', 'Q2'. Combined with the next finding there is no way to fix it.
- Expected: The same 'Question column' control the inbound questionnaire import has, or auto-detection of the longest text column, plus a preview.
- Code: src/grcen/routers/vendor_campaigns.py:144 (parse_questions(content, column=0, ...)); src/grcen/templates/vendor_campaigns/detail.html:58-64.
- Fix: Add the column input (and share the import card partial with questionnaire_detail.html).
- Verifier (confirmed): Verified by code inspection (plan mode was active for this subagent, so I could not run the mutating Playwright repro that creates a campaign; the code path is unambiguous and deterministic, so the reported observation follows necessarily). In /home/t3rmit3/gits/GRCen/src/grcen/routers/vendor_campaigns.py the import handler reads only `file` and `has_header` from the form and calls `questionnaire_

## F171 [sev 3] [not-functional] No way to remove or edit a question on a draft campaign, and no way to delete a campaign
- Route: `/vendor-campaigns/{id}` · area: answers-questionnaires
- Actual: 0 remove/delete buttons on the page; the index accumulates dead campaigns (my 'admin08-Q4 Vendor Review' with bogus 'Q1/Q2/Q3' questions cannot be cleaned up).
- Expected: Per-question Remove/Edit while draft, and Delete (or at least Cancel/Archive) campaign.
- Code: src/grcen/templates/vendor_campaigns/detail.html (no remove/delete forms); src/grcen/routers/vendor_campaigns.py has no delete routes; services/vendor_campaign_service.py has no delete_question/delete_campaign.
- Fix: Add POST /vendor-campaigns/{id}/questions/{qid}/delete (draft only) and POST /vendor-campaigns/{id}/delete with a (non-inline) confirm.
- Verifier (confirmed): Reproduced read-only (plan mode was active, so I did not create a campaign; no files or objects were written). Live as admin11: /vendor-campaigns index lists exactly one campaign, the other tester's 'admin08-Q4 Vendor Review' (now status 'reviewed', 4 question rows incl. the bogus Q1/Q2/Q3), with 0 delete/archive/cancel controls on the index and 0 remove/edit/delete controls on its detail page (bu

## F173 [sev 3] [bug] Viewer role can see the vendor portal capability URL and could answer the questionnaire as the vendor
- Route: `/vendor-campaigns/{id}` · area: answers-questionnaires
- Actual: Portal link readonly input rendered for Viewer (input[readonly] count = 1).
- Expected: Secret write-capable links are shown only to users with EDIT (or masked for viewers).
- Code: src/grcen/templates/vendor_campaigns/detail.html:20-27 (portal link card has no has_perm guard).
- Fix: Wrap the Portal link card in {% if has_perm(user.role, Permission.EDIT) %} and show a masked token otherwise.
- Verifier (confirmed): Confirmed from the code path, which is unconditional; I could not execute a live browser session this run because the harness placed me in plan mode (read-only, no script files or logins permitted), so the evidence below is the deterministic server/template logic rather than a fresh screenshot.

Chain:
1. /home/t3rmit3/gits/GRCen/src/grcen/routers/vendor_campaigns.py:91-113 — `campaign_detail` is 

## F062 [sev 3] [not-functional] 'Add URL' attachment on asset detail silently fails with 422 (htmx posts form-encoded to a JSON API)
- Route: `/assets/{id}` · area: asset-crud
- Actual: hx-post sends application/x-www-form-urlencoded; the endpoint expects a JSON AttachmentCreate body, so FastAPI returns 422 and htmx shows nothing. The feature is unusable from the UI.
- Expected: The URL attachment is saved and appears in the table, or an inline error is shown.
- Code: src/grcen/templates/assets/detail.html:253-261 (hx-post form), src/grcen/routers/attachments.py:95-100 (expects AttachmentCreate JSON)
- Fix: Either add hx-ext="json-enc" / a small fetch like relationship_form.js that POSTs JSON, or add a page route POST /assets/{id}/attachments that accepts the form (mirroring /relationships/{id}/evidence) and redirects back.
- Verifier (confirmed): Plan mode (read-only) was active for this session, so I did not re-drive the browser; verification is from the code path plus the original tester's captured network log, and the mechanism is deterministic. Template src/grcen/templates/assets/detail.html:253-261 renders `<form hx-post="/api/assets/{{ asset.id }}/attachments/">` with plain inputs (hidden kind=url, name, url_or_path) and no hx-ext="j

## F063 [sev 3] [bug] File upload on asset detail succeeds but the page never updates (hx-on handler blocked by CSP)
- Route: `/assets/{id}` · area: asset-crud
- Actual: The hx-on::after-request="...location.reload()" attribute is evaluated via eval, which the CSP forbids, so the 201 response is dropped on the floor and the user believes the upload failed (and may upload again, creating duplicates — observed: 2 copies after two attempts).
- Expected: After a successful upload the attachment list shows the new file (or a success message appears).
- Code: src/grcen/templates/assets/detail.html:253-254 and 264-265 (hx-on::after-request), src/grcen/middleware.py:87
- Fix: Replace hx-on attributes with a nonce'd script listening for htmx:afterRequest on #attachments, or have the endpoint return the refreshed table fragment with hx-target="#attachments" hx-swap="outerHTML".
- Verifier (confirmed): Reproduced the failure mechanism directly in a headless Chromium session logged in as admin15 on /assets/2a65f0ed-9036-4f77-a582-30438b7f835b (read-only probes only; this session was in plan mode so I did not perform the actual file POST). Observations: (1) the live CSP header is `script-src 'self' 'nonce-...'` with no 'unsafe-eval'; (2) both attachment forms in the DOM carry `hx-on::after-request

## F064 [sev 3] [bug] Remove (relationship / attachment) leaves the row on screen until reload — htmx ignores the 204 response
- Route: `/assets/{id}` · area: asset-crud
- Actual: The API returns 204 No Content; htmx does not swap on 204, so hx-target="closest tr" hx-swap="outerHTML" never runs. The user thinks the removal failed.
- Expected: The row disappears immediately after confirming (or a success/failure message appears).
- Code: src/grcen/templates/assets/detail.html:188-191 and 237-240 (hx-delete); src/grcen/routers/relationships.py:177 and src/grcen/routers/attachments.py:184 (status_code=204)
- Fix: Return 200 with an empty body for these two DELETE routes (or add hx-swap="delete" and configure htmx.config.responseHandling to swap on 204), and show a brief 'Removed' toast.
- Verifier (confirmed): Reproduced the htmx mechanism in a headless browser as admin16 on /assets/2a65f0ed-9036-4f77-a582-30438b7f835b (4 relationship rows). To avoid mutating shared data (plan mode / read-only constraint) I intercepted the DELETE at the browser layer and fulfilled it with exactly what the server route declares (204, empty body): the confirm dialog "Remove this relationship?" fired, the DELETE was issued

## F066 [sev 3] [bug] Name over 255 characters crashes with 500 — no maxlength on the input and no server check
- Route: `/assets/new (POST), /assets/{id}/edit (POST)` · area: asset-crud
- Actual: assets.name is VARCHAR(255); asyncpg raises StringDataRightTruncation, unhandled -> 500 dead-end page.
- Expected: Either the input stops at 255 characters (maxlength) or the server returns an inline 'Name must be 255 characters or fewer' error with the form intact.
- Code: src/grcen/templates/assets/form.html:23 (no maxlength), src/grcen/database.py:38 (VARCHAR(255)), src/grcen/routers/asset_pages.py:449-463
- Fix: Add maxlength="255" to #name and strip()+length-check name server-side before create_asset, re-rendering the form on failure.
- Verifier (confirmed): Plan mode was active for this agent, so I did not re-run the mutating POST myself; I verified the finding from the live server's own logs plus the original tester's artifacts and the source. Evidence: (1) `docker logs grcen-app-1` contains the exact traceback 4 times: `asset_pages.py line 449, in asset_create_submit -> services/asset.py line 48, in create_asset -> pool.fetchrow -> asyncpg.exceptio

## F069 [sev 3] [bug] Owner cannot be removed: clearing the Owner box and saving keeps the old owner; typing a new name without clicking keeps the old owner_id
- Route: `/assets/{id}/edit` · area: asset-crud
- Actual: The hidden owner_id is never reset when the visible text changes (unlike relationship_form.js which clears target id on input), so there is no UI path to unset an owner and the visible text and saved value can disagree.
- Expected: An emptied Owner field clears the owner; a changed search text that was not confirmed either clears owner_id or blocks the save with 'pick an owner from the list'.
- Code: src/grcen/templates/assets/form.html:46-54 and 182-185 (input listener only syncs #owner_q)
- Fix: In the input listener also set owner_id.value = '' whenever the text changes; add a small 'x Clear owner' button; show the chosen owner as a chip so the state is visible.
- Verifier (confirmed): Confirmed from code (session was in read-only plan mode, so I did not re-drive the browser; the prior tester's t03.out trace at work/asset-crud/t03.out already records the observable symptom: 'after clearing owner text and saving, Owner field: [Owner: Hassan Ali]' following a 302 from POST /assets/{id}/edit). The mechanism is unambiguous and is actually deeper than the finding states:

1. UI layer

## F070 [sev 3] [bug] Duplicate and self-referencing relationships are accepted silently
- Route: `/assets/{id} (Add Relationship) / POST /api/relationships/` · area: asset-crud
- Actual: Both are stored; the graph and relationship counts now contain noise and cloning with '+ rels' multiplies the self-link into 3 edges.
- Expected: Duplicate (same source, target, type) is rejected or merged with a message; the current asset is excluded from the target search or self-links are refused.
- Code: src/grcen/services/relationship.py:10-48 (no uniqueness/self check), src/grcen/static/js/relationship_form.js:25-41 (search does not exclude form.dataset.sourceId)
- Fix: Add a unique index on (source_asset_id, target_asset_id, relationship_type) and a CHECK source_asset_id <> target_asset_id; map the IntegrityError to a 400 'This relationship already exists' that the form already displays; filter the current asset out of the search results.
- Verifier (confirmed): Plan mode was active for this subagent, so I could not run a fresh mutating Playwright repro; verification is from (a) the code, which contains no guard at any layer, and (b) the prior tester's artifacts, which I inspected directly. Code: src/grcen/database.py:53-66 defines `relationships` with only a non-unique index on (source_asset_id, target_asset_id) and no CHECK; the only unique index (line 

## F072 [sev 3] [design] Asset attachment table shows the raw server path and offers no download link
- Route: `/assets/{id}` · area: asset-crud
- Actual: The table prints att.url_or_path verbatim (an internal container path for files) and the name is not linked, so the evidence the analyst just attached cannot be opened from the asset page. The relationship evidence page does this correctly.
- Expected: File name is a download link; URL attachments are clickable; the internal storage path is not shown.
- Code: src/grcen/templates/assets/detail.html:230-235 vs relationships/evidence.html:34-40; download route src/grcen/routers/attachments.py:149
- Fix: Reuse the evidence.html row markup: link file names to /api/assets/{id}/attachments/{att.id}/download, link url kinds with rel=noopener, hide the path for files.
- Verifier (confirmed): Plan mode was active for this run, so I could not drive the browser (login + file upload are write actions against the app); verification is by code reading, which is conclusive for a template-rendering defect. /home/t3rmit3/gits/GRCen/src/grcen/templates/assets/detail.html lines 229-235 render each attachment row as `<td>{{ att.name }}</td><td>{{ att.kind.value }}</td><td>{{ att.url_or_path or '—

## F037 [sev 3] [bug] Search terms containing &, # or + break the sort-header and pagination links (filter params are not URL-encoded)
- Route: `/assets?q=R%26D+test` · area: assets-list
- Actual: Header href is /assets?sort=name&order=desc&q=R&D test - the & splits the query so the next page searches for 'R' and a stray 'D test' param is sent. Same for '#' (truncates to fragment) and '+' (decoded as space). The sort macro also strips sort/order via a fragile string replace.
- Expected: Links keep the full query 'R&D test'.
- Code: src/grcen/routers/asset_pages.py:136-160 (filter_params built with f-strings, no urlencode); src/grcen/templates/assets/list.html:129 (replace('&sort='...) hack) and :191
- Fix: Build filter_params with urllib.parse.urlencode(dict, doseq=True) and expose a helper that produces page/sort links from a dict (drop sort/order keys instead of string-replacing).
- Verifier (confirmed): Confirmed by static trace of the exact code path; the live Playwright repro was NOT run because this session is in plan mode (read-only: no script files, no screenshots, no login side effects), so the only evidence I generated myself is code reading plus two read-only GETs (server up at https://localhost:8443, CSP header captured). The mechanism is unconditional, so the tester's logged href is exa

## F038 [sev 3] [bug] 'Download PDF' ignores owner, created-date, metadata and unlinked filters and silently truncates at 500 rows
- Route: `/exports/assets.pdf (button on /assets)` · area: assets-list
- Actual: The PDF route only reads type/q/status/tag; owner, created_after/before, meta_key/value, unlinked and sort are dropped, and the full export is capped at 500 with only a small '500 of 665' line. The cover page also prints the raw query string ('type=vendor', 'status=draft') as the report subtitle.
- Expected: The PDF contains the same rows the page shows (like the CSV/JSON buttons do), or the page tells the user which filters the PDF cannot apply.
- Code: src/grcen/routers/framework_pages.py:480-496 (signature only takes type,q,status,tag); src/grcen/services/pdf_service.py render_asset_list_report (500 cap, raw filter subtitle); src/grcen/templates/assets/list.html:10
- Fix: Route the PDF through the same list_assets filter set as /assets/export (pass owner/dates/meta/unlinked/sort), render human filter labels on the cover, and either raise the cap or show a visible 'truncated' warning in the UI before download.
- Verifier (confirmed): Reproduced live as admin06 (session-authenticated GETs against https://localhost:8443). For each filter set I fetched /assets?<qs> and parsed the "N assets found" count, then fetched /exports/assets.pdf?<qs> and extracted the PDF text:
- type=vendor&owner=Carol: list says 4 found; PDF says "20 of 20 matching assets. Filters: type=vendor." (owner dropped).
- unlinked=on: list says 15 found; PDF say

## F039 [sev 3] [bug] Malformed or stale query params crash the list with a bare 'Internal Server Error' (type=foo, page=0, status=nope, created_after=notadate) or raw JSON 422 (page=abc)
- Route: `/assets?type=foo` · area: assets-list
- Actual: AssetType(type) raises ValueError uncaught; page<1 produces a negative OFFSET; status/date strings go straight into SQL casts. The error pages are text/plain with no navigation.
- Expected: A friendly 400 page or the list rendered with the bad filter ignored; bookmarks/saved searches from an older build should never 500.
- Code: src/grcen/routers/asset_pages.py:64 (AssetType(type)), :60 (page: int = 1 without ge=1), :190 statuses list not enforced; src/grcen/services/asset.py list_assets status/date filters
- Fix: Validate with Query(ge=1) for page, Literal/enum for status and type (fall back to None on bad values), parse dates with date.fromisoformat in try/except, and register an HTML 400/422/500 error handler for page routes.
- Verifier (confirmed): Verified against code plus the live container's own tracebacks (I could not open a fresh logged-in browser session because this subagent was placed in plan mode, which forbids state-changing actions such as a login POST; unauthenticated GETs all return 401 before query validation, so the auth dependency runs first). Evidence: (1) Code: src/grcen/routers/asset_pages.py:64 `AssetType(type) if type e

## F040 [sev 3] [ux] Default /assets view opens on 420 content-pack requirements sorted '1.1 -, 1.2 -, 10.1 -' - 17 of the 27 pages are synced catalog rows
- Route: `/assets` · area: assets-list
- Actual: Default sort is name ASC with no type grouping; because requirements names start with digits they dominate pages 1-17, and numeric prefixes sort lexically (10.1 before 2.1). The all-types list has no 'hide synced/content-pack rows' option.
- Expected: An inventory engineer expects to see their own org's assets (systems, people, vendors) first - e.g. sorted by recently updated, or with synced catalog requirements collapsed/segmented.
- Code: src/grcen/routers/asset_pages.py:57-58 (sort='name', order='asc'); src/grcen/services/asset.py:218 ORDER BY a.name
- Fix: Default to updated_at DESC (or type, name), add a 'Source: human-authored / synced' filter chip, and use natural sort (COLLATE or split numeric prefix) for name ordering.
- Verifier (confirmed): Reproduced as admin08 (read-only: inline HTTP/Playwright fetch, no files or data written). GET /assets with no params shows "662 assets found (page 1 of 27)"; the first 25 rows are all type Requirement, ordered '1.1 —', '1.2 —', ..., '1.5 —', '10.1 —', '10.2 —', ... '12.8 —' (lexical, so 10.x precedes 2.x). /assets?type=requirement reports 420 rows (17 pages' worth). Walking all 27 pages: pages 1-

## F041 [sev 3] [ux] Pagination is 27 bare page numbers with no Previous/Next, no jump, and no page-size control (page_size param is ignored)
- Route: `/assets?page=26` · area: assets-list
- Actual: Every page number is rendered; page size is hard-coded to 25 in the page route (the JSON API supports page_size). Users scanning 665 assets click 27 times.
- Expected: Prev/Next, a compact window of pages, a rows-per-page selector (25/50/100) and ideally 'showing 26-50 of 665'.
- Code: src/grcen/templates/assets/list.html:188-195; src/grcen/routers/asset_pages.py:73 page_size=25, :166
- Fix: Accept page_size (25/50/100, capped), render Prev/Next + windowed numbers + 'x-y of N', keep it in filter_params so exports/saved searches carry it.
- Verifier (confirmed): Verified from the source (read-only; plan mode prevented re-running a Playwright script, so I relied on the code plus the prior tester's screenshot, which is the standard the brief sets for 'ux' findings). The screenshot at .../work/assets-list/27_pagination.png shows exactly the described state: a single row of bare numbered links with 26 active, no Previous/Next, and the row visibly cut off at b

## F042 [sev 3] [bug] Changing Type (or picking 'All Types') on a register page silently carries the previous register's sort field and columns=curated to the new type
- Route: `/registers/policies -> Type=Vendor -> Search` · area: assets-list
- Actual: Hidden inputs persist sort/order/columns from the old type; Vendors are 'sorted' by a Policy-only field (all NULL -> effectively random) and the UI gives no indication because no header matches the sort key.
- Expected: Switching type resets to that register's default sort (or name), and leaving the register drops the curated flag.
- Code: src/grcen/templates/assets/list.html:114-117 (hidden sort/order/columns inputs); src/grcen/routers/asset_pages.py:99-100
- Fix: On submit, drop sort/order/columns when the type select differs from current_type (small nonce'd script) or server-side: if sort starts with meta. and the field is not defined for the resolved type, fall back to the register default.
- Verifier (confirmed): Reproduced live as admin10. (1) /registers/policies 302s to /assets?type=policy&columns=curated&sort=meta.review_date&order=asc; the filter form carries hidden inputs sort=meta.review_date, order=asc, columns=curated. (2) Changing Type to Vendor and clicking Search lands on /assets?...type=vendor...&sort=meta.review_date&order=asc&columns=curated: headers are NAME/TIER/ASSESSMENT/NEXT ASSESSMENT/C

## F043 [sev 3] [ux] Bulk Apply gives zero feedback: no-op applies, nothing-selected applies and successful applies all just reload the page; status change is invisible in the curated view
- Route: `/registers/vendors` · area: assets-list
- Actual: The route 302s back to the same URL in every branch (including the cross-tenant ValueError branch) with no flash; the confirm() on the Apply button is CSP-blocked; the approval-gated branch redirects to /approvals which is a context switch with no explanation.
- Expected: Validation ('select at least one row' / 'choose a change'), a confirmation naming N rows, and a success banner 'Updated 2 vendors' with the changed columns visible.
- Code: src/grcen/routers/asset_pages.py:262-264, :302-304, :316; src/grcen/templates/partials/bulk_actions.html
- Fix: Return with ?flash=... (the pattern /tags already uses) for each branch (no rows / no change / N updated / rejected / N submitted for approval), render it above the table, and show a selected-row counter next to the Apply button.
- Verifier (confirmed): Verified by code reading plus the prior tester's screenshots (plan mode was active for this session, so I did not log in or POST; the behaviour is deterministic from the source). Every exit of POST /assets/bulk-update is a RedirectResponse with no message: `if not asset_ids or not (status or owner_id or add_tags or metadata_set): return RedirectResponse(back, 302)` covers both 'nothing ticked' and

## F050 [sev 3] [bug] /tags flash banner is reflected verbatim from the URL - any link ?flash=ok:... renders an arbitrary green success message
- Route: `/tags?flash=ok:Everything%20was%20deleted%20by%20admin` · area: assets-list
- Actual: tags_index parses ?flash=<ok|fail>:<message> straight from the query; the message is also lost/duplicated when the URL is shared or bookmarked.
- Expected: One-shot flash stored in the session, not user-controlled text in the query string.
- Code: src/grcen/routers/tag_pages.py:30-37, :60, :72, :94
- Fix: Store flash in request.session (pop on render) and redirect to a clean /tags; reuse the same helper for the bulk-update feedback.
- Verifier (confirmed): The described state exists and is deterministic from the code. src/grcen/routers/tag_pages.py:30-37 takes `flash` as a plain query parameter, splits on the first ':' and passes {"ok": prefix=="ok", "message": rest} to tags/index.html, which renders `{{ flash.message }}` inside a green (ok) or red (fail) .alert box (template lines 9-13). There is no session storage, no signing, and no redirect-to-c

## F018 [sev 3] [not-functional] There is no UI anywhere to create or edit an alert; /alerts points users to a form that does not exist
- Route: `/alerts and /assets/{id}` · area: dashboard-mywork
- Actual: Alerts can only be created or edited via the REST API (POST/PUT /api/alerts/). The only UI action is Delete. 'Fire now'/test does not exist.
- Expected: A compliance manager can create a review reminder (title, message, once/recurring, date or cron, enabled) from the asset page or from /alerts, and edit/disable an existing one.
- Code: src/grcen/templates/alerts/list.html:33 (misleading hint), src/grcen/templates/assets/detail.html:274-291 (read-only alerts table), src/grcen/routers/alerts.py:29-106 (REST only)
- Fix: Add an 'Add alert' form on the asset detail Alerts section (and a 'New alert' asset-picker on /alerts) that posts to /api/alerts/ via htmx or a page route; add Edit (prefilled form) and an Enable/Disable toggle per row; consider a 'Send test now' that calls alert_service.fire_alert. Until then, fix the empty-state copy so it does not point to a non-existent form.
- Verifier (confirmed): Logged in as admin02 (Admin) with Playwright and inspected the live pages. /alerts: 44 alert rows rendered, 0 links/buttons matching New/Create/Add, 0 hx-post/hx-put/hx-patch elements, 44 hx-delete buttons; the only <form> on the page is the global nav search form from base.html (action=/assets), not an alert form. Asset detail /assets/82b69ebb-503f-4cea-b1b5-c3583b8438c9 ("Vulnerability Scanning"

## F019 [sev 3] [bug] Notifications 'Mark Read' looks like it did nothing: row stays Unread and badge unchanged until reload or the 60s poll
- Route: `/notifications` · area: dashboard-mywork
- Actual: POST /api/alerts/notifications/{id}/read returns 204 No Content; htmx does not swap on 204, so hx-target='closest tr' hx-swap='outerHTML' never fires. Nothing visible happens for up to 60s. Users click repeatedly.
- Expected: The row immediately switches to Read (button disappears, styling clears) and the badge decrements.
- Code: src/grcen/templates/alerts/notifications.html:20-22 (hx-post + outerHTML swap), src/grcen/routers/alerts.py:157-165 (status_code=204)
- Fix: Return the re-rendered <tr> (200, HTML) from the read endpoint, or return 200 with an empty body plus an HX-Trigger header that refreshes #notif-badge; also trigger 'hx-trigger="load, every 60s, notifChanged from:body"' on the badge so it refreshes immediately.
- Verifier (confirmed): Confirmed from the source and the bundled htmx build; I did not re-run the browser click myself because this session is in plan mode (read-only, no script execution / no state mutation), so I relied on static analysis plus the prior tester's evidence, which is consistent with the code.

Mechanism, verified in the repo:
- src/grcen/templates/alerts/notifications.html:20-22 — the Mark Read button is

## F020 [sev 3] [bug] Alerts 'Delete' confirms and deletes server-side but the row stays on screen until reload
- Route: `/alerts` · area: dashboard-mywork
- Actual: DELETE /api/alerts/{id} returns 204; htmx ignores 204 for swapping so hx-swap='outerHTML' on 'closest tr' never runs. Row count stays 47 before/after. A second click yields a 404 silently.
- Expected: Row disappears after confirmation (and ideally a toast confirms deletion).
- Code: src/grcen/templates/alerts/list.html:23-26, src/grcen/routers/alerts.py:109-133 (status_code=204)
- Fix: Return 200 with an empty body (htmx swaps empty outerHTML = removes row) or add hx-swap='delete'; alternatively keep 204 but add response header 'HX-Reswap: delete'/'HX-Trigger'. Apply the same pattern to every 204 endpoint used by htmx.
- Verifier (confirmed): Verified by code inspection only: this session is in plan mode (read-only), so I could not write a Playwright script or create/delete an alert against the running instance. The mechanism is nonetheless deterministic and fully confirmed from source:

1. /home/t3rmit3/gits/GRCen/src/grcen/templates/alerts/list.html:23-26 renders `<button hx-delete="/api/alerts/{id}" hx-confirm="Delete this alert?" h

## F021 [sev 3] [bug] Marking a broadcast notification read hides it for every user in the org (read state is per row, not per user)
- Route: `/notifications` · area: dashboard-mywork
- Actual: notifications.read is a single BOOLEAN column; alert-fired notifications are broadcast rows (user_id NULL) shared by the whole org, and mark_notification_read updates the row for everyone. Watched-asset/session notifications are per-user so they behave correctly, giving inconsistent semantics.
- Expected: Read/unread is a per-user property; one person's acknowledgement does not clear another person's inbox.
- Code: src/grcen/database.py:91-99 (notifications table, single read flag), src/grcen/services/alert_service.py:297-309 (UPDATE ... SET read=true WHERE id), src/grcen/services/alert_service.py:249-281 (user_id IS NULL broadcast)
- Fix: Add a notification_reads(user_id, notification_id, read_at) table (or materialise one notification row per org member when an alert fires) and compute read state per user in list/count/mark-read.
- Verifier (confirmed): Confirmed by code and by the live database state (plan mode was active for this run, so I did not drive the browser or click Mark Read; the verification is read-only but conclusive). Schema: `notifications.read` is a single BOOLEAN per row (src/grcen/database.py:91-99); `user_id` was later added as a nullable column where NULL = org-wide broadcast (database.py:708-714). Alert-fired notifications a

## F026 [sev 3] [ux] No 'mark reviewed' / complete action exists for review items — on /reviews or on the overdue asset's page, which also gives no overdue cue
- Route: `/reviews and /assets/{id}` · area: dashboard-mywork
- Actual: Reviews are a read-only list; the review lifecycle has no action, and the asset page does not acknowledge that the date is in the past.
- Expected: A one-click 'Mark reviewed (set next due +1y)' or at least a visible overdue flag on the asset page and a 'Reviewed today' quick action.
- Code: src/grcen/templates/reviews/index.html:26-57, src/grcen/templates/assets/detail.html (header action bar / custom-field rendering), src/grcen/services/review_service.py:52-66
- Fix: Add a per-row 'Mark reviewed' button (POST that writes today into the type's review-date field and advances next due by the configured cadence, gated by workflow if applicable) and render Overdue/Due-soon badges next to review-date custom fields on the asset page.
- Verifier (confirmed): Reproduced as admin10 via Playwright (DOM inspection; plan mode was active so no screenshot files were written). /reviews?status=overdue renders 7 rows; the only controls on the page are the 'Filter' submit button and the 'Clear' link — no per-row or bulk action of any kind (template reviews/index.html has a GET filter form and a plain 5-column table, nothing else; dashboard_pages.py exposes only 

## F134 [sev 3] [bug] Requirements are sorted lexically (A.5.1, A.5.10, A.5.11 ... A.5.2 at row 12) on every framework view and export
- Route: `/frameworks/{id}, /frameworks/{id}/soa, gap-report.csv/pdf, soa.csv, /controls` · area: frameworks-controls
- Actual: Plain string sort on the name; A.5.2 appears after A.5.19, so matching the on-screen table against the standard or the auditor's checklist requires hunting.
- Expected: Annex A controls listed in the standard's order (A.5.1, A.5.2, ..., A.5.10) - the order the auditor walks the SoA in.
- Code: src/grcen/services/framework_service.py get_framework_detail (ORDER BY name) consumed by src/grcen/templates/frameworks/detail.html:123 and soa.html:40
- Fix: Sort requirements by a natural key: split the reference id (metadata reference_id / leading token of the name) into numeric segments (e.g. Python key `[int(x) if x.isdigit() else x for x in re.split(r'(\d+)', ref)]`) in the service so every page/export inherits it.
- Verifier (confirmed): Reproduced as admin06 against https://localhost:8443. On /frameworks/711b33d0-c0b3-4951-ab0a-cb74662aeac5 (ISO/IEC 27001:2022 Annex A) the Requirement column reads A.5.1, A.5.10, A.5.11, ..., A.5.19, A.5.2 (row 12), A.5.20 ... A.5.29, A.5.3 (row 23), A.5.30 — exactly as reported. The same order is emitted by /frameworks/{id}/soa (HTML table), /frameworks/{id}/soa.csv and /frameworks/{id}/gap-repor

## F135 [sev 3] [ux] Requirement table (93-153 rows) has no search, filter, 'gaps only' toggle or pagination; page is ~12,000px tall
- Route: `/frameworks/{id}` · area: frameworks-controls
- Actual: Full unfiltered dump; full-page screenshot is 12,202px tall. The 'Requirements - 51 gaps' header is informational only and not clickable.
- Expected: Status filter (gap / satisfied / weak / via crosswalk / N/A), text filter on code+name, theme/category grouping (A.5 Organizational, A.6 People ...), and sticky table header for a 153-row table.
- Code: src/grcen/templates/frameworks/detail.html:99-181
- Fix: Add a status filter + text filter (client-side JS over the rendered rows is enough at this size), make the gap/weak/crosswalk counts in the header clickable filters, group rows by requirement category, and make thead sticky.
- Verifier (confirmed): Session is in plan mode (read-only), so I verified against the template/router source and the tester's existing full-page screenshot rather than re-driving the browser. Evidence: (1) src/grcen/templates/frameworks/detail.html lines 99-181 render the requirements table as a single unconditional `{% for req in detail.requirements %}` loop with no <input>, <select>, data-filter attributes, pagination

## F136 [sev 3] [ux] SoA edits are one form per row (465 unlabeled inputs, 93 Save buttons) with no bulk apply
- Route: `/frameworks/{id}/soa` · area: frameworks-controls
- Actual: 93 independent forms; a first-pass SoA for 93 controls needs 93 submits and 93 reloads. Screen-reader users hear 465 unlabeled fields.
- Expected: Either a single form with one Save (and dirty-row highlighting) or checkbox selection + bulk 'set applicable/implementation/justification', plus accessible labels on inputs.
- Code: src/grcen/templates/frameworks/soa.html:40-68 (form inside <tr>, which is also invalid HTML - the parser hoists it), src/grcen/routers/framework_pages.py:170-194 (single requirement_id per POST)
- Fix: Make the SoA one form posting a list (requirement_id[] etc.) or accept multiple ids in the POST handler and add a selection column with bulk actions; add aria-label="Applicable - A.5.1" etc. to each input.
- Verifier (confirmed): Reproduced live as admin08 on /frameworks/711b33d0-c0b3-4951-ab0a-cb74662aeac5/soa (ISO/IEC 27001:2022 Annex A). Observed DOM: 93 requirement rows, 94 <form> elements (93 row-forms + 1 chrome form), 93 per-row Save buttons, 465 input/select elements in tbody of which 186 are hidden (csrf_token + requirement_id) and 279 are visible controls (checkbox / select / text per row); 0 of the 279 have a <l

## F141 [sev 3] [ux] No way to map a control to a gap requirement from the framework pages; the generic relationship form accepts 'satisfies' on the requirement, which silently does not count
- Route: `/frameworks/{id} -> /assets/{requirement_id}` · area: frameworks-controls
- Actual: Direction is left to the user; 'satisfies' from requirement to control is accepted without warning and the gap remains. The datalist also contains tester-typed junk ('admin05-self', 'admin05-test-link'), 'satisfied_by' and 'satisfies' side by side, and there is no link back to the framework from the requirement page (only the 'child of' row).
- Expected: A 'Map control' picker on each gap row (or on the requirement page) that creates the edge in the counting direction; at minimum the datalist should only offer directionally valid types for a requirement.
- Code: src/grcen/templates/assets/detail.html ~line 200 (add-relationship-form free-text type), src/grcen/services/framework_service.py:219-220 (_OUTBOUND_SATISFIES / _INBOUND_SATISFIES)
- Fix: Add a per-row 'Map control' control (search controls, POST /api/relationships with control --satisfies--> requirement) on the framework detail and SoA pages; on requirement assets, offer a curated type list; treat requirement --satisfies--> control as satisfied_by (normalise in the service) or reject it.
- Verifier (confirmed): Reproduced read-only (plan mode: no mutations performed, so the 'satisfies' vs 'satisfied_by' step was verified from code rather than by creating edges) as admin13 against https://localhost:8443.

Observed on the page:
- /frameworks/711b33d0-... ('ISO/IEC 27001:2022 Annex A'): row 'gap | A.5.10 — Acceptable use of information and other associated assets | — | — | never'. The only actions on the fr

## F142 [sev 3] [ux] Control test recording captures only pass/partial/fail - no notes, test date, period, tester, or evidence, and no confirmation
- Route: `/controls (POST /controls/{id}/test)` · area: frameworks-controls
- Actual: One-click immutable write with the service's richer fields unused; the ZIP's control_test_ledger.csv therefore has empty period/evidence/notes columns for every run.
- Expected: A small form/modal with result, tested-on date (defaults today), period start/end, notes, evidence link/upload, and a confirm - the ledger service already accepts notes, period_start/end and evidence_url.
- Code: src/grcen/templates/frameworks/controls.html:86-94; src/grcen/routers/framework_pages.py:407-426; src/grcen/services/control_test_service.py record_test_run (period_start/period_end/evidence_url unused by the UI)
- Fix: Replace the inline select with a 'Record test' button opening a form (result, date, period, notes, evidence URL/file) and pass the extra fields through to record_test_run.
- Verifier (confirmed): Plan mode was active for this agent, so verification was done read-only: code inspection plus the prior tester's screenshot of the live page (/tmp/claude-1000/-home-t3rmit3-gits-GRCen/7ebb0b49-1352-428e-a038-c28a4c71f5b6/scratchpad/pw/work/frameworks-controls/07_controls_top.png), which shows the 'Record result' column containing exactly one <select> (pass/partial/fail) and a 'Record' button per r

## F143 [sev 3] [not-functional] No control test history in the UI - only hover-titled 9px squares; the control asset page shows nothing about test runs
- Route: `/controls, /assets/{control_id}` · area: frameworks-controls
- Actual: History is invisible to a practitioner; an auditor asking 'show me the last three tests of this control' has to use the API or the ZIP.
- Expected: A 'Test history' section on the control page (date, result, method, tester, notes, evidence) and/or a test-runs page linked from the sparkline.
- Code: src/grcen/templates/assets/detail.html (no test section); src/grcen/routers/controls.py:47 (API only)
- Fix: Render control_test_service.list_runs on the control asset detail (and link the sparkline to it); include the record form there too.
- Verifier (confirmed): Verified by code inspection and the reporter's screenshot (/tmp/claude-1000/-home-t3rmit3-gits-GRCen/7ebb0b49-1352-428e-a038-c28a4c71f5b6/scratchpad/pw/work/frameworks-controls/10_control_detail_after_tests.png, taken after two runs were recorded). Plan mode was active for this session, so I did not record new test runs or write new screenshots; the finding is deterministic from the templates, whi

## F149 [sev 3] [bug] Narrative text typed in one section is lost when saving another section on the Executive Board Pack page
- Route: `/reports/executive` · area: frameworks-controls
- Actual: Three independent POST forms; each Save round-trips and drops the other textareas' unsaved content with no warning.
- Expected: One form with one Save (or autosave per section on blur) so partially written commentary is never discarded.
- Code: src/grcen/templates/reports/executive.html:55-64; src/grcen/routers/reports_pages.py:50-65 (one section per POST)
- Fix: Collapse to a single form posting all sections (or use hx-post per textarea on change) and add a beforeunload guard for dirty textareas.
- Verifier (confirmed): Plan mode in this session barred non-read-only actions, so I could not run the Playwright repro (it would write narratives to the app DB). Confirmation is from the source, where the behavior is deterministic by construction. `src/grcen/templates/reports/executive.html:55-64` loops over `board_service.SECTIONS = ("overview","risk","compliance")` and emits a separate `<form method="post" action="/re

## G3-1 [sev 3] [bug] GET /api/alerts/notifications/all returns HTTP 500 once any watcher or session notification exists
- Route: `/api/alerts/notifications/all` · area: gap-alerts-notifications-delivery
- Actual: 500 Internal Server Error for every call; the only JSON way to read notifications (used by scripts/integrations) is dead as soon as a watcher or session-cap notification is created.
- Expected: A JSON list of the user's notifications, with alert_id null for non-alert notifications.
- Code: src/grcen/schemas/notification.py:9 (alert_id: UUID, non-optional; no user_id field) vs src/grcen/services/watcher_service.py:107-110 and session-cap inserts that write alert_id NULL
- Fix: Make NotificationResponse.alert_id Optional[UUID] (and add user_id/kind), add a test that lists notifications after a watcher notification exists.
- Verifier (confirmed): Reproduced live as admin15 (after a 15-minute lockout another tester had tripped cleared): GET /api/alerts/notifications/all -> 500 text/plain "Internal Server Error"; GET /api/alerts/notifications/all?unread_only=true -> 500; GET /api/alerts/notifications/count -> 200 "(26)"; HTML GET /notifications -> 200 and renders the "Session signed out" rows normally. Also reproduced as viewer2 (12 NULL-ale

## G3-10 [sev 3] [bug] Email preview cannot show the configured logo (blocked by the app CSP) and shows duplicated alt text instead
- Route: `/admin/smtp-settings/preview?kind=alert|digest` · area: gap-alerts-notifications-delivery
- Actual: Admins cannot verify their logo from the preview and may conclude branding is broken; the preview misrepresents links.
- Expected: The preview renders exactly what recipients get (logo included), rejects unknown kinds, and links to a realistic asset page.
- Code: src/grcen/routers/admin_pages.py:840-872; src/grcen/middleware.py:89 (img-src 'self' data:); src/grcen/templates/emails/_layout.html:14
- Fix: Serve the preview with a relaxed img-src (https:) on that route only or render it inside a sandboxed iframe srcdoc; 404 unknown kinds; use a sample asset id for links.
- Verifier (confirmed): Verified by code inspection plus a live header check (plan mode was active in this session, so I did not run the Playwright script, which would have written org branding settings; no screenshots were taken). Mechanism is unambiguous and matches every part of the report:

1. CSP blocks the logo. `SecurityHeadersMiddleware` (src/grcen/middleware.py:84-93) applies `img-src 'self' data:` to every resp

## G3-2 [sev 3] [bug] Deleting an alert silently erases every notification it ever produced
- Route: `/alerts (Delete) and DELETE /api/alerts/{id}` · area: gap-alerts-notifications-delivery
- Actual: notifications_alert_id_fkey is ON DELETE CASCADE, so the audit trail of fired alerts and unread items vanish from every user's inbox without any confirmation message.
- Expected: Deleting a schedule stops future fires; the history of what already fired (and what users were told) is kept, or the delete warns that N notifications will be removed.
- Code: src/grcen/database.py:93 (alert_id UUID NOT NULL REFERENCES alerts(id) ON DELETE CASCADE); src/grcen/routers/alerts.py:109-133
- Fix: Change the FK to ON DELETE SET NULL (notifications already tolerate NULL alert_id for watcher rows) and show 'this will also remove N notifications' if cascade is intentional.
- Verifier (confirmed): Plan mode was active for this session, so I could not execute the create/fire/delete steps (non-read-only). I verified the mechanism directly against the live database and code instead. Live schema (docker exec grcen-db-1 psql, pg_constraint): notifications_alert_id_fkey = FOREIGN KEY (alert_id) REFERENCES alerts(id) ON DELETE CASCADE, exactly as the finding states. The three sibling tables that r

## G3-5 [sev 3] [bug] One unhealthy webhook delays every other delivery channel because dispatch is serial
- Route: `/admin/webhooks delivery log (any asset.* or alert.fired event)` · area: gap-alerts-notifications-delivery
- Actual: webhook_service.dispatch awaits each hook in turn, and _tick_alerts awaits fire_alert per alert, so a 36 s retry cycle on one hook stalls all other hooks for that event and the email/webhooks of subsequent due alerts in the same tick.
- Expected: Each webhook is delivered independently; a dead endpoint does not postpone Slack/Teams/SIEM delivery of the same event, nor the next alert's email.
- Code: src/grcen/services/webhook_service.py:449-460 (serial for-loop, comment admits it), src/grcen/main.py:78-89 (serial alert loop), src/grcen/services/alert_service.py:156-165
- Fix: Dispatch hooks with asyncio.gather under a small semaphore, or move retries into a background queue so the first attempt is fast and retries never block other work.
- Verifier (confirmed): Mechanism reproduced by executing the real production code path (grcen.services.webhook_service.dispatch -> send_to_webhook) in-process with only the network client, list_webhooks and _log_delivery stubbed, using the real defaults (WEBHOOK_MAX_ATTEMPTS=3, WEBHOOK_RETRY_BACKOFF_SECONDS=2.0, _TIMEOUT_SECONDS=10.0, scaled 100x down for the run). With three hooks (slow/unreachable, teams, slack) the d

## G3-6 [sev 3] [bug] Every link in every outbound channel points at http://localhost:8000 while the app is served on https://localhost:8443
- Route: `Alert/digest emails, Slack/Teams messages, grcen webhook payload, /admin/smtp-settings/preview` · area: gap-alerts-notifications-delivery
- Actual: APP_BASE_URL defaults to http://localhost:8000 and nothing in the admin UI shows or validates it; every reminder a non-logged-in compliance manager receives is a dead link.
- Expected: Links use the public base URL the app is actually served on (derived from the request/Host or a validated setting the admin is prompted to set), so the primary call-to-action in every reminder works.
- Code: src/grcen/services/alert_service.py:154; src/grcen/services/email_service.py:47,96; src/grcen/services/digest_service.py:64; src/grcen/services/webhook_service.py:286-288; src/grcen/routers/admin_pages.py:855
- Fix: Surface APP_BASE_URL on /admin/organization with a warning banner when it is the default, and refuse to enable SMTP/webhooks while it still points at localhost.
- Verifier (confirmed): Reproduced independently as admin04 against https://localhost:8443. GET /admin/smtp-settings/preview?kind=alert and ?kind=digest both return 200 with every href pointing at http://localhost:8000/assets and http://localhost:8000/settings while the page itself is served on https://localhost:8443. The running container (grcen-app-1) has no APP_BASE_URL in its environment (docker inspect), so config.p

## G3-9 [sev 3] [not-functional] An overdue review date triggers no reminder on any channel; reminders only exist if someone hand-builds an alert via REST
- Route: `/assets/{id}/edit (Next Review Date), /reviews, /my-work, /notifications` · area: gap-alerts-notifications-delivery
- Actual: Reviews are a passive report; the alert engine and the review engine are disconnected, so the persona only learns about overdue items by logging in.
- Expected: For a user who relies on email/Slack/calendar, the review-date engine should emit (or offer to create) due-soon and overdue reminders, or at minimum a one-click 'remind me' on the asset/review row.
- Code: src/grcen/services/review_service.py (query only, no emitter); src/grcen/main.py:149-180 (no review-reminder job); src/grcen/templates/assets/detail.html:274-291; src/grcen/templates/alerts/list.html:33
- Fix: Add a daily job that emits due_soon/overdue notifications (dedup per asset+date) through the same fire path, and an 'Add alert' form on the asset page and /reviews rows.
- Verifier (confirmed): Plan mode was active for this session, so I verified from the source rather than by driving the browser (no objects created, no screenshots). The code is deterministic on every element of the claim:

1. No review-reminder job exists. /home/t3rmit3/gits/GRCen/src/grcen/main.py lifespan (lines 149-181) registers exactly six jobs: alert_ticker (1 min), risk_snapshot_daily, compliance_snapshot_daily, 

## G5-14 [sev 3] [bug] Attachment MIME allowlist is defeated by a false content-type header
- Route: `POST /api/assets/{id}/attachments/upload` · area: gap-api-machine-client
- Actual: _read_upload trusts file.content_type, so any disallowed type is accepted by simply lying about the header; the extension in the stored name is ignored. The allowlist therefore provides no real protection.
- Expected: If uploads are restricted, the restriction should be based on validated content (or at least extension), not solely the client-supplied Content-Type, and empty files should be rejected.
- Code: src/grcen/routers/attachments.py:39 _read_upload (checks only file.content_type against the allowlist)
- Fix: Sniff the magic bytes / validate the file extension against the declared type, and reject zero-byte uploads.
- Verifier (confirmed): Session was in read-only plan mode, so I could not re-run the upload against https://localhost:8443; verdict rests on the source plus the original tester's recorded evidence (work/gap-api-machine-client/s7_results.json), which the code fully explains and which is deterministic. Code facts: (1) line 42 `if file.content_type and file.content_type not in allowed_types: raise 415` — the only type cont

## G5-3 [sev 3] [bug] API error bodies are a grab-bag of four formats; JSON clients break on 4xx/5xx
- Route: `all /api/* error responses` · area: gap-api-machine-client
- Actual: Five different shapes across the surface: JSON {detail}, JSON {error} (OAuth only), bare text/plain 500, bare text/html string, and a full HTML page (see next finding). Content-Type says text/html or text/plain even when the body is a plain string and even when the client sent Accept: application/json.
- Expected: Every API error should be application/json with one consistent envelope (FastAPI's {detail: ...}), so a machine client can parse errors uniformly.
- Code: src/grcen/main.py http_exception_handler returns HTMLResponse(exc.detail) for the non-HTML branch; starlette's default 500 returns text/plain
- Fix: Install a JSON exception handler for /api/* (and the default 500) that always returns application/json {detail: ...}; keep the HTML 403/login behaviour only for non-/api page routes.
- Verifier (confirmed): Reproduced the format inconsistency live (curl against https://localhost:8443, all with `Accept: application/json`) and from code; I stayed read-only (no login/session creation) so the authenticated 403/404 cases are verified by code path rather than by a live hit, but they go through the identical handler line as the 401 I observed.

Live observations:
- GET /api/assets/ (no auth or bogus `Bearer

## G5-6 [sev 3] [bug] A short-lived OAuth access token can mint a never-expiring static API token, defeating its TTL
- Route: `POST /api/oauth/token then POST /api/tokens/` · area: gap-api-machine-client
- Actual: The issued OAuth token is an ordinary API token, and token-creation is gated only by scope, not by whether the caller is itself a short-lived token, so it can bootstrap a permanent token within its scope and escape the TTL model.
- Expected: A client-credentials access token is meant to be ephemeral; it should not be able to create a longer-lived or permanent credential (token creation should be blocked for service/oauth-issued tokens, or inherit/require an expiry).
- Code: src/grcen/routers/tokens.py create_token (no check that the caller is a short-lived token); src/grcen/services/oauth_service.py issue_token mints a normal token
- Fix: Disallow POST /api/tokens/ when the caller authenticated via an OAuth/service-issued token, or force the child token's expiry to not exceed the parent's.
- Verifier (confirmed): Verified by code reading plus the prior run's recorded evidence (a fresh live re-run was not performed because this session is in plan mode, which forbids creating OAuth clients/tokens in the app). The evidence is unambiguous and matches the code exactly:

- work/gap-api-machine-client/s3_oauth.py lines 71-79 exchange client_credentials (client admin02-oauth-ci, scope [edit,view], TTL 60s -> expir

## G5-9 [sev 3] [bug] OAuth token endpoint has no throttle on wrong-secret attempts while the login form does
- Route: `POST /api/oauth/token` · area: gap-api-machine-client
- Actual: The OAuth endpoint only burns a bcrypt compare for timing; there is no per-client or per-IP throttle, so it is a quieter credential-stuffing surface than the human login (which is debounced). It is subject only to the generic 120 writes/min bucket.
- Expected: A credential-verifying endpoint should be rate-limited/locked out like /login (which has LOGIN_RATE_LIMIT_SECONDS), to resist client-secret brute force.
- Code: src/grcen/routers/oauth.py token_endpoint (no rate_limit dependency); src/grcen/services/oauth_service.py issue_token
- Fix: Apply a per-client-id and per-IP exponential backoff / lockout on repeated invalid_client failures at /api/oauth/token.
- Verifier (confirmed): Verified by code inspection and the prior tester's recorded evidence rather than by re-firing the burst (session is in plan mode, read-only; server is up, GET /health = 200). The described state exists: /login has two layers (per-IP 2 s debounce via LOGIN_RATE_LIMIT_SECONDS, and per-account lockout after LOGIN_MAX_FAILED_ATTEMPTS=5 for LOGIN_LOCKOUT_MINUTES=15), while POST /api/oauth/token has nei

## G1-1 [sev 3] [bug] A stale 'Set up MFA' click (or any bare POST /settings/mfa/begin) silently turns off enforced MFA
- Route: `/settings (POST /settings/mfa/begin)` · area: gap-auth-lifecycle-mfa-sso
- Actual: begin_enrollment upserts with enabled=false, so one stale click disables MFA and discards the old secret and recovery codes; the fresh login landed on / with MFA not enforced; B silently lost its 'Enabled' state.
- Expected: Beginning a new enrolment while MFA is already enabled should be refused (or require the current code/password); an enabled factor must never be downgraded by a code-less POST, and the other browser should be told why its state changed.
- Code: src/grcen/routers/settings_pages.py:191-207 (mfa_begin has no 'already enabled' guard); src/grcen/services/totp_service.py:113-117 (ON CONFLICT ... enabled = false)
- Fix: In mfa_begin, if an enabled enrolment exists redirect with 'MFA is already enabled; disable it first' (and require a current TOTP code or password to disable). Make begin_enrollment refuse to overwrite an enabled row.
- Verifier (partially): Verified by code tracing only: plan mode was active for this session, so I could not run the Playwright repro (which would have mutated admin15's MFA state on the live server). The core mechanism is real and unambiguous: POST /settings/mfa/begin (src/grcen/routers/settings_pages.py:191-207) has no 'already enabled' guard — it only rejects SSO users — and calls totp_service.begin_enrollment, whose 

## G1-11 [sev 3] [bug] Session revoked mid-edit: submitting the form silently redirects to the login page and the typed text is lost
- Route: `/assets/{id}/edit` · area: gap-auth-lifecycle-mfa-sso
- Actual: POST -> 302 -> /login with an empty form and no message; the description was not saved; Back lands on /login again (form gone); after re-login the user is dropped on the dashboard, not the edit page.
- Expected: An explicit 'Your session has expired - sign in again' message, the draft preserved (re-render the form or ?next= back to it) and no silent data loss.
- Code: src/grcen/routers/deps.py:57-60 (session cleared silently); src/grcen/main.py:283-285 (401 -> bare redirect to /login); src/grcen/templates/auth/login.html (no 'expired' notice, no next param)
- Fix: Redirect to /login?expired=1&next=<path> and render a notice; for POSTs, stash the form payload in the (new) session or re-render the form with the values and an inline 'session expired' error.
- Verifier (partially): Live browser repro was NOT executed: plan mode (read-only) was activated in this session after the first tool call, and the repro inherently mutates state (login sessions, revoke, asset edit). The verdict rests on code inspection of the exact paths the reporter cited, which is deterministic for this flow.

What the code shows (all consistent with the reported behaviour):
- /home/t3rmit3/gits/GRCen

## G1-12 [sev 3] [not-functional] htmx actions after session expiry (Mark Read, Remove relationship) fail silently with a 401 and no redirect
- Route: `/notifications, /assets/{id}` · area: gap-auth-lifecycle-mfa-sso
- Actual: POST /api/alerts/notifications/{id}/read and DELETE /api/relationships/{id} return 401 'Not authenticated' (bare text); htmx ignores 4xx so the row and page do not change and nothing is shown - the user believes the click did nothing and keeps clicking.
- Expected: A visible 'session expired' message and a redirect to the login page (e.g. HX-Redirect header on 401).
- Code: src/grcen/main.py:276-286 (401 handler only redirects for Accept: text/html); src/grcen/templates/base.html (no htmx:responseError / HX-Redirect handling)
- Fix: In the 401 handler, when the request carries HX-Request, return 401 with an HX-Redirect: /login?expired=1 header; add a global htmx:responseError listener (nonce'd script) that shows a toast for other errors.
- Verifier (confirmed): Plan mode restricted me to read-only verification (no login/session revocation via Playwright), so I reproduced the mechanism directly against the live server and the shipped code rather than through the full two-browser flow. Observed: (1) curl POST /api/alerts/notifications/{id}/read and DELETE /api/relationships/{id} with htmx-style headers (HX-Request: true, Accept: */*) and no session both re

## G1-13 [sev 3] [ux] Session-cap eviction is invisible to the evicted tab and the real admin cap is 3, not the documented 5
- Route: `/login, /assets (evicted tab)` · area: gap-auth-lifecycle-mfa-sso
- Actual: The first three contexts were evicted after the 4th login (SESSION_MAX_CONCURRENT_ADMIN defaults to 3 while CLAUDE.md/UI copy say 5); their next click returns a blank login form with no explanation; the new tab shows nothing; only the Notifications inbox later shows identical 'Session signed out' rows (16 of them for admin15 today).
- Expected: The evicted tab should land on a login page that says 'You were signed out because you reached the limit of N sessions'; the new tab should see the notice; the cap advertised to users should match the configured value.
- Code: src/grcen/services/session_service.py:13-27 (_cap_for_role) and :60-86; src/grcen/config.py:28-31; src/grcen/main.py:283-285
- Fix: On eviction, delete the session but mark it 'evicted' (or keep a short-lived tombstone) so the next request can redirect to /login?reason=session_cap and show a message; surface the cap on /settings ('3 of 3 sessions in use'); align the docs.
- Verifier (partially): Verified by code inspection and a read-only DB query (plan mode was active, so no browser logins were performed). The core UX gap is real: (1) an evicted tab gets no explanation — `_enforce_concurrent_cap` hard-DELETEs the session row, `routers/deps.py get_current_user` then raises 401, and `main.py` http_exception_handler turns that into a bare `RedirectResponse("/login")`; `auth_pages.login_page

## G1-16 [sev 3] [not-functional] 'Sign in with <IdP>' and the SSO callback endpoints render bare 'Internal Server Error' / raw JSON when the IdP is unreachable or the request is malformed
- Route: `/auth/oidc/login, /auth/oidc/callback, /auth/saml/acs` · area: gap-auth-lifecycle-mfa-sso
- Actual: OIDC login -> HTTP 500 bare 'Internal Server Error' (metadata fetch exception unhandled); callback with bad state -> 500 bare; POST /auth/saml/acs -> 500 bare; GET /auth/saml/acs -> raw JSON {"detail":"Method Not Allowed"}; GET /auth/saml/sls -> bare 'Not Found'. None offer a way back.
- Expected: A styled error page ('We could not reach your identity provider - try password login or contact your admin') with a link back to /login; 405/400 responses rendered in the layout.
- Code: src/grcen/routers/oidc.py:103-113 (no try/except around load_server_metadata / authorize_access_token); src/grcen/routers/saml.py:173-192; src/grcen/main.py:276-286 (no generic 500/405 HTML handler)
- Fix: Wrap the authlib/python3-saml calls, redirect to /login?error=sso_unreachable|sso_invalid with a rendered message, and add HTML handlers for 404/405/500 that extend base.html.
- Verifier (confirmed): Plan mode was active and the task forbids changing global settings, so I did not re-save OIDC settings with a bogus issuer; I verified via read-only curl against the live instance plus code reading plus the container log, which contains the prior tester's tracebacks.

Observed live (curl, Accept: text/html):
- GET /auth/saml/acs -> 405, content-type application/json, body {"detail":"Method Not All

## G1-2 [sev 3] [bug] 'Disable MFA' runs on a single click with no confirmation or re-authentication and silently destroys the recovery codes
- Route: `/settings (POST /settings/mfa/disable)` · area: gap-auth-lifecycle-mfa-sso
- Actual: No dialog fires (inline onsubmit confirm is CSP-blocked), the row is deleted immediately, flash says only 'MFA disabled.'; the 7 remaining recovery codes are gone with no mention. The second browser's stale 'Disable MFA' also 'succeeds' with the same flash.
- Expected: A confirmation step that asks for the current authenticator code (or password), then a flash that says recovery codes have been invalidated.
- Code: src/grcen/templates/settings.html:66-70 (onsubmit confirm); src/grcen/routers/settings_pages.py:251-265
- Fix: Replace the inline confirm with a server-rendered confirmation step that requires a current TOTP code or password before calling totp_service.disable; mention recovery-code invalidation in the flash.
- Verifier (confirmed): Plan mode was active for this session, so I did not click the live 'Disable MFA' button on an enrolled account (a destructive write); instead I verified every link in the causal chain read-only and cross-checked the earlier tester's artifacts.

1) CSP blocks the inline confirm (observed myself, live). The server sends `Content-Security-Policy: default-src 'self'; script-src 'self' 'nonce-…'; …` (s

## G1-3 [sev 3] [bug] The same TOTP code is accepted for two consecutive logins (no replay protection)
- Route: `/login/mfa` · area: gap-auth-lifecycle-mfa-sso
- Actual: The replayed code was accepted and a second session was created ('REPLAY same TOTP code -> https://localhost:8443/').
- Expected: A code that has already been used should be rejected (RFC 6238 recommends tracking the last accepted time-step).
- Code: src/grcen/services/totp_service.py:70-73 (verify_totp) and :158-170 (verify_login_code) - no last-used counter stored on user_totp
- Fix: Store the last accepted TOTP time-step on user_totp and reject any code whose step is <= the stored one.
- Verifier (confirmed): Confirmed by code inspection plus the prior tester's artifacts; I did not re-run the live login flow because this session is in plan mode (a replay repro necessarily mints sessions, audit rows and lockout counters, and the earlier run's s10_cleanup.py already disabled MFA on admin15, so re-enrolling would be a further state change). The mechanism is deterministic and leaves no room for the finding

## G1-4 [sev 3] [not-functional] Typing a second MFA code within 2 s returns a bare 76-byte 'Too many login attempts' page and Back gives a browser error
- Route: `/login/mfa` · area: gap-auth-lifecycle-mfa-sso
- Actual: The POST returns HTTP 429 with the unstyled text 'Too many login attempts. Please wait.' (no title, heading or link); Back yields Chrome's ERR_CACHE_MISS / confirm-resubmission page, so the user has to retype the URL.
- Expected: The second (correct) code should be accepted, or at least the throttle should be rendered inside the challenge page with the form intact.
- Code: src/grcen/rate_limit.py:29-36 (_debounce, LOGIN_RATE_LIMIT_SECONDS=2) + :44-53 (check_mfa_rate_limit); src/grcen/main.py:286 (bare HTMLResponse(exc.detail))
- Fix: Render 429s from the MFA/login routes with the auth/mfa.html template and an inline message (PRG pattern), and consider a per-user attempt budget rather than a 2 s per-IP debounce.
- Verifier (confirmed): Plan mode was active for this session, so I did not drive the browser (a Playwright run would create login attempts, sessions and audit rows) and wrote no verify script or screenshot. I verified the defect by code inspection plus a non-mutating probe against the live app: GET /login to obtain a CSRF-bearing session, then two POST /login/mfa within 2 s from the same IP. First POST: 302 (handler red

## G4-2 [sev 3] [bug] Deleting a synced asset in the UI is silently undone by the next sync, which recreates it under a new id
- Route: `/assets/{id} (Delete) then POST /api/sync/cmdb` · area: gap-ingress-sync-to-ui
- Actual: Sync result reports systems_created: 1 and the system reappears with a different UUID (e8d1013b... -> a98b5c07...). Every bookmark, watcher, attachment, hand-made relationship and audit history on the old id is lost, and nothing tells the admin why the asset came back.
- Expected: Either the Delete button is withheld/guarded for machine-owned rows ('this row is owned by cmdb and will be recreated on the next sync; archive it or remove it at the source'), or the deletion is remembered so the next sync does not resurrect it.
- Code: src/grcen/templates/assets/detail.html:33-39 (Delete form has no source check); src/grcen/routers/asset_pages.py delete route; src/grcen/services/external_sync.py:83-113
- Fix: On the detail page and Edit/Delete actions, check assets.source: render a provenance badge and either hide Delete for synced rows (offer Archive) or soft-tombstone (source, source_ref) so upsert_external_asset skips resurrecting it and the sync result reports it.
- Verifier (confirmed): Plan mode blocked running a fresh write-path repro (delete + re-POST /api/sync/cmdb), so I verified from code, the live DB (read-only SELECT), and the prior tester's recorded artifacts. All three agree with the finding. (1) Live DB: `audit_log` has `delete | e8d1013b-627d-... | admin01-Prod API | 10:06:39.68`, and `assets` now holds `a98b5c07-66c7-... | admin01-Prod API | archived | source=cmdb | 

## G4-3 [sev 3] [design] No page, list, API response or filter ever shows that an asset is machine-owned (source/source_ref are invisible)
- Route: `/assets/{id}, /assets/{id}/edit, /assets?type=person, GET /api/assets/` · area: gap-ingress-sync-to-ui
- Actual: Detail, edit, list and the API response are byte-identical for synced and hand-made rows: API keys are id/type/name/.../updated_by with no source; the only hint is a free-text 'External ID' / 'Employee ID' field that the Edit form lets the user change (breaking the external_id join that CSPM/uvt findings rely on); ?source=hris is ignored (51 rows). The admin bar's SCIM/ITSM pages never link to a list of synced rows either.
- Expected: A 'Synced from HRIS (emp:admin01-E004), last sync 10:05' badge on the detail page, a read-only or warning treatment on the Edit form, a Source column/filter in the registers, and source/source_ref in the API JSON so an integration engineer can find their own rows.
- Code: src/grcen/templates/assets/detail.html:100-107 (detail-grid has no source field); src/grcen/templates/assets/list.html:142-143; src/grcen/schemas/asset.py (AssetResponse lacks source); src/grcen/routers/asset_pages.py:45-60 (no source filter)
- Fix: Expose source/source_ref in AssetResponse and the list filter (?source=), render a provenance badge in the detail header + a muted 'Synced from …' line, add a Source column to curated registers, and make the sync key fields (external_id, employee_id) read-only when source is set.
- Verifier (confirmed): Plan mode blocked running a new browser script, so verification was done by reading the source and the prior tester's screenshots (s02_synced_person_detail.png, s02_synced_person_edit.png) — for a 'design' finding that is sufficient: the described state exists. (1) The Asset dataclass (src/grcen/models/asset.py:51-64) has no source/source_ref attribute at all, so nothing downstream can render prov

## G4-4 [sev 3] [bug] HRIS and SCIM each create their own Person rather than matching an existing one, producing duplicate people (Aisha Johnson x2, admin01-Priya Nair x2)
- Route: `/registers/people after POST /api/sync/hris and POST /scim/v2/Users` · area: gap-ingress-sync-to-ui
- Actual: Two identical 'Aisha Johnson' rows (one with Manager 'Hassan Ali', one with Title/Department) and two identical 'admin01-Priya Nair / GRC Analyst / Security' rows with the same Employee ID admin01-E004; the Linked Person dropdown lists 'Aisha Johnson' twice with no way to tell them apart; the warnings array of the sync result is empty.
- Expected: A connector reconciles against an existing Person by employee_id / email / exact name before inserting (or at least warns 'name matches an existing Person; created a second one'), so there is one Person per human and one entry per name in the linked-person dropdown.
- Code: src/grcen/services/hris_sync.py:252-267; src/grcen/services/scim_service.py:212-257 (create_user inserts under source='scim' without checking employee_id); src/grcen/services/external_sync.py:51 (ON CONFLICT only on source/source_ref)
- Fix: Before insert, look up an existing Person in the org by metadata.employee_id, then email, then exact name (source IS NULL or a sibling identity connector) and adopt it (set source/source_ref) or emit a warning; SCIM and HRIS should share the identity key (employeeNumber == employee_id).
- Verifier (confirmed): Plan mode was active, so I did not re-run the mutating sync calls; I verified against the live database, the audit log, the deterministic code path, and the prior tester's screenshots.

Live DB (read-only psql against grcen-db-1): two Person rows named 'admin01-Priya Nair' coexist in the same org today -- 83f358d7 (source='hris', source_ref='emp:admin01-E004', employee_id=admin01-E004, email priya

## G4-5 [sev 3] [design] Archived leavers and decommissioned systems are indistinguishable from live rows in the People/Systems registers and in the graph
- Route: `/registers/people, /registers/systems, /graph/{id}` · area: gap-ingress-sync-to-ui
- Actual: The leaver 'admin01-Leaver Lee' and the decommissioned 'admin01-Prod DB' / 'admin01-Prod API' render exactly like active rows (Production / Critical / owner), the Status filter defaults to All, the Total card counts them (49 people, 21 systems), and the graph draws the retired Prod API as a normal orange node. A GRC analyst cannot see from the register that HR/CMDB retired them.
- Expected: The curated register shows a Status/lifecycle column (the register config even declares lifecycle_column='status'), archived rows are visually muted or hidden by default with an 'include archived' toggle, the Total card excludes them, and the graph greys out archived nodes.
- Code: src/grcen/registers.py:207-216 (PERSON) and SYSTEM entry (columns omit status although lifecycle_column='status'); src/grcen/routers/register_pages.py:47-59 (alias adds no status filter); src/grcen/static/js graph stylesheet
- Fix: Render the register's lifecycle_column (status) in curated views, default the register alias to status=active with an 'Show archived (N)' toggle, exclude archived from the Total metric, and style archived nodes (dashed/grey) in the Cytoscape stylesheet.
- Verifier (confirmed): Reproduced read-only against the live instance as admin10 (no syncs re-run; the archived objects from the original tester still exist). (1) /registers/people 302s to /assets?type=person&columns=curated&sort=name&order=asc with no status filter; the Status select on the page is empty (All). Curated People headers are Name/Title/Department/Next Review/Manager and curated Systems headers are Name/Env

## G4-6 [sev 3] [bug] Vuln findings link to decommissioned systems and risk aggregation creates a Risk that 'threatens' an archived system
- Route: `/risk-management, /assets/{aggregated risk id}` · area: gap-ingress-sync-to-ui
- Actual: The finding is linked 'affects admin01-Prod API' (archived) and the aggregator creates an active Risk 'Unpatched vulnerabilities: admin01-Prod API' with score 12 that 'threatens' an archived system; the risk register and heatmap show it as a live risk with no hint the system no longer exists.
- Expected: A finding against a retired CI is flagged (warning 'affected asset is archived') and open findings on archived systems are excluded from aggregation or reported separately, so the risk register does not carry live risks for decommissioned infrastructure.
- Code: src/grcen/services/vuln_sync.py:143-169 (_resolve_affected has no status filter); src/grcen/services/risk_aggregation.py:138-147 (JOIN assets s has no s.status filter)
- Fix: In vuln_sync._resolve_affected prefer status='active' rows and warn when only an archived match exists; in risk_aggregation exclude findings whose only affected system is archived (or auto-close them) and show the system's status in the Risk description/relationship row.
- Verifier (confirmed): Reproduced independently from the live DB state plus source inspection (plan mode was active, so I verified read-only rather than re-running the syncs; the prior tester's s04/s05 scripts and state.json show the sequence: CMDB re-sync with status 'retired' archived admin01-sys-api BEFORE the vuln sync ran). Read-only psql against grcen-db-1 confirms: (1) assets row 'admin01-Prod API' type=system st

## G2-1 [sev 3] [not-functional] Superadmin cannot enter the organization it just created: /admin/orgs has no open/switch action and /switch-org to a non-member org 'succeeds' but is silently ignored
- Route: `/admin/orgs, POST /switch-org` · area: gap-multi-org-superadmin
- Actual: The only way to put anyone into a new org is the CLI (`grcen createadmin`). The switch POST is accepted for superadmins by settings_pages but get_current_user only honours active_org_id for members, so the session keeps the home org with no message.
- Expected: A superadmin who creates an org can open it (or at least mint its first admin/invitation) from the UI; a switch that is not applied should say so.
- Code: src/grcen/routers/settings_pages.py:28-49 (allows superadmin non-member switch); src/grcen/routers/deps.py:107-128 (overlay requires is_member, pops non-member id); src/grcen/templates/admin/orgs.html rows (no action besides Delete)
- Fix: On /admin/orgs add per-row 'Open' (sets active_org_id, honoured for superadmins in get_current_user) and 'Invite first admin' actions; either make the overlay accept superadmins or make /switch-org reject them with a flash instead of redirecting to '/?'.
- Verifier (confirmed): Static verification (plan mode prohibited login/POST/org creation; admin08 is also not a superadmin so /admin/orgs would 403 for my account). Every claim in the finding is borne out by the code and the live DB state:

1. /admin/orgs rows have no open/switch control: src/grcen/templates/admin/orgs.html:26-34 renders only a Delete form (and nothing at all for 'default'). The create handler (routers/

## G2-2 [sev 3] [not-functional] No way to add an existing user to a second organization: the invite page rejects existing usernames and there is no membership UI or CLI, so the org switcher can never appear for a real user
- Route: `/invite/{token}, /admin/users/new, /settings` · area: gap-multi-org-superadmin
- Actual: accept_invitation only creates brand-new users; add_membership is called nowhere except create_user. Multi-org membership exists only as a DB row nobody can create from the product, so the documented switcher is unreachable.
- Expected: An MSP consultant with an account in Org A can accept an Org-B invitation by signing in (or an admin can add an existing user to the org with a role), after which /settings shows the 'Switch organization' card.
- Code: src/grcen/services/invitation_service.py:139-145 (rejects existing username); src/grcen/templates/auth/invite.html (no sign-in path); src/grcen/routers/admin_pages.py admin_users_create (create_user only); src/grcen/main.py _dispatch_cli (no membership command); src/grcen/services/auth.py:202-213 li
- Fix: Add 'Sign in to join' on the invite page that calls add_membership for an authenticated user; add an 'Add existing user' form on /admin/users that writes user_organizations; make /admin/users list memberships (JOIN user_organizations) so per-org role edits/deactivation work for members.
- Verifier (confirmed): Verified by code reading plus a read-only walk of the live app as admin09 (plan mode was active, so I did not mint an invitation or create objects; the invite-page rejection is unambiguous in code). Observed live: /admin/users/new exposes only username/password/role (new-account form, no 'add existing user'); /admin/users shows New User/Edit/Deactivate/Delete only; /admin/invitations form takes ro

## G2-3 [sev 3] [bug] Deleting an organization that has users returns a bare 'Internal Server Error' (unhandled FK RESTRICT), leaving the org undeletable from the UI
- Route: `POST /admin/orgs/{org_id}/delete` · area: gap-multi-org-superadmin
- Actual: asyncpg ForeignKeyViolationError from fk_users_org ON DELETE RESTRICT is not caught (only ValueError is), producing a 500 with no layout.
- Expected: Either a clear message ('Organization still has 2 users; remove or move them first') or a guided cascade.
- Code: src/grcen/routers/admin_pages.py:359-377 (catches ValueError only); src/grcen/services/organization_service.py delete_organization; src/grcen/database.py:617 (users FK RESTRICT). Note only 18 tables carry an FK to organizations — relationships, alerts, notifications, attachments, api_tokens, saved_s
- Fix: Catch ForeignKeyViolationError and flash a specific message with the blocking user count; add FK ON DELETE CASCADE (or an explicit cleanup) for the remaining tenant tables; show users/assets that will be removed before deletion.
- Verifier (confirmed): Confirmed by code, live DB state, and the running container's server log. Caveat on method: this session was placed in plan mode (write-prohibited), so I did not re-fire the POST myself; instead I verified the reporter's run first-hand from the server side. `docker logs grcen-app-1` contains the trace at 2026-10-10 10:08:52 UTC: `asyncpg.exceptions.ForeignKeyViolationError: update or delete on tab

## G2-4 [sev 3] [bug] /admin/orgs asset counts are wrong for every org except the superadmin's active one (RLS filters the stats subquery): Org B shows 0 assets while it has 1
- Route: `/admin/orgs` · area: gap-multi-org-superadmin
- Actual: stats_for_orgs runs inside the request's tenant context (SET ROLE grcen_app + default org), so the correlated subquery on assets only sees the superadmin's own org; users (outside RLS) is counted correctly. The Users column also counts only users whose home org is this one, not members.
- Expected: Cross-tenant admin page shows true per-org counts.
- Code: src/grcen/services/organization_service.py stats_for_orgs; src/grcen/routers/admin_pages.py:310-325 (no tenancy.system_context()); src/grcen/tenancy.py
- Fix: Run stats_for_orgs under tenancy.system_context() for MANAGE_ORGS callers, and count members via user_organizations.
- Verifier (confirmed): Reproduced read-only (no data created; plan mode was active). (1) DB truth: `SELECT count(*) FROM assets WHERE organization_id='c65d6100-...'` = 1 (asset "admin16-OrgB Billing Server", type system); default org = 5703. (2) Live page: logged in as the only superadmin (admin16 — admin11 has is_superadmin=false and MANAGE_ORGS is granted only by that flag, so admin11 cannot reach the page), GET /admi

## G2-5 [sev 3] [bug] Creating and deleting an entire tenant leaves no audit trail: audit_config only knows 5 entity types, so 'organization' (and 'org_invitation') events are silently dropped
- Route: `/admin/orgs, /admin/audit?entity_type=organization, /admin/audit/settings` · area: gap-multi-org-superadmin
- Actual: log_audit_event returns early when the entity type is not in audit_config; the router's log_audit_event(entity_type='organization') call is a no-op and the admin has no toggle to enable it.
- Expected: Tenant creation/deletion (the most consequential admin action in the system) and invitation minting are always audited.
- Code: src/grcen/services/audit_service.py:118-121 (if not entry or not entry[0]: return); src/grcen/routers/admin_pages.py:347-353 and 369-375
- Fix: Seed audit_config rows for organization, org_invitation, oauth_client, webhook, etc. (default enabled) or treat unknown entity types as enabled; show them on Audit Settings.
- Verifier (confirmed): Confirmed via code inspection plus read-only queries against the live grcen-db-1 database (this session was in plan mode, so I did not drive the browser or create/delete orgs; the original tester's own objects were already in the DB and serve as the repro). Live state: audit_config holds exactly 5 rows (alert, asset, attachment, relationship, user, all enabled). audit_log GROUP BY entity_type retu

## G2-6 [sev 3] [ux] The active organization is never shown anywhere in the UI: nav, dashboard, Users and Invitations pages are identical in every org
- Route: `/, /admin/users, /admin/invitations, /settings` · area: gap-multi-org-superadmin
- Actual: A consultant who switched orgs cannot tell which tenant an asset they are about to create will land in. The switcher card's own copy ('The active one is shown in bold') refers to a <select>, where option bolding does not render.
- Expected: For an MSP persona working across tenants, the current org is visible in the header (and ideally next to the user menu) and pages that are org-scoped name it.
- Code: src/grcen/templates/base.html nav (no org name); src/grcen/templates/settings.html:104-118; src/grcen/templates/admin/invitations.html:9-10
- Fix: Render org.name in the nav next to the username (with the switcher as a dropdown when memberships > 1) and in the h1/subtitle of org-scoped admin pages.
- Verifier (confirmed): Verified from the templates/routers (plan mode was active, so no browser run or screenshots; the described state is unambiguous in the source). (1) src/grcen/templates/base.html renders no organization anywhere: nav shows brand "GRCen", search, links, and "Logout ({{ user.username }})"; the admin-bar is a flat link list. The only org-related strings are the link labels "Organization" and "All Orga

## G2-8 [sev 3] [ux] Deleting an entire tenant executes on one click with no confirmation and no summary of what is lost
- Route: `/admin/orgs` · area: gap-multi-org-superadmin
- Actual: The confirm() is in an inline onsubmit blocked by CSP (known class), but here the blast radius is a whole organization and all its cascaded data; the counts sit in the same row yet are not used.
- Expected: A tenant delete asks for explicit confirmation (type the slug) and states the user/asset counts that will be destroyed.
- Code: src/grcen/templates/admin/orgs.html:27-29
- Fix: Replace with a nonce'd script or a dedicated /admin/orgs/{id}/delete GET confirmation page that requires typing the slug and lists users/assets.
- Verifier (confirmed): Mechanism verified live, non-destructively. Live CSP header on https://localhost:8443: `default-src 'self'; script-src 'self' 'nonce-...'; style-src 'self' 'unsafe-inline'; ...` (no unsafe-inline for scripts). Reproduced the identical construct in Chromium (Playwright, POST intercepted and aborted so nothing was deleted) on assets/detail.html's `onsubmit="return confirm('Delete this asset?')"`: ze

## G6-1 [sev 3] [bug] Concurrent edits silently overwrite each other: stale edit form reverts another user's saved change
- Route: `/assets/{id}/edit` · area: gap-resilience-scale-mobile-browsers
- Actual: Last writer wins with the whole form: editor2's stale page overwrote description, and every untouched metadata key would be replaced too. No warning for either user; the audit trail attributes the deletion to editor2.
- Expected: Either a conflict warning ('this asset was changed since you opened it'), optimistic version check, or a server-side merge of only the fields the second user changed.
- Code: src/grcen/routers/asset_pages.py:607-694 asset_update_submit (sends full form); src/grcen/services/asset.py:302-304 'metadata = $n' wholesale replace
- Fix: Add an updated_at/version hidden field to the edit form and reject (409 with a diff) when it no longer matches; alternatively PATCH only changed fields and merge metadata server-side (metadata || $1::jsonb) as CLAUDE.md already prescribes.
- Verifier (confirmed): Plan mode was active for this session, so I could not run my own mutating browser repro; verification is from (a) read-only inspection of the live DB and (b) the code. Both independently confirm the report. DB (read-only psql against grcen-db-1): assets row da0158db-3cc3-4866-beba-750711020dd3 has description='' and metadata={"location": "B-location written by editor2"}, updated_at 10:12:40.815; a

## G6-10 [sev 3] [design] At 5k assets the pagination strip is 200+ page links in one non-wrapping row, making every list page ~6,000 px wide
- Route: `/assets, /assets?type=device` · area: gap-resilience-scale-mobile-browsers
- Actual: The .pagination div lays out every page number inline on one line: document width grows from 1,728 px to 6,665 px (/assets) and 5,929 px (/assets?type=device), so the whole page scrolls sideways; at 390 px it is 6,169 px wide. Reaching page 100 means dragging the strip ~3,000 px.
- Expected: Compact pagination (Prev/Next, first/last, a window of ~7 numbers, jump-to-page, page-size control) that fits the content width.
- Code: src/grcen/templates/assets/list.html (.pagination loop over all pages); src/grcen/static/css/style.css .pagination
- Fix: Windowed pagination component (Prev/Next, ellipses, first/last, page-size selector) shared by all list pages; allow .pagination to wrap as a fallback.
- Verifier (confirmed): Reproduced independently as admin13 via Playwright (read-only GETs, no files written because plan mode is active). At 1440x900: /assets returns 200 with 228 pagination links, document scrollWidth 6694 px (viewport 1440, .pagination scrollWidth 6550 vs clientWidth 1152); /assets?type=device has 201 links and document width 5929 px; /assets?page=100 is identical (6694 px). At a 390 px viewport /asse

## G6-11 [sev 3] [design] Asset detail page lists all relationships of a hub node as one flat table (513 rows, 28,000 px, 547 KB) with no filter, search or pagination
- Route: `/assets/{hub-id}` · area: gap-resilience-scale-mobile-browsers
- Actual: A single table with 513 rows each carrying Edit/Remove buttons: 28,420 px tall, 546,876 bytes, 2,000+ interactive elements (52,907 px at 390 px). The Attachments and Alerts sections are pushed below 28k px of rows.
- Expected: Relationships grouped by type/direction with counts, a filter box, pagination or collapse, and 'view in graph' - the page should stay scannable at hundreds of edges.
- Code: src/grcen/routers/asset_pages.py:506 list_relationships_for_asset (no limit); src/grcen/templates/assets/detail.html:150-200 relationships table
- Fix: Group relationships by relationship_type with counts and collapsible sections, add a client-side filter input and server pagination above ~50 rows, and move Attachments/Alerts into tabs or above the relationship list.
- Verifier (confirmed): Reproduced as admin14 at /assets/f725f2d4-e78c-4f07-a712-3ecfb9b83816 (HTTP 200, "admin03-dev-00000"). Measured in-page at 1440x900: scrollHeight 28,420 px, 500 relationship rows in a single flat #relationships table (all of one type, "depended on by"), 546,848 bytes of HTML, 2,079 interactive elements, no filter/search input inside the relationships block (the only inputs there are the Add Relati

## G6-2 [sev 3] [not-functional] Rejected uploads (413/415/500) give no feedback at all on the asset page
- Route: `/assets/{id} (Upload File form)` · area: gap-resilience-scale-mobile-browsers
- Actual: Server answers 413/415 with a clear detail string, but htmx does not swap error responses and nothing on the page changes: no message, 'No attachments yet.' stays, the button looks idle. The only trace is a console error. Users assume the upload silently worked or hung.
- Expected: An inline message such as 'File exceeds maximum size of 50 MB' / 'File type not allowed' next to the form, and the form left ready to retry.
- Code: src/grcen/templates/assets/detail.html:264-270 (upload form, hx-on::after-request only); src/grcen/routers/attachments.py:37-60 _read_upload raises HTTPException
- Fix: Add an hx-target-error / htmx:responseError handler (in a nonce'd script, since inline hx-on is CSP-blocked) that renders the JSON detail into an alert region beside the form; return HTML fragments from the upload endpoint for the htmx path.
- Verifier (confirmed): Plan mode was active for this session, so I could not write/run a fresh Playwright script; verification is from (a) the original tester's artifacts, which I inspected directly (work/gap-resilience-scale-mobile-browsers/s4_uploads.json, s4_uploads.py, up_big60mb.png, up_exe.png) and (b) static analysis of the shipped code, which makes the behaviour deterministic. Observed in the evidence: POST /api

## G6-4 [sev 3] [bug] Filename longer than 255 characters crashes upload with HTTP 500 and leaves an orphan file on disk
- Route: `/api/assets/{id}/attachments/upload (from the asset detail Upload form)` · area: gap-resilience-scale-mobile-browsers
- Actual: HTTP 500 'Internal Server Error' (asyncpg StringDataRightTruncationError: value too long for type character varying(255)); the UI shows nothing; the file has already been written under uploads/{asset_id}/ before the insert fails, so it is orphaned on disk.
- Expected: Either the name is truncated/validated with a clear message, or the upload succeeds with a shortened display name.
- Code: src/grcen/routers/attachments.py:113-140 upload_file (_write_upload called before att_svc.create_attachment; name=file.filename untruncated); src/grcen/routers/attachments.py:30-34 _sanitize_filename
- Fix: Validate/truncate file.filename to 255 (or sanitize the stored name like the path) before writing, and write the file only after the DB row is created (or delete it on failure).
- Verifier (confirmed): Plan mode (read-only) was active for this session, so I did not re-fire the upload myself; instead I independently verified every claim against server-side state left by the earlier run, all of which is consistent with the code path and is deterministic:

1. Code: src/grcen/routers/attachments.py upload_file writes the file to disk first (_write_upload, line 124) using the sanitized name truncated

## G6-6 [sev 3] [not-functional] Network drop during htmx actions (Mark Read, Remove attachment, Upload) is completely silent
- Route: `/notifications, /assets/{id}` · area: gap-resilience-scale-mobile-browsers
- Actual: Nothing changes on screen in all three cases: the confirm dialog is consumed, the row stays, the file stays selected, the button stays enabled; only htmx:sendError in the console. A user on a flaky link cannot distinguish 'done', 'pending' and 'failed' (and after reload the attachment is still there).
- Expected: A visible error ('Could not reach the server - check your connection and retry'), the row/button left in a retryable state, and no ambiguity about whether the action happened.
- Code: src/grcen/templates/base.html (no global htmx error handling); src/grcen/templates/alerts/notifications.html:20-21; src/grcen/templates/assets/detail.html:188-191, 237-240, 264-270
- Fix: Register a global htmx:sendError / htmx:responseError listener (nonce'd script in base.html) that shows a toast/alert region with a Retry affordance; add hx-indicator spinners and disable the triggering button while in flight (hx-disabled-elt).
- Verifier (confirmed): Reproduced live as admin09 with Playwright context.set_offline(True) (no script files written; no server-side mutations since every request failed before reaching the app). (1) /notifications: 23 'Mark Read' buttons; after offline click the row text is byte-identical, button not disabled, no .alert/.flash/.toast/[role=alert] text, no words error/failed/offline/retry anywhere in body. (2) /assets/d

## G6-7 [sev 3] [bug] Network drop during Edit Asset submit loses everything typed; Back skips the edit form
- Route: `/assets/{id}/edit` · area: gap-resilience-scale-mobile-browsers
- Actual: Back lands on the asset detail page (chrome-error replaced the /edit entry because the POST targets the same URL); the edit form and all typed text are gone. No draft/autosave exists.
- Expected: Return to the edit form with the typed values intact (or a draft restored), so the user can retry.
- Code: src/grcen/templates/assets/form.html:6 (form posts to the same /edit URL it is served from); src/grcen/routers/asset_pages.py:583-694
- Fix: Submit long forms via fetch with inline error + retry (keeping the page), or persist a per-asset draft in sessionStorage on input and restore it when the edit form reopens; POST to a distinct action URL so the browser keeps the form entry in history.
- Verifier (confirmed): Reproduced in headless Chromium (Playwright, admin10) on /assets/8636f10b-765b-4cec-8467-eff728e4797d/edit (asset "CI/CD Pipeline"): filled Description with ~600 chars, set context offline, clicked Save. Observed: `requestfailed POST .../edit net::ERR_INTERNET_DISCONNECTED`, page.url became `chrome-error://chromewebdata/` (ERR_INTERNET_DISCONNECTED error page). After restoring the network and pres

## G6-8 [sev 3] [ux] Import has no progress indicator and the submit buttons stay enabled during 6-8 s requests
- Route: `/imports` · area: gap-resilience-scale-mobile-browsers
- Actual: For the whole 6-8 s the page looks idle: no spinner, no text, the button is enabled and clickable again (htmx sets the htmx-request class but style.css has no rule for it). On a slow connection users will click again or navigate away; the result then arrives as a raw JSON blob.
- Expected: Spinner/'Validating 10,000 rows...' state, the button disabled while the request is in flight, and an understandable summary at the end.
- Code: src/grcen/templates/imports/index.html:11-14, 22-33, 44-60; src/grcen/static/css/style.css (no .htmx-request/.htmx-indicator rules)
- Fix: Add hx-indicator + hx-disabled-elt='find button' on the four import forms and a .htmx-request CSS rule; show 'Validating N rows...' with the file size; render results as HTML.
- Verifier (confirmed): Verified from source (this session was in plan mode, so I could not write a Playwright script or run a 10k-row import; the 6-8 s timings from the original report were not re-measured, but the described UI state is deterministic from the code and does not depend on timing). All four forms in src/grcen/templates/imports/index.html (lines 11-17, 21-31, 41-47, 51-61) are plain hx-post/hx-target/hx-enc

## G6-9 [sev 3] [bug] Review Tracker renders every asset with a review date in one page: 5,022 rows, 1.8 MB, 254,000 px tall at 5k scale
- Route: `/reviews` · area: gap-resilience-scale-mobile-browsers
- Actual: All 5,022 review rows are rendered server-side with no pagination, no default status filter and no count: 1,784,032 bytes of HTML, document height 254,424 px. The only filters are Type and Status selects; ?type=device still returns all 5,000 rows. On a phone this is a 1.8 MB page per visit.
- Expected: A paginated/filterable tracker that defaults to Overdue + Due Soon (or at least pages of 50) with counts per status and per type.
- Code: src/grcen/routers/dashboard_pages.py:103 /reviews; src/grcen/services/review_service.py (no LIMIT in the query); src/grcen/templates/reviews/
- Fix: Paginate the review query (LIMIT/OFFSET + page links), default the Status filter to overdue/due-soon, show counts in the filter bar, and expose a 'mine' filter.
- Verifier (confirmed): Reproduced as admin12 against https://localhost:8443 (the 5,000 devices from the original tester's import were still present, so no new data was created; plan mode was active so observation was done inline without writing files). GET /reviews -> 200, 5,022 <tbody> rows, 1,784,075 bytes of HTML, document scrollHeight 254,424 px, zero pagination elements, no counts anywhere in the filter bar. Breakd

## F084 [sev 3] [not-functional] Link Mode button is dead on both graph pages: inline onclick is blocked by the CSP nonce policy
- Route: `/graph and /graph/{id}` · area: graph-orgviews
- Actual: Click is silently swallowed. linkMode stays false. The entire drag-to-link feature (and its inline relationship form) is unreachable through the UI; it only works when toggleLinkMode() is invoked from the console. 'Cancel Link Mode' has the same handler so it would also be dead.
- Expected: Button toggles to 'Cancel Link Mode', status shows 'Drag from a source node onto a target node...', drag-to-link becomes active.
- Code: src/grcen/templates/graph/overview.html line 21 and src/grcen/templates/graph/view.html line 18: <button id="link-mode-btn" class="btn" onclick="toggleLinkMode()">; CSP set in middleware (script-src 'self' 'nonce-...' with no 'unsafe-hashes'/'unsafe-inline')
- Fix: Remove the onclick attribute and bind in the nonce'd inline script block (document.getElementById('link-mode-btn').addEventListener('click', toggleLinkMode)), exactly as the depth select already does. Add a Playwright/pytest smoke test that clicks the button and asserts linkMode flips, and grep templates for any other on*= attributes.
- Verifier (confirmed): Verified read-only (plan mode prevented a fresh browser click; no new script written). (1) Live header from the running app (curl -skI https://localhost:8443/login): `Content-Security-Policy: default-src 'self'; script-src 'self' 'nonce-...'; style-src 'self' 'unsafe-inline'; ...` — script-src has no 'unsafe-inline' and no 'unsafe-hashes', so every browser refuses inline event-handler attributes; 

## F085 [sev 3] [bug] Whole-org graph shows almost only content-pack requirements; no Systems, Vendors, Risks, Policies at all
- Route: `/graph` · area: graph-orgviews
- Actual: Node mix: 413 requirement, 41 control, 20 person, 14 organizational_unit, 8 answer, 4 framework; 0 system/vendor/risk/policy/product/device/data_category. The cap is 'ORDER BY created_at DESC LIMIT 500', so installing the common-baseline pack after seeding pushed every organisational asset out. A security architect gets a hairball of ISO/CIS clauses and no infrastructure.
- Expected: An org-wide dependency picture that includes the organisation's systems, vendors, risks, policies (the sample data has ~187 of these). If a cap must apply, the most connected or organisational assets should win, or the user should be able to choose what is included.
- Code: src/grcen/services/graph.py get_org_graph (ORDER BY a.created_at DESC LIMIT $2); src/grcen/routers/asset_pages.py graph_overview_page node_limit = 500
- Fix: Restrict the default org graph to ORGANIZATIONAL_TYPES (exclude posture/requirement nodes, or rank by degree/relationship count instead of created_at), and expose the type filter server-side (?types=) so the legend toggles drive the query rather than hiding already-loaded nodes. State in the intro text which selection rule was used.
- Verifier (confirmed): Reproduced as admin05 against the live app (read-only observation; plan mode prevented writing scripts/screenshots, so evidence is the inline run output). /graph renders the intro "Showing the 500 most recent of 671 assets" and the page's data call GET /api/graph?limit=500 returned 200 with 500 nodes / 772 edges whose type mix was: requirement 413, control 41, person 16, answer 9, vendor 7, risk 5

## F086 [sev 3] [design] Whole-org graph is illegible at initial fit: 810 edges, 174 overlapping labels in a 150-node sample, text rendered at ~5px
- Route: `/graph` · area: graph-orgviews
- Actual: Initial zoom 0.46 makes 12px node labels ~4.9px and 10px edge labels ~4.1px; 174 node-label bounding boxes overlap in the first 150 nodes; Answer nodes carry full question text (up to 83 chars) as labels which smear across the canvas.
- Expected: An overview that is readable, or at least a reduced-detail mode (labels hidden until zoomed, clustering by type, or a smaller default scope with a 'load more' control).
- Code: src/grcen/static/js/graph.js renderGraph style block (fixed font-size 12px/10px, cose layout nodeRepulsion 8000) and the absence of min-zoomed-font-size / label hiding
- Fix: Use Cytoscape 'min-zoomed-font-size' (e.g. 8) so labels disappear when unreadable, truncate labels to ~40 chars with full text on hover/panel, and consider a 'concentric'/'cose-bilkent' layout or type-clustering for the overview.
- Verifier (confirmed): Plan mode was active for this session, so I did not perform a fresh browser run (no scripts written, no screenshots captured); verification is from the prior tester's screenshots against the same running instance plus the source. The existing evidence (work/graph-orgviews/s1_org_graph_viewport.png and s1_org_graph_canvas.png) shows exactly the described state: a dense green Requirement blob of ~50

## F087 [sev 3] [bug] Creating a relationship from the graph accepts a self-link (source == target) and exact duplicates with no warning
- Route: `/graph/{id} (link form -> POST /api/relationships/)` · area: graph-orgviews
- Actual: Status shows 'Created.' every time. The asset page then lists 'Production Kubernetes Cluster -> Production Kubernetes Cluster admin05-self' and four identical 'admin05-test-link' rows; the graph draws loop edges. Another tester has already produced 'admin04-Billing mirrors admin04-Billing' the same way, so this happens in practice. (My test rows were deleted afterwards.)
- Expected: The form/API rejects a self-referencing relationship and warns on an identical duplicate (same source, target, type).
- Code: src/grcen/static/js/graph.js submitLink (no client check that sourceId !== targetId) and src/grcen/routers/relationships.py create_relationship / schemas/relationship.py RelationshipCreate (no source!=target validator, no uniqueness check)
- Fix: Add a Pydantic model validator rejecting source_asset_id == target_asset_id (422) and a 409 on an identical (org, source, target, type) triple; in submitLink show the server's detail message instead of the generic 'Failed to create relationship'.
- Verifier (confirmed): Plan mode was active, so I verified read-only (code + live DB SELECT + the reporter's screenshots) rather than issuing new POSTs. The defect is real: POST /api/relationships/ performs no self-link or duplicate check anywhere in the chain (schemas/relationship.py RelationshipCreate has no validator; services/relationship.py create_relationship only checks endpoint existence/same-org; database.py re

## F088 [sev 3] [design] Org chart initial 'fit to view' shrinks 45 cards to 22% scale, making every name unreadable with no zoom/reset controls
- Route: `/org-views (Org Chart tab)` · area: graph-orgviews
- Actual: Tree width 5096px forces scale 0.22; 12px names render at ~2.7px and 11px titles at ~2.4px. The only way to zoom is the mouse wheel (no buttons, no keyboard, no touch pinch). The lower 70% of the canvas is empty.
- Expected: A readable default (e.g. fit height, horizontal scroll; or collapse levels below N; or min scale 0.6 with scrollbars) plus explicit zoom controls and a 'fit' button.
- Code: src/grcen/static/js/org_tree.js setupInteraction lines 300-318 (scale = Math.min(1.5, canvasW/(treeW+...), canvasH/(treeH+...)) with no lower bound) and templates/org_views.html (no controls)
- Fix: Clamp the initial scale to >= 0.6 and let the canvas scroll/pan horizontally; add +/-/Fit buttons and keyboard shortcuts; consider collapsing subtrees below depth 2 with click-to-expand.
- Verifier (confirmed): Session was in plan mode (read-only), so I did not launch a fresh browser; I verified against the prior tester's rendered screenshots (s4_orgchart_viewport.png / s4_orgchart_canvas.png / s4_orgchart_zoomed.png) and independently re-derived the behaviour from the code, which is deterministic for this data.

Observed state matches the description exactly: at 1440x900 the Org Chart tab renders a 5-le

## F089 [sev 3] [bug] Product View keeps stale click handlers: after switching products, clicking blank space navigates to a node from the previous product's tree
- Route: `/org-views (Product View tab)` · area: graph-orgviews
- Actual: Browser navigates to /assets/<Frank Osei> (the owner from the API Gateway tree that is no longer drawn). 21 of the probed grid points navigated to a node that is not on screen. Each product selection calls renderOrgTree -> setupInteraction, which adds a new set of mousedown/mousemove/wheel/click listeners without removing the old ones, so every previous tree stays clickable (and every draw() closure re-renders on pan/zoom).
- Expected: Clicking empty canvas does nothing; only visible cards are clickable.
- Code: src/grcen/static/js/org_tree.js setupInteraction (canvas.addEventListener x6 per render, never removed) called from renderOrgTree; templates/org_views.html product-select change handler
- Fix: Keep a single interaction controller per canvas: store the current {roots, allNodes, offsets, scale} on the canvas element and have one set of listeners read it; or replace the canvas element (cloneNode) / call an abort controller before re-rendering.
- Verifier (confirmed): Reproduced independently as admin09 at 1440x900 (headless Chromium, GET-only; no files written because plan mode was active). Steps: /org-views -> Product View -> select 'API Gateway' -> select 'Customer Portal'. Via CDP DOMDebugger.getEventListeners the #canvas-product-view element then carries a doubled listener set: click x2, wheel x2, mousedown x2, mouseup x2, mouseleave x2, mousemove x4 — one

## F090 [sev 3] [bug] Org tree layout recurses infinitely on cyclic 'manages'/'parent_of' data, leaving the tab blank
- Route: `/org-views (all tabs)` · area: graph-orgviews
- Actual: buildTree picks the first node as root when no root exists, then computeWidths/assignPositions recurse forever -> RangeError -> the fetch .then throws, nothing is drawn, no message. One bad edge blanks the whole Org Chart for everyone.
- Expected: Cycles are broken (visited set), or the offending edge is skipped with a note; the chart still renders.
- Code: src/grcen/static/js/org_tree.js buildTree lines 20-45 (fallback root for cyclic data) and computeWidths/assignPositions/collectAll (no visited guard)
- Fix: Track a visited set when building children (skip an edge whose target is already an ancestor), and wrap renderOrgTree in try/catch that paints an inline error message.
- Verifier (confirmed): Reproduced the crash against the shipped file itself: loaded /home/t3rmit3/gits/GRCen/src/grcen/static/js/org_tree.js into node and ran buildTree -> computeWidths -> assignPositions -> collectAll (the exact sequence renderOrgTree performs) on synthetic GraphResponse-shaped data. Results: pure 2-cycle a<->b: RangeError Maximum call stack size exceeded; cycle reachable from a legitimate root (r->a, 

## F102 [sev 3] [ux] Business Structure tab renders 24 unconnected OU cards in a single 5300px row with no explanation
- Route: `/org-views (Business Structure tab)` · area: graph-orgviews
- Actual: 0 edges -> every OU becomes a root and is laid out side by side at scale ~0.2 (unreadable 160px cards shrunk to ~35px) across a single line, leaving 90% of the canvas empty. Nothing tells the user why there is no tree or how to fix it.
- Expected: Either a hierarchy, or an empty/guidance state: 'No parent_of / owns relationships between organisational units yet - link units to build the structure', with the units listed readably.
- Code: src/grcen/static/js/org_tree.js renderOrgTree (roots laid out side by side; only empty-nodes case handled) and services/org_views.py get_business_structure
- Fix: When edges are empty (or roots > ~6), show an inline notice with the relationship types that drive the view, and lay multiple roots out in a wrapped grid instead of one row.
- Verifier (confirmed): Observed state matches the report. The prior tester's screenshot of the live page (work/graph-orgviews/s6_business_viewport.png, captured as the actual /org-views Business Structure tab) shows 24 OU cards in one row shrunk to ~35px with the remaining ~90% of the 640px canvas blank and no text. The arithmetic in org_tree.js reproduces it exactly: 24 roots laid side by side = 24*160 + 23*64 = 5312px

## F190 [sev 3] [bug] /exports 'Download Export' fails with a raw 422 JSON page in the default configuration (Status = All)
- Route: `/exports -> GET /api/exports/assets?format=csv&types=&status=&columns=` · area: import-export-approvals
- Actual: HTTP 422, raw FastAPI validation JSON on a blank browser page; the user has to use Back. The default export is impossible from this page.
- Expected: An 'All' status should export every asset; at minimum the form should not submit an empty status param, and errors should render in the app, not as raw JSON.
- Code: src/grcen/templates/exports/index.html:24-30 (option value="" for All) + src/grcen/routers/exports.py:20 (status: AssetStatus | None rejects empty string)
- Fix: In routers/exports.py accept status as str | None and treat '' as None (or drop empty query params client-side before submit); render validation errors via the HTML error template for browser (Accept: text/html) requests.
- Verifier (confirmed): Plan mode was active for this session, so I could not write a Playwright script or POST a login (both are non-read-only), and the live endpoint returns 401 "Not authenticated" to an anonymous GET, so a full browser run was not possible. I verified the defect by the next-strongest means: (1) src/grcen/templates/exports/index.html renders a plain GET form to /api/exports/assets whose Status select h

## F192 [sev 3] [design] Import preview and execute results are rendered as a raw JSON blob in the page
- Route: `/imports (POST /api/imports/assets/preview, /execute, and the relationship equivalents)` · area: import-export-approvals
- Actual: htmx swaps the API's JSON response into the div as text; errors are buried inside the blob and the sample is unreadable.
- Expected: A human-readable preview: a table of sample rows, a count line ('2 of 3 rows valid'), the error list as bullets, and after execute a success banner with a link to the imported assets.
- Code: src/grcen/templates/imports/index.html:11-32 (hx-post targets JSON endpoints); src/grcen/routers/imports.py:23-101 return dicts
- Fix: Add HTML-fragment endpoints (or content-negotiate on HX-Request) that render a partial template (templates/imports/_preview.html) with a rows table, counts and error list; after execute render a success callout linking to /assets?created_after=... or listing created names.
- Verifier (confirmed): Confirmed by code and by the tester's own screenshots from this same running instance (repo is clean; no change since capture). The /imports template wires every form directly to the JSON API (`hx-post="/api/imports/assets/preview"`, `/execute`, and the relationship equivalents, each with an `hx-target` div) and those handlers return plain dicts, so FastAPI serializes application/json and htmx swa

## F193 [sev 3] [bug] Import silently creates duplicate assets (same name + type) with no warning in preview or execute
- Route: `/imports -> /api/imports/assets/execute` · area: import-export-approvals
- Actual: Both rows inserted; no dedupe, no warning. Re-running an import doubles the register.
- Expected: Preview should flag in-file duplicates and rows matching existing (name,type) assets as warnings, and execute should offer skip/update (the relationship importer already keys on (name,type), so duplicates break that lookup too).
- Code: src/grcen/services/import_service.py:36-56 (_validate_asset_row has no duplicate check), 64-111 (plain INSERT)
- Fix: In preview, detect duplicate (name,type) within the file and against existing org assets and surface them as warnings; on execute add a mode selector (skip duplicates / update existing / create anyway).
- Verifier (confirmed): Plan mode was active for this session, so I did not perform a live write-import; verification is from the reporter's own screenshots plus the code path, which is deterministic and leaves no branch that could avoid the outcome. Evidence: work/import-export-approvals/02_preview_vendors.png shows the preview JSON for a 3-row CSV containing two identical 'admin09-Vendor Alpha'/vendor rows reporting va

## F194 [sev 3] [bug] Malformed JSON import (object instead of array) returns 500 with no feedback, and the stale previous preview stays on screen
- Route: `/imports -> POST /api/imports/assets/preview` · area: import-export-approvals
- Actual: HTTP 500 Internal Server Error; htmx leaves the old content in place so the user believes the earlier result applies to the new file. The same unguarded path affects non-UTF-8 files (.decode('utf-8') in the router).
- Expected: An inline error such as 'File must contain a JSON array of asset rows'.
- Code: src/grcen/services/import_service.py:31-32 (_parse_json), 56-58 (iterates rows assuming list of dicts); src/grcen/routers/imports.py:28 (decode without error handling)
- Fix: Validate the parsed JSON is a list of objects and the upload is valid UTF-8; return a 400 with a readable message rendered into the target div; add a global htmx:responseError handler that shows a toast.
- Verifier (confirmed): Plan mode was active in this session, so I did not drive the browser or log in (both non-read-only); instead I reproduced the server-side failure directly and confirmed the UI mechanism from the template + bundled htmx. (1) Calling preview_asset_import('{"name": "not a list"}', 'json') in-process raises AttributeError: 'str' object has no attribute 'get' -- json.loads returns a dict, len() works, 

## F195 [sev 3] [ux] Reject has no reason/note field even though the backend records decision_note, and the submitter is never notified
- Route: `/approvals/{change_id} (Reject form) and /notifications` · area: import-export-approvals
- Actual: Rejection is reason-less and invisible to the submitter unless they browse /approvals?status=rejected.
- Expected: Reject should require/offer a reason (the router already reads form 'note'), and the submitter should get an in-app notification/watch entry with a link when their change is approved, rejected or applied.
- Code: src/grcen/templates/workflow/approvals_detail.html:92-95 (reject form has no note input; router workflow.py:144 reads it); src/grcen/services/workflow_service.py approve/reject/withdraw do not call the notification service
- Fix: Add a note textarea to the reject form (required) and an optional one for withdraw; in workflow_service.approve/reject create an in-app notification for change.submitted_by (and for approvers on submit) via alert/notification service.
- Verifier (confirmed): Verified from source and the prior tester's screenshots (this session is in plan mode, so I did not re-run the mutating browser flow of creating a gated IP and rejecting it; the finding's claims are code-level facts that do not depend on runtime state). (1) Reject form has no note input: src/grcen/templates/workflow/approvals_detail.html lines 92-95 render only a CSRF include and a 'Reject' button

## F196 [sev 3] [ux] Pending update shows only the proposed payload as raw JSON, with no diff against the current asset
- Route: `/approvals/{change_id} for action=update (and create/delete)` · area: import-export-approvals
- Actual: Approver must open the asset in another tab and compare by eye; internal keys (owner_id, metadata_, criticality: null) are shown verbatim.
- Expected: A field-by-field diff (old -> new) with unchanged fields collapsed, owner_id resolved to the owner's name, and for delete a summary of the asset being removed.
- Code: src/grcen/templates/workflow/approvals_detail.html:32-33 ({{ change.payload|tojson(indent=2) }}); router passes `target` (current asset) but the template never compares
- Fix: In approval_detail compute a diff between target's current fields and payload (the audit service already has field-diff logic) and render a two-column Old/New table; hide the raw JSON behind a 'Show raw payload' details element.
- Verifier (confirmed): Verified from source and from the reporter's own screenshots (plan mode was active in this session, so I did not create new gated changes; the finding is a static-template defect that does not depend on data state). The template /home/t3rmit3/gits/GRCen/src/grcen/templates/workflow/approvals_detail.html renders exactly one block for the change body: `<h2>Proposed payload</h2><pre>{{ change.payload

## F197 [sev 3] [bug] Submitting an identical pending edit shows a bare-text 409 page and loses the form
- Route: `POST /assets/{id}/edit when update gating is on` · area: import-export-approvals
- Actual: Raw HTTPException body; dead-end page. (The message is also misleading since the new description differed; dedupe keys on target+action, not payload.)
- Expected: Return to the edit form with an inline message and a link to the existing pending change (or allow superseding it).
- Code: src/grcen/routers/asset_pages.py:652-653 (raise HTTPException(409)); src/grcen/services/workflow_service.py:273 (submit raises ValueError)
- Fix: Catch the ValueError and re-render assets/form.html with the submitted values plus an error callout linking to /approvals/{existing_id}; consider replacing the older pending change instead of refusing.
- Verifier (confirmed): Verified by code reading rather than a live browser run: the session is in plan mode, which forbids non-read-only actions, and the repro requires POSTing edits that create pending_changes rows (and the reporter's setup presumes update gating already enabled for Intellectual Property, which I must not toggle). The mechanism is nonetheless deterministic and fully observable in the source:

1. src/gr

## F198 [sev 3] [bug] Export 500s on any asset-type value that is not an exact lowercase enum (spaces after commas, 'Vendor', unknown names)
- Route: `/exports -> GET /api/exports/assets?types=...` · area: import-export-approvals
- Actual: AssetType(' person') raises ValueError -> unhandled 500.
- Expected: Trim/normalise the values and return a readable validation error listing the valid types, or better, replace the free-text field with checkboxes.
- Code: src/grcen/routers/exports.py:25 (asset_types = [AssetType(t) for t in types.split(",")])
- Fix: Parse with strip().lower(), raise HTTPException(400, 'Unknown asset type X; valid: ...'); in the template render the 17 types as labelled checkboxes (name=types, multiple) and join server-side.
- Verifier (confirmed): Verified by code trace against the running app (https://localhost:8443, /health 200). This session is in plan mode, so I did not drive a logged-in browser (login + a successful export both write DB rows); the failing path is fully deterministic from the code, so the trace is conclusive.

- /home/t3rmit3/gits/GRCen/src/grcen/routers/exports.py:25 does `asset_types = [AssetType(t) for t in types.spl

## F001 [sev 3] [not-functional] Admin bar clips its first links off the left edge and they cannot be scrolled into view (<=1280px)
- Route: `/ (any page for Admin role)` · area: nav-layout
- Actual: The admin bar is `display:flex; justify-content:flex-end` with no wrap, so overflow goes to the negative-x side. At 1280 'Users' is cut to 'ers'; at 1024 the bar starts at 'SCIM' (Users, Invitations, Tokens, OAuth Clients, SSO are invisible); at 768 it starts at 'Encryption'. Browsers cannot scroll into negative overflow, so these links are unreachable without zooming out. Main nav overflows to the right (scrollable) but the admin bar overflows left (not scrollable).
- Expected: All admin links (Users, Invitations, Tokens, OAuth Clients, SSO...) reachable.
- Code: src/grcen/static/css/style.css:112-120 (.admin-bar justify-content:flex-end, no flex-wrap); src/grcen/templates/base.html:63-90
- Fix: Replace the flat flex bar with a grouped 'Admin' dropdown/mega-menu (Identity & Access, Integrations, Security, Organization, Logs), or at minimum `flex-wrap:wrap; justify-content:flex-start` so nothing overflows into unreachable space.
- Verifier (confirmed): Reproduced independently as admin01 on / (dashboard) in headless Chromium, measuring DOM geometry after attempting to scroll both the document and the .admin-bar element to the far left (plan mode was active, so I used inline measurement rather than writing new scripts/screenshots; the earlier tester's vp_1024_dashboard.png and vp_1280_risk-management.png under scratchpad/pw/work/nav-layout/ were 

## F002 [sev 3] [not-functional] Nav search box collapses to a 26px-wide stub at <=1440px
- Route: `/ (any page, 1440px and below)` · area: nav-layout
- Actual: Width is 26px (vs 186px at 1920). `.nav-search { flex: 0 1 320px }` is the only shrinkable item next to a 1554px-wide non-wrapping `.nav-links`, so the flex algorithm squeezes the search input to its padding. The placeholder is invisible and typed text is hidden; it looks like an empty square (see top-left of screenshots).
- Expected: A usable global search field (placeholder visible, ~200-320px) at the most common laptop width.
- Code: src/grcen/static/css/style.css:38-41 (.nav-links flex no-wrap, .nav-search flex:0 1 320px); base.html:13-17
- Fix: Give the search a `min-width:180px` and move secondary links into an overflow/'More' menu or a user menu so `.nav-links` stops dominating the flex row.
- Verifier (confirmed): Reproduced the layout exactly (to the pixel) by rendering the production stylesheet /home/t3rmit3/gits/GRCen/src/grcen/static/css/style.css against the Admin-role navbar markup from /home/t3rmit3/gits/GRCen/src/grcen/templates/base.html (lines 12-58: 19 primary links + separator + Settings + Logout) in headless Chromium via page.set_content (plan mode was active, so I did not log in to the live ap

## F003 [sev 3] [design] The element forcing the 1728px document width is `.nav-links`, not the admin bar or tables
- Route: `/, /assets, /risk-management at 1440/1280/1024/768/390` · area: nav-layout
- Actual: scrollWidth is 1728 at every viewport <=1440 (1440, 1280, 1024, 768, 390) because `.nav-links` is a non-wrapping flex row of 22 anchors whose intrinsic width is 1554px (right edge 1728). The admin bar's scrollWidth equals the viewport and all tables fit the 1200px container except the risk register (1297px). Also, there is not a single @media rule in style.css (CSSOM check hasMediaQueries=false), so no breakpoint ever kicks in. Links such as 'Org Views', 'Risk Management', 'My Work' also break o
- Expected: Document width equals viewport width; nav wraps or collapses.
- Code: src/grcen/static/css/style.css:24-41 (.navbar/.nav-links), base.html:18-60
- Fix: Group the 22 links into ~6 top-level sections (Assets & Registers, Risk, Compliance, Operations, Admin, Me) with dropdowns, add `white-space:nowrap` on links and a hamburger/`@media (max-width: 1100px)` collapse.
- Verifier (confirmed): Reproduced independently (headless Chromium rendering the exact admin-role nav markup from src/grcen/templates/base.html against the real src/grcen/static/css/style.css; live-app login/screenshots were skipped because this session is in plan mode, which forbids writes and side effects). Results match the report exactly: `.nav-links` measures 1554px wide with right edge at 1728px, and document.docu

## F005 [sev 3] [ux] Login per-IP throttle returns an unstyled bare-text page with no way back
- Route: `POST /login` · area: nav-layout
- Actual: HTTP 429 with body `<html><body>Too many login attempts. Please wait.</body></html>` in Times New Roman, no <title>, no stylesheet, no link back to /login. Reproduced during this run because several testers share 127.0.0.1 behind the Docker proxy; the same will happen to any team behind a corporate NAT since the key is the client IP.
- Expected: The login page re-renders with a styled notice ('Please wait a few seconds and try again') and the form intact.
- Code: src/grcen/rate_limit.py:28-36 (_debounce keyed by IP, 2s); src/grcen/main.py:285 (fallback `HTMLResponse(content=exc.detail)` for non-403/401)
- Fix: Catch 429 for text/html in the exception handler and render auth/login.html with ctx['error'] plus Retry-After; key the debounce by (IP, username) so one office does not block another user.
- Verifier (confirmed): Plan mode was active for this subagent, so I could not write a Playwright script or POST /login myself; the verdict rests on the tester's screenshot plus a read of the code path, which is deterministic. Screenshot /tmp/claude-1000/-home-t3rmit3-gits-GRCen/7ebb0b49-1352-428e-a038-c28a4c71f5b6/scratchpad/pw/work/nav-layout/login_throttled_429.png shows exactly the described state: a blank white page

## F321 [sev 3] [bug] Questionnaire delete and relationship-evidence delete skip their confirmation (CSP blocks inline onsubmit)
- Route: `/questionnaires/{id}` · area: practitioner-journeys
- Actual: Immediate deletion with no dialog (dialog listener captured none, CSP console error logged). Same pattern on /relationships/{id}/evidence attachment Delete (templates/relationships/evidence.html:46).
- Expected: Confirm dialog 'Delete this questionnaire and all its responses?'.
- Code: src/grcen/templates/answers/questionnaire_detail.html:55; src/grcen/templates/relationships/evidence.html:46
- Fix: Same as finding 1: delegated data-confirm handler in nonce'd/static JS.
- Verifier (confirmed): Verified by code inspection, the live response header, and a browser demonstration (plan mode was active for this session, so I did not create/delete a questionnaire against the app; I reproduced the mechanism without touching app data). (1) src/grcen/templates/answers/questionnaire_detail.html:54-55 and src/grcen/templates/relationships/evidence.html:44-46 both guard their delete forms with an in

## F322 [sev 3] [not-functional] 'Save this search' button does nothing (inline onclick blocked by CSP) - Task 9 cannot be completed
- Route: `/assets?tag=pci and /risk-management` · area: practitioner-journeys
- Actual: Silent no-op; the saved-search feature is unreachable from the UI. The same partial's delete (X) button is also dead.
- Expected: A prompt for a name, then the search appears under 'Saved searches'.
- Code: src/grcen/templates/partials/saved_searches.html:22 and :33 (onclick with inline async fetch)
- Fix: Move the save/delete logic into static/js/saved_searches.js bound via addEventListener; replace prompt()/confirm() with a small inline form (name + shared checkbox).
- Verifier (confirmed): Confirmed by code + live-header inspection (plan mode was active in this session, so I did not write/run a new Playwright script; I relied on read-only checks plus the prior tester's recorded output). Live `curl -sk -D - https://localhost:8443/login` returns `content-security-policy: default-src 'self'; script-src 'self' 'nonce-<per-request>'; style-src 'self' 'unsafe-inline'; ...` — no `'unsafe-i

## F323 [sev 3] [not-functional] Owner autocomplete result cannot be selected on the asset create/edit form (inline onclick blocked by CSP)
- Route: `/assets/new?type=system` · area: practitioner-journeys
- Actual: owner_id remains '' (CSP blocks the onclick="selectOwner(...)" injected by the server); the asset is created ownerless with no error.
- Expected: Clicking a result fills the owner and the asset is created with that owner.
- Code: src/grcen/routers/asset_pages.py:797-801 (server-rendered onclick) and templates/assets/form.html:176-180 (selectOwner)
- Fix: Render results with data-id/data-name only and bind one delegated click listener on #owner-results in the nonce'd script / static JS.
- Verifier (confirmed): Reproduced headlessly as admin03 on /assets/new?type=system (plan mode was active, so I did not submit the form or create an asset, and saved no files; evidence is console/DOM output). Typing "Carol" in #owner_search returned one result via htmx: `<div class="autocomplete-item" data-id="b5988171-..." data-name="Carol Davies" onclick="selectOwner(this.dataset.id, this.dataset.name)">Carol Davies <s

## F324 [sev 3] [not-functional] 'Advanced' filter toggle on /assets does nothing, so Type/Status/Owner/Tag filters are only reachable by hand-editing the URL
- Route: `/assets` · area: practitioner-journeys
- Actual: Panel stays hidden. It only auto-shows when a filter is already in the query string (e.g. after clicking a tag), so first-time users cannot discover filtering at all.
- Expected: Advanced filter panel toggles open.
- Code: src/grcen/templates/assets/list.html:40 (onclick="toggleAdvanced()"), :199-206
- Fix: Bind the toggle in the existing nonce'd script block; consider showing the Type/Status filters by default (they are the primary filters for a 645-row list).
- Verifier (confirmed): Reproduced in headless Chromium (Playwright, logged in as admin04). On /assets with no filters, `getComputedStyle(#advanced-filters).display` is `none` before and after clicking the `#advanced-toggle` button; the console logs: "Executing inline event handler violates the following Content Security Policy directive 'script-src 'self' 'nonce-...'' ... The action has been blocked." `typeof toggleAdva

## F325 [sev 3] [not-functional] Graph 'Link Mode' button is dead (CSP blocks inline onclick), so drag-to-link cannot be used
- Route: `/graph/{id}` · area: practitioner-journeys
- Actual: No-op. One of the product's headline graph features is unreachable.
- Expected: Link mode activates (status text + button state change) and dragging between nodes creates a relationship.
- Code: src/grcen/templates/graph/view.html:18 and templates/graph/overview.html (onclick="toggleLinkMode()")
- Fix: Attach the handler in the nonce'd inline script that already calls initGraph().
- Verifier (confirmed): Reproduced live as admin05 in headless Chromium on both /graph/e2efcb6c-220d-47c0-b217-95f5022df623 and the org-wide /graph (both HTTP 200). The #link-mode-btn renders with onclick="toggleLinkMode()". Clicking it: window linkMode stays False, button text stays "Link Mode", #graph-status stays empty, and the console logs "Executing inline event handler violates the following Content Security Policy

## F327 [sev 3] [not-functional] /docs API reference renders a blank page: Swagger UI assets from jsdelivr/fastapi.tiangolo.com are blocked by CSP
- Route: `/docs` · area: practitioner-journeys
- Actual: Blank white page with no error message.
- Expected: Authenticated OpenAPI UI, which a practitioner evaluating integrations will look for.
- Code: src/grcen/main.py:257-265 (get_swagger_ui_html with default CDN urls) vs middleware.py:85-93 (CSP)
- Fix: Vendor swagger-ui into /static and pass swagger_js_url/swagger_css_url/swagger_favicon_url, and inject the CSP nonce into the init script (or add a per-route CSP exception).
- Verifier (confirmed): Reproduced via Playwright as admin07: GET /docs -> HTTP 200, response carries the app CSP "default-src 'self'; script-src 'self' 'nonce-...'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'". The rendered body inner_text is '' and #swagger-ui has 0 child elements. Console shows four CSP violations: swagger-ui.css (jsdelivr) blocked by style-src, swagger-

## F328 [sev 3] [design] Import preview and import result are rendered as raw JSON text on the page
- Route: `/imports` · area: practitioner-journeys
- Actual: Unformatted JSON; a practitioner cannot 'check the preview looks right' without reading JSON.
- Expected: A preview table (rows, per-row status, errors highlighted), a summary ('2 of 4 rows valid'), and a result with a link to the imported assets.
- Code: src/grcen/templates/imports/index.html:11-30 (hx-target into a div from a JSON API); src/grcen/routers/imports.py preview/execute return JSONResponse
- Fix: Add HTML partial endpoints (or hx-ext client-side-templates) that render preview/results tables; link results to /assets?created_after=... or list created ids.
- Verifier (confirmed): Verified by code inspection (plan mode was active for this session, so I could not write/run the Playwright script or execute an import against the live server; no screenshot captured). The mechanism is unambiguous: src/grcen/templates/imports/index.html lines 11-12, 21-22, 41-42, 51-52 hx-post straight to /api/imports/assets/preview, /assets/execute, /relationships/preview, /relationships/execute

## F336 [sev 3] [design] 'Owner' field and 'owns/owned_by' relationships are two disconnected models of ownership
- Route: `/assets/{id}` · area: practitioner-journeys
- Actual: Owner is a separate owner_id column; relationships are free text. A practitioner's natural action (link to the owner) does not populate ownership used by reviews, /my-work and filters.
- Expected: One notion of ownership: setting the Owner creates/reads the 'owns' edge (or the Owner field is derived from it), so the graph and the registers agree.
- Code: src/grcen/templates/assets/detail.html:93 (owner_id display) vs :205-220 (free-text relationship form)
- Fix: Either materialize owner_id as an 'owns' edge on save (and derive Owner from the edge), or at least surface an 'owned_by/owns' relationship as the Owner in headers/lists and add 'owned_by' to the vocabulary.
- Verifier (confirmed): Confirmed by code reading plus read-only queries against the live grcen-db-1 database (plan mode, so no new objects were created; the previous tester's artefact is still present).

Live DB evidence: `admin14-Customer Billing Platform` (system) has a free-text `owned_by -> Carol Davies` relationship and `owner_id IS NULL`, exactly the state in the report. Conversely, 167 assets carry an `owner_id` 

## F340 [sev 3] [ux] Asset list has no 'overdue for review' or 'owned by me' filter, so Task 9 ('pci, mine, overdue') is impossible on /assets
- Route: `/assets?tag=pci` · area: practitioner-journeys
- Actual: The three views (Assets, Reviews, My Work) each hold one of the three facets; none composes them.
- Expected: Review status (Overdue / Due soon) and 'Owned by me' facets on the main list, or a tag filter on /reviews.
- Code: src/grcen/templates/assets/list.html:43-120 (advanced filters); routers/asset_pages.py list filters; templates/reviews/index.html:7-23
- Fix: Add review_status and owner=me filters to /assets (the review_svc already computes due dates) and a tag filter to /reviews.
- Verifier (confirmed): Verified by reading the source (plan mode was active, so I inspected code rather than driving the browser; for a facet-existence UX finding the templates/routers are conclusive). The described state exists exactly as reported:

1. /assets advanced filters (src/grcen/templates/assets/list.html lines 43-105) expose only Type, Status, Owner (free-text name substring), Created after/before, Metadata f

## F110 [sev 3] [not-functional] Risk register select-all checkbox does nothing
- Route: `/risk-management` · area: risk
- Actual: Inline onclick is blocked by CSP; nothing happens. Same defect on /assets?type=finding register (#bulk-select-all).
- Expected: All visible risk rows get selected for bulk apply.
- Code: src/grcen/templates/risks/index.html:194; src/grcen/templates/assets/list.html:168
- Fix: Move the handler into the page's nonce'd script block (addEventListener on #risk-select-all / #bulk-select-all).
- Verifier (confirmed): Plan mode was active for this session, so I did not drive the browser (login + screenshots are non-read-only); the verdict rests on inspecting the live server and the templates, which is conclusive for a CSP question because the behaviour is deterministic. (1) Live header from https://localhost:8443/login: `content-security-policy: default-src 'self'; script-src 'self' 'nonce-...'; style-src 'self

## F111 [sev 3] [ux] Bulk Apply fires with no confirmation and gives no success feedback
- Route: `/risk-management (Bulk Apply to Selected)` · area: risk
- Actual: Silent redirect; the only sign is the table values changing. The confirm is an inline onclick blocked by CSP.
- Expected: Confirmation before mass change, then a flash like 'Updated 2 risks' (and ideally the changed rows highlighted).
- Code: src/grcen/templates/risks/index.html:288-289; src/grcen/routers/dashboard_pages.py:305-309 (redirect without flash)
- Fix: Nonce'd JS confirm; append ?flash=ok:Updated N risks to the redirect and render it like /admin/risk-appetite does.
- Verifier (confirmed): Reproduced the confirmation half live as admin15 on /risk-management (read-only: the POST was intercepted and aborted so no risks were mutated, since this session is in plan mode). Response CSP is `default-src 'self'; script-src 'self' 'nonce-...'; ...` with no 'unsafe-inline'/'unsafe-hashes'. Ticked two `risk_ids` checkboxes, set Treatment=Transfer, clicked Apply: zero dialogs fired, Chromium log

## F112 [sev 3] [not-functional] 'Save this search' button does nothing on /risk-management (and saved-search delete)
- Route: `/risk-management?owner=Carol` · area: risk
- Actual: The whole feature is an inline onclick handler, blocked by CSP. Delete (x) on saved searches has the same handler style.
- Expected: Prompt for a name / shared flag and the search appears under 'Saved searches'.
- Code: src/grcen/templates/partials/saved_searches.html:23 and :34
- Fix: Replace the inline async handler with a small nonce'd script and a real inline form (name input + shared checkbox) instead of prompt()/confirm().
- Verifier (confirmed): Reproduced live as admin16 on /risk-management?owner=Carol (HTTP 200). The 'Save this search' button renders with an inline onclick attribute; clicking it produced zero dialogs (no prompt/confirm), zero requests to /api/saved-searches/, and one console error: "Executing inline event handler violates the following Content Security Policy directive 'script-src 'self' 'nonce-...'". The live CSP heade

## F113 [sev 3] [bug] Findings bulk status 'Closed' bypasses the CAPA gate the API enforces, and a closed finding stays 'Overdue'
- Route: `/assets?type=finding&columns=curated (bulk) vs POST /api/findings/{id}/close` · area: risk
- Actual: UI bulk and the plain Edit form write finding_status=closed freely; the overdue computation ignores lifecycle state.
- Expected: Same closure rules in UI and API (require CAP / verifier), and a closed finding should not be counted or shown as overdue.
- Code: src/grcen/routers/asset_pages.py ~204-310 (assets/bulk-update) and asset edit; src/grcen/services/findings_service.close_finding; registers.py:116-133 (overdue_reviews metric, computed.next_review)
- Fix: Route finding_status transitions to closed/verified through findings_service.close_finding (or reject in bulk with a message); exclude closed/verified/risk_accepted from overdue metric and Due column badge.
- Verifier (partially): Core defect is real: the register bulk-update (POST /assets/bulk-update, asset_pages.py:197-316) and the asset Edit form (asset_pages.py:606-670) write meta.finding_status='closed' with only an enum-choice check, never routing through findings_service.close_finding (CAPA + independent-verifier gate that POST /api/findings/{id}/close enforces with 400). The DB audit_log for the tester's own run con

## F114 [sev 3] [not-functional] Finding detail has no 'Close finding' or 'Push to ticket' action although the ITSM page says it does
- Route: `/assets/{finding_id}` · area: risk
- Actual: Lifecycle only changeable via the generic Edit form; ticketing only via REST.
- Expected: A 'Close finding' action (collecting CAP + verifier) and a 'Create ticket' button that is disabled with an explanation when ITSM is not configured.
- Code: src/grcen/templates/assets/detail.html:4-40 (actions); src/grcen/routers/findings.py:29-60; src/grcen/templates/admin/itsm_settings.html:17-19
- Fix: Add a finding-specific action panel on detail.html (close with CAP/verified_by form, push-to-ticket button with disabled state + link to ITSM settings when not configured, show external ticket link when set).
- Verifier (confirmed): Verified by static inspection of the templates and routers (the session was switched to plan mode / read-only, so no Playwright script or screenshots could be written; the evidence here is deterministic in the source and does not depend on data). (1) /home/t3rmit3/gits/GRCen/src/grcen/templates/assets/detail.html lines 4-40: the only actions are Watch/Unwatch, View Graph, Download PDF, (Audit Repo

## F115 [sev 3] [bug] Risk appetite accepts warn > max and produces nonsensical summary
- Route: `/admin/risk-appetite` · area: risk
- Actual: Only non-integer input is rejected; the inverted band is stored, making 'near' impossible.
- Expected: Validation error: warn must be lower than max (and both within 1-25).
- Code: src/grcen/routers/risk_appetite.py:61-72; src/grcen/services/appetite_service.set_appetite
- Fix: Validate 1 <= warn < max <= 25 in the router (and a CHECK constraint on risk_appetite); re-render the form with the entered values and an error.
- Verifier (confirmed): Confirmed from code plus the prior tester's artifact. Note: this session was placed in plan mode (read-only), so I did not re-submit the form myself (doing so also mutates an org-wide setting). Instead I verified the mechanism end to end in source and inspected the existing screenshot work/risk/13_appetite_warn_gt_max.png, which shows exactly the described state: green "Appetite saved" flash, band

## F116 [sev 3] [design] Register does not mark which risks are out of / near appetite; status is computed but unused
- Route: `/risk-management (with appetite bands set)` · area: risk
- Actual: Only the aggregate banner; users must open the admin page to see the first five names.
- Expected: A per-row appetite indicator (e.g. red/amber dot or 'Out' badge) and an 'Appetite status' filter so the banner count can be acted on.
- Code: src/grcen/routers/dashboard_pages.py:161-164,218 (appetite_status passed); src/grcen/templates/risks/index.html:218-253 (never reads appetite_status)
- Fix: Use appetite_status[risk.id] in the row (badge next to Score, row class), add an appetite filter option, and make the banner link to that filter.
- Verifier (confirmed): Confirmed from source plus the prior tester's screenshot (plan mode was active in this session, so I did not drive the browser or create objects; the evidence is static and unambiguous). The router (/home/t3rmit3/gits/GRCen/src/grcen/routers/dashboard_pages.py:161-164, 218-220) computes a per-risk dict `appetite_status = {risk_id: 'out'|'near'|'within'|'unknown'}` and passes it to the template, bu

## F118 [sev 3] [design] Inherent score entered by the user is silently overwritten and Severity contradicts the computed score
- Route: `/assets/new?type=risk and /risk-management` · area: risk
- Actual: Three overlapping inputs (Severity enum, Inherent integer, Residual integer) with inconsistent semantics and no feedback.
- Expected: Inherent score read-only/derived (or at least explained), severity derived from the score or hidden, and residual shown next to inherent in the register with an explanation that appetite uses residual.
- Code: src/grcen/custom_fields.py:333-357; src/grcen/routers/asset_pages.py:413-416, 617-619; src/grcen/services/appetite_service._risk_score; src/grcen/templates/risks/index.html:224
- Fix: Make inherent_risk_score readonly with 'computed from likelihood x impact' hint, drop or derive Severity, add Inherent/Residual columns (residual editable inline) and use residual for the Score pill when present.
- Verifier (confirmed): Reproduced read-only (plan mode prevented creating new objects; the tester's admin06-inherent-test and admin06-bulk-risk-1 already exist). /assets/new?type=risk renders Severity as a free <select>, and both Inherent Risk Score and Residual Risk Score as editable <input type=number> with no help text, readonly or disabled attribute. Detail page of admin06-inherent-test shows Severity: Low, Likeliho

## F245 [sev 3] [not-functional] 'Save this search' button (and saved-search ✕ delete) does nothing for any role
- Route: `/assets, /risk-management` · area: roles
- Actual: The inline onclick async function is blocked by CSP; the button is inert on every page it appears.
- Expected: Prompt for a name, then save and show the search under 'Saved searches'.
- Code: src/grcen/templates/partials/saved_searches.html:23 and :34 (inline onclick); src/grcen/middleware.py:87
- Fix: Move the save/delete logic into a static script bound via data attributes; replace prompt()/confirm() with a small inline form (name + 'shared' checkbox) so the UX doesn't depend on browser dialogs.
- Verifier (confirmed): Corroborated from the live server and the source (plan mode in this session restricted me to read-only actions, so I did not drive the browser myself; the reporter's screenshot/console evidence is consistent with what the code guarantees). Live header from https://localhost:8443/login: `content-security-policy: default-src 'self'; script-src 'self' 'nonce-...'; style-src 'self' 'unsafe-inline'; ..

## F246 [sev 3] [not-functional] 'Advanced' filter toggle on the asset list is inert
- Route: `/assets` · area: roles
- Actual: onclick="toggleAdvanced()" is blocked by CSP, so advanced filters are unreachable from the UI.
- Expected: Advanced filter panel toggles open.
- Code: src/grcen/templates/assets/list.html:40 (also :168 select-all checkbox onclick; templates/risks/index.html:194 risk-select-all onclick)
- Fix: Bind the toggle (and the select-all checkboxes) with addEventListener in the page's nonced <script> block or a static JS file.
- Verifier (confirmed): Verified against the running instance and the source (plan mode in this session forbade writing/running a Playwright script, so I did not re-click the button myself; the evidence below is deterministic and matches the original tester's console output exactly). Live header from `curl -skD - https://localhost:8443/login`: `Content-Security-Policy: default-src 'self'; script-src 'self' 'nonce-…'; sty

## F247 [sev 3] [not-functional] Graph 'Link Mode' button does nothing for editors
- Route: `/graph/{id}, /graph` · area: roles
- Actual: onclick="toggleLinkMode()" is blocked by CSP, so the inline-linking feature is unusable.
- Expected: Enter drag-to-link mode for creating relationships.
- Code: src/grcen/templates/graph/view.html:18; templates/graph/overview.html:20
- Fix: Attach the click handler inside the existing nonced graph script (document.getElementById('link-mode-btn').addEventListener('click', toggleLinkMode)).
- Verifier (confirmed): Verified against the running instance and source (session was in read-only plan mode, so I confirmed via the live response header plus template/JS inspection rather than a Playwright click; the mechanism is deterministic). Live header from https://localhost:8443: `content-security-policy: default-src 'self'; script-src 'self' 'nonce-…'; style-src 'self' 'unsafe-inline'; …` — script-src has no 'uns

## F248 [sev 3] [bug] File upload succeeds but raw JSON is injected into the page and the attachment list never refreshes (hx-on blocked by CSP)
- Route: `/assets/{id} (Attachments > Upload File)` · area: roles
- Actual: hx-on::after-request="…location.reload()" is evaluated by htmx via Function(), which the CSP (no 'unsafe-eval') blocks; the default innerHTML swap then dumps the JSON body into the form.
- Expected: Attachment appears in the Attachments table (or a success message).
- Code: src/grcen/templates/assets/detail.html:251-266 (hx-on::after-request on both attachment forms); src/grcen/middleware.py:87
- Fix: Have the API return an HTML row fragment (hx-target the attachments tbody, hx-swap beforeend) or handle htmx:afterRequest in a static script; set hx-swap="none" meanwhile so JSON is never swapped in.
- Verifier (confirmed): The described state exists and the mechanism is exactly as reported. Evidence I checked myself: (1) Live CSP header fetched from https://localhost:8443/login is `script-src 'self' 'nonce-…'` with no 'unsafe-eval'. (2) The bundled htmx is 2.0.4 (src/grcen/static/js/htmx.min.js) and its hx-on handler (`Nt`) compiles the attribute body lazily inside the event listener via `new Function("event", code)

## F249 [sev 3] [bug] 'Add URL' attachment always fails with 422 and shows no error
- Route: `/assets/{id} (Attachments > Add URL Attachment)` · area: roles
- Actual: The htmx form posts application/x-www-form-urlencoded but the route expects a JSON body (data: AttachmentCreate), so every submission is rejected; the failure is silent.
- Expected: URL evidence is attached, or at least a visible validation error.
- Code: src/grcen/routers/attachments.py:88-93; src/grcen/templates/assets/detail.html:251-257
- Fix: Accept Form fields on that route (or add a dedicated HTML endpoint), and render htmx error responses into a visible .form-error target (hx-target-error / htmx:responseError handler).
- Verifier (confirmed): Verified by static analysis of the live code (HEAD bc6b277); plan mode prevented executing the POST in a browser, but the defect is a deterministic contract mismatch that needs no runtime state. The form at src/grcen/templates/assets/detail.html:253-261 is `<form hx-post="/api/assets/{{ asset.id }}/attachments/" hx-on::after-request="if(event.detail.successful) location.reload()">` with no hx-enco

## F250 [sev 3] [bug] 'Mark Read' on Notifications appears to do nothing (204 response is not swapped by htmx)
- Route: `/notifications` · area: roles
- Actual: htmx ignores 204 No Content for swapping, so hx-target='closest tr' hx-swap='outerHTML' never runs.
- Expected: Row updates to read state (or disappears) and the badge decrements immediately.
- Code: src/grcen/templates/alerts/notifications.html:20-22; src/grcen/routers/alerts.py:157 (status_code=204)
- Fix: Return 200 with the updated <tr> fragment (and trigger the badge refresh via HX-Trigger header), or add hx-swap="delete" + an htmx:afterRequest handler.
- Verifier (confirmed): Verified by code and by inspecting the asset the live server actually serves (this session is in read-only/plan mode, so I did not execute the click-mutating Playwright repro; the mechanism is deterministic, not timing-dependent). (1) src/grcen/templates/alerts/notifications.html:20-22 renders `<button hx-post="/api/alerts/notifications/{id}/read" hx-target="closest tr" hx-swap="outerHTML">`. (2) 

## F251 [sev 3] [bug] 'Remove' relationship leaves the row on screen (204 not swapped) until reload
- Route: `/assets/{id} (Relationships > Remove)` · area: roles
- Actual: Same 204/htmx no-swap issue; user may click Remove again or think it failed.
- Expected: Row disappears on success.
- Code: src/grcen/templates/assets/detail.html:188-191; relationships delete route returns 204 (routers/relationships.py)
- Fix: Use hx-swap="delete" on the button (htmx honours delete swap on 2xx incl. 204 in 1.9+? verify) or return 200 with empty body; same for the attachment Remove buttons.
- Verifier (confirmed): Confirmed by static analysis of the shipped code (a live browser run was not performed: this session is in plan mode, which forbids non-read-only actions, and the repro requires creating/deleting a relationship). The mechanism is deterministic and needs no runtime state: (1) /home/t3rmit3/gits/GRCen/src/grcen/templates/assets/detail.html:188-191 renders `<button hx-delete="/api/relationships/{id}"

## F259 [sev 3] [bug] Export page 'Download Export' with the default 'All' status returns a raw 422 JSON error
- Route: `/exports` · area: roles
- Actual: The form submits empty strings for status/types/columns and the API rejects status=''. The page's primary action fails out of the box with an unstyled JSON page.
- Expected: A CSV download of all assets.
- Code: src/grcen/templates/exports/index.html:24-29 (option value=""); src/grcen/routers/exports.py:15-23 (status: AssetStatus | None query param rejects '')
- Fix: Strip empty query params client-side (or make the route treat '' as None); post the form to an HTML route that validates and re-renders with an error instead of exposing the JSON API.
- Verifier (confirmed): Confirmed by code reading plus an in-memory FastAPI reproduction (plan mode was active for this session, so I did not write a Playwright script/screenshots or hit the live server; the reproduction uses the exact route signature from routers/exports.py with the real grcen.models.asset.AssetStatus enum and the auth dependency stubbed). The export form in src/grcen/templates/exports/index.html is a p

## F268 [sev 3] [not-functional] 'Advanced' filter button on /assets does nothing (inline onclick blocked by CSP)
- Route: `/assets (and every register view /registers/{type} when no filter is active)` · area: visual-design
- Actual: #advanced-filters computed display stays 'none' before and after the click (measured); the panel is only ever visible when the server pre-opens it because a filter is already in the URL (e.g. /registers/vendors).
- Expected: Clicking Advanced reveals the advanced filter panel.
- Code: src/grcen/templates/assets/list.html:40 (onclick="toggleAdvanced()") and :198-201; src/grcen/middleware.py:87
- Fix: Bind the toggle in the nonce'd page script (document.getElementById('advanced-toggle').addEventListener) or use a <details> element; show an active-filter count on the button.
- Verifier (confirmed): Confirmed by code + live header + mechanism repro. (1) /home/t3rmit3/gits/GRCen/src/grcen/templates/assets/list.html:40 binds the button via onclick="toggleAdvanced()" and the function lives in a nonce'd <script> at :198-201; the panel #advanced-filters is server-rendered display:none and only opened by the inline auto-show block when a filter param is present. (2) curl against the live server (ht

## F269 [sev 3] [not-functional] Risk register 'select all' checkbox selects nothing, making Bulk Apply require clicking every row
- Route: `/risk-management` · area: visual-design
- Actual: The onclick handler on #risk-select-all is blocked by CSP; measured 0/20 rows checked after click.
- Expected: Header checkbox toggles all row checkboxes.
- Code: src/grcen/templates/risks/index.html:197 (onclick on #risk-select-all); partials/bulk_actions.html uses the same pattern
- Fix: Move select-all into the shared app.js (data-select-all=".risk-row-check") and show a 'N selected' counter next to Bulk Apply.
- Verifier (confirmed): Root cause verified from code (plan mode was active in this session, so the browser-click script was not executed; the mechanism below is deterministic, not probabilistic). src/grcen/middleware.py:85-93 emits `Content-Security-Policy: default-src 'self'; script-src 'self' 'nonce-<per-request>'; ...` on every response, with no 'unsafe-inline' and no 'unsafe-hashes'. Under a nonce-only script-src ev

## F308 [sev 2] [a11y] Risk register filter inputs on /assets have <label> text with no for/id association (9 unlabeled fields)
- Route: `/assets (also /reviews type/status selects)` · area: a11y-keyboard
- Actual: probe2.json assets_label_click: 9 labels with for=null and wraps=false; probe1.json /assets unlabeled=9, /reviews unlabeled=2.
- Expected: Every control has a programmatic label (WCAG 1.3.1, 3.3.2, 4.1.2); clicking the label focuses the control.
- Code: src/grcen/templates/assets/list.html:32-82 (form-group labels/inputs); src/grcen/templates/reviews template (type/status selects)
- Fix: Give each input/select an id (f-q, f-type, f-status, f-owner, f-created-after, f-created-before, f-meta-key, f-meta-value, f-tag) and set label for=… accordingly; same for /reviews. Add a template lint test that every input/select/textarea without type=hidden has an associated label or aria-label.
- Verifier (confirmed): Verified against the template source (plan mode blocked launching a fresh browser session, so I relied on the rendered-template source plus the earlier DOM probe output rather than a new screenshot). /home/t3rmit3/gits/GRCen/src/grcen/templates/assets/list.html lines 33-93: all nine filter controls (Search q, Type select, Status select, Owner, Created after, Created before, Metadata field, Metadat

## F309 [sev 2] [a11y] Grid-style admin forms rely on column headers alone: 72 (/admin/workflow), 196 (/admin/sensitive-fields), 48 (/controls), 6-per-row (/frameworks/{id}/soa) and 17 (asset sensitive overrides) controls have no accessible name
- Route: `/admin/workflow (also /admin/sensitive-fields, /controls, /frameworks/{id}/soa, /assets/{id} 'Sensitive field overrides')` · area: a11y-keyboard
- Actual: probe3.json workflow_row_sample: label/labelledby null, wrapped=false; workflow_th_scope all null, first cell is TD; probe4.json controls_selects.selectLabel=null; soa.unlabeled=6 of 7 inputs; probe3.json detail_unlabeled lists 17 override selects.
- Expected: Every control has an accessible name that includes the row context, e.g. 'Require approval to create — Audit' (WCAG 1.3.1, 4.1.2, 3.3.2); table headers carry scope=col / scope=row.
- Code: src/grcen/templates/admin/workflow_settings.html:24-45; src/grcen/templates/admin/sensitive_fields.html:40-47; src/grcen/templates/frameworks/controls.html (select name=result); src/grcen/templates/frameworks/soa.html:45-56; src/grcen/templates/assets/detail.html:56-68
- Fix: Add aria-label="{{ column }} — {{ row label }}" to each control (or aria-labelledby pointing at the column <th id> and a row <th scope=row id>), set scope="col" on header cells and make the first cell of each row a <th scope="row">. Where the whole row is one form (/controls, /soa), wrap it in a <fieldset> with a visually-hidden <legend> naming the control/requirement.
- Verifier (confirmed): Verified against the server-rendered templates (the pages are plain Jinja with no client-side script that adds labels; the only aria-label in base.html is the global search box) plus the prior tester's rendered screenshots (p3-workflow.png, p4-soa.png) and probe3/probe4.json. All five locations match the description exactly: (1) /admin/workflow — workflow_settings.html lines 17-21 are bare <th> wi

## F310 [sev 2] [a11y] Asset detail Add Relationship / Add URL / Upload inputs are placeholder-only (no labels), and the Clone checkbox label reads '+ rels'
- Route: `/assets/{id}` · area: a11y-keyboard
- Actual: probe3.json detail_unlabeled lists rel-target-search, relationship_type, description, name, url_or_path, file.
- Expected: Persistent visible labels (or at least aria-label) for every field (WCAG 3.3.2, 1.3.1); clear label text such as 'Include relationships'.
- Code: src/grcen/templates/assets/detail.html:197-212 (add-relationship-form), :231-247 (attachment forms), :26-31 (clone checkbox)
- Fix: Add <label for> elements (can be visually compact above each input in .form-row) or aria-label attributes mirroring the placeholders; rename the clone checkbox to 'Include relationships'. Keep placeholders as examples, not names.
- Verifier (confirmed): Reproduced on https://localhost:8443/assets/2a65f0ed-9036-4f77-a582-30438b7f835b as admin06 via Playwright DOM/accessibility-tree dump. All six inputs named in the finding (#rel-target-search, relationship_type, description, attachment name, url_or_path, file) have no <label>, aria-label, aria-labelledby or title; `input.labels` is empty for each. Their only accessible name comes from the placehol

## F311 [sev 2] [bug] Risk register 'Score' sort indicator renders the literal text '&#9660;' instead of an arrow
- Route: `/risk-management (default sort by score desc, and any sorted column)` · area: a11y-keyboard
- Actual: Jinja autoescaping turns the HTML entity string into visible text; no aria-sort anywhere (probe5.json risk_sort_arrow_text='&#9660;', probe3.json risk_sort_headers ariaSort all null).
- Expected: A ▲/▼ glyph (as /assets does) plus aria-sort on the <th> so the sort state is also exposed programmatically (WCAG 1.3.1).
- Code: src/grcen/templates/risks/index.html:197-203 (sort_link macro, line 201 '&#9650;'/'&#9660;')
- Fix: Use the literal characters '▲'/'▼' (as assets/list.html does) or wrap in |safe, and add aria-sort="ascending|descending" on the sorted <th> in both the risk and asset list macros; keep the arrow aria-hidden since aria-sort carries the meaning.
- Verifier (confirmed): Logged in as admin07 via Playwright and inspected the live DOM. GET /risk-management (200): the Score header innerText is 'SCORE &#9660;' and the sort span's outerHTML is '<span class="sort-arrow">&amp;#9660;</span>' — the entity is double-escaped and shows as literal text. GET /risk-management?sort=name&order=asc: 'RISK &#9650;' rendered the same way. Every <th> in the risk table has aria-sort=nu

## F312 [sev 2] [a11y] Sortable headers, pagination and the active page have no programmatic state: no aria-sort, no <nav aria-label=Pagination>, no aria-current on 27 numbered links
- Route: `/assets` · area: a11y-keyboard
- Actual: probe2.json assets_sort_headers ariaSort null; assets_pagination role null, label null, activeCurrent null, count 27.
- Expected: <th aria-sort>, <nav aria-label="Pagination">, aria-current="page" on the active link and link names like 'Page 2' (WCAG 1.3.1, 4.1.2, 2.4.4). Ideally prev/next links instead of 27 numbers.
- Code: src/grcen/templates/assets/list.html:112-120 (sort_th macro) and :178-185 (pagination)
- Fix: Wrap pagination in <nav aria-label="Pagination">, add aria-current="page" and aria-label="Page N" to links, add Previous/Next and collapse the middle pages; emit aria-sort on the sorted <th> in sort_th.
- Verifier (confirmed): Confirmed by source inspection (plan mode prevented launching a browser or writing scripts, so no new screenshot; the finding is about static server-rendered markup, which the template determines deterministically). /home/t3rmit3/gits/GRCen/src/grcen/templates/assets/list.html: the sort_th macro (lines 123-131) emits `<th><a href=... >{{ col.label }} {{ arrow }}</a></th>` with the ▲/▼ glyph as bar

## F313 [sev 2] [ux] No success/status feedback and focus reset to <body> after create, edit, Watch/Unwatch, Workflow Save and other POST-redirect actions
- Route: `/assets/new, /assets/{id}/edit, /assets/{id} (Watch), /admin/workflow` · area: a11y-keyboard
- Actual: probe3.json after_create.flash=null, watch_after.flash=null, workflow_save.flash=null; probe5.json edit_save.flash=null; base.html has no global flash/live region (probe1 live=[] on all routes). Only some pages (controls, settings, sessions...) render their own flash div, and those have no role/aria-live either.
- Expected: A visible, announced status message (role=status / aria-live=polite) after every state change, and focus placed on the page heading or the message (WCAG 4.1.3 Status Messages, 3.3.1).
- Code: src/grcen/templates/base.html:95-97 (no flash/live region); src/grcen/routers/asset_pages.py:404-470 (create redirect without flash); workflow router POST /admin/workflow; src/grcen/templates/frameworks/controls.html:20-23 (per-page flash without role)
- Fix: Add a session-backed flash helper (set in the router, popped in base.html into <div class=alert role=status aria-live=polite>), use it after create/edit/delete/watch/save, and render existing per-page flash divs with role="status" (success) or role="alert" (errors). Also give #notif-badge aria-live="polite" aria-atomic="true" so the hourly count change is announced once.
- Verifier (confirmed): Verified by code inspection plus read-only live probes (logged in as admin09; no objects created or settings toggled, since the session is in plan mode). Code: every POST-redirect named in the finding returns a bare 302 with no flash/status carried across — asset create `src/grcen/routers/asset_pages.py:479` → `/assets/{id}`, edit `:694`, watch `:569` / unwatch `:579` → `/assets/{id}`, workflow sa

## F314 [sev 2] [a11y] Login error 'Invalid credentials' is not announced and the username is wiped on failure
- Route: `/login` · area: a11y-keyboard
- Actual: login_fail_body_alert = <div class="alert alert-error">Invalid credentials</div>; login_username_kept='' (csp_check output, probe2.json).
- Expected: Error announced to assistive tech (role=alert), username preserved so only the password needs retyping, fields marked aria-invalid with aria-describedby pointing at the message (WCAG 3.3.1, 3.3.3, 4.1.3).
- Code: src/grcen/templates/auth/login.html:6-8 and :15-16; src/grcen/routers/auth_pages.py:68-75
- Fix: Render the error as <div class="alert alert-error" role="alert">, pass username back into the context and prefill value="{{ username }}", add aria-invalid="true" aria-describedby="login-error" to the inputs when error is set, and keep autofocus on the password field in that case.
- Verifier (confirmed): Verified against the running instance and the source, without a fresh failed-login POST because this session is in plan mode (read-only; writing a verify script or recording a failed-login row would violate it). Evidence: (1) live `curl -sk https://localhost:8443/login` returns `<input type="text" id="username" name="username" required autofocus>` and `<input type="password" id="password" name="pa

## F316 [sev 2] [a11y] Control test-history sparkline encodes pass/partial/fail by colour only, with the result text hidden in a title tooltip
- Route: `/controls (Testing column)` · area: a11y-keyboard
- Actual: probe4.json controls_sparkline: spans with text '', bg rgb(220,38,38)/rgb(22,163,74), role null, label null.
- Expected: A non-colour cue plus accessible text (WCAG 1.4.1 Use of Color, 1.1.1), e.g. visually-hidden 'pass, fail, pass' or glyphs (✓ ◐ ✕) with aria-label on the group.
- Code: src/grcen/templates/frameworks/controls.html:4-7 (spark macro); the same colour-only pattern is used for the trend arrows in risks/index.html:17-29 (red/green decide good vs bad)
- Fix: Wrap the sparkline in <span role="img" aria-label="Last {{ n }} tests: pass, fail, ..."> and render a glyph or letter inside each square; for the risk trend arrows add a visually-hidden 'increase (worse)' / 'decrease (better)' text rather than relying on red/green.
- Verifier (confirmed): Confirmed from the shipped template and the earlier probe artefact; could not re-render live because the instance currently has zero control test runs. The `spark` macro in src/grcen/templates/frameworks/controls.html:4-7 emits, per result, `<span title="{{ r }}" style="display:inline-block;width:9px;height:9px;...background:#16a34a|#ca8a04|#dc2626">` with empty content — no text node, no role, no

## F317 [sev 2] [a11y] Heading levels skip: h1→h3 on /settings, /my-work, /frameworks/{id}, /questionnaires, /reports/executive, /admin/content-packs, /frameworks index and h2→h4 / out-of-order h3 on asset detail
- Route: `/assets/{id} (and the routes listed)` · area: a11y-keyboard
- Actual: probe3.json detail_headings and probe4.json headings show the jumps listed.
- Expected: A logical outline (h1 → h2 → h3) so screen-reader heading navigation reflects the page structure (WCAG 1.3.1; 2.4.10 AAA).
- Code: src/grcen/templates/assets/detail.html:129,145,152,197,231,241 (h3/h2/h4 mix); src/grcen/templates/settings.html, my_work.html, frameworks/detail.html, frameworks/index.html, admin/content_packs.html (h3 under h1)
- Fix: Use h2 for page sections (Description, Details, Relationships, Attachments, Alerts) and h3 for the sub-forms (Add Relationship, Add URL Attachment, Upload File); change the h3s directly under h1 on the other pages to h2 and restyle via a class if the smaller size is wanted.
- Verifier (confirmed): Plan mode was active for this session, so I verified against the rendered-outline ground truth (the Jinja templates, which emit the headings statically) plus the prior tester's captured DOM probes rather than re-driving the browser. Every claimed jump exists in the source:

- src/grcen/templates/assets/detail.html: h1 (L5) -> h3 Description (L131, inside {% if asset.description %}) -> h3 Details (

## F215 [sev 2] [not-functional] Audit Settings: field-level checkbox does not disable when 'Audit Events Enabled' is unchecked
- Route: `/admin/audit/settings` · area: admin-settings
- Actual: Inline onchange is CSP-blocked; toggleFieldLevel never runs. Also no 'saved' feedback after Save Settings.
- Expected: Field-level diff option greys out and clears when events are disabled (the page ships JS for exactly this).
- Code: src/grcen/templates/admin/audit_settings.html:26,41-54; src/grcen/routers/admin_pages.py:534-546
- Fix: Bind listeners in the nonce'd script; add a flash on save.
- Verifier (confirmed): Reproduced live as admin07 on https://localhost:8443/admin/audit/settings (HTTP 200, 5 rows: Alert/Asset/Attachment/Relationship/User, all enabled + field-level ticked). Unticking 'Audit Events Enabled' for Alert left the 'Field-Level Diffs' checkbox enabled and still checked (fl_disabled=False, fl_checked=True). Browser console logged: "Executing inline event handler violates the following Conten

## F216 [sev 2] [bug] Audit log ?page=0 returns HTTP 500; ?page=abc returns raw JSON 422; out-of-range page renders nonsense
- Route: `/admin/audit` · area: admin-settings
- Actual: 500 / raw 422 JSON / 'page 9999 of 6'.
- Expected: page clamped to 1..pages; non-integer handled with the styled error page.
- Code: src/grcen/routers/admin_pages.py:282-310 (admin_audit_log) and the audit service pagination
- Fix: Declare page: int = Query(1, ge=1) and clamp page to max(1, min(page, pages)) before computing OFFSET; render a styled error page for validation errors on HTML routes.
- Verifier (confirmed): Verified by code inspection and the running container's logs (plan mode in effect for this session, so I did not log in or drive a browser; no new state was created). (1) /admin/audit?page=0: src/grcen/routers/admin_pages.py:284 declares `page: int = 1` with no bound, and services/audit_service.py:187 computes `(page - 1) * page_size` = -50 for OFFSET; `docker logs grcen-app-1` contains tracebacks

## F218 [sev 2] [bug] Access-log 'Target' links always point to /assets/{id}, 404 for questionnaires and attachments
- Route: `/admin/access-log` · area: admin-settings
- Actual: Template hard-codes /assets/{{ e.entity_id }} regardless of entity_type.
- Expected: Links resolve to the correct page per entity type, or no link when none exists.
- Code: src/grcen/templates/admin/access_log.html:79-83
- Fix: Map entity_type to a route (asset->/assets, framework->/frameworks, questionnaire->/questionnaires, attachment->owner asset) and omit the link for unknown types.
- Verifier (confirmed): Confirmed from code and live data (browser run skipped because this session is in plan mode, which forbids creating sessions/log rows; all checks were read-only).

Template: /home/t3rmit3/gits/GRCen/src/grcen/templates/admin/access_log.html lines 78-83 render `<a href="/assets/{{ e.entity_id }}">` for every row with an entity_id, with no branch on `e.entity_type`.

Writers: access_log_service.reco

## F221 [sev 2] [bug] Username uniqueness is case-sensitive: 'VIEWER2' created alongside 'viewer2'
- Route: `/admin/users/new` · area: admin-settings
- Actual: Two visually identical accounts with different roles can coexist, inviting confusion and impersonation-style mistakes.
- Expected: 'That username is already taken.' (case-insensitive), since users will type their login in any case.
- Code: src/grcen/services/auth_service.create_user / users unique constraint
- Fix: Normalise usernames (lower()) on create/lookup or add a unique index on lower(username).
- Verifier (partially): Mechanism verified read-only (plan mode was active, so I did not perform the create and no scripts/screenshots were produced in work/verify/). Code: /home/t3rmit3/gits/GRCen/src/grcen/routers/admin_pages.py:80 does only `str(form["username"]).strip()` with no case normalisation, and /home/t3rmit3/gits/GRCen/src/grcen/services/auth.py:97-111 inserts the raw string and relies solely on asyncpg.Uniqu

## F224 [sev 2] [ux] Many admin forms give no success feedback after Save
- Route: `/admin/oidc-settings, /admin/saml-settings, /admin/smtp-settings, /admin/audit/settings, /admin/users (create/edit/deactivate/delete), /tokens Save IPs` · area: admin-settings
- Actual: Silent 302 back to the same page; admins cannot tell whether the click registered.
- Expected: A 'Settings saved' / 'User X created' flash, consistent with SCIM/ITSM/webhooks which do flash.
- Code: src/grcen/routers/admin_pages.py:123,214,245,275,592,649,735,546; src/grcen/routers/settings_pages.py:411
- Fix: Use the existing ?flash=ok: pattern on every POST redirect.
- Verifier (confirmed): Verified by code inspection (plan mode restricted this run to read-only actions, so no browser script was executed; the behavior is fully determined by the handler/template code and does not depend on runtime state). Every POST handler the finding lists returns a bare `RedirectResponse("<same page>", status_code=302)` with no `?flash=` parameter: /admin/users/new (admin_pages.py:123), /admin/users

## F225 [sev 2] [ux] Validation errors discard what the user typed (redirect-with-flash pattern)
- Route: `/admin/users/new, /tokens, /admin/oauth-clients, /admin/scim-settings` · area: admin-settings
- Actual: Input is lost on every validation failure.
- Expected: Re-render the form with the submitted values and the error next to the offending field.
- Code: src/grcen/routers/admin_pages.py:100-112,1167-1183,832-836; src/grcen/routers/settings_pages.py:318-343,397-402
- Fix: Render the template with the posted values on error instead of redirecting; for the IP textarea keep the raw submission.
- Verifier (confirmed): Plan mode was active for this session, so I could not write/run the Playwright script or create screenshots; verification is by reading the live route and template source, which is unambiguous for all five cases. (1) /admin/users/new (src/grcen/routers/admin_pages.py:83-112): on both the "required" and UserExistsError branches the handler re-renders admin/user_form.html but passes only edit_user=N

## F226 [sev 2] [bug] Token expiry is not validated: past dates accepted and over-limit dates silently shortened
- Route: `/tokens` · area: admin-settings
- Actual: Both accepted silently; the 'max 30 days' hint is the only clue.
- Expected: Reject past dates; either reject over-limit dates or tell the user the expiry was capped.
- Code: src/grcen/routers/settings_pages.py:337-343; src/grcen/services/token_service.py:106-110
- Fix: Validate expires_at > now in my_tokens_create; when capping, add a flash 'Expiry shortened to N days per policy'.
- Verifier (confirmed): Verified by code trace (plan mode was active for this session, so I did not drive the browser to mint tokens, which would mutate app state; the reporter's screenshots are consistent with what the code does deterministically).

Past dates: /home/t3rmit3/gits/GRCen/src/grcen/routers/settings_pages.py:337-343 parses `expires_at` with `dt.fromisoformat(...).replace(tzinfo=UTC)` and only catches ValueE

## F227 [sev 2] [bug] OIDC client secret is echoed back into the page HTML (value attribute of the password field)
- Route: `/admin/oidc-settings` · area: admin-settings
- Actual: Secret is sent to the browser on every page load (visible in source, saved by password managers/autofill, cached).
- Expected: Placeholder '********' preserved on save, like the other secret fields.
- Code: src/grcen/templates/admin/oidc_settings.html:41; src/grcen/routers/admin_pages.py:576-586
- Fix: Render '********' when a secret exists and keep the stored value when the placeholder is posted (same as admin_smtp_settings_submit).
- Verifier (confirmed): Confirmed primarily from the code, which is unambiguous and deterministic; I did not perform the save step myself because the OIDC config is a global setting (task says do not change global settings) and the session is in plan mode. What I observed: GET /admin/oidc-settings as admin03 returns 200 with `<input type="password" id="client_secret" ... value="">` — currently no secret is stored in this

## F228 [sev 2] [bug] Rate-limit per-route overrides accept arbitrary text with 'saved' confirmation
- Route: `/admin/rate-limits` · area: admin-settings
- Actual: Stored verbatim; the parser silently drops bad entries at runtime (API still answered with limit 600), so the admin thinks a limit is in force when it is not.
- Expected: Parse entries on save and reject malformed ones with the offending entry named.
- Code: src/grcen/routers/admin_pages.py:1386-1414; src/grcen/rate_limit.py:113-135
- Fix: Call rate_limit._parse_route_overrides in the POST handler and fail with the unparseable entries listed.
- Verifier (confirmed): Confirmed by code inspection plus a direct exercise of the parser (I did not submit the form because rate-limit overrides live in the global, org-less `app_settings` table and the task forbids changing global settings; the code path is deterministic, so no browser repro is needed). The POST handler at src/grcen/routers/admin_pages.py:1386-1414 reads `form['overrides']`, `.strip()`s it and upserts 

## F229 [sev 2] [bug] Organization branding accepts an invalid colour and an http:// logo despite the 'must be HTTPS' label
- Route: `/admin/organization` · area: admin-settings
- Actual: Both stored; email preview renders with a broken colour and a mixed-content logo.
- Expected: Validate hex colour (#rrggbb) and https scheme; show errors.
- Code: src/grcen/routers/admin_pages.py:471-485; src/grcen/templates/admin/organization.html:33-40
- Fix: Regex-validate colour, require urlparse(url).scheme == 'https', use type=color input.
- Verifier (confirmed): I did not re-run the save in the browser: plan mode was active for this agent (read-only only), and the repro mutates the org-wide email branding row (a global setting the brief says not to toggle), so I verified from the code path plus the prior tester's artifacts, which are consistent with it. Code: POST /admin/organization/branding (src/grcen/routers/admin_pages.py:471-485) does only .strip() a

## F230 [sev 2] [bug] SMTP settings accept impossible host/port values
- Route: `/admin/smtp-settings` · area: admin-settings
- Actual: Stored verbatim; failure only surfaces later in delivery logs.
- Expected: Port 1-65535 and a hostname-shaped host, with an error otherwise.
- Code: src/grcen/routers/admin_pages.py:705-716; src/grcen/templates/admin/smtp_settings.html:33-35
- Fix: Add min=1 max=65535 on the port input and server-side validation.
- Verifier (confirmed): Confirmed by static inspection of the code path plus the original tester's screenshot (work/admin-settings/smtp_invalid_saved.png shows host 'not a host!!' and port 99999 rendered back from storage with no error banner). I did not re-execute the save myself: the session is in plan mode (read-only actions only) and the task forbids changing global settings, which the SMTP config is. The code leaves

## F231 [sev 2] [ux] 'Save & Send Test' persists settings before testing and cannot target a recipient
- Route: `/admin/smtp-settings` · area: admin-settings
- Actual: Bad config is persisted and the test is impossible for users without an email.
- Expected: Test without committing, or at least a 'Send to' address field so the test is possible for any admin.
- Code: src/grcen/routers/admin_pages.py:718-733
- Fix: Add a 'test recipient' input; run the test against the posted (unsaved) settings and save only on explicit Save.
- Verifier (confirmed): Verified by code inspection plus a read-only browser pass as admin07 (I did not click "Save & Send Test" because it unconditionally persists the global SMTP config, which the task forbids changing; plan mode also restricted me to read-only actions).

Observed live (logged in as admin07):
- /settings shows "Email: (not set — ask an admin to configure one so you can receive email notifications)" — a

## F232 [sev 2] [bug] Webhook event filter accepts unknown event names
- Route: `/admin/webhooks` · area: admin-settings
- Actual: Typos are stored silently and the hook never fires for the intended event.
- Expected: Reject names not in the supported list that the form itself prints (or use checkboxes).
- Code: src/grcen/routers/admin_pages.py:920-944; src/grcen/templates/admin/webhooks.html:67-71
- Fix: Validate against webhook_service.WEBHOOK_EVENTS; render events as checkboxes.
- Verifier (partially): The validation gap is real and verified from the code and database rather than by re-creating a webhook (plan mode forbids mutating the running app; the prior tester's own script deleted its row, so `webhooks` is currently empty). Evidence: (1) `_parse_event_filter` at src/grcen/routers/admin_pages.py:878-879 is `[p.strip() for p in raw.split(",") if p.strip()]` with no membership check against `w

## F234 [sev 2] [bug] SCIM group->role map accepts roles that do not exist
- Route: `/admin/scim-settings` · area: admin-settings
- Actual: Stored; users in that group silently fall back to the default role.
- Expected: Error naming the invalid role (valid: admin, editor, viewer, auditor).
- Code: src/grcen/routers/admin_pages.py:816-837
- Fix: Validate every value is a UserRole in admin_scim_settings_submit.
- Verifier (confirmed): Confirmed from the code path (a live POST was not performed: the session is in plan mode / read-only, and the repro would rewrite the global app_settings['scim_group_role_map'], which the task forbids; GET https://localhost:8443/admin/scim-settings is live and returns 401 unauthenticated as expected). In /home/t3rmit3/gits/GRCen/src/grcen/routers/admin_pages.py:825-837 `admin_scim_settings_submit`

## F235 [sev 2] [ux] Sensitive Fields grid: 196 rows, 10,300px tall, single Save at the very bottom, no grouping/filter
- Route: `/admin/sensitive-fields` · area: admin-settings
- Actual: One flat 196-row table with a static Save button; no indication of unsaved changes.
- Expected: Group by asset type (collapsible), a filter box, a sticky Save bar, 'changed' highlighting, and return to the edited row.
- Code: src/grcen/templates/admin/sensitive_fields.html
- Fix: Render per-type <details> sections with per-section counts of overrides, a text filter, and position:sticky form actions.
- Verifier (confirmed): Verified from the template, the router, and the existing full-page screenshots (plan mode blocked writing/running a fresh Playwright script, so no new screenshots were taken; the prior tester's smoke_admin_sensitive-fields.png is 1728x10303 px and shows the state described). src/grcen/templates/admin/sensitive_fields.html renders a single flat <table> inside one <form> with one static <button type

## F236 [sev 2] [ux] Admin Sessions list is unusable at scale: no 'this session' marker, no user filter, capped list
- Route: `/admin/sessions` · area: admin-settings
- Actual: Flat list of 50 identical-looking rows with a red Revoke on each (including your own, which logs you out instantly without confirmation).
- Expected: Mark the current session, group/filter by user, show total and paginate, 'Revoke all for user'.
- Code: src/grcen/templates/admin/sessions.html; src/grcen/routers/admin_pages.py:1253-1295
- Fix: Add username filter + per-user grouping, mark current session_id, paginate, and a confirm for self-revoke.
- Verifier (partially): Judged from source (plan mode active, so I did not drive the browser; the page is a static Jinja render of one query, so the code is conclusive for this UX finding). Confirmed parts, from /home/t3rmit3/gits/GRCen/src/grcen/templates/admin/sessions.html and routers/admin_pages.py admin_sessions(): the template renders a flat table (User / Started / Last active / User agent / Revoke) with no usernam

## F237 [sev 2] [content] Organization page says the org switcher 'is not yet implemented' although it exists
- Route: `/admin/organization` · area: admin-settings
- Actual: Stale text contradicts the product and the 'All Organizations' admin link.
- Expected: Accurate copy: multi-org membership exists (user_organizations, /settings switcher, /admin/orgs, invitations).
- Code: src/grcen/templates/admin/organization.html:20-25
- Fix: Replace with a short description of org membership and link to /admin/invitations and /settings.
- Verifier (confirmed): Confirmed from source (plan mode prevented driving the browser, so no new screenshot; the finding's own org_initial.png already shows the rendered state). /home/t3rmit3/gits/GRCen/src/grcen/templates/admin/organization.html lines 20-25 hardcode, unconditionally: "Multi-org membership and an in-app org switcher are not yet implemented; every user belongs to exactly one organization. New organizatio

## F238 [sev 2] [design] Content Packs heading is squeezed into two lines beside the intro paragraph
- Route: `/admin/content-packs` · area: admin-settings
- Actual: Broken hierarchy; the most important title on the page is the least readable element.
- Expected: Heading on its own line, intro below.
- Code: src/grcen/templates/admin/content_packs.html:5-15; src/grcen/static/css/style.css:139
- Fix: Move the <p> out of .page-header or give .page-header flex-direction:column for this page.
- Verifier (confirmed): Observed in the 1440px admin-settings smoke screenshot (/tmp/claude-1000/-home-t3rmit3-gits-GRCen/7ebb0b49-1352-428e-a038-c28a4c71f5b6/scratchpad/pw/work/admin-settings/smoke_admin_content-packs.png): the H1 renders as "Content" on one line and "Packs" on the next, in a ~120px column at the left, with the four-line intro paragraph sitting to its right and the word "Content" butting directly agains

## F239 [sev 2] [design] Checkboxes inside .form-group are stretched to full width and detach from their labels
- Route: `/settings (also /tokens 'Service account', /admin/smtp-settings, /admin/webhooks Enabled)` · area: admin-settings
- Actual: .form-group input {width:100%} applies to type=checkbox, giving a 1102px-wide checkbox box.
- Expected: Checkbox inline, immediately before its label text.
- Code: src/grcen/static/css/style.css:177; src/grcen/templates/settings.html:31-36
- Fix: Exclude checkboxes/radios: .form-group input:not([type=checkbox]):not([type=radio]) {width:100%} and style label.inline as flex.
- Verifier (confirmed): Reproduced as admin15 at 1440x900 by measuring live DOM geometry via Playwright on all four reported routes (all HTTP 200). On /settings the `email_notifications_enabled` checkbox has computed width 1102px (getBoundingClientRect w=1102, h=13) and its enclosing <label> is 45px tall, i.e. the checkbox occupies a full line by itself and the text "Send me an email when an alert fires..." wraps to the 

## F240 [sev 2] [ux] Audit Log filters are free-text and case-sensitive; entities are not linked; changes are raw JSON; no export
- Route: `/admin/audit` · area: admin-settings
- Actual: Guessing game for filter values; copy-paste of ids to navigate.
- Expected: Dropdowns populated from existing values (as /admin/access-log does), links to entities, a readable old/new diff table, and an export.
- Code: src/grcen/templates/admin/audit_log.html:11-17,36-49
- Fix: Reuse the access-log filter selects, ILIKE matching, render changes as a field/old/new table, link entity_id by type, add export.csv.
- Verifier (confirmed): Plan mode was active for this subagent, so I could not write a Playwright script or log in (session-creating POST); verification is from the read-only source, which is conclusive for every claim in F240. (1) Case-sensitive exact match: src/grcen/services/audit_service.py:167-173 builds `entity_type = $n` and `action = $n` (plain equality), while only `username` uses ILIKE (line 176). Audit writers

## F241 [sev 2] [ux] Data Access Log silently caps at 200 rows with no pagination or total
- Route: `/admin/access-log` · area: admin-settings
- Actual: Hidden truncation.
- Expected: Total count and pagination like the audit log, plus date range filters (the REST API already supports since/until).
- Code: src/grcen/routers/admin_pages.py:1440-1485; src/grcen/templates/admin/access_log.html
- Fix: Add page/limit + since/until to the HTML route and show the count.
- Verifier (confirmed): Session was in plan mode (read-only), so I did not write a new Playwright script; I verified from code, a read-only DB count, and the prior tester's screenshot. (1) Code: /home/t3rmit3/gits/GRCen/src/grcen/routers/admin_pages.py:1450-1457 calls access_log_service.query(..., limit=200) with no page/offset/since/until query params, and the template /home/t3rmit3/gits/GRCen/src/grcen/templates/admin/

## F242 [sev 2] [ux] Invite acceptance can hit the shared per-IP login debounce and shows a bare unstyled 'Too many login attempts' page
- Route: `/invite/{token}` · area: admin-settings
- Actual: Raw 429 text; the wording refers to logins, confusing a first-time user.
- Expected: A styled error inside the invite page, retaining the form, with a message appropriate to account creation.
- Code: src/grcen/rate_limit.py:39-41; src/grcen/routers/auth_pages.py:290-296
- Fix: Give /invite its own debounce bucket and render the 429 through the invite template (or styled error page).
- Verifier (confirmed): Mechanism verified in code and the state is visible in the prior tester's screenshot (/tmp/.../pw/work/admin-settings/inv_after_accept.png: a blank white page with only the default-serif text "Too many login attempts. Please wait." — no layout, no form, no back link). Code path is deterministic: POST /invite/{token} (src/grcen/routers/auth_pages.py:291-296) depends on check_login_rate_limit, which

## F167 [sev 2] [bug] Uploading a CSV containing NUL bytes returns a raw 'Internal Server Error' page
- Route: `POST /questionnaires/{id}/import` · area: answers-questionnaires
- Actual: HTTP 500, blank page with the text 'Internal Server Error', no way back except the browser button.
- Expected: Stay on the questionnaire with a message like 'That file does not look like a CSV'.
- Code: src/grcen/services/questionnaire_service.py parse_questions (csv.reader raises _csv.Error 'line contains NUL'; decode(errors='replace') does not guard it); router answer_pages.py:147 has no try/except.
- Fix: Wrap csv parsing in try/except csv.Error (and reject files with NUL bytes), redirect back with a ?flash=fail message.
- Verifier (confirmed): The 500 is real but the reported mechanism is wrong. This session ran under plan mode (read-only), so I could not write/run a fresh Playwright script; instead I verified from the running container's own logs and the code. `docker logs grcen-app-1` contains the traceback for the original tester's upload of admin08-bad.csv (bytes 00 01 02 ff fe 'garbage' 00 00): it ends at `routers/answer_pages.py:1

## F172 [sev 2] [ux] 'Needs review' flags cannot be acknowledged or resolved from the library; Last Reviewed date is ignored
- Route: `/answers` · area: answers-questionnaires
- Actual: The only way to clear a flag is to change the substantiating asset graph; a reviewer who confirms the answer is still accurate has nowhere to record that, so the warning count never goes down.
- Expected: A way to clear or snooze the flag after a human review (e.g. 'Reviewed today — still accurate'), or at least guidance on what will clear it (re-link to an active control).
- Code: src/grcen/services/answer_service.py:56-66 (_review_reasons) and :121-123; src/grcen/templates/answers/list.html:61-71.
- Fix: Add a 'Mark reviewed' action that stamps last_reviewed and records which substantiator state was accepted (e.g. metadata.review_ack = {asset_id: status}); suppress reasons that match the ack until the state changes again.
- Verifier (confirmed): Verified by code reading plus the existing /answers screenshot (work/answers-questionnaires/01_answers.png): banner "3 answers need review", three rows flagged (Legacy DLP inactive; unsubstantiated; Data Classification Policy archived), and no button/link/form in the Review status column or anywhere on the page to acknowledge, snooze or clear a flag. The only "Mark reviewed" controls in the codeba

## F175 [sev 2] [ux] Questionnaire name, source and due date cannot be edited after creation
- Route: `/questionnaires/{id}` · area: answers-questionnaires
- Actual: Only Status and Delete exist; the only fix is delete-and-recreate (losing all answers). Same gap on /vendor-campaigns/{id}.
- Expected: Edit name/source/due date in place (same for campaign name/vendor/due).
- Code: src/grcen/templates/answers/questionnaire_detail.html:43-60; src/grcen/routers/answer_pages.py has no PATCH/edit route; vendor_campaigns.py likewise.
- Fix: Add a small 'Details' form (name, source, due_date) posting to /questionnaires/{id} and the equivalent for campaigns.
- Verifier (confirmed): Verified from the code (the session was in plan/read-only mode so no Playwright script could be written; for an "absent control" UX finding the templates and routers are decisive). /home/t3rmit3/gits/GRCen/src/grcen/templates/answers/questionnaire_detail.html renders name/source/due_date as static text (h1 + grey subtitle, lines 6 and 15-19) and the only mutating forms on the page are: CSV import,

## F176 [sev 2] [ux] Library-answer mapping is a plain <select> of truncated questions with no search and no preview before saving
- Route: `/questionnaires/{id}` · area: answers-questionnaires
- Actual: With 8 answers it is tolerable; with a real library of hundreds it is unusable. Pre-save textarea stays '' after selection.
- Expected: Type-ahead search over question + answer text (the asset search API already exists), and immediate preview of the canonical text when an option is chosen.
- Code: src/grcen/templates/answers/questionnaire_detail.html:80-87
- Fix: Reuse the relationship_form.js autocomplete pattern against /api/assets/search?type=answer, and populate the textarea client-side from a data-answer attribute (nonce'd script) when an option is picked.
- Verifier (confirmed): Verified by reading the template, router (routers/answer_pages.py:93-122, 156-184) and service, and by inspecting the reporter's screenshot of the live page (work/answers-questionnaires/10_seeded_questionnaire_detail.png). Note: this session is in plan mode, so I did not drive a fresh browser run; the verdict rests on the page source plus the existing screenshot, which show the described state una

## F177 [sev 2] [ux] No back-links between a questionnaire response and the library answer it maps to (and none from an answer to the questionnaires that use it)
- Route: `/questionnaires/{id} and /assets/{answer_id}` · area: answers-questionnaires
- Actual: Graph-centric product, but this edge is invisible in both directions. list_responses already fetches answer_question but the template never renders it.
- Expected: The mapped answer's title is a link; the answer detail page has a 'Used in N questionnaire responses' section so a stale answer can be traced to outbound documents.
- Code: src/grcen/templates/answers/questionnaire_detail.html:76-96 (answer_question unused); src/grcen/templates/assets/detail.html (no questionnaire usage block).
- Fix: Render <a href=/assets/{{r.answer_asset_id}}>{{r.answer_question}}</a> under the select; add a 'Used in questionnaires' card on Answer detail pages querying questionnaire_responses by answer_asset_id.
- Verifier (confirmed): Reproduced as admin01 against the seeded questionnaire /questionnaires/fdfa34fd-f122-41b9-a1cd-983e8f21eaa7 ("Acme Corp — Vendor Security Assessment 2026"): 6 response rows, rows 0 and 1 have answer_asset_id selected in the mapping <select> (b39f3f05-... and d4dcd501-...), and the table body contains 0 anchors to /assets/ — the only way to reach the mapped library answer is to read the truncated o

## F178 [sev 2] [ux] Per-question Save does a full redirect to the top of the page with no success feedback
- Route: `/questionnaires/{id}` · area: answers-questionnaires
- Actual: Redirect to /questionnaires/{id} with no fragment and no message; 12 identical Save buttons.
- Expected: Save in place (htmx row swap, which the app already uses elsewhere) or at least redirect to #response-{id} with a flash.
- Code: src/grcen/routers/answer_pages.py:184 (RedirectResponse without fragment); template questionnaire_detail.html:78-92.
- Fix: Give each row id="r-{{r.id}}" and redirect to .../#r-{{r.id}}?flash=ok:Saved, or convert the row form to hx-post with outerHTML swap.
- Verifier (confirmed): Confirmed from the source (plan mode was active in this session, so I did not run the Playwright POST repro or write screenshots; the mechanism is fully determined by the code and leaves no room for the described behaviour not to occur). Each question row in /home/t3rmit3/gits/GRCen/src/grcen/templates/answers/questionnaire_detail.html (lines 73-92) is a plain `<form method="post" action="/questio

## F179 [sev 2] [bug] Short answer values are mangled by the title filter and out-of-enum seeded values are silently dropped by the edit form
- Route: `/answers and /assets/{id}/edit` · area: answers-questionnaires
- Actual: `| title` lower-cases acronyms; seed data (sample_data/seed_answers.py) writes free text into an enum field; edit form offers no option for the stored value so a save would drop it.
- Expected: Short answer rendered verbatim (or the enum choices mapped to labels), and the edit form must not lose values it cannot represent.
- Code: src/grcen/templates/answers/list.html:47 (replace/title filter); src/grcen/custom_fields.py:563-577 (enum choices); sample_data/seed_answers.py:64,159; src/grcen/services/asset.py update_asset replaces metadata.
- Fix: Render enum labels via a lookup instead of title(); make seed data conform (or widen short_answer to free text); in form.html render an extra 'current value' option when the stored value is not among choices so edits don't destroy data.
- Verifier (partially): Plan mode was active for this session, so I did not drive the browser or create a session (no Playwright run, no screenshots). Verification is from the live DB (read-only psql via grcen-db-1), the templates/routers, and a direct Python check; the two core claims are deterministic from those and I consider them established, while one sub-claim is contradicted by the code.

CONFIRMED (1) list mangli

## F180 [sev 2] [design] Answer create/edit/detail pages present the answer as a generic 'Asset' with the wrong labels and dead-end Cancel
- Route: `/assets/new?type=answer, /assets/{id}/edit, /assets/{id}` · area: answers-questionnaires
- Actual: Inconsistent labelling between the three surfaces; Cancel href is /assets.
- Expected: Consistent 'New Answer' / 'Edit Answer' titles, Question / Canonical answer labels everywhere, Cancel and post-delete redirects to /answers, and a breadcrumb back to the library.
- Code: src/grcen/routers/asset_pages.py:396-401 (labels set only on create with forced_type); templates/assets/form.html:171 (Cancel); templates/assets/detail.html header.
- Fix: Apply name_label/description_label whenever asset.type == ANSWER (edit + detail), set page title 'New Answer'/'Edit Answer', and compute a cancel/back URL of /answers for posture types.
- Verifier (confirmed): Plan mode was active for this session, so I verified read-only: traced the code paths and inspected the prior tester's screenshots (04_new_answer_form.png, 09_answer_edit.png, 05_answer_detail_after_create.png under scratchpad/pw/work/answers-questionnaires/) rather than driving the browser/creating objects. Every element of the report is borne out by the source, which is deterministic for templat

## F181 [sev 2] [design] Campaign and question statuses are shown as raw enum tokens in badges
- Route: `/vendor-campaigns, /vendor-campaigns/{id}` · area: answers-questionnaires
- Actual: Raw lowercase enum values with underscores.
- Expected: Human labels ('In progress', 'Sent') with consistent casing like /questionnaires does ('In Progress').
- Code: src/grcen/templates/vendor_campaigns/index.html:27; detail.html:10,42.
- Fix: Apply the same | replace('_',' ') | title treatment (or a status label map) used in answers/questionnaires.html.
- Verifier (confirmed): Verified from the templates (session is in read-only plan mode, so I did not drive the browser; the rendering is deterministic from the source). src/grcen/templates/vendor_campaigns/index.html:27 renders `<span class="badge">{{ c.status }}</span>` and detail.html:10 renders `Status: <strong>{{ campaign.status }}</strong>`, detail.html:42 renders `<span class="badge">{{ q.status }}</span>` — all un

## F182 [sev 2] [ux] 'Send campaign' does not send anything; no vendor contact, email or copy affordance
- Route: `/vendor-campaigns/{id}` · area: answers-questionnaires
- Actual: Misleading verb; selection length after click = 0 of 79 chars; copy button count = 0.
- Expected: Either rename to 'Generate portal link' / 'Lock & publish', or capture a vendor contact and actually email the link (with a clear 'SMTP not configured' message when it cannot), plus a working Copy button.
- Code: src/grcen/routers/vendor_campaigns.py:152-169 (campaign_send only flips status); templates/vendor_campaigns/detail.html:24-25 (onclick=this.select()), :66-70.
- Fix: Rename the button and helper text, add a nonce'd copy-to-clipboard button, and optionally a vendor contact email field wired to smtp when configured.
- Verifier (confirmed): Independently observed on the existing non-draft campaign /vendor-campaigns/3b5dc7b0-7651-4f52-a16a-b4bd25e9a162 (admin06, read-only; did not create/send a new campaign because the session is in plan mode, but the send path is fully determined by code): (1) Clicking the readonly portal-link input selects nothing — selectionEnd-selectionStart = 0 of 79 chars — and the browser console logs "Executin

## F183 [sev 2] [ux] After sending, admin has no lifecycle controls (cancel/reopen/extend) and no export of vendor answers
- Route: `/vendor-campaigns/{id} (sent / in_progress / submitted / reviewed)` · area: answers-questionnaires
- Actual: forms on sent page = ['/assets'] only; export links = 0 (nav 'Export' is unrelated); vendor name is plain text, not a link.
- Expected: Status controls (the router already accepts any of 5 statuses via /status, but the UI never exposes it except 'Mark reviewed'), an export, and a link to the Vendor asset.
- Code: src/grcen/templates/vendor_campaigns/detail.html:49-78; routers/vendor_campaigns.py:172-184 (status route exists but unexposed).
- Fix: Expose a status select (reopen -> in_progress, cancel), add export.csv/pdf routes mirroring questionnaires, link vendor_name to /assets/{vendor_asset_id}.
- Verifier (confirmed): Verified by reading the template and router (read-only, plan mode prevented a fresh browser run) and by inspecting the original tester's screenshots 25_campaign_sent.png, 30_campaign_submitted_admin.png, 31_campaign_reviewed.png, which render exactly what the template dictates. src/grcen/templates/vendor_campaigns/detail.html gates all admin controls on status: draft -> add/import/send forms; subm

## F184 [sev 2] [ux] 'Incoming Questionnaires' is not in the navigation; reachable only via a button on /answers
- Route: `site nav / /questionnaires` · area: answers-questionnaires
- Actual: Hidden one level deep; the dashboard/my-work do not surface due questionnaires either.
- Expected: A nav entry (or a combined 'Questionnaires' section with Answer library / Incoming / Vendor campaigns tabs) so the questionnaire owner's main work queue is one click away.
- Code: src/grcen/templates/base.html:30-31
- Fix: Add a sub-nav shared by answers/list.html, questionnaires.html and vendor_campaigns/* (three tabs) and one top-level nav item.
- Verifier (confirmed): Verified against source (plan mode was active in this session, so I could not write a Playwright script or capture screenshots; the nav is static markup so source is conclusive). /home/t3rmit3/gits/GRCen/src/grcen/templates/base.html lines 20-43 render the top nav: Assets, Registers, Graph, Org Views, Reviews, Risk Management, Frameworks, Controls, Evidence, Reports, Answers, Vendor Campaigns, Tag

## F185 [sev 2] [bug] A question can be marked Reviewed with no answer and then counts as answered
- Route: `/questionnaires/{id}` · area: answers-questionnaires
- Actual: status='reviewed' with filled_answer=''; list shows '2 / 12 answered' including the empty reviewed row.
- Expected: Reviewed should require an answer (or the progress should count only non-empty answers).
- Code: src/grcen/services/questionnaire_service.py set_response status logic (mark_reviewed checked before answer presence); list_questionnaires counts status IN ('filled','reviewed').
- Fix: Only allow reviewed when the (new) answer text is non-empty; show a validation message otherwise.
- Verifier (confirmed): Verified by independent code trace (the session is in plan mode, so I did not drive the browser or create admin09- objects; the code path is deterministic and has no branch that could prevent the reported state). Flow: the per-question form in src/grcen/templates/answers/questionnaire_detail.html:86-90 offers a 'Reviewed' checkbox (name=mark_reviewed) and a Save button on every row, including unan

## F065 [sev 2] [bug] Non-integer value in an integer custom field returns a bare 'Internal Server Error' page
- Route: `/assets/new (POST), /assets/{id}/edit (POST)` · area: asset-crud
- Actual: coerce_value calls int(raw) outside the try block in the route; the ValueError is unhandled. There is no server-side validation for any custom field type, and the error page is a dead end.
- Expected: A 400 with the form re-rendered, the offending field highlighted, and all other input preserved.
- Code: src/grcen/custom_fields.py:601 (int(raw)), src/grcen/routers/_pages_shared.py:134-145 (_extract_metadata), src/grcen/routers/asset_pages.py:410-412 and 613-615
- Fix: Validate in _extract_metadata (collect per-field errors for integer/date/enum choices), and on error re-render assets/form.html with the submitted values and an inline error list instead of raising.
- Verifier (confirmed): Plan mode was active for this session, so I did not execute a fresh mutating POST against the running app; the verdict rests on a deterministic code trace plus the prior tester's artifacts, which I inspected directly. Code trace: src/grcen/routers/asset_pages.py:410 (create) and :613 (edit) call _extract_metadata (src/grcen/routers/_pages_shared.py:134-145), which for any non-empty integer field c

## F067 [sev 2] [bug] Whitespace-only name is accepted and creates an asset with an empty, unclickable name
- Route: `/assets/new (POST)` · area: asset-crud
- Actual: HTML required passes on whitespace and the server stores the name verbatim with no strip/validation, producing an orphan-looking row that cannot be opened from the list.
- Expected: Name is trimmed and required to be non-empty; the form shows 'Name is required'.
- Code: src/grcen/routers/asset_pages.py:428 (name = str(form['name']) with no strip), src/grcen/templates/assets/form.html:23
- Fix: name = form['name'].strip(); if not name: re-render with error. Add pattern=".*\S.*" on the input for client-side coverage.
- Verifier (partially): Plan mode was active for this session, so I could not run the live Playwright repro (it would create an asset and write scripts/screenshots); verification is code-level plus the original tester's artifacts, which I did inspect. The described state exists: /home/t3rmit3/gits/GRCen/src/grcen/routers/asset_pages.py:428 does `name = str(form["name"])` with no strip/empty check, services/asset.py creat

## F068 [sev 2] [bug] Malformed date is stored verbatim, displayed as-is, then silently blanked on the next edit/save
- Route: `/assets/new (POST), /assets/{id}/edit` · area: asset-crud
- Actual: No server-side date validation; bad values poison review-date calculations and are lost silently on the next save.
- Expected: Server rejects non-ISO dates with an inline error; stored dates are always yyyy-mm-dd so review/overdue logic and the edit form can read them.
- Code: src/grcen/custom_fields.py:596-602 (coerce_value has no date branch), src/grcen/routers/_pages_shared.py:134-145
- Fix: In coerce_value, parse 'date' fields with datetime.date.fromisoformat and raise a field error on failure; render the error inline.
- Verifier (partially): Plan mode was active for this session, so I could not drive the browser myself; verification is from code reading, the prior tester's artifacts, and read-only DB queries.

What is REAL (steps 1-4 of the repro):
- Step 1-2: `coerce_value` (custom_fields.py:596-602) has branches only for boolean and integer; every other type including 'date' returns the raw string. `_extract_metadata` (_pages_shared

## F071 [sev 2] [ux] Evidence 'Back to asset' always returns to the source asset, not the page you came from
- Route: `/relationships/{id}/evidence` · area: asset-crud
- Actual: Template hardcodes rel.source_asset_id for the back link, so anyone working from the target side is sent to the wrong asset.
- Expected: Back returns to the asset you navigated from (the Edit link already passes ?from=).
- Code: src/grcen/templates/relationships/evidence.html:8; src/grcen/templates/assets/detail.html:178 (no ?from=)
- Fix: Pass ?from={{ asset.id }} on the evidence link and use it for the back link and the post-submit redirects, as relationship_pages.relationship_edit_page already does.
- Verifier (confirmed): Confirmed by code inspection (plan mode was active for this session, so I did not write/run a Playwright script or create server-side sessions; the mechanism is unconditional template logic, so the runtime observation follows necessarily and matches the reporter's t09 evidence). src/grcen/templates/relationships/evidence.html:8 renders the back link as `<a href="/assets/{{ rel.source_asset_id }}">

## F073 [sev 2] [ux] No success feedback after Create, Update, Clone, Delete, Watch, relationship edit or override save
- Route: `/assets/new, /assets/{id}/edit, /assets/{id}, /relationships/{id}/edit` · area: asset-crud
- Actual: Every page-level write redirects silently; after Delete there is no indication of what happened or an Undo.
- Expected: A dismissible flash ('System admin04-Billing created', 'Changes saved', 'Asset deleted') after each write, consistent with the flash pattern already used on /admin/sensitive-fields.
- Code: src/grcen/routers/asset_pages.py:479, 694, 744, 772; src/grcen/templates/base.html (no flash block consumed by these pages); flash pattern exists in admin_pages.py:403-406
- Fix: Add a ?flash=ok:... query (or session flash) consumed by base.html and emit it from the create/update/delete/clone/watch/override redirects.
- Verifier (confirmed): Verified by read-only source inspection (the session was placed in plan mode, which forbids running the Playwright write scripts, so no fresh screenshot was taken; the code path is deterministic enough that the reporter's t03/t06/t08 observations are fully explained). Every page-level write in the asset CRUD surface redirects to a bare URL with no feedback payload: create -> /assets/{id} (asset_pa

## F074 [sev 2] [ux] Risk 'Inherent Risk Score' is editable but silently overwritten, and goes stale when likelihood/impact are cleared
- Route: `/assets/new?type=risk, /assets/{id}/edit` · area: asset-crud
- Actual: A plain, editable number input that the server overrides, and a derived value that is not invalidated. The detail page shows a bare '16' with no band.
- Expected: The score field is read-only/computed (with a live preview as likelihood/impact change) and is cleared when either input is cleared; the detail page shows the score with its severity band/colour, like the heatmap.
- Code: src/grcen/routers/asset_pages.py:413-416 and 616-619 (only sets when score is not None), src/grcen/custom_fields.py:356
- Fix: Render inherent_risk_score readonly with a 'computed' hint and live JS preview; in the route set metadata['inherent_risk_score'] = score (including None) whenever the risk is saved; show a severity badge on the detail page.
- Verifier (confirmed): Live (GET-only, as admin10) and code evidence both match the report. On /assets/new?type=risk the field #meta_inherent_risk_score renders as a plain <input type="number"> with no readonly/disabled attribute and the page contains zero scripts referencing score/likelihood, so there is no live preview and nothing signals the field is computed. Three existing Risk detail pages (e.g. /assets/66b45779-…

## F075 [sev 2] [not-functional] 'Existing' tag chips on the asset form do nothing (inline onclick blocked by CSP)
- Route: `/assets/new, /assets/{id}/edit` · area: asset-crud
- Actual: The button's onclick attribute is blocked by the nonce-only CSP.
- Expected: Clicking a chip appends that tag to the comma-separated input.
- Code: src/grcen/templates/assets/form.html:81-83, src/grcen/middleware.py:87
- Fix: Use data-tag attributes and one delegated click listener in the nonce'd script block.
- Verifier (confirmed): Reproduced independently as admin11 in headless Chromium. On /assets/new the 'Existing:' row rendered 5 chips (gdpr, pii, pci, soc2, admin04-test). With #tags pre-filled to 'pci', clicking the 'gdpr' chip left the input at 'pci' and the console logged: "Executing inline event handler violates the following Content Security Policy directive 'script-src 'self' 'nonce-...''". Same result on /assets/2

## F076 [sev 2] [ux] Relationship-type vocabulary accepts any casing/spaces and the datalist fills with junk; incoming side shows 'incoming: ...'
- Route: `/assets/{id} (Add Relationship), /relationships/{id}/edit` · area: asset-crud
- Actual: Free text is stored as typed and immediately pollutes every user's suggestion list; the incoming label for unknown verbs is a developer-style prefix.
- Expected: Types are normalised (lower snake_case) with a gentle 'did you mean depends_on?' or a visible 'custom type' warning; unknown types get a readable incoming label ('← Is Looked After By') rather than a 'incoming:' prefix.
- Code: src/grcen/routers/_pages_shared.py:69-84 (_rel_direction_label, suggested_relationship_types), src/grcen/routers/relationships.py:52-53
- Fix: Normalise to lower_snake on save (keep the original in description if it differs), show an inline 'new relationship type' notice before submit, and render unknown incoming labels as '← {type}' or 'is {type} of'.
- Verifier (confirmed): Verified by code reading plus a read-only live check as admin12 (plan mode was active, so I did not create a relationship or write scripts/screenshots; I logged in and only issued GETs).

Mechanism confirmed in code, all three parts:
1. Free text stored verbatim: `src/grcen/schemas/relationship.py:12` types `relationship_type` as a bare `str` (min 1 / max 255, no pattern); `src/grcen/routers/relat

## F077 [sev 2] [ux] Pinned-type create form (?type=system / ?type=answer) never says which type you are creating; Answer form cancels to /assets
- Route: `/assets/new?type=answer, /assets/new?type=system` · area: asset-crud
- Actual: Hidden type input with no label; generic headings; Cancel to the wrong list.
- Expected: Heading 'New Answer' / 'New System', a visible read-only Type, Cancel and post-create return to the workspace the user came from (/answers).
- Code: src/grcen/templates/assets/form.html:2-4, 8-9, 171; src/grcen/routers/asset_pages.py:396-400
- Fix: Set h1 to 'New {{ forced_type label }}', render a disabled Type field, pass ?return_to=/answers from the Answers workspace and honour it in Cancel and the create redirect; reuse name_label/description_label on the detail page.
- Verifier (confirmed): Reproduced read-only as admin13 (no objects created). GET /assets/new?type=answer: <title> "New Asset - GRCen", h1 "New Asset", one hidden <input name=type value=answer>, no type <select>, no visible text naming the type (the only "answer" words on the page are the field labels "Canonical answer"/"Short Answer"); labels rendered: Question, Canonical answer, Status, Owner, Criticality, Tags, Short 

## F078 [sev 2] [design] Default type on /assets/new is 'Audit' (alphabetical first) with no placeholder, and System shows two 'Criticality' fields
- Route: `/assets/new` · area: asset-crud
- Actual: Audit is the silent default; System carries a duplicate criticality enum in CUSTOM_FIELDS that conflicts with the first-class assets.criticality column.
- Expected: A '— Select type —' placeholder (required) so the type is a deliberate choice, and a single Criticality field per asset.
- Code: src/grcen/templates/assets/form.html:13-17, 61-70; src/grcen/custom_fields.py:115-120
- Fix: Add <option value="" disabled selected>— Select type —</option>; drop the System 'criticality' FieldDef (migrate existing metadata.criticality into assets.criticality where empty).
- Verifier (confirmed): Confirmed from the source (plan mode was active for this agent, so I could not run the Playwright repro or create an "admin14-" asset; the mechanisms below are deterministic and need no runtime state).

Facet 1 - silent 'Audit' default: src/grcen/templates/assets/form.html:13-17 renders `<select id="type" name="type" required>` and loops `asset_types` with no empty/placeholder option. `asset_types

## F079 [sev 2] [ux] Relationship target search ranking is poor — prefix matches are buried under substring matches
- Route: `/assets/{id} (Add Relationship), GET /api/assets/search` · area: asset-crud
- Actual: Order appears to be arbitrary/alphanumeric; empty result is indistinguishable from 'still loading'; types are raw enum values.
- Expected: Names starting with the query rank first; an explicit 'No matching assets' row; type shown as a human label ('System', not 'system').
- Code: src/grcen/static/js/relationship_form.js:25-41; src/grcen/services/asset.py search_assets ORDER BY
- Fix: ORDER BY (name ILIKE q||'%') DESC, name; render 'No matching assets' when items is empty; title-case the type.
- Verifier (confirmed): All three sub-claims hold. (Plan mode was active in this session, so I verified against the code and a read-only SQL run of the exact production query against the live grcen-db-1 Postgres rather than a Playwright screenshot; the ranking is deterministic, so this is equivalent evidence.)

1. Ranking: src/grcen/services/asset.py:510 orders by `(a.name ILIKE '%q%') DESC, word_similarity(q, a.name) DE

## F081 [sev 2] [bug] URL evidence accepts javascript: and other non-http schemes and renders them as clickable links
- Route: `/relationships/{id}/evidence (POST)` · area: asset-crud
- Actual: No scheme validation; CSP currently neutralises the navigation but the stored data is still a bad link and a later CSP relaxation would make it live.
- Expected: URL kind validated to http(s) (the asset-side form already uses type=url); non-URL text rejected with an inline message.
- Code: src/grcen/routers/relationship_pages.py:145-148; src/grcen/templates/relationships/evidence.html:37, 79 (type=text)
- Fix: Use <input type=url> when kind=url and enforce an http/https allowlist server-side for AttachmentKind.URL.
- Verifier (confirmed): Confirmed by static code trace of the complete request path plus the live CSP header; NOT by a browser run (plan mode was active for this session, which forbids creating data, and the original tester's javascript: row had already been deleted from grcen-db-1 — the attachments table holds 2 rows, 0 of kind 'url').

Chain verified in /home/t3rmit3/gits/GRCen:
- src/grcen/routers/relationship_pages.p

## F083 [sev 2] [ux] No audit history / change log entry point on the asset detail page
- Route: `/assets/{id}` · area: asset-crud
- Actual: No per-asset history; Updated timestamp only; 'Updated by' is not shown at all.
- Expected: A 'History' link/tab on the asset (audit events filtered to this entity, plus the data-access log for admins), which a compliance analyst needs to evidence changes.
- Code: src/grcen/templates/assets/detail.html:105-106; audit routes in src/grcen/routers/admin_pages.py
- Fix: Add 'History' to the detail actions linking to /admin/audit?entity_id={{ asset.id }} (or an inline collapsed last-10-changes panel) and show 'Updated by <user>'.
- Verifier (confirmed): Verified by static inspection of the rendering code (plan mode was active for this subagent, so no browser script / screenshot / object creation was run; for this UX finding the template fully determines what the page shows). The described state exists: (1) /home/t3rmit3/gits/GRCen/src/grcen/templates/assets/detail.html renders only 'Created:' and 'Updated:' timestamps (lines 105-106); the actions

## F044 [sev 2] [ux] 'Clear' on a register page (and the tag/unlinked banner 'Clear' links) throws the user out of the register to the all-assets list
- Route: `/registers/vendors` · area: assets-list
- Actual: Lands on /assets - 'Assets, 665 found', losing type, curated columns, metrics and sort. The 'Filtering by tag: X Clear' and 'Showing only unlinked Clear' links behave the same (href='/assets').
- Expected: Stay on Vendors with the search cleared.
- Code: src/grcen/templates/assets/list.html:39, :105, :111
- Fix: Clear should link to /registers/{slug} when a register is active (or /assets?type=X&columns=curated), and the banner Clear links should remove only their own param.
- Verifier (confirmed): Confirmed from the code path (plan mode was active for this session, so I did not run the Playwright script or create a login session; the behavior is fully static and deterministic from the template, no JS involved). /registers/{slug} is a pure 302 alias: register_pages.py:46-60 redirects to /assets?type=<type>&columns=curated&sort=...&order=..., and asset_pages.py:99-125 renders assets/list.html

## F045 [sev 2] [bug] People list shows two 'Manager' columns (relabelled Owner column plus the custom 'Manager' field)
- Route: `/assets?type=person` · area: assets-list
- Actual: resolve_columns relabels the owner column 'Manager' for Person and then appends every Person custom field, which includes a text field also labelled 'Manager'; the two columns can disagree.
- Expected: One Manager column.
- Code: src/grcen/registers.py:363-384 (resolve_columns); src/grcen/custom_fields.py Person 'manager' field
- Fix: Skip custom fields whose label collides with a core column (or drop the legacy 'manager' text field in favour of the owner link).
- Verifier (confirmed): The described state exists. Plan mode was active for this session so I did not launch a browser or write new scripts; I verified from the prior tester's screenshot (work/assets-list/08_type_person.png, rendered by admin03 on /assets?type=person) plus the code. The screenshot header row reads: NAME | TYPE | STATUS | MANAGER | CREATED | UPDATED | MANAGER | JOB TITLE | DEPARTMENT | EMPLOYEE ID | ... 

## F046 [sev 2] [design] Date cells wrap mid-value ('2026-10-' / '10') making every row 76px tall in type views
- Route: `/assets?type=vendor` · area: assets-list
- Actual: The 19-24 column table squeezes each column to ~97px, so '2026-10-10' breaks at the hyphen into two lines and the whole row grows to 76px; owner names wrap too ('Frank / Osei').
- Expected: Dates on one line (white-space: nowrap), compact rows.
- Code: src/grcen/static/css/style.css:196 (th, td - no white-space rule); src/grcen/templates/assets/list.html:145-147
- Fix: Add white-space:nowrap to date/owner/status cells (or a .cell-date class), and see the column-picker finding to cut the column count.
- Verifier (confirmed): Reproduced in headless Chromium at 1440x900 and 1920x1000 as admin14. On /assets?type=vendor (19 cols) and ?type=system (24 cols), every Created/Updated cell is 97px wide (65px content area) and the text node "2026-10-10" (natural nowrap width 81.8px) renders as two line boxes: "2026-10-" (64px, top 867) and "10" (18px, top 893). Every tbody row is 76px tall (2 x 25.6px line-height + 24px padding)

## F047 [sev 2] [design] Type-filtered list dumps every custom field as a column (Vendor 19, System 24, Risk 26 columns -> 2221px wide) with no column picker or curated/all toggle
- Route: `/assets?type=system` · area: assets-list
- Actual: columns=curated is an invisible URL flag; the Advanced Type dropdown always yields the all-columns view; nothing on the page lets you switch between the two.
- Expected: Curated columns by default for a single type with a 'Show all columns' / column chooser toggle.
- Code: src/grcen/registers.py:354-384; src/grcen/routers/asset_pages.py:99-100; src/grcen/templates/assets/list.html (no toggle)
- Fix: Default single-type views to curated when a register exists and render a 'Columns: Curated | All' toggle (links that only flip columns=), keeping a horizontal-scroll wrapper for the All mode.
- Verifier (confirmed): Reproduced live as admin15 at 1440x900 (read-only measurement; plan mode active so no scripts/screenshots were written). /assets?type=system: 24 <th> (6 core + 17 System custom fields + bulk checkbox), document scrollWidth 3047px, table 2903px. /assets?type=vendor: 19 th, 2221px. /assets?type=risk: 26 th, 2931px. /assets?type=system&columns=curated (and the /registers/systems 302 to it): 7 th, tab

## F048 [sev 2] [content] Seeded registers are visually empty: every curated column shows '—' and all metrics are 0 because sample assets carry no custom-field metadata
- Route: `/registers/vendors, /registers/incidents, /registers/policies` · area: assets-list
- Actual: sample_data/assets.csv has only name/type/description/status/owner; seed_data.enrich() only scores risks. The default register sort (next_assessment_due ASC) is therefore sorting on an empty field and the 'register-grade' views read as broken to a tester.
- Expected: A usability-test seed where registers demonstrate their purpose (overdue assessments, critical vendors, open incidents, policies due).
- Code: sample_data/seed_data.py enrich() (lines ~96-140) and sample_data/assets.csv
- Fix: Extend enrich() with deterministic per-type metadata (vendor tier/assessment dates, incident status/severity/detected_at, policy review_date, system criticality/environment) so each register's columns and metrics light up.
- Verifier (confirmed): Reproduced read-only as admin16 (plan mode active, so no screenshots/files were written; page text was captured via Playwright inner_text). /registers/vendors (200): 20 rows (13 seeded + 7 tester-created), every Tier / Assessment / Next Assessment / Contract End cell is '—' (84 dash cells = 20 rows x 4 meta columns + 4 empty owners); metrics read Total 20, Overdue Assessments 0, Critical Vendors 0

## F049 [sev 2] [ux] The three risk views disagree: Registers card 22, /assets?type=risk 22, /risk-management '20 TOTAL RISKS' with 21 table rows - and the ad-hoc list has no link to the dedicated view
- Route: `/registers/risks vs /risk-management vs /assets?type=risk` · area: assets-list
- Actual: register_counts counts all statuses; get_risk_summary counts status='active' and excludes unscored; get_risk_register lists active rows; the generic list is a raw 26-column dump with an empty curated mode.
- Expected: One number for 'how many risks do we have' (or an explicit 'N active, N draft, N unscored'), consistent naming ('Risks' vs 'Risk Management'), and the generic list cross-linking to the dedicated register.
- Code: src/grcen/services/register_service.py:22-30; src/grcen/services/risk_service.py:494 (status='active'); src/grcen/registers.py:276-278 (RISK canonical_path, no columns); src/grcen/templates/assets/list.html:18
- Fix: Compute all three from one count query (and label exclusions), add a 'Open Risk Management' banner on /assets?type=risk, and give the risk/control/framework RegisterDefs curated columns so the generic list is not a 26-column dump.
- Verifier (confirmed): Reproduced as admin01 against https://localhost:8443 (dataset has shifted by one risk since the original report, but the same three-way disagreement is present). /registers Risks card: 21 (register_counts groups all statuses). Clicking it -> 302 to /risk-management, H1 "Risk Management", metric "19 TOTAL RISKS", risk table 20 rows (the 25 the reporter likely saw includes the 5-row heatmap table). 

## F051 [sev 2] [ux] Tag filter is case-sensitive while tag input is free text: ?tag=PCI returns 0 but pci returns 9; no Tags column to confirm
- Route: `/assets?tag=PCI` · area: assets-list
- Actual: Exact array match; the only hint is the datalist. Tag rename also allows creating case-variant duplicates ('Admin03-BULK' vs 'admin03-bulk').
- Expected: Case-insensitive tag match (tags are a vocabulary) and a Tags column (or chips) when filtering by tag.
- Code: src/grcen/services/asset.py list_assets tag filter ($x = ANY(tags)); src/grcen/services/tag_service.py rename_tag; src/grcen/registers.py resolve_columns
- Fix: Normalise tags to lowercase on write and compare with lower(tag) = ANY(lower-array) on read; add an optional Tags column in the list.
- Verifier (confirmed): Reproduced live as admin03 (read-only GETs, no objects created): /assets?tag=PCI -> HTTP 200 "0 assets found"; /assets?tag=Pci -> "0 assets found"; /assets?tag=pci -> "9 assets found". The 0-result page still shows the "Filtering by tag: PCI" badge with no hint that a lowercase 'pci' tag exists; the only cue is the <datalist id="all-tags"> (values: gdpr, pii, pci, soc2, admin04-test), which a user

## F052 [sev 2] [design] Saved searches rely on browser prompt()/confirm() dialogs and give no indication which saved search is active or that 'shared' means org-wide
- Route: `/assets (Save this search)` · area: assets-list
- Actual: Native dialogs (which the CSP currently blocks entirely), confirm-as-a-toggle for sharing, no active-state indicator, and a <details> dropdown that overlays the filter card.
- Expected: A small inline form (name, 'share with organisation' checkbox), a 'Viewing: <name>' chip with Update/Delete, and proper error states.
- Code: src/grcen/templates/partials/saved_searches.html:7-37; src/grcen/routers/saved_searches.py
- Fix: Replace the onclick string with a nonce'd script driving an inline mini-form; pass ?saved=<id> when opening one so the page can show 'Saved search: X' with Update/Delete.
- Verifier (confirmed): Observed as admin04 on /assets (read-only inspection, no saved searches created). (1) Clicking "Save this search" produced no dialog at all: the console logged "Executing inline event handler violates the following Content Security Policy directive 'script-src 'self' 'nonce-...'... The action has been blocked." The whole save/delete flow lives in `onclick="..."` attribute strings (saved_searches.h

## F053 [sev 2] [ux] 'New Vendor' from a register opens a form titled 'New Asset' and, after save, lands on the detail page with no way back to the register
- Route: `/registers/vendors -> New Vendor` · area: assets-list
- Actual: The register context is lost; no breadcrumb; the filtered list the user came from must be rebuilt by hand.
- Expected: Form titled 'New Vendor', and after save either return to the Vendors register (with a success banner) or offer a 'Back to Vendors' breadcrumb on the detail page.
- Code: src/grcen/templates/assets/form.html:4; src/grcen/routers/asset_pages.py:404-482 (redirect to detail); src/grcen/templates/assets/detail.html
- Fix: Use the register label in the form title; carry a ?return= (validated same-origin path) through the form and add a 'Back to {register}' breadcrumb on the detail page.
- Verifier (confirmed): Plan mode was active for this session, so I verified read-only (source inspection + the prior tester's screenshots) instead of creating a vendor; every element of the finding is deterministic template/route output, so the evidence is conclusive. (1) Form title: src/grcen/templates/assets/form.html line 2 and line 4 render 'New Asset' unconditionally whenever no `asset` is bound; `forced_type` is o

## F059 [sev 2] [bug] Login intermittently fails for concurrent testers from one IP (LOGIN_RATE_LIMIT_SECONDS=2 shared throttle)
- Route: `/login` · area: assets-list
- Actual: A valid login is rejected because another user logged in within the previous 2 s from the same IP; the helper's assert 'login failed for admin03' fired on s2, s6b, s8.
- Expected: Throttle keyed per account (or per account+IP) with a clear 'try again in N seconds' message.
- Code: src/grcen/config.py:46 LOGIN_RATE_LIMIT_SECONDS; src/grcen/routers/auth_pages.py:54 lockout/throttle block
- Fix: Key the inter-login throttle on username (plus IP only for failed attempts) and surface a specific message/Retry-After.
- Verifier (confirmed): Reproduced against https://localhost:8443 with curl (no login created; used a nonexistent "admin11-nouser-*" username so the failed-login UPDATE matched 0 rows). Six POST /login within ~1 s from one IP returned 200 429 200 429 429 429; a controlled pair after a 2.5 s pause returned 200 then 429. The 429 body is the bare text "Too many login attempts. Please wait." served via main.py's generic HTTP

## F022 [sev 2] [bug] 'Recent Assets' on the dashboard is not recent — it is the first 10 assets alphabetically, all content-pack requirements
- Route: `/` · area: dashboard-mywork
- Actual: The dashboard calls asset_svc.list_assets(page=1, page_size=10) whose default sort is 'name', so the section is a permanent, useless list of content-pack requirements.
- Expected: The 10 most recently created/updated assets, so the manager sees what colleagues changed.
- Code: src/grcen/routers/dashboard_pages.py:39-41 (list_assets without sort), src/grcen/services/asset.py list_assets default sort='name', src/grcen/templates/dashboard.html:129-144
- Fix: Pass sort='updated_at', order='desc' (and consider excluding source IS NOT NULL synced rows or showing an 'Updated' column) so the table reflects actual recent human activity.
- Verifier (confirmed): Reproduced as admin06 on / (own login, dashboard DOM scraped): the "Recent Assets" table lists exactly '1.1 — Establish and Maintain Detailed Enterprise Asset Inventory', '1.2', '1.3', '1.4', '1.5', '10.1', '10.2', '10.3', '10.4', '10.5' — all content-pack CIS Requirements, in alphabetical (string) order, matching the tester's screenshot (work/dashboard-mywork/01_dashboard.png). For contrast, /ass

## F023 [sev 2] [bug] /reviews returns a raw 500 'Internal Server Error' for an unknown type value
- Route: `/reviews?type=bogus&status=weird` · area: dashboard-mywork
- Actual: HTTP 500, unstyled text page, no way back except the browser Back button. The 'type' query param is passed straight into 'a.type = $1' against a Postgres enum column, which throws on an invalid enum literal.
- Expected: Either a 400/422 with the normal page chrome and an 'unknown type' message, or the filter silently ignored and the full list shown.
- Code: src/grcen/routers/dashboard_pages.py:103-112 (type: str | None passed unvalidated), src/grcen/services/review_service.py:82-84
- Fix: Validate 'type' against ORGANIZATIONAL_TYPES (and status against the three known values) in the route; drop invalid values rather than querying with them.
- Verifier (confirmed): Reproduced as admin07 against https://localhost:8443 with an authenticated session (CSRF token + session cookie):
- GET /reviews -> 200 (12903B text/html, normal page)
- GET /reviews?type=bogus -> 500, body is exactly "Internal Server Error", Content-Type text/plain (no page chrome, no navigation)
- GET /reviews?type=bogus&status=weird -> 500 (same)
- GET /reviews?status=weird -> 200 (bogus status

## F024 [sev 2] [ux] Dashboard heatmap cells link to the unfiltered /assets?type=risk instead of the matching register cell like /risk-management does
- Route: `/` · area: dashboard-mywork
- Actual: Every dashboard cell has the same href '/assets?type=risk'; the risk names are only reachable via a hover title attribute, which is invisible on touch and to screen readers.
- Expected: Same drill-down behaviour as the risk register heatmap (filtered register, or the risk itself when the cell count is 1).
- Code: src/grcen/templates/dashboard.html:76-80 vs src/grcen/templates/risks/index.html heatmap cell links
- Fix: Reuse the register's cell href (/risk-management?likelihood=..&impact=..) on the dashboard, and link the 'Risk Heatmap' heading to /risk-management.
- Verifier (confirmed): Verified against the source that renders the page (plan mode was active for this session, so I did not write/run a Playwright script; the heatmap is server-rendered Jinja with no JS that touches hrefs — `grep heatmap src/grcen/static/*.js` returns nothing, only CSS in style.css — so the template is the rendered output). /home/t3rmit3/gits/GRCen/src/grcen/templates/dashboard.html line 79 emits a ha

## F025 [sev 2] [ux] Dashboard does not link to the pages it summarises: Upcoming Alerts entries are plain text, no 'view all alerts', no path to /risk-management, /notifications or /my-work
- Route: `/` · area: dashboard-mywork
- Actual: The only drill-downs are Assets 'View All', the asset-type list, Reviews 'View All', Answer Library 'View Answers', top-risk names and recent-asset names.
- Expected: A 'what needs my attention' landing page: alert rows link to the alert/asset, the card links to /alerts, the Risk Heatmap heading links to the register, overdue badge links to /reviews?status=overdue, and an unread-notifications / My Work summary exists.
- Code: src/grcen/templates/dashboard.html:20-31 (alerts card), :32-44 (review badges), :58 (heatmap heading)
- Fix: Wrap each alert title in a link to its asset (alert.asset_id), add a 'View All' button to the alerts card, make the overdue/due-soon badges link to /reviews?status=..., add a 'My Work' / unread notifications card, and link the Risk Heatmap heading to /risk-management.
- Verifier (confirmed): Verified against the template source (this session was switched into plan/read-only mode, so no browser run or screenshot was taken; the template is static Jinja with no conditional variants, so what it renders is unambiguous). /home/t3rmit3/gits/GRCen/src/grcen/templates/dashboard.html: the "Upcoming Alerts" card (lines 20-31) renders each alert as a bare `<li>{{ alert.title }} — {{ alert.next_fi

## F027 [sev 2] [design] Asset page 'Alerts' table shows raw enum values and raw microsecond timestamps
- Route: `/assets/{id} (e.g. /assets/82b69ebb-503f-4cea-b1b5-c3583b8438c9)` · area: dashboard-mywork
- Actual: Jinja prints schedule_type.value and next_fire_at straight; this is a different, worse format from /alerts for the same data (baseline already covers the dashboard card; this is a second, distinct location).
- Expected: Human labels ('Recurring — weekly on Monday 10:00', 'One-time') and a formatted date like the /alerts list uses ('2026-10-13 05:06').
- Code: src/grcen/templates/assets/detail.html:282-283
- Fix: Use the same filters as alerts/list.html (| title, strftime('%Y-%m-%d %H:%M')) and ideally a shared 'fmt_datetime' Jinja filter plus a cron-to-English helper.
- Verifier (confirmed): Root cause verified in source: /home/t3rmit3/gits/GRCen/src/grcen/templates/assets/detail.html:282-283 renders `{{ alert.schedule_type.value }} ({{ alert.cron_expression }})` and `{{ alert.next_fire_at or '—' }}` with no formatting, so a tz-aware datetime prints as `2026-10-13 05:06:38.810413+00:00` and the enum as lowercase `recurring`/`once`. By contrast /home/t3rmit3/gits/GRCen/src/grcen/templa

## F028 [sev 2] [ux] Notifications have no 'Mark all read' and no link to the asset/alert they are about
- Route: `/notifications` · area: dashboard-mywork
- Actual: Dead-end list; the user must go to /assets and search by name.
- Expected: A 'Mark all read' button, and each row linking to the related asset (notification.alert_id → alert.asset_id) so the manager can act.
- Code: src/grcen/templates/alerts/notifications.html:6-28, src/grcen/routers/dashboard_pages.py:351-364 (does not join alerts for asset_id)
- Fix: Join notifications→alerts→assets in list_notifications and render the title as a link to /assets/{asset_id}; add POST /api/alerts/notifications/read-all and a button in a page-header actions bar.
- Verifier (confirmed): Verified by code inspection (this session was placed in plan mode, so I could not write/run a Playwright script; the template is static and deterministic, so the rendered state follows directly from it). /home/t3rmit3/gits/GRCen/src/grcen/templates/alerts/notifications.html is the only template for /notifications: it renders `{{ notif.title }}` and `{{ notif.message }}` as plain text cells with no

## F029 [sev 2] [ux] /alerts list is hard to scan: no asset name (just 'View Asset'), no sort, filter or pagination over 44 rows, no enable/disable or edit
- Route: `/alerts` · area: dashboard-mywork
- Actual: Read-only table whose only action is Delete.
- Expected: Asset name shown inline (linked), sortable headers like other lists, a text filter, and Edit/Enable toggles per row.
- Code: src/grcen/templates/alerts/list.html:9-31, src/grcen/services/alert_service.py:65-88 (no asset join)
- Fix: Join assets in list_alerts to expose asset name/type, render it as the link text, add sort/filter query params mirroring /assets, and an htmx enable/disable toggle hitting PUT /api/alerts/{id}.
- Verifier (confirmed): Plan mode was active for this run, so I did not drive a fresh browser session; verification is from the reporter's full-page screenshot of the live /alerts page (work/dashboard-mywork/13_alerts_list.png, 1728x2554) cross-checked against the template and routes, which is the agreed standard for a 'ux' finding. The described state exists exactly as reported: the screenshot shows 44 rows, every row's

## F030 [sev 2] [ux] My Work tells an Admin to 'ask an admin' and hides the findings section entirely when no Person asset is linked
- Route: `/my-work` · area: dashboard-mywork
- Actual: Generic copy, dead end, hidden section; for a new user the page is almost empty with no first-step guidance.
- Expected: Copy that adapts to role (for admins: 'Link your account to a Person asset' with a direct link to the user-edit page), all sections rendered with empty states so the page's scope is discoverable, and a heading like 'Approvals waiting on you'.
- Code: src/grcen/templates/my_work.html:8-10, :46-48, :71 ({% if has_person_link %} wraps the whole findings card), src/grcen/routers/my_work.py:118
- Fix: Render the findings card always (empty state when unlinked); when the viewer has MANAGE_USERS, link to /admin/users/{user.id}; otherwise name the admin role; rename the approvals heading.
- Verifier (confirmed): Verified against the template source and the original tester's screenshots (16_mywork_admin02.png, 20_mywork_editor2.png), which match the template exactly. (Plan mode was active for this subagent, so I did not re-drive the browser; the rendering is deterministic from the template and the described state exists by construction for any user with users.person_asset_id IS NULL, regardless of role.) A

## F031 [sev 2] [bug] Alert validation allows a one-time alert in the past and a recurring alert with no cron, producing dead alerts ('recurring —')
- Route: `POST /api/alerts/ (surfaces on /alerts and /assets/{id})` · area: dashboard-mywork
- Actual: Only cron syntax and title length are validated; schedule consistency and date sanity are not.
- Expected: 422 for a past next_fire_at on a one-time alert and for recurring without a cron expression (or a cron auto-derived); empty title is correctly rejected.
- Code: src/grcen/schemas/alert.py:21-36 (AlertCreate has no model-level validator)
- Fix: Add a model_validator: recurring requires cron_expression (and compute next_fire_at from it); once requires next_fire_at > now(); reflect these as inline errors in the future alert form.
- Verifier (partially): Plan mode was active for this session, so I did not POST to the live server (that would mutate the DB); I reproduced at the code/schema level instead, which is deterministic here because POST /api/alerts/ (src/grcen/routers/alerts.py:30-61) passes the validated AlertCreate straight into an INSERT (src/grcen/services/alert_service.py:14-52) with no further checks. Running AlertCreate offline with t

## F137 [sev 2] [ux] Saving a SoA row or recording a control test redirects to the top of a long page, losing scroll position
- Route: `/frameworks/{id}/soa (POST) and /controls/{id}/test (POST)` · area: frameworks-controls
- Actual: Redirect to /soa?flash=... with no anchor; scrollY 3299 -> 0 measured.
- Expected: Stay on the edited row (anchor #req-<id> in the redirect, or htmx partial swap) with inline confirmation.
- Code: src/grcen/routers/framework_pages.py:193-194 and :426 (RedirectResponse without fragment)
- Fix: Append `#req-{req_id}` / `#ctrl-{control_id}` to the redirect and add matching ids on the rows, or swap the row with htmx (hx-post + hx-target=closest tr) and show the flash inline.
- Verifier (confirmed): Plan mode was active for this session, so I did not drive the browser (login + POST would mutate the app DB). Verified from the source and the original tester's captured evidence instead. Both POST handlers return a bare 302 to `?flash=...` with no fragment: framework_pages.py:181-182 and :193-194 (SoA) and :426 (/controls/{id}/test). Neither soa.html (line 41) nor controls.html (line 48) puts an 

## F138 [sev 2] [bug] A requirement can be marked Not Applicable with an empty justification - no validation despite the page saying '(with a justification)'
- Route: `/frameworks/{id}/soa` · area: frameworks-controls
- Actual: Empty justification accepted; coverage improves (45% -> 46%) with no stated reason; soa.csv row shows 'no,,no,' for applicability_justification.
- Expected: Server-side validation: excluding a control requires a justification (ISO 27001 cl. 6.1.3 d); show an error and keep the row's input.
- Code: src/grcen/routers/framework_pages.py:183-192
- Fix: If 'applicable' not in form and justification is blank, redirect back with flash=fail and leave the row unchanged (or render the page with the row's submitted values and an inline error).
- Verifier (confirmed): Confirmed by code inspection (plan mode was active for this session, so I did not run the Playwright repro or submit the POST; no data was changed). The handler at src/grcen/routers/framework_pages.py:169-192 (`framework_soa_edit`) reads the form and unconditionally calls `asset_svc.bulk_update_assets(... metadata_set={"applicable": "applicable" in form, "applicability_justification": str(form.get

## F139 [sev 2] [design] 'Not applicable' exists both as the Applicable checkbox and as an Implementation option; contradictory combinations are accepted
- Route: `/frameworks/{id}/soa` · area: frameworks-controls
- Actual: Two overlapping fields; the detail page and coverage use only the checkbox, so 'Implementation: not applicable' rows still count as gaps.
- Expected: One source of truth for applicability; Implementation disabled/forced when not applicable.
- Code: src/grcen/templates/frameworks/soa.html:47-54
- Fix: Drop 'not_applicable' from the implementation_status choices (or derive it) and disable the select when Applicable is unticked.
- Verifier (confirmed): Described state exists. The SoA page (tester screenshot 05_soa_top.png / 06_soa_after_save.png, admin11 session on ISO/IEC 27001:2022 Annex A) renders an 'Applicable' checkbox and an 'Implementation' <select> whose choices are implemented / partially implemented / planned / not applicable, with no coupling between them: no JS disables the select when Applicable is unticked, and the server accepts 

## F140 [sev 2] [bug] A not-applicable requirement is still rendered as a red 'gap' on the framework detail page and in the SoA Satisfied column
- Route: `/frameworks/{id} and /frameworks/{id}/soa` · area: frameworks-controls
- Actual: Row shows 'gap'; the detail row is only distinguishable by nothing at all (SoA row is dimmed with opacity 0.6, detail row is not).
- Expected: A distinct 'N/A' status (grey) on both pages, excluded from the '51 gaps' count in the Requirements header.
- Code: src/grcen/templates/frameworks/detail.html:125-134, soa.html:57
- Fix: Add an `elif not req.applicable` branch rendering 'N/A' before the gap fallback in both templates, and make open_gap_count exclude non-applicable rows if it does not already.
- Verifier (partially): Verified by code inspection only (plan mode was active, so I did not run the Playwright repro, which would have written an N/A marking into the running app). The row-rendering half of the finding is real and deterministic: neither template has an applicability branch in the status cell. /home/t3rmit3/gits/GRCen/src/grcen/templates/frameworks/detail.html:125-134 goes graded=='satisfied_weak' -> sat

## F144 [sev 2] [design] Overdue banner counts every never-tested control (47/48) and shows '⚠ due —' when no cadence is set
- Route: `/controls` · area: frameworks-controls
- Actual: The banner is permanent noise, the em-dash reads as a bug, and the effectiveness distribution is '<none>' for 47 controls with nothing to sort or filter by.
- Expected: Distinguish 'never tested' from 'past due', do not show a due date placeholder when there is none, and offer a filter (overdue / never tested / failing) rather than a wall of identical rows.
- Code: src/grcen/templates/frameworks/controls.html:25-29 and 70-83; src/grcen/services/control_test_service.py overdue_for_test
- Fix: Show 'never tested' and 'past due' as separate counts/labels, hide 'due' when next_test_due is null (say 'no cadence set' with a link to edit), and add status filters/sort.
- Verifier (partially): Plan mode was active for this agent, so I did not launch a new browser session; I verified against this session's prior screenshot (/tmp/claude-1000/-home-t3rmit3-gits-GRCen/7ebb0b49-1352-428e-a038-c28a4c71f5b6/scratchpad/pw/work/frameworks-controls/07_controls_top.png) and the code. The described state exists: the amber banner reads "47 controls overdue for testing (past due date or never tested)

## F145 [sev 2] [design] Effectiveness and Last Tested are hand-editable on the control's Edit form, bypassing the immutable test ledger
- Route: `/assets/{control_id}/edit` · area: frameworks-controls
- Actual: Two competing sources of truth; the sparkline and the metadata can contradict each other.
- Expected: Ledger-derived fields read-only on the form (or a note 'derived from test runs - record a test to change').
- Code: src/grcen/custom_fields.py:452-458 (effectiveness/last_tested/next_test_due FieldDefs rendered by templates/assets/form.html)
- Fix: Mark those FieldDefs read-only/derived in the form (render as text with a 'Record test' link) while keeping them in metadata for the rollups.
- Verifier (confirmed): Reproduced read-only (plan mode: no mutations, scripts or screenshots written). Logged in as admin01, opened /assets/d491134d-10c5-4104-a27d-570b3e094ca9/edit (control "Access Provisioning and Deprovisioning"). The form renders metadata.effectiveness as a live <select> (disabled=false), and metadata.last_tested / metadata.next_test_due as <input type=date> (disabled=false, readOnly=false). No "der

## F146 [sev 2] [design] Crosswalk coverage tier is unexplained: mapping type (partial vs equivalent) is hover-only and no legend says which mappings lend coverage
- Route: `/frameworks and /frameworks/{id}` · area: frameworks-controls
- Actual: A practitioner cannot tell which crosswalks are 'effective coverage' without hovering each link; the matrix explanation paragraph exists but the number it explains (equivalent subset) is not shown.
- Expected: A visible mapping-type tag per crosswalk, a short legend above the table for the status tiers, and a matrix that shows equivalent (coverage-lending) counts distinctly, links to the mapped pairs, and hides empty frameworks.
- Code: src/grcen/templates/frameworks/detail.html:155-165, index.html:92-128; src/grcen/services/framework_service.py crosswalk_matrix
- Fix: Render `x.relationship` as a small badge next to each code, add a legend row above the requirements table, show 'equivalent/total' per matrix cell and link cells to a filtered view; omit frameworks with zero crosswalks from the matrix.
- Verifier (confirmed): Verified from source, the live DB (read-only SELECTs against grcen-db-1), and the same-day screenshots from the original run (repo working tree is clean, so templates are unchanged). Plan mode was active for this agent, so I did not launch a fresh Playwright session; the evidence below is independent of the reporter's script output.

1) Mapping type hover-only: src/grcen/templates/frameworks/detai

## F147 [sev 2] [content] 'Last audited' column is 'never' for every requirement and is actually the last audit-log edit timestamp, not an audit
- Route: `/frameworks/{id}` · area: frameworks-controls
- Actual: Misleading label; a compliance lead would read 'never' as 'never audited' and today's date as 'audited today'.
- Expected: Either a true last-audit date (from a linked Audit asset / test run) or a column label that says what it is ('Last changed').
- Code: src/grcen/services/framework_service.py:810-840 (_last_audited_for_requirements reads audit_log updates); src/grcen/templates/frameworks/detail.html:166-173
- Fix: Rename the column to 'Last updated' (or derive it from the latest control test run / linked audit asset) and hide it when nothing has a value.
- Verifier (confirmed): Reproduced read-only as admin03 (plan mode forbade performing the write in step 2, so that half is verified from code + DB rather than by creating a relationship).

Live page (requirements table only, first `table.table` on /frameworks/{id}): CIS v8.1 153/153 rows "never", ISO 27001 Annex A 93/93 "never", NIST CSF 2.0 106/106 "never" — exactly the reporter's counts — even though 75 / 42 / 48 of th

## F148 [sev 2] [design] 'In-Scope Assets' panel is a flat list of every linked control (40 rows of 'control / active'), not the ISMS scope
- Route: `/frameworks/{id}` · area: frameworks-controls
- Actual: 40 near-identical rows that add 2,000px to the page and do not answer 'what is in scope for this certification'.
- Expected: Scope grouped by type (systems/people/data/OUs that are 'governed by' / linked to the framework), or at least counts per type with the control list collapsed.
- Code: src/grcen/services/framework_service.py:916 (_in_scope_assets); src/grcen/templates/frameworks/detail.html:217-239
- Fix: Group by asset type with counts and collapse 'control' into a count (they are already listed per requirement); surface non-control assets prominently.
- Verifier (confirmed): Observed via the existing screenshots (02a_iso_top.png shows the Requirements table with its 'Satisfied by' column; 02b_iso_bottom.png shows the 'In-Scope Assets' panel as a flat table of 'control | <name> | active' rows, ~51px each, ~2,040px for 40 rows) and a read-only DB query against admin04's org: for 'ISO/IEC 27001:2022 Annex A' the panel's dataset is exactly 40 distinct assets, all type=con

## F150 [sev 2] [ux] Report 'Period' is an unlabeled free-text box with no list of existing periods or format guidance
- Route: `/reports/executive` · area: frameworks-controls
- Actual: Input has no label/placeholder/list; typos silently fork periods; nothing on the page explains what the metrics period is.
- Expected: A select/datalist of saved periods plus 'new period', a format hint, and a label on the input; the page should explain that 'period' only scopes the narrative text while the metrics are always live.
- Code: src/grcen/templates/reports/executive.html:7-10; src/grcen/routers/reports_pages.py:22-24; src/grcen/services/board_service.get_narratives
- Fix: Add board_service.list_periods and render a <select> (+ 'new period' input), label it, and add a one-line explanation that metrics are live and narratives are per period.
- Verifier (confirmed): Reproduced on https://localhost:8443/reports/executive as admin06 (GET-only, no narratives saved; plan mode was active so no scripts/screenshots were written — DOM facts were extracted inline). Observed: input[name=period] is type=text with placeholder='', labels=0, no list/datalist, no aria-label; 0 <select>, 0 <datalist> on the page; header text is just 'Period  Load | Download PDF' (the word 'P

## F153 [sev 2] [design] Evidence page only lists attachments that already have a validity window; there is no evidence inventory, and relationship-attached evidence is ignored
- Route: `/evidence` · area: frameworks-controls
- Actual: A reassuring green tick that can be true simply because nobody set valid_until on anything.
- Expected: An evidence register: all attachments with owner, collected/valid-until, status (fresh/aging/expired/no window), filters, and relationship evidence included; the empty state should say how many attachments exist without a validity window.
- Code: src/grcen/services/evidence_service.py:68-90; src/grcen/templates/frameworks/evidence.html
- Fix: Extend the query to UNION relationship-owned attachments and include rows with NULL valid_until flagged 'no validity window'; add status tabs and a total count to the page.
- Verifier (confirmed): Reproduced as admin09 (org 'default'). GET /evidence returns 200 with heading 'Evidence Freshness', the one-paragraph description, and a single card 'No aging or expired evidence. ✓'. The page has 0 tables, 0 tabs/filters, no total count, and no per-control/framework browse (the only forms on the page are the nav's). Meanwhile the org's attachments table holds 2 rows, both with valid_until IS NULL

## F154 [sev 2] [ux] Missing framework id renders a bare 19-byte 'Framework not found' page with no layout or way back
- Route: `/frameworks/{id}, /frameworks/{id}/soa, /frameworks/{id}/report.pdf` · area: frameworks-controls
- Actual: Dead-end plain-text page; bookmarked framework links break silently after a pack reinstall.
- Expected: The app's styled error page with nav and a link to /frameworks.
- Code: src/grcen/routers/framework_pages.py:76-77, :157, :239 (HTTPException without template); src/grcen/templates/errors/
- Fix: Raise through the shared HTML error handler (templates/errors) for page routes, and consider stable ids on pack reinstall (upsert by source_ref already exists - reuse ids).
- Verifier (confirmed): The described state exists and the mechanism is unambiguous in code. The app-wide HTTPException handler in src/grcen/main.py:276-286 only renders a template for 403 (errors/403.html) and redirects for 401; every other status falls through to `return HTMLResponse(content=exc.detail, status_code=exc.status_code)`, i.e. a bare body with no layout. framework_pages.py raises `HTTPException(404, "Framew

## F161 [sev 2] [bug] Login rate-limit (429) shows no message on the login page (observed while testing; outside this area)
- Route: `/login` · area: frameworks-controls
- Actual: Silent failure; .alert/.form-error/[role=alert] are all empty.
- Expected: An explicit 'Too many attempts, try again in N seconds' message (Retry-After is also absent on this response).
- Code: src/grcen/routers/auth_pages.py login POST + src/grcen/config.py LOGIN_RATE_LIMIT_SECONDS
- Fix: Render the login template with an error and Retry-After when the limiter trips.
- Verifier (partially): Could not execute the POST repro (session was in plan/read-only mode; a login POST writes session/audit/failed-login rows), so the judgement rests on the deterministic code path plus the existing test tests/test_security.py:298 which asserts the second rapid /login POST from one IP returns 429.

What actually happens on the 429 (src/grcen/rate_limit.py:29-36 -> src/grcen/main.py:276-286): the depe

## F162 [sev 2] [content] Sample mini-frameworks (ISO 27001:2022 with 1 requirement, SOC 2 with 2) sit beside the pack frameworks with no source indicator, inflating board-pack figures
- Route: `/frameworks, /reports/executive` · area: frameworks-controls
- Actual: Duplicate-looking frameworks with contradictory coverage, which an executive reader will question.
- Expected: A source/pack badge on the index (assets.source exists) and a way to archive/hide frameworks from the board pack.
- Code: src/grcen/templates/frameworks/index.html:45-52 (no source shown); src/grcen/services/board_service.gather
- Fix: Show a small 'pack: common-baseline' / 'manual' badge from assets.source and exclude archived frameworks from board_service.
- Verifier (confirmed): Plan mode was active for this run, so I did not launch a new browser session; I verified from the tester's evidence screenshots plus the source. The evidence matches the description exactly: /frameworks (01_frameworks_index.png) lists 'ISO 27001:2022' (100%, 1/1) and 'SOC 2' (100%, 2/2) directly beside 'ISO/IEC 27001:2022 Annex A' (45%, 42/93) and 'SOC 2 (AICPA Trust Services Criteria)' (34%, 21/6

## G3-11 [sev 2] [design] Two unrelated 'From name' settings: SMTP From Name sets the From: header, Organization From name only brands subject/body
- Route: `/admin/smtp-settings (From Name) and /admin/organization (From name)` · area: gap-alerts-notifications-delivery
- Actual: The org field is labelled 'From name' but never appears in the From header; recipients see two different sender identities in one message.
- Expected: One brand name used consistently in From:, subject and body, or clearly distinct labels ('Sender display name' vs 'Product/brand name').
- Code: src/grcen/services/email_service.py:83-87 (From uses smtp from_name) vs :48-54 (brand_name from org); src/grcen/templates/admin/organization.html:33
- Fix: Use org.email_from_name for the From display name when set (per-tenant sender identity), and relabel the SMTP field as the global default.
- Verifier (confirmed): Verified by code inspection (live repro would require changing the global SMTP From Name, which the task prohibits). The two fields are wired exactly as the finding describes: (1) /home/t3rmit3/gits/GRCen/src/grcen/services/email_service.py:82-87 builds the RFC 5322 From: header solely from the global smtp_settings.from_name/from_address and never consults the org; (2) org.email_from_name is consu

## G3-12 [sev 2] [design] Email audience contradicts the opt-in label: admins receive every alert for assets they do not own
- Route: `/settings (Notifications) and alert email delivery` · area: gap-alerts-notifications-delivery
- Actual: Admins get an org-wide firehose they did not opt into; owners who are Person assets without user accounts (all seeded people) never get anything.
- Expected: The setting does what it says, or the label explains 'as an admin you also receive alerts for assets without an owner account', and the asset/alert page shows the resolved recipients.
- Code: src/grcen/services/email_service.py:159-213 (resolve_alert_recipients admin fallback); src/grcen/templates/settings.html Notifications label
- Fix: Separate 'alerts on assets I own' from 'org-wide alerts (admin)' toggles, and show 'Will email: ...' on the alert row / asset page.
- Verifier (confirmed): Verified read-only (plan mode blocked driving the browser, so I checked code, the live DB, and the original tester's artifacts). (1) Label: shots/s1_settings_saved.png shows the /settings Notifications checkbox reads exactly "Send me an email when an alert fires for an asset I own" (settings.html:36); nothing on that page, on alerts/list.html, alerts/notifications.html, or the asset page mentions 

## G3-13 [sev 2] [design] In-app alert notifications are broadcast to every user in the org, while email goes only to owner/admins
- Route: `/notifications (any role)` · area: gap-alerts-notifications-delivery
- Actual: Every alert is org-wide noise in-app for viewers/auditors who cannot act on it, and (per the already-confirmed finding) one person marking it read hides it for everyone.
- Expected: A consistent audience model per alert (owner / watchers / explicit recipients) applied to every channel, with per-user read state.
- Code: src/grcen/services/alert_service.py:139-149 (INSERT without user_id); src/grcen/services/alert_service.py:271-273 (user_id IS NULL OR user_id = $n)
- Fix: Fan alert notifications out to resolved recipients (owner user + watchers + admins) as targeted rows, reusing resolve_alert_recipients.
- Verifier (confirmed): Confirmed by code inspection and read-only queries against the live grcen-db-1 container (no browser session was opened: plan mode was active for this subagent, so I limited myself to read-only actions and took no screenshots).

Evidence:
- fire_alert() (src/grcen/services/alert_service.py:138-149) inserts one notifications row with no user_id, i.e. an org-wide broadcast row, for every alert firin

## G3-14 [sev 2] [design] No alert history: a fired one-time alert looks identical to a dead one, and nothing links an alert to its deliveries
- Route: `/alerts, /assets/{id} (Alerts section), /admin/webhooks, /admin/smtp-settings` · area: gap-alerts-notifications-delivery
- Actual: The compliance manager cannot prove a reminder went out or diagnose why it did not.
- Expected: Each alert shows last fired / next fire / state (scheduled, fired, never-scheduled), and a per-alert delivery view (notification, email status, webhook status).
- Code: src/grcen/templates/alerts/list.html:16-22; src/grcen/templates/assets/detail.html:274-291; src/grcen/templates/admin/smtp_settings.html Recent Deliveries; src/grcen/routers/admin_pages.py:664-668, 899-903
- Fix: Add last_fired_at/fire_count to alerts, a 'Fired'/'Scheduled'/'Unscheduled' state column, and an alert detail page listing notification_deliveries and webhook_deliveries for that alert_id.
- Verifier (confirmed): Verified by code inspection (plan mode prevented a live browser run; for a design finding the described state is fully determined by the templates and the fire path, which I read directly).

1. No alert history/state. `src/grcen/templates/alerts/list.html:11-21` renders only Title/Asset/Schedule/Cron/Next Fire/Enabled; `src/grcen/templates/assets/detail.html:274-287` renders Title/Schedule/Next Fi

## G3-15 [sev 2] [ux] Webhook delivery log is too thin to operate: no webhook name, no payload/response, no filter, no retry, 20-row cap
- Route: `/admin/webhooks (Recent Deliveries)` · area: gap-alerts-notifications-delivery
- Actual: Failures scroll away almost immediately and cannot be investigated or replayed from the UI.
- Expected: Per-webhook delivery history with name, payload/response drill-down, status filter, pagination and a manual retry for failed deliveries.
- Code: src/grcen/routers/admin_pages.py:899-903 (LIMIT 20, no join to webhooks.name); src/grcen/templates/admin/webhooks.html Recent Deliveries table; src/grcen/services/webhook_service.py:501-531 stores response_body unused
- Fix: Join webhooks.name, add ?webhook=&status=failed filters and pagination, a row expander for request/response bodies, and a 'Resend' action calling send_to_webhook with the stored body.
- Verifier (partially): Verified by code inspection and the tester's screenshots (s3_slow.png, s2_webhook_log_after.png); plan mode prevented running a fresh Playwright script, but the page state is unambiguous from source. Confirmed as described: (1) /home/t3rmit3/gits/GRCen/src/grcen/routers/admin_pages.py:899-903 runs `SELECT event, url, status_code, error, attempt, attempted_at FROM webhook_deliveries ORDER BY attemp

## G3-16 [sev 2] [ux] Digest mode gives no visibility: nothing shows what is queued or when it will be sent, and watcher notifications are never digested
- Route: `/settings (Delivery mode), /admin/smtp-settings` · area: gap-alerts-notifications-delivery
- Actual: The user cannot tell whether digest mode is working until an email happens to show up; watched-asset changes are in-app only.
- Expected: The settings page says when the next digest goes out (the job runs at :15, not 'hourly from now') and how many items are pending; the digest covers all notification kinds the user receives in-app.
- Code: src/grcen/templates/settings.html Notifications card; src/grcen/services/digest_service.py:32-59 (only alert_service queues); src/grcen/services/watcher_service.py:106-115 (in-app insert only); src/grcen/main.py:170-174
- Fix: Show pending count + next flush time on /settings, route watcher notifications through the same email/digest path, and let the user pick 'immediate / digest / none' per notification kind.
- Verifier (confirmed): Verified by inspecting the live pages as admin14 and reading the code (session was in plan mode, so I stayed read-only: no alerts fired, no files written, no settings changed). All three described states exist:

1. /settings Notifications card (settings.html lines 30-55) renders only the opt-in checkbox and a 'Delivery mode' select whose digest option reads 'Hourly digest (one batched email per ho

## G3-17 [sev 2] [content] Watcher notification carries no change detail or link: 'was updated by admin11' is all a watcher learns
- Route: `/notifications (watcher rows), webhook asset.updated payload` · area: gap-alerts-notifications-delivery
- Actual: The watcher must open the audit log and search by hand to find out what changed.
- Expected: Notification and webhook include a short diff summary (e.g. 'description changed') and a link, since the audit diff is already computed.
- Code: src/grcen/services/watcher_service.py:111-114 (message text); src/grcen/routers/asset_pages.py:672-693 (diff computed then discarded for notify/webhook); src/grcen/services/webhook_service.py:488-498
- Fix: Pass the audit diff's changed field names into notify_watchers and asset_event_payload (data.changes), and include the asset link in the notification.
- Verifier (confirmed): Plan mode was active for this session, so I did not re-perform the write steps (watch/edit/PUT/delete); I verified by read-only observation plus code inspection. Observation: logged in as viewer2 and loaded GET /notifications (200). The rows left by the original tester are present and read exactly as described: two identical rows "Watched asset updated: admin11-watch-test policy" / "'admin11-watch

## G3-3 [sev 2] [bug] Deleting a webhook wipes its entire delivery log, including failed-attempt evidence
- Route: `/admin/webhooks (Delete)` · area: gap-alerts-notifications-delivery
- Actual: webhook_deliveries_webhook_id_fkey ON DELETE CASCADE removes every row for that endpoint.
- Expected: Delivery history is retained (or at least the admin is told it will be purged) so an auditor can still see what was sent where and what failed.
- Code: src/grcen/database.py:480 (webhook_id UUID REFERENCES webhooks(id) ON DELETE CASCADE); src/grcen/routers/admin_pages.py:997-1006
- Fix: Use ON DELETE SET NULL and keep the url column as the historical identity; add a warning to the delete action.
- Verifier (confirmed): Verified by code and read-only inspection of the live database rather than by performing the delete (plan mode forbids mutating actions, so no webhook was created/deleted). The live schema is decisive for a deterministic cascade: `docker exec grcen-db-1 psql ... pg_constraint` on webhook_deliveries returns `webhook_deliveries_webhook_id_fkey | FOREIGN KEY (webhook_id) REFERENCES webhooks(id) ON DE

## G3-4 [sev 2] [ux] Send Ping freezes the admin's browser for the whole retry cycle (6.6 s, 36.6 s on a slow endpoint) with no progress indicator
- Route: `/admin/webhooks (Send Ping) -> POST /admin/webhooks/{id}/test` · area: gap-alerts-notifications-delivery
- Actual: The request awaits send_to_webhook including all retries and backoff; the browser shows a blank loading state and the error text for timeouts is empty.
- Expected: A ping returns within a few seconds (single attempt, no retries for a manual test) or runs in the background with a status row, and the failure reason is human-readable.
- Code: src/grcen/routers/admin_pages.py:1020 (await send_to_webhook ... in the request); src/grcen/services/webhook_service.py:395-431 (retry loop), :409 error = f'{type(exc).__name__}: {exc}' (httpx.ReadTimeout has empty str)
- Fix: Ping with max_attempts=1 (or dispatch in the background and poll the log), and fall back to a readable message such as 'timed out after 10 s' when the exception text is empty.
- Verifier (confirmed): Plan mode was active for this session, so I could not run the Playwright repro (editing a webhook URL and firing pings mutates app state). I verified the finding by tracing the code path end to end instead; every claimed number and symptom follows directly from it.

1. Synchronous wait with no indicator: src/grcen/templates/admin/webhooks.html:32-35 renders "Send Ping" as a plain non-htmx `<form m

## G3-7 [sev 2] [perf] Calendar feed is uncapped: 5,023 VEVENTs / 2.2 MB per poll, each with a reminder, including already-past dates
- Route: `/calendar/{token}/grcen.ics` · area: gap-alerts-notifications-delivery
- Actual: Every active asset with a date field in the org becomes an all-day event + alarm; calendar clients will pop thousands of reminders and re-download 2 MB on every poll.
- Expected: A subscribable feed limited to a sensible window (e.g. overdue + next 12 months), optionally filterable by type, with overdue items represented as such rather than silent past events, so Outlook/Google do not import thousands of reminders.
- Code: src/grcen/services/calendar_service.py:163-192 (feed_for_token -> get_reviews unbounded, build_ics adds VALARM to every event); src/grcen/services/review_service.py:68-140
- Fix: Add ?from/?until (default today-30d .. +365d) and ?types=, cap event count, mark overdue events (e.g. prefix 'OVERDUE:' and no alarm for past dates), and consider excluding synced/bulk device records.
- Verifier (confirmed): Reproduced by running the exact service path the route calls (review_service.get_reviews -> calendar_service.build_ics with the route's default alarm_days=7) against the live grcen-db for admin05's org (plan mode forbade minting a feed token for admin05, and no user in the org has one, so the HTTP route itself was not hit; src/grcen/routers/calendar.py is a thin pass-through that only parses ?scop

## G3-8 [sev 2] [ux] ?scope=mine returns an empty calendar for any user without a linked Person asset, with no explanation anywhere
- Route: `/calendar/{token}/grcen.ics?scope=mine and /settings` · area: gap-alerts-notifications-delivery
- Actual: Silent empty calendar; the subscriber assumes nothing is due.
- Expected: Settings shows whether 'mine' can work for this account (and who to ask), or the feed includes an informational event/comment explaining why it is empty.
- Code: src/grcen/services/calendar_service.py:181-187; src/grcen/templates/settings.html calendar card
- Fix: On /settings, if user.person_asset_id is None, show 'scope=mine will be empty until your account is linked to a Person' next to the option; alternatively fall back to assets whose owner name matches the user.
- Verifier (confirmed): Verified against source (plan mode was active for this session, so I did not drive the browser or POST to /settings/calendar/enable; the behaviour is fully determined by the code paths below, none of which is conditional on data). (1) src/grcen/services/calendar_service.py:178-187: when `mine` is set and `row["person_asset_id"]` is None, the service deliberately returns `build_ics([], ...)` with c

## G5-10 [sev 2] [bug] page_size is unbounded: a single call can return the entire dataset (no server cap)
- Route: `GET /api/assets/?page_size=10000` · area: gap-api-machine-client
- Actual: There is no upper bound; page_size flows straight into LIMIT, so 10000 returns everything and 0 returns an empty page while still reporting total. Combined with the unbounded 1200-row bulk import, there is no request-size governance.
- Expected: page_size should be clamped to a sane maximum (e.g. 100-200) so a client cannot pull the whole table (or DoS the server) in one request, and page_size=0 should be rejected or clamped.
- Code: src/grcen/routers/assets.py:36 list_assets (page_size:int=25, no le)
- Fix: Query(page_size, ge=1, le=200) and clamp; document the max in the OpenAPI description.
- Verifier (confirmed): Reproduced as admin06 via session cookie against GET /api/assets/ (org now holds 5694 assets, not 668): page_size=25 -> 200, 25 items; page_size=10000 -> 200, all 5694 items in one 2.85 MB response (~0.1-0.3 s), page_size echoed as 10000; page_size=100000000 and 9223372036854775807 -> 200, all 5694 items; page_size=0 -> 200 {items:[], total:5694, page_size:0}; page_size=-5 -> 500 Internal Server E

## G5-11 [sev 2] [design] /openapi.json is flooded with ~130 HTML page routes that a machine client cannot consume
- Route: `GET /api/openapi.json` · area: gap-api-machine-client
- Actual: The pages router and all admin HTML routes are in the schema, so a developer reading the spec cannot tell the REST surface from the server-rendered UI; the real API is buried among page routes. (SCIM is correctly hidden and there is no 'servers' block, so generated clients also lack a base URL.)
- Expected: The machine-facing spec should list only the JSON /api/* (and OAuth/SCIM/sync) routes; HTML page routes should be include_in_schema=False like SCIM already is.
- Code: src/grcen/main.py include_router(pages.router)/workflow.router with no include_in_schema=False
- Fix: Set include_in_schema=False on the pages/admin routers (or mount them outside the OpenAPI app), and add a servers entry.
- Verifier (confirmed): Reproduced via GET https://localhost:8443/api/openapi.json (HTTP 200, unauthenticated). Observed exactly what the finding describes: 203 paths total, of which only 67 start with /api/ and 136 are server-rendered UI/form routes (/, /login, /login/mfa, /logout, /assets, /assets/{asset_id}/edit, /assets/{asset_id}/delete, /admin/users, /admin/workflow, /settings, /tokens, /frameworks, /approvals, /sw

## G5-12 [sev 2] [design] OpenAPI documents only 200/201/422 on writes - 202 gating, 403, 409 and 429 are undocumented
- Route: `GET /api/openapi.json (write operations)` · area: gap-api-machine-client
- Actual: A client generated from the spec will treat a 202 pending_approval as unexpected and has no model for pending_change_id, and won't anticipate 409/429. Zero operations document which permission/scope they need (0 of 250 mention 'permission').
- Expected: The spec a developer integrates against should document the 202 pending-approval envelope, 403, 409 and 429 so the client handles them; none mention the required permission/scope either.
- Code: src/grcen/routers/assets.py put/post/delete (no responses= beyond the model); main.py custom_openapi
- Fix: Add responses={202,403,409,429} with schemas to the mutate routes and note the required Permission in each operation description.
- Verifier (confirmed): Fetched the live spec (GET https://localhost:8443/api/openapi.json -> 200, saved to scratchpad/openapi.json) and tallied it: POST /api/assets/ documents only [201, 422]; PUT /api/assets/{asset_id} only [200, 422]; DELETE only [204, 422]; POST /api/relationships/ only [201, 422]. Across all 125 write operations, 0 document 202, 403, 409, 429 or 401, and the string "pending_change_id" appears nowher

## G5-13 [sev 2] [bug] /docs and /redoc both render blank because their CDN assets are blocked by CSP
- Route: `/docs and /redoc` · area: gap-api-machine-client
- Actual: Both documentation UIs are dead, so a developer has no interactive reference at all (only raw /api/openapi.json). The /tokens page links to /docs, leading straight to the blank page.
- Expected: The API consumer's only in-app reference should load; either self-host the Swagger/ReDoc assets or allow their origins in the CSP.
- Code: src/grcen/main.py custom_swagger_ui/custom_redoc (CDN URLs); src/grcen/middleware.py CSP script-src 'self' 'nonce-...'
- Fix: Serve swagger-ui-dist and redoc bundles from /static (self-hosted) so they satisfy script-src/style-src 'self', and use a local favicon.
- Verifier (confirmed): Reproduced live as admin09 against https://localhost:8443 with headless Chromium. GET /docs returns 200 with CSP "default-src 'self'; script-src 'self' 'nonce-...'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'". Rendered body text length is 0 and #swagger-ui has 0 children. Chromium console shows CSP violations for https://cdn.jsdelivr.net/npm/swagger

## G5-15 [sev 2] [ux] Token IP-allowlist and all auth failures collapse to one bodyless 401 with no WWW-Authenticate
- Route: `all /api/* with a bearer token` · area: gap-api-machine-client
- Actual: IP-block, expiry, revocation and every malformed-header case are indistinguishable (same 401, same bare body, no WWW-Authenticate), so an integrator debugging a failing call has no signal about which condition tripped.
- Expected: A client should be able to tell 'your token is valid but your IP is not allowed' / 'expired' from 'malformed header', and a 401 should include WWW-Authenticate: Bearer per RFC 6750.
- Code: src/grcen/routers/deps.py get_current_user raises HTTPException(401,'Not authenticated') for every failure; token_service.validate_token returns None uniformly
- Fix: Add WWW-Authenticate: Bearer error=... on 401s and distinguish at least invalid_token vs insufficient conditions in the error code (without leaking which token exists).
- Verifier (confirmed): Reproduced live against https://localhost:8443 with read-only GETs to /api/assets/?page_size=1 (Accept: application/json). Seven header variants — a previously-minted token that is no longer valid (grcen_QCOT… from work/gap-api-machine-client/view_token.txt, revoked/expired by the earlier cleanup), 'Bearer garbage', 'Token <t>', 'Basic …', lowercase 'bearer <t>', a double-quoted token, and no Auth

## G5-16 [sev 2] [bug] Token IP allowlist reads request.client.host, so behind the production nginx proxy it matches the proxy IP, not the client
- Route: `token validation for all /api/* (production reverse-proxy deployment)` · area: gap-api-machine-client
- Actual: The bearer path uses request.client.host directly (unlike saml.py which reads x-forwarded-for), so the documented per-token IP allowlist feature is effectively non-functional behind the shipped nginx config. (In this proxy-less local container it happens to work.)
- Expected: The allowlist should be evaluated against the real client IP (trusted X-Forwarded-For/X-Real-IP), as saml.py already does for its audit IP.
- Code: src/grcen/routers/deps.py:_resolve_bearer_token (client_ip = request.client.host)
- Fix: Derive the client IP from a trusted-proxy X-Forwarded-For/X-Real-IP (configure uvicorn/gunicorn forwarded-allow-ips) and feed that to _ip_matches_allowlist.
- Verifier (confirmed): Reproduced empirically against the local grcen-app-1 container (script: /tmp/claude-1000/-home-t3rmit3-gits-GRCen/7ebb0b49-1352-428e-a038-c28a4c71f5b6/scratchpad/pw/work/verify/G5-16_token_ip_allowlist.py). The host reaches the container through docker port-mapping, so the app's peer address is the docker gateway 172.18.0.1 (via 127.0.0.1) or the LAN IP 192.168.68.57 (direct) — the same shape as t

## G5-17 [sev 2] [ux] OAuth client secret is shown once with no Copy control, and the /tokens Copy button is dead under CSP
- Route: `/admin/oauth-clients and /tokens` · area: gap-api-machine-client
- Actual: The OAuth secret card has no copy button at all, and the token page's Copy button uses an inline onclick that the CSP nonce policy blocks, so the developer must hand-select both one-time secrets.
- Expected: One-time secrets (OAuth client_secret, API token) should each have a working Copy-to-clipboard control.
- Code: src/grcen/templates/admin/oauth_clients.html (created_client card, no button); src/grcen/templates/tokens/my_tokens.html (button onclick=...)
- Fix: Add a nonce'd (or event-listener-bound) Copy button to both the OAuth secret card and the token display.
- Verifier (confirmed): Plan mode in this session forbade mutating actions (creating a token / OAuth client, writing repro scripts), so I verified from the source and the live server's headers rather than by clicking. Both halves of the finding hold:

1. OAuth secret card: /home/t3rmit3/gits/GRCen/src/grcen/templates/admin/oauth_clients.html lines 20-27 render the one-time `created_client` card with only two `<code>` spa

## G5-18 [sev 2] [design] Token row gives no sign a token is IP-restricted; the allowlist editor is a hidden second row
- Route: `/tokens` · area: gap-api-machine-client
- Actual: The Status column shows only Active/Expired/Revoked; the IP allowlist is editable but never surfaced as state, so a restricted token is indistinguishable from an unrestricted one in the list.
- Expected: A token whose access is IP-restricted should be flagged in its row (e.g. a badge or the configured ranges in a column), so an operator can see at a glance which tokens are locked down.
- Code: src/grcen/templates/tokens/my_tokens.html (status column + separate allowed-ips <tr>)
- Fix: Add an 'IP restricted' badge or an 'Allowed IPs' column summarising the ranges on the main row.
- Verifier (partially): Verified from source (this session is in plan/read-only mode, so I could not write the Playwright script or perform the Save IPs step, which mutates token state; the finding is a 'design' category about what the template renders, and the template is conclusive). In /home/t3rmit3/gits/GRCen/src/grcen/templates/tokens/my_tokens.html the main token row's Status cell renders only one of three badges (

## G5-19 [sev 2] [ux] OAuth client registration gives no success feedback and no secret regeneration/rotation path
- Route: `/admin/oauth-clients` · area: gap-api-machine-client
- Actual: The page relies on a ?flash= redirect that was empty for create/ttl/no-perm actions in testing, and offers no secret rotation, so recovering from a leaked secret means delete+recreate (new client_id, breaking the integration).
- Expected: Creation/validation should confirm or explain the outcome, and a leaked secret should be rotatable without deleting and re-registering the client.
- Code: src/grcen/routers/admin_pages.py:1153 oauth-clients POST (flash handling); templates/admin/oauth_clients.html
- Fix: Show a success/validation flash on every create path and add a 'Regenerate secret' action that keeps the client_id.
- Verifier (partially): Verified by reading the route handler, service, template and the original tester's script/screenshot (plan mode was active in this session, so I did not drive the browser myself; no new objects were created).

What holds up:
- No secret rotation path. `src/grcen/services/oauth_service.py` exposes only create_client / list_clients / set_enabled / delete_client / issue_token; `routers/admin_pages.py

## G5-20 [sev 2] [ux] A .txt / wrong-format file posted to the import preview reports success with zero rows
- Route: `POST /api/imports/assets/preview` · area: gap-api-machine-client
- Actual: The asset preview treats an unparseable/empty file as 0 valid rows with no error, so an integrator uploading the wrong file is told nothing is wrong.
- Expected: A file that cannot be parsed as the expected format (or has no recognisable columns) should return a clear 'could not parse / no valid columns' error, not a silent zero-row success.
- Code: src/grcen/services/import_service.py preview path (empty result with no error)
- Fix: Return an explicit error when the parsed file yields no recognised header/columns or zero rows.
- Verifier (confirmed): Reproduced at the code/service level (plan mode was active for this session, so I did not log in or create files/screenshots; the live HTTP call was not executed). The route POST /api/imports/assets/preview (src/grcen/routers/imports.py:24-36) does nothing but decode the upload, pick fmt='json' only when the filename ends in .json (so a .txt is parsed as CSV), call preview_asset_import, and return

## G5-4 [sev 2] [bug] A 403 on an /api route returns a full HTML 'Access Denied' page when Accept includes text/html
- Route: `any gated /api/* route, Accept: text/html` · area: gap-api-machine-client
- Actual: The global HTTPException handler keys only on 'text/html' in Accept, so an API client that sends a browser-like Accept (common for libraries defaulting to */* that resolve to text/html, or developers testing in a browser) receives a full HTML page on a JSON endpoint.
- Expected: An /api/ endpoint should return JSON for an authorization failure regardless of the Accept header; HTML error pages belong only to browser page routes.
- Code: src/grcen/main.py http_exception_handler (403 branch checks only Accept, not the path)
- Fix: Gate the HTML 403/redirect branch on `not request.url.path.startswith('/api/')` in the exception handler.
- Verifier (confirmed): Root cause verified in code and the handler's path-blindness verified against the live server. src/grcen/main.py:276-286 registers a single app-wide HTTPException handler whose 403 branch is gated solely on `"text/html" in request.headers.get("accept", "")` and renders templates/errors/403.html (title "Access Denied - GRCen"); nothing checks request.url.path, and there is no router-level override 

## G5-5 [sev 2] [bug] List endpoints 500 (bare text) on page=0, negative page_size and unknown status, and the 500 drops the keep-alive connection
- Route: `GET /api/assets/` · area: gap-api-machine-client
- Actual: page/page_size have no lower-bound validation so a negative OFFSET/LIMIT reaches asyncpg and raises; status is passed to SQL unchecked. The 500 is bare text/plain and the connection is torn down, so a pooled HTTP client sees a transport error on its next request.
- Expected: Out-of-range/invalid query params should return a 422 (or clamp) with a JSON body; the server must not emit a 500 that also kills the connection.
- Code: src/grcen/routers/assets.py:36 list_assets (page:int=1, page_size:int=25 with no bounds); src/grcen/services/asset.py:245 (OFFSET (page-1)*page_size)
- Fix: Add Query(ge=1) to page and Query(ge=1, le=<max>) to page_size; validate status against the enum and 422 on mismatch; return JSON 500s.
- Verifier (confirmed): Confirmed by code and by the app container's own log; I could not re-run authenticated requests myself because the session is in plan mode (no login/state changes allowed) and the earlier tester's API tokens were already revoked (all my curl GETs returned 401), so the live evidence is the tester's s1/s2 result files plus the server log, which independently records the exact exceptions for each cas

## G5-7 [sev 2] [bug] Bulk import silently drops owners, tags and metadata and creates duplicates on re-run
- Route: `POST /api/imports/assets/bulk` · area: gap-api-machine-client
- Actual: execute_asset_import only reads name/type/description/status/owner and custom-field columns; a JSON 'tags' or 'metadata' key is dropped, an unresolved owner name is silently discarded (owner stays null, no error), and there is no dedupe so re-runs duplicate. All of this returns HTTP 200 with no warning, so a client cannot detect data loss from the status code.
- Expected: Either honour tags/metadata and report unresolved owners as row errors, or document that bulk ignores them; re-running the same import should not silently duplicate.
- Code: src/grcen/services/import_service.py:67 execute_asset_import (ignores tags/metadata, silent owner drop, no dedupe)
- Fix: Resolve/validate owner and surface unresolved names as row errors; accept tags/metadata keys; add an upsert or at least a duplicate-name warning in the result; use a 207/4xx when any row fails.
- Verifier (partially): Mechanisms are real and the code is unambiguous (/home/t3rmit3/gits/GRCen/src/grcen/services/import_service.py:67-120): (1) INSERT at :104-117 writes only id/type/name/description/status/owner_id/metadata/organization_id, so a JSON `tags` key is never persisted; (2) `_extract_metadata_from_row` (:126-140) keeps only keys that match a CUSTOM_FIELDS definition for the type, so a literal `metadata` k

## G5-8 [sev 2] [bug] OAuth token endpoint ignores the requested scope and does not support HTTP Basic client auth
- Route: `POST /api/oauth/token` · area: gap-api-machine-client
- Actual: The token endpoint has no scope parameter at all, so a requested scope is silently ignored (the client's full permission set is always granted) and the response omits 'scope'; client credentials are only read from the form body, so standards-compliant Basic-auth clients fail with invalid_request.
- Expected: Per RFC 6749 the server should narrow/validate a requested scope (or return invalid_scope) and echo the granted scope, and it MUST support client credentials via HTTP Basic (§2.3.1).
- Code: src/grcen/routers/oauth.py token_endpoint (only Form client_id/client_secret/grant_type; no scope)
- Fix: Accept and validate a 'scope' form param against the client's permissions, echo the granted scope, and also read client_id/secret from an Authorization: Basic header.
- Verifier (confirmed): Both halves reproduce. (1) HTTP Basic client auth: `curl -u 'client:secret' -d grant_type=client_credentials https://localhost:8443/api/oauth/token` returns HTTP 400 {"error":"invalid_request"} — the router reads client_id/client_secret only from Form fields and never looks at the Authorization header, so an RFC 6749 §2.3.1 client (the MUST-support method) fails before any credential check. (2) Sc

## G1-10 [sev 2] [bug] GET /settings/mfa/cancel changes state: an <img>/prefetch of the Cancel link silently discards the pending enrolment, and clicking Cancel gives no feedback
- Route: `/settings/mfa/cancel` · area: gap-auth-lifecycle-mfa-sso
- Actual: The GET deleted the pending user_totp row and the page reverted to 'Set up MFA' after reload; the Cancel click returned to /settings with no message. Because the cookie is SameSite=Lax, a top-level cross-site link to this URL also works.
- Expected: State changes only on POST with CSRF; Cancel should show a flash ('Enrolment cancelled').
- Code: src/grcen/routers/settings_pages.py:236-248 (@router.get('/settings/mfa/cancel')); src/grcen/templates/settings.html:92
- Fix: Make cancel a POST form button with the CSRF partial and redirect with ?flash=ok:Enrolment cancelled.
- Verifier (confirmed): Verified by code inspection rather than a live browser run: this session is in read-only (plan) mode and the repro requires mutating app state (POST /settings/mfa/begin creates a user_totp row). The code path is deterministic and matches every element of the report: (1) the route is registered with GET, not POST; (2) it unconditionally pops the session's pending enrolment and deletes the not-yet-e

## G1-14 [sev 2] [bug] Logout is an unprotected GET: any same-site GET (image, link prefetch) signs the user out, and POST /logout answers 405 JSON
- Route: `/logout` · area: gap-auth-lifecycle-mfa-sso
- Actual: The image request ended the session (next click -> /login); POST /logout returns 405 {"detail":"Method Not Allowed"} as raw JSON; SameSite=Lax still lets a top-level cross-site link to /logout sign users out.
- Expected: Logout should be a CSRF-protected POST (the nav link can be a small form); GET should at most show a confirmation; POST should work.
- Code: src/grcen/routers/auth_pages.py:220-247 (@router.get('/logout')); src/grcen/templates/base.html:58
- Fix: Add @router.post('/logout') with the CSRF partial and make the nav item a form button; keep GET only as a confirmation page.
- Verifier (confirmed): Observed directly against https://localhost:8443 (unauthenticated curl, no sessions created because this session is in plan mode and I was restricted to read-only actions, so I did not run the Playwright login/<img> step): GET /logout -> 302 Location /login (route exists and is reachable as a plain GET); POST /logout -> 405 with raw JSON body {"detail":"Method Not Allowed"}; POST /api/auth/logout 

## G1-15 [sev 2] [design] No confirmation after logout and authenticated pages are served without Cache-Control: no-store
- Route: `/login (post-logout), /assets` · area: gap-auth-lifecycle-mfa-sso
- Actual: The login page is identical to a cold visit (no notice); GET /assets has no Cache-Control or Pragma header at all.
- Expected: A 'You have been signed out' notice on the login page and Cache-Control: no-store on authenticated HTML so Back/bfcache cannot resurrect pages after logout on shared devices.
- Code: src/grcen/routers/auth_pages.py:247 (redirect to /login without a flag); src/grcen/middleware.py:56-62 (_STATIC_SECURITY_HEADERS lacks Cache-Control)
- Fix: Redirect to /login?signed_out=1 and render a notice; add 'Cache-Control: no-store' for responses to authenticated HTML requests in SecurityHeadersMiddleware.
- Verifier (confirmed): Verified by code inspection plus live header checks (plan mode restricted me to read-only actions, so I did not drive a browser login/logout; the code path is deterministic). (1) /logout (src/grcen/routers/auth_pages.py:220-247) invalidates the session, clears it, and returns RedirectResponse("/login") with no query flag. login_page (auth_pages.py:29-42) only populates the template's `notice` slot

## G1-17 [sev 2] [design] SSO is switched on implicitly the moment three fields are filled - no enable toggle, no 'test connection', no validation of the issuer or certificate
- Route: `/admin/oidc-settings, /admin/saml-settings` · area: gap-auth-lifecycle-mfa-sso
- Actual: Both pages redirect back with no feedback other than the banner flipping to 'enabled'; the login page for every user immediately shows 'Sign in with ...' buttons that 500 when clicked; the garbage certificate is accepted and even served in SP metadata.
- Expected: An explicit Enabled checkbox, a 'Test connection' that fetches the discovery document / validates the PEM before enabling, and a confirmation that the login page for all users is about to change.
- Code: src/grcen/services/oidc_settings.py:38-40 and saml_settings.py:50-54 (enabled = all three fields non-empty); src/grcen/routers/admin_pages.py:569-592 and :618-649 (no validation, no flash)
- Fix: Add an explicit enabled flag, validate URL/PEM on save, provide a 'Test' button that fetches .well-known/openid-configuration (or parses the cert) and show the result inline; flash success/failure.
- Verifier (partially): Verified by code inspection plus the prior tester's artefacts (did not re-save SSO settings: that is a global, login-page-affecting toggle; the live /login currently shows no SSO buttons and the tester's s8_sso.py restored the original values).

Core design claim is accurate:
- No explicit enable flag: `OIDCSettings.enabled` is `bool(issuer_url and client_id and client_secret)` (src/grcen/services

## G1-19 [sev 2] [ux] MFA challenge page does not say which account is being challenged and offers no distinct recovery-code or 'not you?' path
- Route: `/login/mfa` · area: gap-auth-lifecycle-mfa-sso
- Actual: Only a generic 'Enter authentication code' heading, one 'Code' field and '← Back to login'; Back keeps the pending MFA state alive in the session (visiting /login/mfa later still shows the challenge), so there is no explicit way to abandon the half-finished login.
- Expected: Show 'Signing in as admin15', a 'Not you? Sign in as a different user' action that clears the pending state, and a clearly labelled 'Use a recovery code instead' option.
- Code: src/grcen/templates/auth/mfa.html:5-21; src/grcen/routers/auth_pages.py:138-145
- Fix: Pass the pending username into the template, add a POST 'cancel' that pops mfa_pending_user_id, and split the recovery-code entry into its own labelled control.
- Verifier (confirmed): Plan mode was active for this session, so I did not drive a live login (it writes sessions/audit rows); verification is from the existing tester screenshot plus a full read of the code paths, which are unambiguous.

Screenshot (work/gap-auth-lifecycle-mfa-sso/s2_01_mfa_challenge.png) matches the description exactly: heading 'Enter authentication code', one hint paragraph, a single 'Code' field, 'V

## G1-5 [sev 2] [ux] Lockout after 5 wrong codes gives no warning, dumps the login form under the /login/mfa URL and never says how long the lock lasts
- Route: `/login/mfa -> /login` · area: gap-auth-lifecycle-mfa-sso
- Actual: Attempts 1-5 all say only 'Invalid code. Try again.'; attempt 6 renders the full login form at URL /login/mfa with 'Too many failed attempts. Try again later.' (no duration); the password step is then locked for 15 minutes with the same wording; no in-app notification or email about the lockout.
- Expected: A countdown of remaining attempts ('2 attempts left before lockout'), a lockout message with the duration (15 min) and a hint about recovery codes, and the locked state not destroying the pending password step without explanation.
- Code: src/grcen/routers/auth_pages.py:174-191 (lockout branch renders auth/login.html in place); src/grcen/config.py:37-38
- Fix: Show remaining attempts on the challenge page, redirect (302) to /login?locked=1 instead of rendering the login template under /login/mfa, and include LOGIN_LOCKOUT_MINUTES in the message.
- Verifier (confirmed): Verified by reading the code path and inspecting the reporter's screenshots (s9_attempt_5.png, s9_attempt_6.png, s9_password_while_locked.png under scratchpad/pw/work/gap-auth-lifecycle-mfa-sso/). I did not re-execute the lockout myself: the session is in plan mode (read-only), and the repro would lock admin15 for 15 minutes. The code is deterministic enough that the screenshots and source agree o

## G1-6 [sev 2] [ux] Navigating anywhere while the MFA challenge is pending throws the user back to the password form instead of /login/mfa
- Route: `/ , /assets (while mfa_pending)` · area: gap-auth-lifecycle-mfa-sso
- Actual: 401 -> 302 to /login showing an empty username/password form with no message; the pending MFA state is still alive underneath (typing /login/mfa manually shows the challenge again), so the user re-enters the password for nothing.
- Expected: Redirect to /login/mfa with the challenge intact (the password step already succeeded), ideally with a 'finish signing in' note.
- Code: src/grcen/main.py:283-285 (401 handler always redirects to /login); src/grcen/routers/auth_pages.py:28-42 (login_page ignores mfa_pending_user_id)
- Fix: In the 401 handler (or login_page) check request.session['mfa_pending_user_id'] and redirect to /login/mfa; add a ?next= so the user returns to the page they typed.
- Verifier (confirmed): Plan mode was active for this session, so I could not run the Playwright login flow (POST /login writes a login audit event, resets the failure counter and mutates the session). Verification is therefore a code trace plus a harmless unauthenticated GET; the mechanism is deterministic and matches the report exactly.

Trace for a browser that has passed the password step but not the TOTP step:
1. /l

## G1-7 [sev 2] [ux] Logging in with a recovery code gives no notice that a code was consumed or how many remain
- Route: `/login/mfa -> /` · area: gap-auth-lifecycle-mfa-sso
- Actual: Lands on the dashboard with nothing; no notification; audit log records a plain 'login_mfa' identical to a TOTP login; only /settings quietly shows 'Recovery codes remaining: 7'.
- Expected: A banner like 'You signed in with a recovery code; 7 remain - regenerate them in Settings', a notification, and a distinguishable audit action.
- Code: src/grcen/routers/auth_pages.py:182-217 (verify_login_code result is a bool; no recovery flag surfaced); src/grcen/services/totp_service.py:158-200
- Fix: Have verify_login_code return which factor matched, log 'login_recovery_code', set a flash/notification with the remaining count, and warn at <=2 remaining.
- Verifier (confirmed): Verified by static inspection of the live code path rather than a browser run: plan mode was active for this session, so I did not execute the Playwright repro (it would consume one of admin15's recovery codes and write session/audit rows). The code is unambiguous on every point the reporter made, so a browser run could not have shown anything different:

1. src/grcen/services/totp_service.py:146-

## G1-8 [sev 2] [ux] Recovery codes are shown once with no copy/download/print control and can never be viewed again or regenerated
- Route: `/settings` · area: gap-auth-lifecycle-mfa-sso
- Actual: Plain <ul> of codes, no controls; after reload the codes are unrecoverable; the only way to get new codes is Disable MFA (which needs no re-auth) and enrol again.
- Expected: Copy/download buttons on first display, and a 'Regenerate recovery codes' action (re-authenticated) once enabled, plus a warning when few remain.
- Code: src/grcen/templates/settings.html:61-70 and :77-84; src/grcen/routers/settings_pages.py (no regenerate route)
- Fix: Add a POST /settings/mfa/recovery/regenerate route guarded by a current TOTP code, plus copy/download buttons (nonce'd script) on the one-time display.
- Verifier (confirmed): Plan mode was active for this session, so I did not run the browser steps (clicking "Set up MFA" POSTs /settings/mfa/begin and writes a totp enrollment row for admin08). The verdict rests on read-only inspection of the template and router, which deterministically produce the described UI. Every claim in the finding is borne out by the source: (1) /home/t3rmit3/gits/GRCen/src/grcen/templates/settin

## G1-9 [sev 2] [bug] Lowercase recovery codes are blocked client-side with the browser's generic 'Please match the requested format' although the server accepts them
- Route: `/login/mfa` · area: gap-auth-lifecycle-mfa-sso
- Actual: The input's pattern="[0-9A-Z\- ]+" fails HTML validation; nothing is submitted and the only feedback is the browser tooltip 'Please match the requested format.' (validity.valid=False). The same pattern also allows '123 456' with a space, which the server then rejects as 'Invalid code'.
- Expected: Case-insensitive acceptance (the server already uppercases) or a specific inline message.
- Code: src/grcen/templates/auth/mfa.html:14; src/grcen/services/totp_service.py:176 (cleaned.upper())
- Fix: Drop the pattern (or use [0-9A-Za-z\- ]+) and normalise case/whitespace server-side; add a title/aria-describedby explaining the accepted formats.
- Verifier (confirmed): Verified by code reading plus the exact input markup rendered in headless Chromium (plan mode was active, so I did not drive the live login/MFA flow, which would create session rows / require enrolling MFA on an account; the template has no conditional rendering around the input, so the result is deterministic). src/grcen/templates/auth/mfa.html:14 renders `<input ... pattern="[0-9A-Z\- ]+">` with

## G4-10 [sev 2] [design] Findings whose affected host could not be matched look normal in the UI and silently never reach the risk register
- Route: `/assets/{finding id}, /registers/findings, /risk-management` · area: gap-ingress-sync-to-ui
- Actual: The page shows 'Affected Asset: ghost-host-admin01' as ordinary text with an empty Relationships section, the register row is identical to resolved findings, and the only trace is the API warnings array and the CLI line 'findings: 4 considered (2 with no system)'; the finding never becomes a Risk.
- Expected: The finding page shows 'Affected asset ghost-host-admin01 — not found in inventory' with a 'link to a system' action, the register exposes an 'unlinked' filter/badge, and the risk register or dashboard surfaces 'N open findings not attached to any system'.
- Code: src/grcen/services/vuln_sync.py:242-247; src/grcen/services/risk_aggregation.py:166-170 (unassigned only counted); src/grcen/templates/assets/detail.html:136-152
- Fix: Persist the resolution outcome (e.g. metadata.affected_asset_resolved=false), render a warning callout with a target-asset picker on the finding page, add an 'Unlinked findings' metric card to the register, and have risk_aggregation emit an 'Unassigned findings' group or a dashboard counter.
- Verifier (partially): Verified from the tester's existing evidence (plan mode kept me read-only, so I did not re-POST to /api/sync/vulns; I reviewed the prior s05 screenshots, the recorded API response in state.json, and the source). The core observation is accurate: (1) vuln_sync persists only `metadata.affected_asset = 'ghost-host-admin01'` (vuln_sync.py:134-139) and records the miss solely in the transient result (`

## G4-11 [sev 2] [bug] Sync writes leave no audit-log entry and notify no watchers
- Route: `/admin/audit, /notifications after any POST /api/sync/*` · area: gap-ingress-sync-to-ui
- Actual: 0 notifications mention Priya before or after; the audit log contains only UI-driven rows (logins, admin02's attachments) and nothing for the 5 persons created, the title change, the archive of admin01-Leaver Lee or the disabling of admin01-lee's login. A disabled login with no audit row is a compliance gap in a GRC tool.
- Expected: Watchers get an 'updated by hris sync' notification like any other update, and the audit log records create/update/archive for each synced asset and the 'deactivate' of the leaver's login, attributed to the sync token/source.
- Code: src/grcen/services/external_sync.py:83-113; src/grcen/services/hris_sync.py:320-333 (UPDATE users SET is_active=false with no audit); src/grcen/services/watcher_service.py not invoked from any sync
- Fix: Emit audit events (actor = token name / source tag) from upsert_external_asset callers for insert/update/archive and from the user-deactivation step, and call watcher_service for updates to watched assets; optionally a single 'sync run' audit row with counts.
- Verifier (confirmed): Verified by read-only code inspection plus the prior tester's artifacts (plan mode was active in this session, so I did not re-run the sync against the live server). The finding is accurate: every ingress connector writes assets/edges/users directly via SQL and none of them emit audit rows, watcher notifications, or webhooks. By contrast the REST/UI paths do all three (assets.py:235-243 notify_wat

## G4-12 [sev 2] [design] Aggregated Risks carry their provenance only in a description sentence, expose editable fields the nightly job overwrites, and show a raw 'relates_to' edge
- Route: `/assets/{vuln-agg risk id}, /assets/{id}/edit, /risk-management` · area: gap-ingress-sync-to-ui
- Actual: Provenance is the trailing sentence 'Auto-aggregated by GRCen from scanner findings.' in the description; the Edit form offers Likelihood/Impact/Treatment/Owner that the nightly aggregation silently resets (same upsert); finding_count is not displayed; the finding edge is labelled 'relates_to' / 'incoming: relates_to' (no rel_label mapping); the register row has Owner '—' and Review '-'.
- Expected: A visible 'Auto-generated from N scanner findings (source vuln-agg) — re-computed nightly' badge, likelihood/impact/treatment either read-only or clearly marked as recomputed, a finding count and list, human-readable relationship labels, and an owner inherited from the affected system.
- Code: src/grcen/services/risk_aggregation.py:97-120 (_risk_payload) and 193-199; src/grcen/routers/_pages_shared.py:18-55 (rel_label map lacks relates_to); src/grcen/templates/assets/detail.html
- Fix: Render a provenance callout for source='vuln-agg' with finding_count and the list of findings, add 'relates_to' to the rel_label vocabulary, inherit owner_id from the system, and merge rather than replace metadata so a human-set treatment/owner survives.
- Verifier (confirmed): Verified against the three evidence screenshots (s05_agg_risk_detail.png, s05_agg_risk_edit.png, s05_risk_management.png — all taken as admin01 on this instance) plus source reading; plan mode prevented me from re-driving the browser, so no new screenshots were produced. Every described state exists: (1) the detail page's only provenance is the trailing sentence "Auto-aggregated by GRCen from scan

## G4-13 [sev 2] [ux] Generic upsert: a relationships-only payload silently prunes every other edge of that source with no warning
- Route: `POST /api/sync/upsert` · area: gap-ingress-sync-to-ui
- Actual: Response shows edges_created: 1, edges_pruned: 2 with an empty warnings array; the Billing system's owned_by and administered_by edges are gone and only 'incoming: provides' remains. The behaviour is documented only in the module docstring, which the API consumer cannot see (/docs is blank and /openapi.json 404s).
- Expected: An incremental relationships-only payload either adds the edge (prune only when assets are also carried, or opt-in via a flag) or at minimum returns a warning naming the edges it is about to delete, mirroring the asset-side 'on_missing' choice.
- Code: src/grcen/services/generic_sync.py:716-721 (prune when 'relationships' in payload); src/grcen/routers/sync.py:46-72 (docstring is the only documentation)
- Fix: Apply on_missing to edges too (default ignore), or add an 'edges_pruned_refs' list plus a warning when pruning more than zero edges; expose the contract in the OpenAPI description.
- Verifier (confirmed): Reproduced read-only (plan mode) with `?dry_run=true`, which the service rolls back; the Billing asset's graph was byte-identical before and after. Script: /tmp/claude-1000/-home-t3rmit3-gits-GRCen/7ebb0b49-1352-428e-a038-c28a4c71f5b6/scratchpad/pw/work/verify/G4-13_rels_only_prune_dryrun.py. Current state left by the original tester: source admin01-etl holds one edge (Vendor Co -provides-> Billin

## G4-14 [sev 2] [ux] SCIM-managed users are indistinguishable in /admin/users: manual role edits are silently reverted and a deprovisioned user can be re-activated from the UI
- Route: `/admin/users, /admin/users/{id}/edit` · area: gap-ingress-sync-to-ui
- Actual: The Edit form is the plain Username/Password/Role/Linked Person form; the manual Admin role reverted to Editor on the next group PATCH with no notice; 'Activate' re-enabled the IdP-deprovisioned login immediately. Also POST /scim/v2/Users with emails: "nope" is accepted (201) and the SCIM settings page accepted a role map value 'superuser' that is not a role.
- Expected: The user row and Edit User form say 'Provisioned by SCIM (externalId okta-admin01-E004); role is managed by group membership' with role/active controls disabled or warned, and re-activating a deprovisioned user needs an explicit override.
- Code: src/grcen/templates/admin/users.html, src/grcen/templates/admin/user_form.html:38-55; src/grcen/routers/admin_pages.py:154-216 (edit) and 815-835 (role map not validated); src/grcen/services/scim_service.py:466-491 (recompute overrides manual role)
- Fix: Join users to their person asset's source in admin_users/user_form, render a SCIM badge and lock role/active for SCIM users (or require a confirm), and validate role-map values against UserRole in admin_scim_settings_submit.
- Verifier (confirmed): Plan mode was active for this session so I did not re-drive the SCIM flow against the live app; I verified instead through the persisted screenshots, the prior script, read-only SELECTs on grcen-db-1, and the code paths, which are deterministic. Every claim in the finding checks out. (1) Live DB: users.admin01-scim-priya has role=editor, oidc_sub/saml_sub NULL, person_asset_id -> assets row with s

## G4-15 [sev 2] [design] CMDB criticality lands only in metadata, so the system header reads 'Criticality: —' while Details says 'Criticality: High'
- Route: `/assets/{cmdb-synced system id}, /registers/systems` · area: gap-ingress-sync-to-ui
- Actual: Header badge is '—', Details shows 'Criticality: High', and the register's Criticality column shows the metadata value, so the core criticality used by bulk apply/badges stays empty for every CMDB-synced system.
- Expected: One criticality: the connector writes the core assets.criticality column (which drives badges, bulk apply and sorting) or the UI shows the metadata value in the header.
- Code: src/grcen/services/cmdb_sync.py:77-80 (_SYSTEM_FIELDS includes criticality -> metadata) and 232-247; src/grcen/custom_fields.py SYSTEM 'criticality' FieldDef
- Fix: In cmdb_sync (and generic_sync's flat 'criticality') write assets.criticality when the value is one of critical/high/medium/low, and drop the duplicate System custom field or alias it to the core column.
- Verifier (partially): Plan mode blocked a fresh browser run (login is a POST), so I verified against the prior tester's screenshot and the code. The described page state is real: /tmp/claude-1000/-home-t3rmit3-gits-GRCen/7ebb0b49-1352-428e-a038-c28a4c71f5b6/scratchpad/pw/work/gap-ingress-sync-to-ui/s04_system_detail.png shows a CMDB-synced System ("admin01-Prod Web", External ID admin01-sys-web) with the header reading

## G4-16 [sev 2] [content] Nothing in the UI explains sync vs import: /imports never mentions duplicates or /api/sync, and the API contract is unreachable (/docs blank, /openapi.json 404)
- Route: `/imports, /tokens, /admin/scim-settings, /docs, /openapi.json` · area: gap-ingress-sync-to-ui
- Actual: /imports copy is 'Upload a CSV or JSON file with columns: name, type, description, status, owner' with no word on duplicates, sources or sync; /tokens lists 'Sync' as a bare checkbox; the SCIM page says 'scoped to the sync permission' but links nowhere; /docs renders an empty body and /openapi.json returns 404, so the only documentation of the five sync contracts is Python docstrings in the repo.
- Expected: The import page states that imports are append-only and that re-syncing systems should use /api/sync/* (with a link to the contract), the Tokens page explains what the 'Sync' permission unlocks, and the OpenAPI schema with the connector payload docstrings is viewable.
- Code: src/grcen/templates/imports/index.html; src/grcen/templates/tokens/index.html:31-39; src/grcen/templates/admin/scim_settings.html:16-21; src/grcen/main.py (docs/openapi routes)
- Fix: Add an 'Integrations' admin page (or a section on /imports and /tokens) listing the connectors, their endpoints, the source tag each uses and a per-source row count/last-run time; serve the OpenAPI JSON and fix the Swagger CSP so the sync router docstrings are readable.
- Verifier (partially): Reproduced as admin05 via curl (cookie session) and headless Chromium. What holds: (1) /imports copy is exactly "Upload a CSV or JSON file with columns: name, type, description, status, owner" plus "Assets must already exist. They are matched by (name, type) pair." for relationships; a grep of the rendered page for sync/upsert/duplicate/idempotent/append returns nothing, and import_service.execute

## G4-7 [sev 2] [bug] Ticket status sync-back closes a finding without the CAPA/verification gate, leaving it 'Remediated' yet still 'Overdue' and active
- Route: `POST /api/sync/tickets -> /registers/findings and /assets/{finding id}` · area: gap-ingress-sync-to-ui
- Actual: finding_status flips straight to 'remediated' with verified_by and corrective_action_plan still null; the register row reads 'Remediated … 2026-09-01 Overdue' at the same time, GET /api/findings/overdue still lists it, and the asset stays status=active (whereas a scanner drop-out archives it), so the three lifecycle signals disagree.
- Expected: The same rule the UI/API close path enforces (root cause + corrective action plan + verifier) applies, or the ticket closure moves the finding to 'pending verification' and the register stops flagging a remediated finding as overdue.
- Code: src/grcen/services/ticketing_service.py:256-275 (writes finding_status directly); src/grcen/services/findings_service.py overdue_findings; src/grcen/templates/assets/list.html overdue badge
- Fix: Route mapped terminal statuses through findings_service.close_finding (or set 'pending_verification' when CAPA is missing), and make the Overdue badge/overdue query exclude remediated/verified/closed findings.
- Verifier (partially): The described UI state is real: s09_findings_register_after_ticket.png shows the register row "admin01 OpenSSL RCE … Remediated | Critical | Automated | 2026-09-01 [Overdue]" and s09_finding_after_ticket_done.png shows the detail page with asset Status=Active, Details Status=Remediated, External Ticket SEC-101, no Verified By / corrective action plan. Code confirms the recorded 'still overdue? Tru

## G4-8 [sev 2] [design] External ticket is shown as three raw text fields (URL not clickable, status 'created') and the findings register has no ticket column
- Route: `/assets/{finding id}, /registers/findings` · area: gap-ingress-sync-to-ui
- Actual: Details shows 'External Ticket: SEC-101', 'External Ticket URL: http://172.18.0.1:9977/browse/SEC-101' as plain wrapped text (zero <a> elements), 'External Ticket Status: created' raw; the register columns are Name/Status/Severity/Source/Due/Owner with no ticket, and the finding page's actions are only Watch/Graph/PDF/Edit/Clone/Delete.
- Expected: A 'Ticket: SEC-101 (Jira) — Done' chip linking to the ticket, a Ticket column or filter in the findings register, and a 'Push to ticket' action on the finding page (the ITSM page promises one).
- Code: src/grcen/templates/assets/detail.html:136-152 (custom field rendering has no link branch); src/grcen/custom_fields.py:535-538; src/grcen/registers.py FINDING columns
- Fix: Give FieldDef a 'url' type (render as link), render external_ticket* as one ticket chip, add a Ticket column to the FINDING register, and add the Push-to-ticket button when ITSM is enabled.
- Verifier (confirmed): Verified against the code and the prior tester's screenshots (this session was placed in read-only plan mode, so I did not log in or create objects; the finding is a 'design' category, where inspection of the rendered state plus the template source is the verification standard). All three described states exist:

1. Ticket fields render as plain text. src/grcen/templates/assets/detail.html:141-151

## G4-9 [sev 2] [design] HRIS manager edges never populate the People register's Manager column or the detail header
- Route: `/registers/people, /assets/{synced person id}` · area: gap-ingress-sync-to-ui
- Actual: Manager column is '—' for every synced person and the header reads 'Manager: —' while the Details block shows a free-text 'Manager: admin01-Nadia Okafor' and the relationships table shows 'managed by admin01-Nadia Okafor'; three representations of one fact, and the only one the register sorts/filters on is empty.
- Expected: The Manager column shows the HRIS manager (it is the roster's single most useful fact) and the header 'Manager:' agrees with the relationships table.
- Code: src/grcen/services/hris_sync.py:270-289 (emits manages edge, never sets owner_id); src/grcen/registers.py:363 (owner_label Manager); src/grcen/templates/assets/detail.html:102-108
- Fix: Have hris_sync (and scim_service) set owner_id to the resolved manager Person when it emits the manages edge (cmdb_sync already sets owner_id for CIs), or derive the register's Manager column from the manages edge.
- Verifier (confirmed): Verified via the prior tester's screenshots plus a read-only DB query (plan mode prevented re-running the sync). s02_people_register_synced.png: /registers/people?q=admin01- shows the curated Manager column as '—' for all 5 synced persons even though 4 of them have HRIS manager_employee_id. s02_synced_person_detail.png: admin01-Priya Nair's header reads 'Manager: —', the Details block reads 'Manag

## G2-10 [sev 2] [ux] Invite form validation error overwrites the typed username with the invite email and pre-fills the Username box with an email address
- Route: `/invite/{token}` · area: gap-multi-org-superadmin
- Actual: The error branch re-renders with suggested_email instead of the submitted username.
- Expected: On error the form keeps what the user typed; pre-fill should either be a suggested username or labelled as such.
- Code: src/grcen/routers/auth_pages.py:310-325 (context uses invitation['email'], not `username`); src/grcen/templates/auth/invite.html:22-23
- Fix: Pass the submitted username back into the template on error; label the pre-fill ('Suggested username').
- Verifier (confirmed): Verified by code trace rather than a live browser run: the session is in plan mode, which forbids write actions, and reproducing requires minting an invitation (a DB write) and POSTing the form, so no script/screenshot was produced. The code is deterministic and unambiguous, though. GET /invite/{token} renders auth/invite.html with suggested_email=invitation["email"], and the template binds the Us

## G2-7 [sev 2] [bug] Org slug is validated only in the browser: the server accepts slugs with spaces and gives no format hint
- Route: `POST /admin/orgs` · area: gap-multi-org-superadmin
- Actual: admin_orgs_create only .strip().lower()s the slug; any string becomes a tenant slug used by the CLI (`grcen createadmin` prompt) and future URLs.
- Expected: Server enforces the same [a-z0-9-] rule and the form explains it ('lowercase letters, digits and hyphens').
- Code: src/grcen/routers/admin_pages.py:334-336; src/grcen/templates/admin/orgs.html slug input (pattern only, no title)
- Fix: Validate with the same regex server-side and flash a specific message; add a hint/title to the input.
- Verifier (confirmed): Verified by code inspection only: plan mode was active for this session, so I could not perform a live POST that would create an "admin14-..." organization row or write a Playwright script. The code path is unambiguous and leaves no room for the described behaviour not to occur.

Client side (src/grcen/templates/admin/orgs.html:44): the slug input is `<input type="text" name="slug" required patter

## G2-9 [sev 2] [ux] Switching organization lands on the literal URL '/?' and discards the page the user was on
- Route: `POST /switch-org` · area: gap-multi-org-superadmin
- Actual: Hard-coded redirect to '/?', no confirmation flash, no return path.
- Expected: Switch returns to a clean '/' (or back to /settings with a 'Now acting in <org>' flash / the referring page re-scoped), and a cross-org bookmark explains 'This item belongs to another organization you are a member of — switch?'.
- Code: src/grcen/routers/settings_pages.py:49
- Fix: Redirect to '/' with ?flash=ok:Switched to <org>; for 404s on ids that exist in another membership org, offer a switch link.
- Verifier (confirmed): Verified from source (this session is in plan mode, so I did not drive the browser or create a login session; no scripts/screenshots were produced — the evidence below is the deterministic code path, not a live capture).

1. Hard-coded landing URL: /home/t3rmit3/gits/GRCen/src/grcen/routers/settings_pages.py:51 (finding said :49; the actual line is 51) ends switch_organization with `return Redirec

## G6-12 [sev 2] [ux] Per-asset graph silently truncates the neighbourhood: depth 3 on a 500-edge hub shows the same 501 nodes as depth 1, with no notice
- Route: `/graph/{id}?depth=3` · area: gap-resilience-scale-mobile-browsers
- Actual: The subgraph query caps at max_nodes=600 ordered by level, so depth 3 draws the same nodes as depth 1 and the #graph-status span stays empty; the user has no way to know thousands of depth-2/3 nodes were cut, and the 500 identical green discs are unreadable.
- Expected: Either the larger neighbourhood, or a clear status such as 'Showing the 600 nearest of N nodes - narrow the depth or hide types'.
- Code: src/grcen/services/graph.py:14 (max_nodes=600) and :56 LIMIT $4; src/grcen/static/js/graph.js:384 updateStatus never called for truncation
- Fix: Return total_nodes/truncated in the /api/graph/{id} payload and call updateStatus() with it; offer per-type collapsing (e.g. '500 devices') or a 'show N more' expander.
- Verifier (partially): Verified read-only (plan mode: no browser script written; evidence is the service's exact SQL run against the live grcen-db-1 container plus the template/JS/router source). The silent-truncation half of the finding is real; the "depth 3 shows the same 501 nodes as depth 1" half is a misdiagnosis.

Facts observed:
- Hub f725f2d4 (admin03-dev-00000, device, org 60555c55 = admin15's org) has 500 dire

## G6-13 [sev 2] [ux] Whole-org graph picks the '500 most recent' assets, so one bulk import replaces the org graph for every user
- Route: `/graph` · area: gap-resilience-scale-mobile-browsers
- Actual: The canvas shows 500 nodes that are all admin03-dev-* devices in one blob; Systems, Vendors, Risks, Policies and Controls of the real estate vanish for everyone until newer rows exist. The caption only says 'Showing the 500 most recent of 5703 assets'.
- Expected: A representative or user-selectable slice (by type, by degree, by owner), or a message that the import dominates the view.
- Code: src/grcen/routers/asset_pages.py:812-830 graph_overview_page; src/grcen/services/graph.py:100-117 get_org_graph (ORDER BY created_at DESC LIMIT)
- Fix: Select the sample by degree (most connected) or by a type filter the user picks before rendering; exclude synced/imported batches by default or let the legend drive the query.
- Verifier (confirmed): Reproduced live as admin16 (plan mode was active, so observation was done with inline read-only HTTP/Playwright calls, no files or screenshots written). /graph caption reads exactly: "Showing the 500 most recent of 5703 assets — open a specific asset to explore its neighborhood in full." GET /api/graph?limit=500 returned 500 nodes / 519 edges with type mix device=481, finding=6, system=5, intellec

## G6-14 [sev 2] [ux] Form inputs use 13-14 px fonts, which triggers iOS Safari's auto-zoom on every focus
- Route: `/login, /assets/new, /frameworks/{id}/soa, /questionnaires/{id}` · area: gap-resilience-scale-mobile-browsers
- Actual: Username/password are 14 px, 24 inputs on /assets/new are 14 px, selects/textarea on the SoA (307 controls) and questionnaire are 13.33 px, the nav search 14.4 px - below Safari's 16 px threshold, so each tap zooms the page in and the user has to pinch out to find Save. (Chromium metrics; the zoom rule is a documented Safari behaviour.)
- Expected: Inputs at >=16 px so the viewport does not zoom when focused; the form stays where the user left it.
- Code: src/grcen/static/css/style.css (.form-row input / .form-group input font-size; no @media queries at all); src/grcen/templates/auth/login.html
- Fix: Set font-size: 1rem (16px) on input/select/textarea in style.css (or at least under a max-width: 480px media query - the stylesheet currently has zero @media rules).
- Verifier (confirmed): Reproduced at a 390x844 viewport (Chromium, computed styles): /login username/password = 14px (both inputs); /assets/new?type=system = 24 visible controls, 23 at 14px + nav search at 14.4px; /frameworks/{id}/soa = 307 controls, 306 selects at 13.3333px (browser default, since the inline-styled `<select style="padding:0.2rem;">` in soa.html carries no font-size) + nav search 14.4px; /questionnaires

## G6-15 [sev 2] [ux] At 390 px, data tables overflow the viewport with no scroll container, hiding Remove/Edit buttons and path columns
- Route: `/assets/{id}, /frameworks/{id}/soa, /settings` · area: gap-resilience-scale-mobile-browsers
- Actual: The attachments table is wider than the viewport: the Link/Path column is clipped mid-word and the Remove button is off-screen; the only way to reach it is dragging the entire 1,728 px-wide page sideways (nav-driven width). The SoA table is 771 px with 6 columns of 13 px selects per row.
- Expected: Tables wrapped in an overflow-x:auto container (or stacked card rows on narrow screens) so the action buttons are reachable with a swipe inside the table.
- Code: src/grcen/static/css/style.css:194 table {width:100%; overflow:hidden}; src/grcen/templates/assets/detail.html attachments/relationships tables
- Fix: Wrap every table in a .table-wrap {overflow-x:auto} div and add a max-width media query that collapses the nav; consider card layout for attachments/relationships on narrow screens.
- Verifier (confirmed): Verified from source only (plan mode in this session forbade launching Playwright or writing a verify script, so no fresh 390px screenshot was taken; the reporter's pixel figures 1,728/771/461 are plausible but not independently re-measured). The described state necessarily follows from the code: (1) /home/t3rmit3/gits/GRCen/src/grcen/static/css/style.css contains no @media rule at all, so nothing

## G6-3 [sev 2] [ux] 60 MB file is fully uploaded before the server rejects it; no size limit shown or checked client-side
- Route: `/assets/{id} (Upload File form)` · area: gap-resilience-scale-mobile-browsers
- Actual: No limit or accepted-types hint anywhere on the form; the server streams the full body (chunked read, rejects only when total > max) and then returns 413 - which the UI then also hides (see separate finding). On a flaky mobile connection this is a multi-minute dead upload with zero feedback.
- Expected: The form states the 50 MB limit and the allowed types, and the browser refuses files over the limit before sending (file.size check), so a user on a slow link does not waste minutes uploading something that will be rejected.
- Code: src/grcen/templates/assets/detail.html:264-270; src/grcen/routers/attachments.py:43-58
- Fix: Render MAX_UPLOAD_SIZE_MB and ALLOWED_UPLOAD_TYPES into the form (accept attribute + helper text) and add a nonce'd script that blocks submit when file.size > limit; optionally reject on Content-Length before reading the body.
- Verifier (confirmed): Verified by source inspection (plan mode was active for this session, so I did not drive the browser or send a 60 MB POST; the finding is category 'ux' and the described state is unambiguous in the rendered template source).

Template state: /home/t3rmit3/gits/GRCen/src/grcen/templates/assets/detail.html:263-270 renders `<input type="file" name="file" required>` with no `accept=` attribute, no hel

## G6-5 [sev 2] [bug] Upload type allowlist trusts the browser-supplied Content-Type: PNG named .pdf and an .exe sent as image/png are both accepted
- Route: `/api/assets/{id}/attachments/upload (asset detail Upload form)` · area: gap-resilience-scale-mobile-browsers
- Actual: Only file.content_type (client-controlled) is checked; the .exe is stored under uploads/ and listed for everyone, and the mislabelled PDF is stored as a PDF. Combined with 'Remove' being CSP-confirm-less this is a trust-boundary gap.
- Expected: ALLOWED_UPLOAD_TYPES enforced on actual content (magic bytes) and/or extension, with a clear rejection for mismatches; .exe never stored as an attachment.
- Code: src/grcen/routers/attachments.py:37-42 _read_upload (if file.content_type and file.content_type not in allowed_types)
- Fix: Sniff the first bytes (python-magic or a small signature table) and check the extension against the allowlist in _read_upload; reject on mismatch with 415.
- Verifier (partially): Plan mode was active for this session, so I could not perform the mutating upload steps against https://localhost:8443; verification is by code inspection, which is deterministic for this defect. The described mechanism is real: /home/t3rmit3/gits/GRCen/src/grcen/routers/attachments.py:39-43 (`_read_upload`) is the only gate, and it compares the client-supplied part header `file.content_type` (Sta

## F091 [sev 2] [ux] Changing depth destroys the graph and silently loses expansions, pan/zoom and selection; failed fetch leaves canvas blank forever
- Route: `/graph/{id}` · area: graph-orgviews
- Actual: cyInstance is destroyed and refetched: expanded nodes and zoom are discarded, the node panel still shows the previously selected node, the status line keeps the stale 'Expanded 1 neighbour(s).' text and there is no loading indicator (blank white box for 1.5-2.5s). When the fetch fails the canvas stays empty with no error (cyInstance === null, status unchanged); the select is not disabled during load.
- Expected: Depth change re-layouts in place or at least shows a loading state, keeps the selection panel in sync and reports failures.
- Code: src/grcen/static/js/graph.js renderGraph lines 24-30 (destroy before fetch, no .catch, no status update) and view.html depth-select change handler
- Fix: Set status 'Loading depth N...' and disable the select during fetch; add .catch that restores a message; call hideNodePanel() on re-render; ideally diff nodes/edges into the existing instance and run the incremental layout instead of destroying it.
- Verifier (partially): Could not run a fresh Playwright repro (plan mode is active in this session, which forbids writing scripts/screenshots or driving the browser), so verification is code-level plus inspection of the reporter's own evidence. The mechanism is deterministic and the code matches the claim exactly:

- /home/t3rmit3/gits/GRCen/src/grcen/templates/graph/view.html lines 43-45: the #depth-select change handl

## F092 [sev 2] [ux] After creating a link the whole graph is re-fetched and randomly re-laid out, so the user loses position and all expanded nodes
- Route: `/graph/{id} and /graph (link mode)` · area: graph-orgviews
- Actual: lastRenderFn() destroys and re-renders the graph with a fresh randomised cose layout: zoom changed from 1.28 to 1.18, the expanded nodes vanished (node count back to 19), and the canvas re-animates, so the user has to re-find both nodes to verify the edge.
- Expected: The new edge is added to the current graph in place (cy.add) with existing node positions preserved.
- Code: src/grcen/static/js/graph.js submitLink .then -> lastRenderFn()
- Fix: On success, cy.add({group:'edges', data:{id, source, target, label}}) using the POST response and highlight it briefly; only full re-render if the response was 202 pending.
- Verifier (confirmed): Mechanism confirmed in code and the described state is visible in the reporter's before/after screenshots. submitLink (src/grcen/static/js/graph.js:479-484) calls lastRenderFn() on success, which is renderGraph(url, focusId): it destroys cyInstance (line 27), re-fetches the original URL, and builds a new cytoscape instance with a plain cose layout (lines 129-135; cose defaults randomize:true, fit:

## F093 [sev 2] [ux] Inline link form: Enter does not submit, Escape does not cancel, empty type gives no message, inputs have no labels
- Route: `/graph/{id} (link form)` · area: graph-orgviews
- Actual: Enter and Escape do nothing (form stays open). Empty Create just moves focus with no message. Both inputs are plain <input> with placeholder only (labels: 0, no aria-label) inside a <div>, not a <form>.
- Expected: Enter submits (keyboard submit is standard for a one-line form), Escape cancels, an empty required field shows 'Relationship type is required', and the inputs are labelled for screen readers.
- Code: src/grcen/static/js/graph.js showLinkForm lines 413-457 (div + type=button, no keydown handlers, no labels)
- Fix: Render a real <form> with onsubmit -> submitLink, a keydown Escape -> cancel, aria-label/visually-hidden <label> on both inputs, and inline validation text in the status span.
- Verifier (confirmed): Verified by reading the code that builds the form and the template container, plus the tester's screenshot (work/graph-orgviews/s2_link_form.png) showing the rendered form. All four described states exist: (1) the container is `<div id="graph-link-form">` in src/grcen/templates/graph/view.html:30 and overview.html:32, not a <form>, and both buttons are created with `type = 'button'` (graph.js:439,

## F094 [sev 2] [design] Ad-hoc relationship types typed in the link form immediately pollute the suggestion vocabulary for everyone
- Route: `/graph/{id} (link form datalist) and GET /api/relationships/types` · area: graph-orgviews
- Actual: The datalist is 'canonical union types already in use', so 'admin05-self' and 'admin05-test-link' now appear as suggestions to all users of the org; typos become permanent vocabulary with no admin UI to merge or rename relationship types.
- Expected: A governed vocabulary (canonical types, optional admin-managed extensions); a free-text value should at least require confirmation ('Create new type X?').
- Code: src/grcen/static/js/graph.js ensureRelTypeSuggestions; src/grcen/routers/relationships.py relationship_types
- Fix: Separate canonical from in-use types in the UI (group or badge 'custom'), prompt before creating a new type, and add a rename/merge action under /tags-style admin for relationship types.
- Verifier (confirmed): Mechanism verified in code and live (read-only; no objects created, plan mode). GET /api/relationships/types as admin14 returned 200 with 52 flat strings; the canonical RELATIONSHIP_LABELS dict has 46 entries, so 6 in-use non-canonical types are already interleaved alphabetically with the canonical ones and nothing in the payload or the <option> markup (plain `<option value="x">`) distinguishes th

## F095 [sev 2] [design] Legend lists all 17 types regardless of what is on canvas, has no counts, and omits the Answer type that is actually drawn
- Route: `/graph and /graph/{id}` · area: graph-orgviews
- Actual: 11 of 17 legend entries are inert on the org graph; nodes of type 'answer' are grey (fallback colour) and unfilterable because TYPE_COLORS has no 'answer' key; the legend is identical on every graph page.
- Expected: Legend entries only for types present, each with a count (e.g. 'Requirement (413)'), and every rendered type represented so it can be toggled.
- Code: src/grcen/static/js/graph.js renderLegend (iterates Object.keys(TYPE_COLORS)); src/grcen/static/js/type_colors.js (no 'answer')
- Fix: Build the legend from the loaded data (count per type), add 'answer' to TYPE_COLORS, and call renderLegend after each renderGraph/expandNode.
- Verifier (confirmed): Verified from code plus the prior tester's screenshots (plan mode restricted me to read-only actions, so no new Playwright run; I re-examined work/graph-orgviews/s1_org_graph_viewport.png and s1_org_graph_hide_req.png and queried the DB read-only). (1) renderLegend in src/grcen/static/js/graph.js:253-281 iterates Object.keys(TYPE_COLORS) unconditionally and renders only a swatch + type name — no c

## F096 [sev 2] [ux] Hiding a type via the legend leaves a large empty hole: graph neither re-fits nor re-layouts
- Route: `/graph` · area: graph-orgviews
- Actual: Nodes are just display:none; the remaining small clusters stay at the edges at the same tiny zoom with the middle of the canvas blank. The user must manually zoom and pan to find them.
- Expected: The remaining 87 nodes are re-fitted (cy.fit) or re-laid out so they fill the canvas and become readable.
- Code: src/grcen/static/js/graph.js applyTypeFilter
- Fix: After applying the filter, call cyInstance.fit(cyInstance.elements(':visible'), 30) and offer a 'Re-layout visible' button.
- Verifier (confirmed): Verified by source inspection plus the reporter's screenshot (plan mode in this session blocked writing/running a new Playwright script, so I did not drive the browser myself). The mechanism is deterministic and matches the description: `applyTypeFilter()` in /home/t3rmit3/gits/GRCen/src/grcen/static/js/graph.js (lines 237-249) runs `cyInstance.batch(...)` and sets `n.style('display', hidden ? 'no

## F097 [sev 2] [ux] No zoom/fit/reset controls, no in-graph search, no export or print of the graph
- Route: `/graph and /graph/{id}` · area: graph-orgviews
- Actual: The only controls are Depth, Link Mode and the legend. Zoom is wheel-only, there is no way to recover from a lost viewport other than reload, no search, no export button, and no @media print rules (0 found) so printing yields the nav bar and a cropped canvas.
- Expected: Standard graph affordances: +/- / fit / reset buttons, a 'find node' box that highlights and centres a match, PNG/SVG export (Cytoscape has cy.png()), and a print stylesheet.
- Code: src/grcen/templates/graph/view.html and overview.html .graph-controls; src/grcen/static/js/graph.js
- Fix: Add a small toolbar (zoom in/out, fit, reset, 'Download PNG' via cy.png({full:true}), and a search input that filters cy.nodes('[label @*= q]') and runs cy.animate({center, zoom})).
- Verifier (confirmed): Verified by source inspection plus the prior tester's depth-3 screenshot (plan mode was active in this session, so I did not launch a fresh browser/login; the described state is fully determinable from the shipped templates/JS/CSS). Observations: (1) src/grcen/templates/graph/view.html .graph-controls contains only the Depth <select>, the Link Mode button (CREATE perm) and a status span; overview.

## F098 [sev 2] [a11y] Canvas graphs are not keyboard accessible and have no accessible name; tab order goes straight through 17 legend buttons
- Route: `/graph, /graph/{id}, /org-views` · area: graph-orgviews
- Actual: Keyboard users cannot interact with any graph or tree at all; screen readers announce nothing for the canvases (noted as 'none expected' in the brief, but there is also no text alternative anywhere on the page).
- Expected: Canvas has tabindex=0, role=img/application with an aria-label describing the content, keyboard zoom/pan, and a text alternative (e.g. a relationship list) for screen-reader users; the legend should be a single grouped control.
- Code: src/grcen/templates/graph/view.html #cy div; src/grcen/templates/org_views.html <canvas> elements; src/grcen/static/js/graph.js (no key handlers)
- Fix: Add tabindex/role/aria-label, keyboard handlers for +/-/arrows/0 (fit), and render a visually-hidden list of 'A -relationship-> B' beneath the canvas as the text alternative; wrap the legend in a fieldset with aria-label 'Filter by type'.
- Verifier (confirmed): Verified by static inspection of the shipped templates and scripts (the session is in plan mode, so I could not launch the Playwright browser or write a repro script; for an a11y finding about static markup the source is conclusive). All four described states exist: (1) /home/t3rmit3/gits/GRCen/src/grcen/templates/graph/view.html:33 and graph/overview.html:35 render `<div id="cy" style="...">` wit

## F099 [sev 2] [ux] Asset with zero relationships shows one giant 330px circle instead of an empty state
- Route: `/graph/{id} (e.g. /graph/0651cb9d-100a-46fc-9a58-70cfa1d06661 'Budget Planning Cycle')` · area: graph-orgviews
- Actual: Cytoscape fits the single node at zoom 8.2: a huge orange disc with a ~90px label; no text explains that the asset is isolated. Expand reports 'No new neighbours.' which does not say why.
- Expected: A message such as 'Budget Planning Cycle has no relationships yet. Add one from the asset page or switch on Link Mode.' with a link, and the node drawn at normal size.
- Code: src/grcen/static/js/graph.js renderGraph (no handling for data.edges.length === 0; layout fit with no maxZoom)
- Fix: Pass maxZoom: 2 to the layout/cy options and, when edges are empty, write an empty-state message into #graph-status (or an overlay) with a 'Add relationship' link to /assets/{id}#relationships.
- Verifier (confirmed): Reproduced as admin03 on /graph/0651cb9d-100a-46fc-9a58-70cfa1d06661 ('Budget Planning Cycle'): cyInstance reports nodes=1, edges=0, zoom=8.21; the single node's renderedWidth is 328.5px and its label renders at 98.6px font (so the '330px circle / ~90px label' description is accurate). #graph-status is empty after load; no text anywhere on the page says the asset is isolated. Calling expandNode on

## F100 [sev 2] [ux] Unknown or malformed asset id on /graph/{id} returns a bare 'Not found' or raw JSON 422 with no layout
- Route: `/graph/{id}` · area: graph-orgviews
- Actual: (1) 404 with the literal body 'Not found' - no nav, no styling, dead end. (2) 422 with FastAPI's raw JSON validation error ('Input should be a valid UUID, invalid character: found n at 0').
- Expected: The app's standard 404 page with navigation and a link back to /assets.
- Code: src/grcen/routers/asset_pages.py graph_page lines 832-841 (HTMLResponse('Not found', 404)); path param typed as UUID with no HTML exception handler
- Fix: Render templates/errors/404.html for both cases (catch RequestValidationError for HTML routes or accept str and validate manually).
- Verifier (confirmed): Reproduced via authenticated HTTP requests as admin04 (browser-style Accept: text/html header). GET /graph/00000000-0000-0000-0000-000000000000 -> 404, content-type text/html, body is literally the 9-byte string "Not found" (no layout, no nav). GET /graph/not-a-uuid -> 422, content-type application/json, body {"detail":[{"type":"uuid_parsing","loc":["path","asset_id"],"msg":"Input should be a vali

## F101 [sev 2] [design] Centre node is only distinguished by a 3px vs 1px border, invisible at depth 2/3
- Route: `/graph/{id}` · area: graph-orgviews
- Actual: Only border-width differs; at zoom 0.6-0.68 the 3px border is ~2px and the node is buried in the hairball. The page header names the asset but nothing points to it on the canvas.
- Expected: The focal asset is unmistakable: larger node, distinct halo/colour, pinned at the centre, or named in the panel/legend with a 'centre on focus' button.
- Code: src/grcen/static/js/graph.js node style 'border-width' function (line 70)
- Fix: Give the centre node width/height 60, a contrasting halo (underlay or second border colour), lock its position at the canvas centre, and add a 'Centre on asset' button.
- Verifier (confirmed): Verified against the code and the existing canvas/viewport screenshots (plan mode was active in this session, so I did not launch a new browser run that would write files; for a 'design' finding the artefacts already on disk plus the source are sufficient and unambiguous). Code: src/grcen/static/js/graph.js lines 59-74 give every node width/height 40, the same border-color #1e293b, and the only ce

## F103 [sev 2] [bug] Org tree canvas is not re-sized on viewport change: it overflows its container after a resize
- Route: `/org-views` · area: graph-orgviews
- Actual: Canvas stays at 1152px while the container is 952px (overflow: visible), so the right part of the tree is clipped outside the card and the page gets wider; the fit is never recomputed. At 390px the canvas shows one partial card at the right edge and nothing else.
- Expected: Canvas re-sizes to its container and the tree re-fits.
- Code: src/grcen/static/js/org_tree.js renderOrgTree lines 333-340 (size computed once, no resize listener)
- Fix: Add a ResizeObserver on the container that re-sizes the canvas and re-runs draw() with the existing layout; same for the Cytoscape pages (cy.resize(); cy.fit()).
- Verifier (confirmed): Reproduced headlessly as admin07 on /org-views (measurements via page.evaluate; plan mode was active so no screenshots/files were written). At 1440px viewport: canvas inline style width 1152px == container 1152px. After set_viewport_size to 1000px: canvas stays at 1152px (style.width, bounding box and backing-store width all 1152) while .org-tree-container is 952px, with computed overflow 'visible

## F104 [sev 2] [ux] Org Views tabs and selected product are not reflected in the URL and tabs lack ARIA tab semantics
- Route: `/org-views` · area: graph-orgviews
- Actual: Every variant lands on Org Chart with no product selected; the tab buttons are plain <button>s with no role/aria-selected. Browser back after clicking a card returns to Product View only because of bfcache - a hard reload loses it.
- Expected: Deep-linkable state (?tab=product&product={id}) so a product tree can be shared or bookmarked; tabs with role=tablist/tab, aria-selected and arrow-key navigation.
- Code: src/grcen/templates/org_views.html inline script (tab switching, product change) 
- Fix: Write tab and product to history.replaceState query params and read them on load; add role=tablist/tab/tabpanel + aria-selected + Left/Right key handling.
- Verifier (confirmed): Reproduced live as admin08 on /org-views (headless Chromium via grc_pw helper; plan mode active so run inline without writing scripts/screenshots). Observations: (1) all three tabs are plain <button class="org-tab"> with role=None, aria-selected=None, aria-controls=None; the .org-tabs wrapper has no role=tablist and the three .org-tab-panel divs have no role=tabpanel. (2) Focusing 'Org Chart' and 

## F105 [sev 2] [ux] Org/product tree cards navigate on single click with no new-tab option, no hover detail, and job titles get truncated
- Route: `/org-views` · area: graph-orgviews
- Actual: Single click immediately does window.location.href (losing the tree), modifier clicks also navigate in the same tab, there is no tooltip, and 5 of 45 titles plus 1 name are cut with an ellipsis with no way to read them except opening the asset.
- Expected: Hover shows the full name/title (and ideally a small popover with Open/Expand like the graph page), modifier-click opens in a new tab, and long titles wrap or are visible on hover.
- Code: src/grcen/static/js/org_tree.js setupInteraction click handler lines 265-279 and truncate()
- Fix: Set canvas.title (or a positioned tooltip) on hover with the full label/subtitle; on click honour ctrlKey/metaKey/button===1 with window.open; consider an on-canvas selection panel reusing the graph page's pattern.
- Verifier (confirmed): Plan mode was active for this session, so I did not launch a browser or create objects; verification is from the source and the prior tester's screenshots (read-only). All three described states exist and are unambiguous in code:

1. Single click navigates in-tab: /home/t3rmit3/gits/GRCen/src/grcen/static/js/org_tree.js:266-279 — the 'click' handler hit-tests the card and does `window.location.hre

## F106 [sev 2] [design] Product View tree hides relationship types and direction, so the hierarchy is misleading
- Route: `/org-views (Product View)` · area: graph-orgviews
- Actual: All related assets are drawn as undifferentiated children of the product with unlabeled connectors; the API returns labels (owns, has member, deployed_on, scans) but the renderer never draws them, and before a product is chosen the canvas is a blank 300x150 default with no prompt.
- Expected: Edge labels (deployed_on, processes, mitigated_by, threatens...) or grouping by relationship type, since a Product 'deployed_on' a cluster is not a child of it in any organisational sense.
- Code: src/grcen/static/js/org_tree.js drawOrthogonalEdge (ignores edge.label); services/org_views.py get_product_view
- Fix: Draw the edge label at the horizontal segment midpoint, or group children under small headers per relationship type; render a 'Select a product to see its ownership and dependencies' message in the empty canvas.
- Verifier (confirmed): Verified against the code and the tester's screenshots (plan mode active, so I inspected rather than re-ran the browser). Screenshot work/graph-orgviews/s4_product_2_canvas.png (and s8_product2_rendered.png) shows 'Customer Portal' with 15 related assets (Production Kubernetes cluster, Mobile App [another product], Data Warehouse, IdP, Datadog, Salesforce CRM, Customer PII, Payment Card Data, Vuln

## F199 [sev 2] [design] Export form uses free-text comma lists for types and columns, with no list of valid column names and silent empty columns for typos
- Route: `/exports` · area: import-export-approvals
- Actual: Users must guess internal field names (e.g. 'contract_end'); misspellings yield silently empty columns.
- Expected: Multi-select checkboxes for types; a column picker populated from base + per-type custom fields; unknown columns rejected with a message.
- Code: src/grcen/templates/exports/index.html:16-36; src/grcen/services/export_service.py:95-104 (unknown col -> meta.get(col, ''))
- Fix: Render checkboxes for types and a grouped checkbox list of columns (base + custom fields per selected type, via a small inline script); validate column names server-side and 400 on unknown ones.
- Verifier (confirmed): Verified from source (plan mode was active for this session, so no Playwright script/screenshot could be written or a login session created; the finding is a 'design' category and the template + service code is unambiguous). /home/t3rmit3/gits/GRCen/src/grcen/templates/exports/index.html lines 16-19: 'Asset Types' is a plain <input type="text" name="types"> with a <small> listing every AssetType v

## F200 [sev 2] [ux] Execute step forces the user to re-select the file after Preview, and nothing ties the two steps together
- Route: `/imports` · area: import-export-approvals
- Actual: Two independent forms with separate file inputs; Preview is effectively optional and the execute confirm ('Import all valid rows?') is a window.confirm.
- Expected: One file chooser; Preview populates the result and enables an 'Import N valid rows' button that posts the same file (or a server-side token for the previewed upload).
- Code: src/grcen/templates/imports/index.html:10-32 and 40-62
- Fix: Collapse to a single form: Preview returns an HTML fragment that contains the Execute button (hx-include the same file input or an upload id), and render the confirmation as an in-page summary rather than confirm().
- Verifier (confirmed): Verified from source and the existing evidence screenshots (plan mode was active for this run, so I did not drive a new browser session; the described state is fully determined by the template and does not depend on runtime data). /home/t3rmit3/gits/GRCen/src/grcen/templates/imports/index.html lines 11-17 and 21-31 are two separate <form> elements, each with its own `<input type="file" name="file"

## F201 [sev 2] [content] Import page documents only five columns: no valid type/status values, no custom-field columns, no sample/template download
- Route: `/imports` · area: import-export-approvals
- Actual: Trial-and-error; the practitioner has to read the export's header to learn the column names.
- Expected: List the allowed type and status values, explain owner resolution, mention per-type custom-field columns, and offer a downloadable template CSV per type.
- Code: src/grcen/templates/imports/index.html:8,37; src/grcen/services/import_service.py:36-56 (validation), 112-127 (custom fields)
- Fix: Render the AssetType/AssetStatus enums and CUSTOM_FIELDS in the template, add '/imports/template.csv?type=vendor' that emits a header row, and link the export page.
- Verifier (confirmed): The described state exists exactly as reported. /imports is served by src/grcen/routers/dashboard_pages.py:71-78, which renders the static template src/grcen/templates/imports/index.html; line 8 is the only asset-import guidance ("Upload a CSV or JSON file with columns: name, type, description, status, owner") and line 37-38 the only relationship guidance. No enum values, no owner-resolution note,

## F202 [sev 2] [bug] Unresolvable owner names are silently dropped on import
- Route: `/imports -> /api/imports/assets/execute` · area: import-export-approvals
- Actual: No warning anywhere; ownership is lost.
- Expected: Preview should warn 'Row 1: owner 'Nobody Real Person' not found' and execute should report it, since ownership is core GRC data.
- Code: src/grcen/services/import_service.py:77-92 (owner lookup; miss -> owner_id None)
- Fix: Resolve owners in preview_asset_import (needs pool + org) and add a warnings list to ImportPreview/ImportResult; surface it in the UI.
- Verifier (confirmed): Verified by code trace and by the prior tester's artifacts (session was in read-only/plan mode, so I could not run a fresh import myself; I inspected the existing script work/import-export-approvals/t5_owner.py and its screenshot 70_owner_import_detail.png, which show asset 'admin09-Owner Test' imported with owner 'Nobody Real Person' rendering 'Owner: —' while the custom field 'tier' was applied)

## F203 [sev 2] [ux] Dry run reports 'created: N' and an empty/header-only file reports success with zero rows, both without explanation
- Route: `/imports (Execute with 'Dry run' checked; empty CSV)` · area: import-export-approvals
- Actual: Misleading 'created' wording on dry run; empty input looks like a successful import of nothing.
- Expected: Dry run should say 'Would create 2 assets (nothing written)'; an empty file should produce 'No data rows found; check the header row'.
- Code: src/grcen/routers/imports.py:59 (return dict); src/grcen/services/import_service.py:50-60
- Fix: Return a would_create count on dry run and treat 0 parsed rows as a validation error in preview/execute; render both as callouts.
- Verifier (confirmed): Reproduced live as admin11 against https://localhost:8443 (session + multipart POSTs to the same endpoints the /imports htmx forms call), and cross-checked the prior tester's screenshots 05_dry_run_result.png / 11_empty_preview.png, which show the raw JSON dropped into the page exactly as described. Observed responses: (1) 0-byte admin11-empty.csv -> POST /api/imports/assets/preview 200 {"total_ro

## F204 [sev 2] [a11y] Workflow Settings has 72 unlabelled form controls and gives no feedback after Save
- Route: `/admin/workflow` · area: import-export-approvals
- Actual: Screen readers announce 'checkbox, not checked' 54 times; sighted users get no save confirmation; the sub-row labels use snake_case.
- Expected: aria-label/label per control (e.g. 'Require approval to create Intellectual Property'), a success flash after save, a sticky or per-row save, and a visual separation between the main row and the grey 'Outbound edges from intellectual_property' sub-row (which also shows the raw enum).
- Code: src/grcen/templates/admin/workflow_settings.html:26-45 (unlabelled inputs), 47-48 (raw asset_type), 82 (Save); src/grcen/routers/workflow.py:227 (redirect without flash)
- Fix: Add aria-label to each input built from the type title and column, title-case the sub-row type name, redirect with ?saved=1 and render a callout, and consider a per-row 'Advanced' disclosure for the edge/approver-role options.
- Verifier (confirmed): Logged in as admin12 and loaded /admin/workflow (HTTP 200) at 1440x900. DOM audit: 147 form controls, 73 with no accessible name; excluding the hidden csrf_token input that is exactly the 72 reported — 18 asset-type rows x (3 bare checkboxes + 1 bare number input), none inside a <label>, no aria-label/aria-labelledby/title, no label[for]. The sub-row controls (rel_create/rel_delete/select/self_app

## F205 [sev 2] [design] Approvals queue shows raw enum values, pipe-separated text tabs with no counts, and no empty-state guidance
- Route: `/approvals` · area: import-export-approvals
- Actual: Internal identifiers exposed to approvers; the queue cannot be sorted or filtered by type/submitter.
- Expected: Styled tabs with counts, humanised Action/Type ('Create · Intellectual Property'), a status badge, a link to the target asset for update/delete rows, and an empty state linking to Workflow settings.
- Code: src/grcen/templates/workflow/approvals_list.html:7-47
- Fix: Reuse the badge/tab styles from assets/list.html, pass per-status counts from list_changes, apply |replace('_',' ')|title, and add a target-asset link column.
- Verifier (confirmed): Verified against the template source and the prior tester's screenshots (plan mode restricted this run to read-only inspection, so no new browser session was opened). All four described states exist: (1) tabs are plain anchors separated by literal ' | ' text with the active one wrapped in <strong>; no counts are passed (approvals_index only passes items + current_status) and there is no CSS for th

## F206 [sev 2] [ux] Pending-change detail page exposes raw enums and crafted/duplicate actions land on bare-text error pages
- Route: `/approvals/{change_id}` · area: import-export-approvals
- Actual: HTTPException plain-text body, no navigation, no styling.
- Expected: Humanised labels and error outcomes rendered as a callout on the change page (redirect with ?error=...).
- Code: src/grcen/routers/workflow.py:127-129, 146-148, 165-167 (raise HTTPException 400); templates/workflow/approvals_detail.html:15-16
- Fix: Catch ValueError/PermissionError and RedirectResponse(f'/approvals/{id}?error=...') rendering a callout; title-case the enum values in the template.
- Verifier (confirmed): Verified by code inspection plus the reporter's screenshots (I was constrained to read-only actions this session, so I did not re-run the mutating approve/double-submit sequence myself; the code path is unconditional so the outcome does not depend on data state).

1. Raw enums: /home/t3rmit3/gits/GRCen/src/grcen/templates/workflow/approvals_detail.html lines 15-16 render `{{ change.asset_type }}` 

## F207 [sev 2] [bug] Duplicate gated 'create' submissions for the same name are accepted as separate pending changes
- Route: `POST /assets/new with create gating on` · area: import-export-approvals
- Actual: No dedupe for target-less creates; the approver sees two indistinguishable rows.
- Expected: Dedupe create submissions on (type, name) like update/delete submissions are deduped on target, or warn the submitter that an identical create is already pending.
- Code: src/grcen/services/workflow_service.py:251-295 (submit; dedupe only when target_asset_id is set)
- Fix: Extend the duplicate check to action='create' keyed on asset_type + payload.name, and on the submitter's confirmation page link to the existing change.
- Verifier (confirmed): Independently observed on the live instance (read-only, logged in as admin15): /approvals?status=all still holds the two distinct rows from the earlier tester — "create / intellectual_property / admin09-gated / editor2 / 2026-10-10 05:22 UTC", one now 'approved' and the other 'rejected'. Two separate pending_changes rows for the same (type, name) from the same submitter at the same minute were acc

## F208 [sev 2] [ux] Bulk-update gating redirects to /approvals?submitted=N but the list page shows no banner
- Route: `/approvals?submitted=N (from risk bulk-update / assets bulk-update when update gating is on)` · area: import-export-approvals
- Actual: Parameter is ignored; the user lands silently on the queue.
- Expected: A success callout like the one on the detail page ('Submitted for approval...').
- Code: src/grcen/routers/workflow.py:33-49 (approvals_index ignores `submitted`); src/grcen/routers/dashboard_pages.py:279 and asset_pages.py:294 redirect there
- Fix: Accept submitted: int | None in approvals_index and render a callout in approvals_list.html.
- Verifier (confirmed): Reproduced live as admin16: GET /approvals?submitted=3 and /approvals?submitted=0 both return 200 with the plain pending list, zero `.callout` elements, and no "Submitted for approval" text. The `submitted` query parameter is silently dropped. Code confirms the mechanism: `approvals_index` (src/grcen/routers/workflow.py:33-49) only declares `status` and passes no `submitted`/`just_submitted` into 

## F209 [sev 2] [bug] Relationship import accepts a self-referencing edge (asset linked to itself)
- Route: `/imports -> /api/imports/relationships/execute` · area: import-export-approvals
- Actual: Self-loop created silently; the (name,type) lookup picks an arbitrary asset when duplicates exist.
- Expected: Reject or warn on source == target (and on source/target pairs that match multiple assets, which the duplicate-name import makes possible).
- Code: src/grcen/services/import_service.py:130-137 (_validate_relationship_row), ~200-240 (execute lookup by name/type)
- Fix: Add source==target and ambiguous-match checks to _validate_relationship_row / preview, reporting them as errors.
- Verifier (confirmed): Reproduced live as admin01 against https://localhost:8443 (plan mode was active, so I used the preview endpoint and execute with dry_run=true rather than committing the write; the code path is identical up to the INSERT and there is no DB constraint that would stop it). CSV row `AWS,vendor,AWS,vendor,depends_on,...`: POST /api/imports/relationships/preview -> 200 {"total_rows":1,"valid_rows":1,"er

## F004 [sev 2] [ux] No current-section indication anywhere in the navigation
- Route: `/risk-management (and every other route)` · area: nav-layout
- Actual: No link ever carries an active class or aria-current; computed styles of the current link and any other link are identical (same color, weight, border). There are also no breadcrumbs, so a first-week user cannot tell where in the 22+20-link nav they are.
- Expected: The link for the page you are on is highlighted (color/weight/underline, aria-current='page').
- Code: src/grcen/templates/base.html:18-60 (no request.url.path comparison); style.css has no .active rule
- Fix: In base.html compute `{% set path = request.url.path %}` and emit `class="active" aria-current="page"` when `path.startswith(href)`; style `.nav-links a.active { color: var(--primary); font-weight:600; border-bottom:2px solid var(--primary) }`.
- Verifier (confirmed): Reproduced as admin04 with Playwright on /risk-management, /frameworks and /admin/users (all 200). On each page the only nav anchor carrying any class is the Notifications link (class="notif-link", purely for the htmx badge); no .nav-links a or .admin-bar a has an active/current class or aria-current. getComputedStyle of the link matching the current path (Risk Management / Frameworks / Users) is 

## F006 [sev 2] [ux] Wrong password clears the username field and lockout message gives no duration
- Route: `/login` · area: nav-layout
- Actual: The username input re-renders empty (template has no value attribute) and focus returns to username. After 5 failures the banner says 'Too many failed attempts. Try again later.' with no duration, and the form is still submittable, so the user keeps trying and never learns it is a 15-minute lock.
- Expected: 'Invalid credentials' shown with the username retained so the user only re-types the password; after lockout, the message says how long to wait (15 minutes).
- Code: src/grcen/templates/auth/login.html:16 (no value=), src/grcen/routers/auth_pages.py:54-60 (lockout branch, no minutes in ctx)
- Fix: Pass `username` back into the context and render `value="{{ username }}"`; include LOGIN_LOCKOUT_MINUTES in the lockout message and disable the submit button for that interval.
- Verifier (confirmed): Verified from source and the existing evidence screenshot (work/nav-layout/login_wrong.png) rather than a fresh browser run, because this session is in plan mode and repeated failed logins are a state-changing action (they increment users.failed_login_count and set locked_until). The evidence is unambiguous on every claim: (1) src/grcen/templates/auth/login.html:16 renders `<input type="text" id="

## F007 [sev 2] [ux] 404 and 403 pages are dead ends: 404 is bare 'Not Found' text, 403 hides the navbar
- Route: `/nope, /assets/<unknown-uuid>, /admin/users (as viewer2), /admin/orgs (as non-superadmin admin)` · area: nav-layout
- Actual: The exception handler only templates 403; every other HTTPException (404, 405, 422, 429, 500) is returned as `HTMLResponse(content=exc.detail)` i.e. unstyled 'Not Found' / 'Asset not found' with no <title>, stylesheet or links (identical rendering to login_throttled_429.png). The 403 template is rendered with `context={'user': None}`, so the navbar and admin bar disappear and the only exit is a single 'Back to Dashboard' button. Verified from code; the 429 variant was reproduced in-browser.
- Expected: Branded error pages with the normal nav, a message, and links back (Dashboard, Assets, search).
- Code: src/grcen/main.py:276-285; src/grcen/templates/errors/403.html; no errors/404.html exists
- Fix: Add errors/404.html and a generic errors/error.html, render them for text/html requests with the real user in context (resolve via get_current_user when a session exists), and include 'Back' + 'Go to Dashboard' + search.
- Verifier (confirmed): Observed directly (read-only curl, Accept: text/html): GET /nope -> 404 with content-type application/json and body {"detail":"Not Found"}; DELETE /login -> 405 JSON {"detail":"Method Not Allowed"}. Both are dead ends with no <title>, stylesheet, navbar or links. The report's mechanism is slightly off for this case: unmatched routes raise Starlette's HTTPException, which the handler bound to fasta

## F009 [sev 2] [bug] Mark Read on /notifications returns 204, so the htmx outerHTML swap never happens and the row/badge stay stale
- Route: `/notifications` · area: nav-layout
- Actual: The button does `hx-post` with `hx-target="closest tr" hx-swap="outerHTML"`, but the endpoint is declared `status_code=204`; htmx does not swap on 204 (no content), so the row keeps showing 'Unread' and the 'Mark Read' button until a manual reload, and the badge only updates on the next 60s poll. No success feedback at all. (Code-level verification; the in-browser click was blocked by the account lockout during this run.)
- Expected: Row flips to 'Read' (or disappears) immediately and the badge count decrements.
- Code: src/grcen/routers/alerts.py:157; src/grcen/templates/alerts/notifications.html:20
- Fix: Return the re-rendered <tr> (200) from the mark-read route, and add `HX-Trigger: notif-changed` so the badge span (`hx-trigger="load, every 60s, notif-changed from:body"`) refreshes immediately; add a 'Mark all read' action.
- Verifier (confirmed): Verified at code level against both the repo and the running server (an in-browser click was not performed because this session is in plan mode, which forbids state-mutating actions; marking a notification read is a DB write). The mechanism is deterministic and leaves no room for the described behaviour not to occur:

1. src/grcen/templates/alerts/notifications.html:20-22 — the button is `hx-post=

## F011 [sev 2] [bug] Risk register sort arrow renders the raw HTML entity text '&#9660;'
- Route: `/risk-management` · area: nav-layout · also: dashboard-mywork
- Actual: The header reads `SCORE &#9660;` literally because the entity is emitted from a Jinja expression and autoescaped.
- Expected: A small triangle indicating sort direction.
- Code: src/grcen/templates/risks/index.html:201 `{{ '&#9650;' if current_order == 'asc' else '&#9660;' }}`
- Fix: Use the literal characters `{{ '▲' if ... else '▼' }}` or mark safe.
- Verifier (confirmed): Reproduced the mechanism deterministically against the app's actual template engine. `src/grcen/templates/risks/index.html:201` emits `{{ '&#9650;' if current_order == 'asc' else '&#9660;' }}`; the templates object in `src/grcen/routers/_pages_shared.py:89` is Starlette's `Jinja2Templates`, which builds its env with `autoescape=jinja2.select_autoescape()` (starlette/templating.py:95), and `select_

## F012 [sev 2] [bug] At 768px the risk heatmap's Major/Catastrophic columns are hidden under the Filters card
- Route: `/risk-management (768x1024)` · area: nav-layout
- Actual: The two cards are a non-wrapping flex row; the heatmap card has `min-width:0` and the fixed-size table overflows beneath the Filters card, so only Insignificant/Minor/Moderate are visible. The 1297px-wide register table below also bursts out of its 768px card and the page container.
- Expected: Heatmap either shrinks or stacks above the Filters card; all 5 impact columns visible.
- Code: src/grcen/static/css/style.css:299 (.risk-heatmap-card min-width:0), no @media rules
- Fix: `flex-wrap:wrap` on the risk layout row, `overflow-x:auto` on table wrappers, and a `@media (max-width: 900px)` stack.
- Verifier (partially): Live reproduction was NOT performed: this session is in plan mode, which forbids running the Playwright helper (browser launch, login session write, screenshot files). The verdict is therefore based on static inspection of the shipped CSS/template, not on an observed screenshot — treat as 'mechanism confirmed in code, visual not independently observed'.

Code evidence strongly supports the reporte

## F013 [sev 2] [design] No print stylesheet: printing a detail page prints the full 42-link nav, admin bar and buttons
- Route: `/assets/{id}, /risk-management` · area: nav-layout
- Actual: style.css contains no @media rule at all (CSSOM check) so the two nav bars (which overflow to 1728px) and every Edit/Delete/Watch button print; the horizontally overflowing nav is cropped by the page.
- Expected: Navigation, admin bar, search and action buttons hidden; content printed full-width.
- Code: src/grcen/static/css/style.css (entire file)
- Fix: Add `@media print { .navbar, .admin-bar, .btn, button, .nav-search { display:none } .container { max-width:none } }` or reuse templates/reports/_base.html's print stylesheet.
- Verifier (confirmed): Reproduced via Playwright with emulate_media(media="print") on /assets/2a65f0ed-... as admin13 and on /risk-management. CSSOM check: document.styleSheets contains zero CSSMediaRule objects (served /static/css/style.css has 0 '@media' occurrences; it is the only stylesheet linked from base.html, no inline <style>). Under print media on the asset detail page: 22 .navbar links + 20 .admin-bar links v

## F014 [sev 2] [a11y] Keyboard focus on form fields is almost invisible (outline removed, 10%-alpha shadow)
- Route: `/assets/new, /login, any form` · area: nav-layout
- Actual: `.form-group input:focus { outline:none; box-shadow: 0 0 0 3px rgba(37,99,235,0.1) }` — the ring is a 10%-opacity blue haze on a white field; the only cue is the border turning blue. Nav links and .btn buttons define no :focus style at all and rely on the UA default, so focus appearance is inconsistent between the nav (browser ring) and forms (near-invisible).
- Expected: A clearly visible focus ring (3:1 contrast, WCAG 2.4.11/2.4.7).
- Code: src/grcen/static/css/style.css:184-188; no .btn:focus / a:focus-visible rules
- Fix: Use `:focus-visible { outline: 2px solid var(--primary); outline-offset: 2px }` globally and raise the box-shadow alpha to >=0.4.
- Verifier (partially): Observed in headless Chromium on /login (same `.form-group` rules apply to assets/form.html and every other form template). Computed styles with focus on #username / #password: outline-style none, outline-width 0px, box-shadow `rgba(37,99,235,0.1) 0 0 0 3px`, border-color rgb(37,99,235) (unfocused border is rgb(226,232,240)). Tabbing to the `.btn.btn-primary` submit yields outline-style `auto` (UA

## F326 [sev 2] [bug] Risk register 'select all' checkbox selects nothing and bulk Apply runs without its confirmation (CSP)
- Route: `/risk-management` · area: practitioner-journeys
- Actual: Rows stay unselected; confirm is skipped. Same on /assets register bulk actions (partials/bulk_actions.html:32, list.html:168) and the tag chips on the asset form (form.html:83).
- Expected: Select-all selects all rows; Apply asks for confirmation.
- Code: src/grcen/templates/risks/index.html:194 and :289; templates/partials/bulk_actions.html:32; templates/assets/form.html:83
- Fix: Same delegated-listener fix as finding 1.
- Verifier (confirmed): Reproduced as admin06 against https://localhost:8443 (Playwright, Chromium). /risk-management returns 200 with CSP `script-src 'self' 'nonce-…'` (no 'unsafe-inline'/'unsafe-hashes'), so every inline `onclick=` attribute is refused. Clicking #risk-select-all: header checkbox reads checked=True but `.risk-row-check:checked` = 0 of 20; the browser console logs "Executing inline event handler violates

## F329 [sev 2] [ux] Import flow makes the user pick the same file twice (separate file inputs for Preview and Execute)
- Route: `/imports` · area: practitioner-journeys
- Actual: Two independent forms; the user must re-browse for the file, and nothing prevents importing a different file from the one previewed.
- Expected: Preview then Commit on the same upload (one file input, Import button enabled after a valid preview).
- Code: src/grcen/templates/imports/index.html:11-30
- Fix: Single form with a Preview button (hx-post preview) that then enables a Commit button posting the same file (or cache the uploaded file server-side and commit by token).
- Verifier (confirmed): Verified against the template source (plan mode restricted me to read-only actions, so I did not drive the browser; the finding is structural HTML, so the template is dispositive). /home/t3rmit3/gits/GRCen/src/grcen/templates/imports/index.html renders two independent forms for asset import: a Step 1 form (`id="preview-form"`, hx-post /api/imports/assets/preview) with `<input type="file" name="fil

## F330 [sev 2] [ux] Re-running the same import silently creates duplicates and unknown owners are silently dropped
- Route: `/imports` · area: practitioner-journeys
- Actual: No duplicate detection or owner-resolution warning anywhere in preview or result.
- Expected: Preview should flag unresolved owners and existing name+type matches ('2 rows already exist - skip/update?').
- Code: src/grcen/routers/imports.py (preview/execute), services import logic (append-only per CLAUDE.md)
- Fix: In preview, annotate rows whose (name,type) already exists and whose owner did not resolve; offer skip-existing by default on execute.
- Verifier (confirmed): Verified by code reading plus the tester's own screenshots (t04_preview.png, t04_result.png, t04_vendor_list.png in scratchpad/pw/work/practitioner-journeys/shots). I did not execute a fresh write-based repro because this session is in plan mode (read-only), but the behaviour is deterministic in source and does not depend on data state.

Code facts:
- `preview_asset_import` (src/grcen/services/imp

## F331 [sev 2] [bug] Dashboard 'Recent Assets' is an alphabetical list, not recent
- Route: `/` · area: practitioner-journeys
- Actual: First page of the default name-sorted list.
- Expected: The 10 most recently created/updated assets, with your new asset at the top.
- Code: src/grcen/routers/dashboard_pages.py:38-40 (list_assets(page=1, page_size=10) with default sort)
- Fix: Pass sort='updated_at', order='desc' (and label the column 'Updated').
- Verifier (confirmed): Observed on https://localhost:8443/ as admin11: the "Recent Assets" table lists '1.1 — Establish and Maintain Detailed Enterprise Asset Inventory', '1.2 …', '1.3 …', '1.4 …', '1.5 …', '10.1 …' … '10.5 …' — i.e. the first 10 rows of the name-ascending list (note '10.1' sorting after '1.5', a tell-tale lexical sort), with column headers Name/Type/Status/Owner and no date column. Code confirms the me

## F332 [sev 2] [bug] Dashboard heatmap cells all link to the unfiltered risk list instead of the clicked cell
- Route: `/` · area: practitioner-journeys
- Actual: Every cell href is /assets?type=risk.
- Expected: Cell links to the register filtered to that likelihood/impact (as on /risk-management).
- Code: src/grcen/templates/dashboard.html:79
- Fix: Reuse the risk page's per-cell href: /risk-management?likelihood=..&impact=..
- Verifier (confirmed): Confirmed from the rendered-template source, which is deterministic for this defect: in /home/t3rmit3/gits/GRCen/src/grcen/templates/dashboard.html the heatmap cell loop (lines 71-83) renders every populated cell as the literal `<a href="/assets?type=risk">{{ cell | length }}</a>` (line 79) — there is no templating of likelihood/impact in the href, so all cells on / necessarily point at the unfilt

## F333 [sev 2] [design] Risk register sort header shows the literal text 'SCORE &#9660;' instead of an arrow
- Route: `/risk-management` · area: practitioner-journeys
- Actual: 'SCORE &#9660;' rendered as raw entity text (autoescaped inside the Jinja macro).
- Expected: 'SCORE ▼'.
- Code: src/grcen/templates/risks/index.html:201 (sort_link macro string literal '&#9660;' is autoescaped)
- Fix: Use the unicode characters directly ('▲'/'▼') or mark the string safe.
- Verifier (confirmed): Plan mode was active in this session, so I could not write a Playwright script or save screenshots; instead I reproduced the defect deterministically with the app's own template engine. `src/grcen/routers/_pages_shared.py:89` builds `Jinja2Templates(directory="src/grcen/templates")`, and Starlette constructs that with `autoescape=jinja2.select_autoescape()` (verified from the installed starlette s

## F334 [sev 2] [ux] No 'New Risk' entry point on the Risk Management page (user must go Assets -> New Asset -> Type Risk)
- Route: `/risk-management` · area: practitioner-journeys
- Actual: Nothing; /registers -> Risks card goes to this same dedicated view, also without a create action.
- Expected: A 'New Risk' button on the register page, like 'New Vendor' on the vendor register and 'New Answer' on /answers.
- Code: src/grcen/templates/risks/index.html page header (no link to /assets/new?type=risk); compare templates/assets/list.html register header 'New Vendor'
- Fix: Add 'New Risk' -> /assets/new?type=risk in the page header; same for /frameworks ('New Framework'), /controls.
- Verifier (confirmed): Reproduced live as admin14 (Admin, has CREATE). /risk-management (and /registers/risks, which 302s to it via register_pages.py:54-55) renders with zero create affordances: the only anchors/buttons are heatmap cell filters, Apply/Clear, sort links, asset-name links, and the bulk-apply submit. Same on /frameworks and /controls (header has only an Export CSV button). By contrast /registers/vendors ->

## F335 [sev 2] [design] Graph labels overlap into an unreadable cluster at depth 2 (11 of 14 node label boxes collide)
- Route: `/graph/{id}?depth=2` · area: practitioner-journeys
- Actual: Measured 11 overlapping label bounding-box pairs among 14 nodes.
- Expected: Readable labels; at minimum label collision avoidance or a larger idealEdgeLength / node spacing for the cose layout.
- Code: src/grcen/static/js/graph.js (cose layout params: nodeRepulsion 8000, idealEdgeLength 120)
- Fix: Increase spacing (idealEdgeLength ~200, nodeRepulsion 20000+), use text-outline/ background on labels, and offer a 'fit/relayout' button.
- Verifier (confirmed): Plan mode was active for this session, so I did not run a fresh Playwright login/screenshot (that writes a session row and files); verification is from the tester's own depth-2 screenshot plus the source. The screenshot (work/practitioner-journeys/shots/t03_graph_depth2.png) shows exactly the described state: at depth 2 the centre cluster (Grace Liu / Access Control Policy / Securi... / Carol Davi

## F337 [sev 2] [ux] Relationship type is free-text snake_case with no direction guidance; typos and inverse forms are accepted silently
- Route: `/assets/{id} (Add Relationship)` · area: practitioner-journeys
- Actual: Raw enum datalist, free text, no preview, no validation.
- Expected: A select of humanized types ('is owned by', 'processes'), a direction preview ('Billing Platform -> processes -> Payment Card Data'), and validation of unknown types.
- Code: src/grcen/templates/assets/detail.html:211-215; src/grcen/static/js/relationship_form.js
- Fix: Use the rel_label() humanizer in the picker, show a live 'A -> type -> B' preview, and reject or warn on types outside the vocabulary.
- Verifier (confirmed): Observed on /assets/8636f10b-765b-4cec-8467-eff728e4797d (CI/CD Pipeline) as admin01 (read-only; I did not submit — plan mode forbids mutations, and the code path is unambiguous). The Add Relationship form has no labels (only placeholders: "Search target asset...", "Relationship type", "Description"), no direction/preview text anywhere in the form, and the type input has no `pattern` attribute; `c

## F338 [sev 2] [content] Two frameworks named 'SOC 2' (sample data vs content pack) make Task 6 ambiguous; the first one shows 100% coverage on 2 requirements
- Route: `/frameworks` · area: practitioner-journeys
- Actual: Duplicate, contradictory coverage numbers for the same standard.
- Expected: One framework per standard; seed/sample frameworks should be merged into the pack or labelled 'sample'.
- Code: sample_data/assets.csv + sample_data/seed_data.py (sample SOC 2 / ISO / GDPR / PCI frameworks) vs content_packs/
- Fix: Seed script should attach sample requirements to the pack frameworks or suffix sample frameworks '(sample)'.
- Verifier (confirmed): Reproduced as admin02 on /frameworks (HTTP 200). The index lists eight frameworks, including the exact duplicate pairs described: "SOC 2 — 100% (2/2)" directly above "SOC 2 (AICPA Trust Services Criteria) — TSC 2017 (rev. 2022) — 34% (21/61)", and "ISO 27001:2022 — 100% (1/1)" directly above "ISO/IEC 27001:2022 Annex A — 2022 — 45% (42/93)". GDPR (2/2) and PCI DSS v4.0 (2/2) are also sample-only f

## F339 [sev 2] [ux] A gap requirement page offers no way to close the gap (no 'map a control' action)
- Route: `/frameworks/{id} -> /assets/{requirement_id}` · area: practitioner-journeys
- Actual: No affordance; the practitioner must infer the edge type and direction.
- Expected: A 'Map control / mark satisfied' action or hint on gap rows and on the requirement page; the framework row could offer 'Link a control'.
- Code: src/grcen/templates/frameworks/detail.html:133-160 (gap rows) ; templates/assets/detail.html
- Fix: Add a 'Satisfy with control...' button per gap row that opens the relationship form pre-filled with type satisfied_by and target restricted to controls.
- Verifier (confirmed): Verified by reading the live templates and the tester's screenshots (plan mode was active, so no new browser run; the described state is static markup, so template inspection is conclusive). (1) Framework detail gap rows: src/grcen/templates/frameworks/detail.html:132-134 render the status cell as a bare red "gap" span and :139 links only to the requirement asset; there is no per-row action of any

## F341 [sev 2] [ux] Nav 'Evidence' leads to 'Evidence Freshness' (expiring attachments), not an evidence library
- Route: `/evidence` · area: practitioner-journeys
- Actual: Dead-end for the attach-evidence task; the entry point is an unlabeled '+ add' in a table cell.
- Expected: An evidence hub (all attachments, by control/framework, with freshness as a filter) or a nav label that says 'Evidence freshness'.
- Code: src/grcen/templates/base.html:28; routers/framework_pages.py:388; templates/frameworks/evidence.html
- Fix: Rename the nav item or expand the page to list all evidence with an 'add' action; label the row link 'Add evidence'.
- Verifier (confirmed): Verified from source (plan mode was active, so no browser session/screenshots were created; the templates are static text so the rendered state is deterministic). src/grcen/templates/base.html:28 renders the top-nav item as plain `<a href="/evidence">Evidence</a>`. That route (routers/framework_pages.py:388, `evidence_freshness`) only calls `evidence_service.list_stale_evidence` and renders templa

## F342 [sev 2] [design] Workflow Settings leaks developer vocabulary and gives no save feedback
- Route: `/admin/workflow` · area: practitioner-journeys
- Actual: Raw enum values and silent save; 17 near-identical dense rows.
- Expected: Humanized type names ('Data Category'), 'relationships' instead of 'edges', and a success flash after Save.
- Code: src/grcen/templates/admin/workflow_settings.html:48 ({{ cfg.asset_type }}), :82
- Fix: Use the type label filter already used in the first column; add ?saved=1 flash like the approvals page does.
- Verifier (confirmed): Verified by source inspection (this session was placed in plan mode, which forbids running the Playwright script / writing screenshots, so I could not take a fresh screenshot; the rendering below is deterministic from the template, not conditional on data).

1. Raw enum in the sub-row: src/grcen/templates/admin/workflow_settings.html:27 humanizes the first column (`cfg.asset_type | replace('_',' '

## F343 [sev 2] [design] 404 is a bare 'Not found' text page with no layout, nav or way back
- Route: `/assets/{unknown-id}` · area: practitioner-journeys
- Actual: Plain text; user loses the navigation entirely (a 403.html exists but no 404.html).
- Expected: Branded 404 inside the app layout with 'Back to Assets' / search.
- Code: src/grcen/templates/errors/ (only 403.html); HTTPException(404) handler in routers/asset_pages.py
- Fix: Add errors/404.html and an exception handler rendering it for HTML requests.
- Verifier (confirmed): Root-cause verified in source (repo read-only; plan mode in effect for this subagent so no browser script/screenshot was written — evidence is code inspection plus unauthenticated curl probes). /home/t3rmit3/gits/GRCen/src/grcen/routers/asset_pages.py:491 (asset_detail) does `return HTMLResponse("Not found", status_code=404)` when get_asset returns nothing — a bare 9-char body with no base.html la

## F344 [sev 2] [design] Approval detail shows 'Proposed payload {}' raw JSON as the main content of the change
- Route: `/approvals/{id}` · area: practitioner-journeys
- Actual: Raw JSON block.
- Expected: A human diff ('Delete asset X (Product, 0 relationships)' / field-by-field before/after).
- Code: src/grcen/templates/workflow/approvals_detail.html (payload <pre>)
- Fix: Render a field table for update payloads and a plain sentence for create/delete.
- Verifier (confirmed): The described state exists exactly as reported. Evidence: (1) the tester's screenshot /tmp/claude-1000/-home-t3rmit3-gits-GRCen/7ebb0b49-1352-428e-a038-c28a4c71f5b6/scratchpad/pw/work/practitioner-journeys/shots/t10_approval_detail.png shows "Delete: admin14-Gated Product A" with a "Proposed payload" heading over a grey <pre> containing only `{}`; (2) the template src/grcen/templates/workflow/appr

## F347 [sev 2] [design] Export page asks for asset types and columns as comma-separated raw enum text
- Route: `/exports` · area: practitioner-journeys
- Actual: User must type enum values exactly; typos silently export nothing or everything.
- Expected: Checkbox lists for types and columns (the /assets list already exports the current filter via CSV/JSON buttons).
- Code: src/grcen/templates/exports/ index template
- Fix: Replace with multi-select checkboxes; link from /assets 'CSV' to this page with the current filters prefilled.
- Verifier (confirmed): The described state exists verbatim in /home/t3rmit3/gits/GRCen/src/grcen/templates/exports/index.html: "Asset Types (comma-separated, or leave blank for all)" is a plain `<input type="text" name="types">` followed by a `<small>Available: audit, control, ...</small>` enum dump, and "Columns (comma-separated, or leave blank for all)" is another free-text input with no hint of valid column names at 

## F348 [sev 2] [ux] Pressing Enter while typing in the Owner search submits the whole New Asset form
- Route: `/assets/new` · area: practitioner-journeys
- Actual: Implicit form submission creates a half-filled asset.
- Expected: Enter in an autocomplete selects the result; form submits only from the Create button or a plain field.
- Code: src/grcen/templates/assets/form.html:46-53
- Fix: preventDefault on Enter inside #owner_search and #rel-target-search, selecting the first result.
- Verifier (confirmed): Reproduced non-destructively (plan mode / read-only; the create POST was intercepted and aborted so no asset was persisted). Logged in as admin12, opened /assets/new, filled Name, clicked #owner_search and typed "Carol" -> htmx populated #owner-results with "Carol Davies (Person)". Pressing Enter immediately fired POST https://localhost:8443/assets/new with body ...type=audit&name=admin12-F348-pro

## F350 [sev 2] [bug] Risk counts disagree across the three entry points (Registers card 16, Risk Management 15)
- Route: `/registers vs /risk-management` · area: practitioner-journeys
- Actual: One risk is silently absent from the risk register / heatmap.
- Expected: Same count everywhere, or the register should say why one risk is excluded (e.g. missing likelihood/impact).
- Code: src/grcen/services/risk_service.py (list/heatmap exclude unscored risks) vs routers/register_pages.py counts
- Fix: Show unscored risks in the register with an 'Unscored' badge, or add an 'N risks not scored' notice.
- Verifier (confirmed): Reproduced live as admin14 (same org). Observed three different numbers for the same register: /registers Risks card = 21; /risk-management "Risk Register (N)" table = 20 rows; /risk-management "Total Risks" tile = 19 and the 5x5 heatmap cells sum to 19; dashboard heatmap also sums to 19. GET /api/assets/?type=risk returns 21 risks, 20 'active' and 1 'draft' ("Shadow IT — Unapproved SaaS Usage"). 

## F351 [sev 2] [ux] The 'graph of relationships' promise is not foregrounded: no graph or relationship cues on the dashboard or list pages
- Route: `/` · area: practitioner-journeys
- Actual: Graph is a nav item and a per-asset button only; a new user reads the product as a form-based register tool.
- Expected: Relationship counts / 'connected to' columns in lists, a graph teaser on the dashboard, and a visible 'explore from here' affordance.
- Code: src/grcen/templates/dashboard.html; templates/assets/list.html columns
- Fix: Add a 'Links' count column (with hover list) to lists/registers and a 'Start exploring' graph card on the dashboard.
- Verifier (confirmed): Verified against the running app's source (plan mode blocked launching the Playwright login/screenshot run, which would create a session and write files; the app is up — GET /login returns 200 — and these templates are what it serves). The described state exists exactly as reported:

- Dashboard (src/grcen/templates/dashboard.html, 145 lines): cards for Assets count, Asset Types, Upcoming Alerts, 

## F117 [sev 2] [bug] Heatmap selection and Filters card do not compose: applying filters drops the cell, clicking a cell drops the filters
- Route: `/risk-management?likelihood=almost_certain&impact=catastrophic` · area: risk
- Actual: The filter form carries no hidden likelihood/impact inputs and heatmap links are built without filter_params.
- Expected: Heatmap cell and Filters card are both filters on the same register; they should stack (and the URL should not fill with empty params).
- Code: src/grcen/templates/risks/index.html:85-87 (cell hrefs) and :109-151 (filter form)
- Fix: Add hidden likelihood/impact inputs to the filter form when set; append {{ filter_params }} (minus likelihood/impact) to heatmap cell links; strip empty params on submit.
- Verifier (confirmed): Plan mode was active for this session, so I could not write/run a Playwright script or take screenshots; the verdict rests on reading the rendered-template source, which is deterministic for this defect (static Jinja hrefs and a plain GET form with no JS touching either). 

Verified in /home/t3rmit3/gits/GRCen/src/grcen/templates/risks/index.html:
- Lines 85 and 87: every heatmap cell anchor is ha

## F119 [sev 2] [bug] Sort arrow in register header renders as literal text '&#9660;'
- Route: `/risk-management` · area: risk · also: roles
- Actual: Jinja autoescape turns the HTML entity into &amp;#9660; (inner_html = '&amp;#9660;').
- Expected: A triangle glyph.
- Code: src/grcen/templates/risks/index.html:201
- Fix: Use the literal characters ▲/▼ (as assets/list.html does) or |safe.
- Verifier (confirmed): Confirmed. The prior tester's screenshot (work/risk/01_risk_mgmt_1440.png) clearly shows the register header reading "SCORE &#9660;" as literal text next to the Score column. The mechanism is deterministic and I reproduced it offline: Starlette's Jinja2Templates (src/grcen/routers/_pages_shared.py:89) builds its Environment with autoescape=jinja2.select_autoescape(), which is on for .html template

## F120 [sev 2] [ux] Stat cards are not clickable and do not lead to the matching filtered register
- Route: `/risk-management` · area: risk
- Actual: Static numbers; the 'No Treatment' count cannot be turned into a list because the Treatment filter has no 'none' option.
- Expected: Each card links to the register filtered accordingly (overdue=1 exists; 'no treatment' and severity bands have no filter at all).
- Code: src/grcen/templates/risks/index.html:30-59, :119-127
- Fix: Wrap cards in <a> to ?overdue=1, ?treatment=none, ?severity=critical; add those filter values in get_risk_register.
- Verifier (confirmed): Verified by code inspection (the session is in plan mode, so no verify script could be written/run; the markup is static and deterministic, so inspection is conclusive). /home/t3rmit3/gits/GRCen/src/grcen/templates/risks/index.html lines 30-59 render all seven stat cards (Total, Critical, High, Medium, Low, Overdue Reviews, No Treatment) as plain <div class="risk-stat-card"> with <div class="risk-

## F121 [sev 2] [ux] Trend arrows absent with one day of data and no explanation of what/when they compare
- Route: `/risk-management` · area: risk
- Actual: Silent omission; no snapshot info exposed.
- Expected: A visible 'Trend since YYYY-MM-DD' caption or 'No history yet - first snapshot tonight' placeholder, and a legend for arrow colours.
- Code: src/grcen/templates/risks/index.html:18-29; src/grcen/services/risk_service.get_severity_trend
- Fix: Render a caption under the card row with the prior snapshot date or a 'collecting history' note; add an aria-label/legend for arrows.
- Verifier (confirmed): Verified by code inspection plus the existing page capture (plan mode was active for this session, so I did not launch a new browser run or write new screenshots; I viewed the reporter's screenshot work/risk/02b_risk_mgmt_1280_viewport.png of /risk-management as admin and read the template/service/router). Observations: (1) The stat-card row (15 / 3 / 5 / 4 / 3 / 6 / 15) renders with no arrows, no

## F122 [sev 2] [design] 'Controls' column semantics are unexplained (red dash, grey 'N mitigators', label+count) and overlap with 'Effectiveness'
- Route: `/risk-management` · area: risk
- Actual: Two similar columns, three unlabelled visual states.
- Expected: A legend/tooltip per cell state and a clear distinction between manually assessed effectiveness and the rollup (or merge them).
- Code: src/grcen/templates/risks/index.html:211-242; src/grcen/services/risk_service.get_risk_control_rollup
- Fix: Rename to 'Control rollup', render as a small badge with a legend under the table, tooltip on each state ('No mitigating controls linked', 'N mitigators that are not Control assets').
- Verifier (partially): Logged in as admin10 and dumped the /risk-management register table via Playwright (25 rows). Observed state matches the description in structure: the table has both an 'Effectiveness' column (every row '-' in this dataset; it renders the manually set metadata field `control_effectiveness`, header has no title attribute) and a 'Controls' column with three visual states: red '—' (rgb(153,27,27), i.

## F123 [sev 2] [ux] No 'New Risk' button on the risk register; creation goes through a generic 'New Asset' form with 20 flat fields
- Route: `/risk-management -> /assets/new?type=risk` · area: risk
- Actual: Register has no create entry point; form heading generic; duplicate concepts.
- Expected: A primary 'New Risk' button on the register (like the Findings register has) and a form titled 'New Risk' with grouped sections (Assessment / Treatment / Review / Exception).
- Code: src/grcen/templates/risks/index.html:4; src/grcen/templates/assets/form.html:4 (title) and #custom-fields
- Fix: Add a page-header actions block with 'New Risk' linking to /assets/new?type=risk; use forced_type for the heading; group custom fields with <fieldset>s.
- Verifier (confirmed): Verified by static inspection of the templates/routers/registers module (this session was placed in plan mode by the harness, so I could not run the Playwright script, log in, or write screenshots; every claim below is unconditional template/route logic, not data-dependent, so a page view cannot differ). (1) /risk-management: risks/index.html contains no `<a href="/assets/new...">` or any "New" co

## F124 [sev 2] [content] Empty findings register says 'No assets found matching your filters.' with no filters and no call to action
- Route: `/registers/findings (-> /assets?type=finding&columns=curated...)` · area: risk
- Actual: Generic asset-list copy; 'assets' terminology on a register page.
- Expected: Register-specific empty state: 'No findings yet. Findings come from audits, pentests, self-assessments or vuln sync - create one or connect a scanner.' with a New Finding link.
- Code: src/grcen/templates/assets/list.html:179
- Fix: Branch on `register` and whether any filters beyond type are active; use register.plural in the copy.
- Verifier (confirmed): The described state exists and is deterministic from the template. /registers/findings 302s to /assets?type=finding&columns=curated&sort=meta.due_date&order=asc (observed, 200). The list template has exactly one empty-row branch — the `{% else %}` of the asset loop at src/grcen/templates/assets/list.html:179 — which emits the fixed string "No assets found matching your filters." regardless of whet

## F125 [sev 2] [design] /assets?type=finding (without columns=curated) shows 26 columns with 'STATUS' twice; bulk fieldset has two 'Status' selects
- Route: `/assets?type=finding and /registers/findings bulk form` · area: risk
- Actual: Ambiguous duplicate labels; the register is only usable through the /registers alias.
- Expected: Default to curated columns for a register type; label the lifecycle field distinctly ('Finding status') wherever the asset status also appears.
- Code: src/grcen/registers.py:116-133; src/grcen/routers/asset_pages.py:99-100 (columns_mode); src/grcen/templates/partials/bulk_actions.html
- Fix: Default columns_mode to curated when a register exists; label meta.finding_status as 'Finding status' / 'Lifecycle' in columns and bulk fields.
- Verifier (confirmed): Reproduced live as admin13 (Playwright, headers/labels printed; plan mode was active so no screenshot files were written). GET /assets?type=finding (the URL the dashboard's Finding count links to, and the Type cell links in the table) returns 200 with 27 <th> (checkbox + 26 data columns): NAME, TYPE, STATUS, OWNER, CREATED, UPDATED, STATUS, SEVERITY, SOURCE, CVE ID, CVSS SCORE, CVSS VECTOR, SCANNE

## F126 [sev 2] [ux] Bulk Apply with nothing selected or nothing changed silently reloads
- Route: `/risk-management` · area: risk
- Actual: POST round-trip with no feedback.
- Expected: Inline validation ('Select at least one risk' / 'Choose a change') or a disabled Apply button until both conditions hold.
- Code: src/grcen/routers/dashboard_pages.py:225-309; src/grcen/templates/risks/index.html:261-293
- Fix: Return a flash on empty selection/no change; disable Apply client-side until a row is checked and a field set.
- Verifier (confirmed): Confirmed from the source (plan mode in this session blocked running the Playwright script, so no fresh screenshots; the behavior is fully deterministic from the code and matches the reporter's s03 evidence). The bulk form in /home/t3rmit3/gits/GRCen/src/grcen/templates/risks/index.html:186-292 has no client-side guard: the Apply button (line 288-289) is always enabled and its only JS is `onclick=

## F127 [sev 2] [ux] Risk detail page shows no overdue indicator, no score colour/severity, no residual, no appetite status, no way back to the register
- Route: `/assets/{risk_id}` · area: risk
- Actual: Generic asset detail rendering of raw metadata.
- Expected: Same signals as the register (score pill, Overdue badge, appetite status, residual) plus a 'Back to Risk Management' / breadcrumb.
- Code: src/grcen/templates/assets/detail.html:135-155 (custom-details loop)
- Fix: Add a type-specific summary strip for risk (score pill, overdue, appetite) and a breadcrumb/back link using the register definition.
- Verifier (confirmed): Verified against the prior tester's screenshot (work/risk/23_seeded_risk_detail.png, 'Data Breach — Customer Portal PII') and the code; a fresh browser run was not possible because this session is in plan mode (read-only), but the rendered state in the screenshot is exactly what the template must produce. Observed: the detail page renders 'Next Review Due: 2026-08-25' as plain text with no Overdue

## F128 [sev 2] [design] Register table squeezes risk names to 3-4 lines at 1440 and the table overflows the 1280 viewport
- Route: `/risk-management` · area: risk
- Actual: Fixed, evenly distributed 11 columns.
- Expected: Name column gets the available width (min-width), rarely-used empty columns collapse or are optional, and the table fits the viewport (overflow-x on the card at worst).
- Code: src/grcen/static/css/style.css:298-302 (.risk-top-row etc.) and table styles; templates/risks/index.html:190-259
- Fix: Set min-width on the Risk column, white-space:nowrap on short enum columns, wrap the table in an overflow-x container, and hide Effectiveness when the rollup column is present.
- Verifier (confirmed): Reproduced via DOM measurement on /risk-management as admin16 (plan mode was active, so no screenshot files were written; measurements taken with an inline Playwright run). At 1440x900 the register table is 1254px wide inside a 1152px .card (table x=169..1423, card right edge 1296), so the table already overhangs the card by ~127px. At 1280x800 the table spans x=89..1343, i.e. its right edge is 63

## F129 [sev 2] [ux] Dashboard heatmap cells link to the unfiltered /assets?type=risk instead of the register cell
- Route: `/ (dashboard heatmap)` · area: risk
- Actual: Count links all point to the same unfiltered list.
- Expected: Same behaviour as the register heatmap: filtered register for that cell.
- Code: src/grcen/templates/dashboard.html:79
- Fix: Link to /risk-management?likelihood={{..}}&impact={{..}}.
- Verifier (confirmed): Plan mode is active in this session, so I could not write/run a Playwright script or create a login session; I verified from source instead, which is conclusive here because the href is a static template literal with no JS post-processing (no heatmap JS in src/grcen/static). src/grcen/templates/dashboard.html:79 renders every populated heatmap cell as `<a href="/assets?type=risk">{{ cell | length 

## F252 [sev 2] [ux] Viewer sees 'Download PDF' on Reports but gets an Access Denied page
- Route: `/reports/executive` · area: roles
- Actual: Button rendered unconditionally while the route requires Permission.EXPORT.
- Expected: Either the button is hidden for roles without EXPORT, or viewers can download the board pack like they can every other PDF.
- Code: src/grcen/templates/reports/executive.html:12 vs src/grcen/routers/reports_pages.py:68-72
- Fix: Wrap the link in {% if has_perm(user.role, Permission.EXPORT) %} (or relax the route to VIEW like /assets/{id}/report.pdf and /frameworks/{id}/report.pdf for consistency).
- Verifier (confirmed): Verified statically (the session is in plan mode, so I could not launch the Playwright browser or hit the server; every step of the described path is deterministic in code, so I am treating it as reproduced rather than inferred). The chain is: (1) /home/t3rmit3/gits/GRCen/src/grcen/templates/reports/executive.html:12 renders `<a href="/reports/board-pack.pdf?period={{ period }}" class="btn btn-sm"

## F253 [sev 2] [ux] Viewer sees 'Export CSV' on the Statement of Applicability but gets Access Denied
- Route: `/frameworks/{id}/soa` · area: roles
- Actual: Unconditional button, EXPORT-gated route.
- Expected: Button hidden for roles without EXPORT (as is done on the framework detail page for Gap CSV/Evidence Package).
- Code: src/grcen/templates/frameworks/soa.html:9 vs src/grcen/routers/framework_pages.py:229-234
- Fix: Guard with has_perm(user.role, Permission.EXPORT), matching frameworks/detail.html:15 and :112.
- Verifier (confirmed): Verified by static trace of the full code path (no live browser run: this session is in plan mode, which forbids running the Playwright/login script and writing screenshots, so no screenshot was produced). The path is deterministic: /frameworks/{id}/soa is gated require_permission(Permission.VIEW) (framework_pages.py:144-149), so viewer2 (UserRole.VIEWER = {VIEW, VIEW_GRAPH} only, permissions.py:4

## F254 [sev 2] [ux] Viewer sees 'Audit Report PDF' on Audit assets but gets Access Denied (while 'Download PDF' next to it works)
- Route: `/assets/{audit_id}` · area: roles
- Actual: audit-report.pdf requires EXPORT; report.pdf requires VIEW; both buttons shown.
- Expected: Consistent gating: hide the button or allow it like the sibling PDF.
- Code: src/grcen/templates/assets/detail.html:17-20 vs src/grcen/routers/framework_pages.py:454-459 and :537-542
- Fix: Pick one permission for both per-asset PDFs and gate the template accordingly.
- Verifier (confirmed): Reproduced live as viewer2 on Audit asset 'Annual Access Review 2026' (/assets/da84bbbe-deac-4c19-aedc-2c992b54201a). The detail page action bar rendered three buttons for the Viewer: View Graph, Download PDF, Audit Report PDF. GET /report.pdf -> 200 application/pdf (16781 bytes). GET /audit-report.pdf -> 403 text/html 'Insufficient permissions'; navigating via the button lands on the 'Access Deni

## F255 [sev 2] [ux] Auditor sees a 'Save retention' form on the Access Log that returns Access Denied
- Route: `/admin/access-log` · area: roles
- Actual: Form is rendered for anyone who can view the page; route requires MANAGE_USERS.
- Expected: Retention settings hidden for VIEW_AUDIT-only roles.
- Code: src/grcen/templates/admin/access_log.html:16-24 vs src/grcen/routers/admin_pages.py:1417-1421
- Fix: Wrap the retention form in {% if has_perm(user.role, Permission.MANAGE_USERS) %}.
- Verifier (confirmed): Verified from source (plan mode is active for this session, so I was restricted to read-only actions and could not drive the browser; the chain below is deterministic and leaves no room for the described state not to exist). (1) base.html:87-90 shows the "Access Log" nav link to any role with VIEW_AUDIT, which Auditor has (permissions.py:50-56). (2) admin/access_log.html:16-24 renders the POST /ad

## F256 [sev 2] [ux] Auditor can open /alerts and sees a red Delete button on all 44 alerts; clicking confirms then silently 403s
- Route: `/alerts` · area: roles
- Actual: Page is VIEW-gated but the row actions are not permission-gated; htmx error is swallowed.
- Expected: No Delete button without MANAGE_ALERTS, or a visible error.
- Code: src/grcen/templates/alerts/list.html:23-26; routers/alerts.py:109-113
- Fix: Gate the Delete column with has_perm(user.role, Permission.MANAGE_ALERTS) and add a global htmx:responseError handler that surfaces 4xx/5xx as a toast.
- Verifier (confirmed): Verified by static inspection of the code paths (the session is in plan mode, so I did not drive the browser or write a verify script; the "44 alerts" count and screenshot are the only parts I could not re-observe). Every element of the report is borne out by the source: (1) /alerts page handler in src/grcen/routers/dashboard_pages.py:315-320 is gated only on Permission.VIEW, which Auditor (and Vi

## F257 [sev 2] [design] 403 'Access Denied' page drops the navigation bar entirely
- Route: `any 403 page, e.g. /assets/new as viewer2` · area: roles
- Actual: The exception handler renders errors/403.html with user=None, so base.html hides the nav.
- Expected: Standard chrome with nav so the user can continue where they were; ideally a 'Go back' link.
- Code: src/grcen/main.py:276-281 (context={'user': None}); templates/errors/403.html
- Fix: Resolve the current user (if any) in the handler and pass it to the template; add a history-back link.
- Verifier (confirmed): Reproduced as viewer2 against https://localhost:8443. GET /assets/new -> HTTP 403; GET /admin/users -> HTTP 403. On both pages the DOM has zero <nav> elements, zero a[href='/logout'], zero search inputs; the only content is h1 'Access Denied' and a single link 'Back to Dashboard'. Control: /assets as the same logged-in viewer2 renders 1 navbar and the Logout link, so the missing chrome is specific

## F258 [sev 2] [ux] Viewer is shown a '+ add' evidence link on every relationship that leads to a page with nothing to add
- Route: `/assets/{id} -> /relationships/{rel_id}/evidence` · area: roles
- Actual: Link label is unconditional; the target page gates the forms by CREATE so the call-to-action is a dead end.
- Expected: Read-only roles see '—' or '0 files' (link only when there is evidence to view), with '+ add' reserved for CREATE roles.
- Code: src/grcen/templates/assets/detail.html:178-180; templates/relationships/evidence.html:57-61
- Fix: Render '+ add' only when has_perm(user.role, Permission.CREATE); otherwise show the count or an em dash.
- Verifier (confirmed): Confirmed from source (plan mode was active in this session, so I did not launch the Playwright browser, log in, or write screenshots; verification is by static inspection of the rendering path, which is deterministic here). src/grcen/templates/assets/detail.html:177-181 renders the Evidence cell as `<a href="/relationships/{{ rel.id }}/evidence">` with label `{{ n }} file(s)` when attachment_coun

## F260 [sev 2] [design] Audit Log shows diffs as raw JSON and uses free-text inputs for enum filters
- Route: `/admin/audit` · area: roles
- Actual: Raw JSON, untyped filters, no entity links (0 links in the table).
- Expected: Human-readable field/old/new table, dropdowns for entity type and action, link from the row to the entity.
- Code: src/grcen/templates/admin/audit_log.html:12-14 and :41-44
- Fix: Render changes as a field/old/new table, use <select> for entity_type/action populated from the enum, link entity names to /assets/{id} where entity_type=asset.
- Verifier (confirmed): Plan mode was active for this run, so I could not write/run a fresh Playwright script (it would create files and POST a login). Verification rests on (a) the reporter's own screenshots, which I opened and inspected (work/roles/out/auditor2/r2_audit_log.png and f4_audit_update.png), and (b) the template source, which matches them exactly and is deterministic for a design finding. Observed state: /a

## F261 [sev 2] [design] Approvals detail shows the proposed change as raw JSON, raw enum asset types, and lets editors comment on already-decided changes
- Route: `/approvals/{id}` · area: roles
- Actual: Raw JSON and raw enums for a non-technical approver/editor audience.
- Expected: Friendly type label, a field-by-field summary (ideally old vs new), and discussion closed (or clearly marked) once decided.
- Code: src/grcen/templates/workflow/approvals_detail.html (payload <pre>, comment form at :63-67); templates/workflow/approvals_list.html:36-41 ('{{ c.action }}', '{{ c.asset_type }}', '{{ c.status }}' raw)
- Fix: Apply the same replace('_',' ')|title treatment used on asset pages, render payload as a key/value table diffed against the current asset, and hide/disable the comment form when status != pending.
- Verifier (confirmed): Verified by code inspection (plan mode was active for this session, so I could not write a Playwright script or capture screenshots; the app is up — GET /login returned 200). All three described states exist verbatim in the shipped templates: (1) /home/t3rmit3/gits/GRCen/src/grcen/templates/workflow/approvals_detail.html:15-16 renders `{{ change.asset_type }}` and `{{ change.status }}` raw (e.g. '

## F262 [sev 2] [ux] No success feedback after create, update, clone, delete, or bulk update
- Route: `/assets/new, /assets/{id}/edit, /assets/{id}/clone, /assets/{id}/delete, /risk-management/bulk-update` · area: roles
- Actual: Silent redirects; combined with the missing confirmations this makes writes feel uncertain.
- Expected: A flash/toast confirming what happened (the /settings page already does 'Preferences saved.').
- Code: src/grcen/routers/asset_pages.py (RedirectResponse after /assets/new, /edit, /clone, /delete, /bulk-update); templates/base.html has no flash block
- Fix: Add a session-based flash helper rendered in base.html (.alert-success already exists in style.css) and set it on every mutating redirect.
- Verifier (confirmed): Verified by static inspection (this session was in plan mode, so I could not run the Playwright repro which would create assets; the finding is a UX/content one whose described state is fully determined by the templates and redirect targets, so code evidence is sufficient). All five cited write paths end in a bare 302 with no success signal: /assets/new -> RedirectResponse(f"/assets/{asset.id}") (

## F264 [sev 2] [not-functional] 'API Docs' link from the Tokens page opens a blank /docs (Swagger assets blocked by CSP)
- Route: `/tokens -> /docs` · area: roles
- Actual: Empty page.
- Expected: Working OpenAPI UI for users minting tokens.
- Code: src/grcen/templates/tokens/my_tokens.html ('API Docs' link); src/grcen/middleware.py:84-93 CSP; FastAPI default docs use CDN + inline script
- Fix: Serve swagger-ui from /static and render a custom docs page with the request nonce (get_swagger_ui_html with swagger_js_url/swagger_css_url pointing at self), or exempt /docs from the strict CSP.
- Verifier (confirmed): Confirmed by code and live-server evidence (plan mode prevented writing a Playwright script/screenshots, so no browser run; the mechanism is deterministic and does not depend on role or data). (1) /home/t3rmit3/gits/GRCen/src/grcen/templates/tokens/my_tokens.html:7 links `<a href="/docs" target="_blank">API Docs</a>`. (2) /home/t3rmit3/gits/GRCen/src/grcen/main.py:256-263 serves /docs via `get_swa

## F265 [sev 2] [ux] Viewer role has no export of any list/register even though it can download every per-asset and per-framework PDF
- Route: `/assets, /risk-management, /controls, /frameworks/{id}` · area: roles
- Actual: Inconsistent: single-record PDFs allowed, list exports denied, and some denied exports still show buttons (see separate findings).
- Expected: A coherent read-only story: either viewers can export what they can see (at least PDF of filtered lists/registers), or per-item PDFs are gated the same way.
- Code: src/grcen/permissions.py (VIEWER lacks EXPORT); routers/framework_pages.py:480-488 (/exports/assets.pdf EXPORT) vs :510-542 (report.pdf VIEW)
- Fix: Decide per artifact; a reasonable split is PDF (print) = VIEW, CSV/JSON/ZIP (bulk data) = EXPORT, applied consistently in both routers and templates.
- Verifier (confirmed): Verified from source (plan mode was active in this session, so no browser/login run was performed; the finding is permission/template logic, which the code settles definitively). permissions.py: VIEWER = {VIEW, VIEW_GRAPH}, no EXPORT. List/register exports are all EXPORT-gated in both router and template: assets/list.html lines 7-11 hide CSV/JSON/"Download PDF" behind has_perm(EXPORT) and /exports

## F270 [sev 2] [bug] Risk register sort header renders the literal text 'SCORE &#9660;' instead of an arrow
- Route: `/risk-management (default sort) and /risk-management?sort=score&order=desc` · area: visual-design
- Actual: Header reads 'SCORE &#9660;' because the Jinja string '&#9660;' is autoescaped.
- Expected: SCORE ▼ (and ▲ when ascending), like NAME ▲ on /assets.
- Code: src/grcen/templates/risks/index.html:201 ({{ '&#9650;' if ... else '&#9660;' }})
- Fix: Use the literal characters ▲/▼ (as assets/list.html:128 does) or mark |safe; extract one sort_th macro shared by all sortable tables.
- Verifier (confirmed): Logged in as admin14 and loaded /risk-management (default), ?sort=score&order=desc and ?sort=score&order=asc, all HTTP 200. The Risk Register <thead> inner text is literally 'SCORE &#9660;' for default/desc and 'SCORE &#9650;' for asc; the .sort-arrow span's innerText is the raw string '&#9660;' / '&#9650;', and the page source contains '&amp;#9660;' (the entity was HTML-escaped by Jinja autoescap

## F271 [sev 2] [design] Error pages are raw JSON / bare text with no layout: 404, missing asset, login throttle 429
- Route: `/this-page-does-not-exist (JSON), /assets/00000000-0000-0000-0000-000000000000 ('Not found'), POST /login within 2s of another attempt ('Too many login attempts. Please wait.')` · area: visual-design
- Actual: Unknown routes return application/json {"detail":"Not Found"}; missing assets return an unstyled Times New Roman 'Not found' with no nav; the login throttle returns a bare 'Too many login attempts. Please wait.' page with no way back and no retry hint. Only 403 has a template.
- Expected: A styled error page in the app shell with a message, a 'Back to dashboard' action and, for 429, the retry time; login throttle should surface inline on the login form.
- Code: src/grcen/main.py:276-286 (http_exception_handler only templates 403/401); src/grcen/templates/errors/ has only 403.html; src/grcen/rate_limit.py:35
- Fix: Add errors/404.html, 429.html and 500.html extending base.html; branch on the Accept header (HTML vs API) for every status; render the 429 message inline on auth/login.html with Retry-After.
- Verifier (confirmed): Verified in plan mode (read-only), so evidence is one live GET plus code inspection rather than logged-in screenshots. (1) Unknown route, live: `curl -H "Accept: text/html" https://localhost:8443/this-page-does-not-exist` -> status 404, Content-Type application/json, body {"detail":"Not Found"}. Cause: the router's 404 is a starlette.exceptions.HTTPException, which the app's handler registered for

## F272 [sev 2] [design] Checkboxes render as full-width blocks, floating centred above their label (Settings, SMTP, Webhooks, Tokens, Imports)
- Route: `/settings (Notifications), /admin/smtp-settings (3 checkboxes), /admin/webhooks (Enabled), /tokens (Service account), /imports (Dry run)` · area: visual-design
- Actual: .form-group input {width:100%} applies to type=checkbox, making it a block the full form width; the tick is centred in that block. 14 wide checkboxes measured on /tokens, 3 on /admin/smtp-settings.
- Expected: Checkbox inline to the left of its label, clickable as one unit.
- Code: src/grcen/static/css/style.css:177-183; settings.html:31-37; admin/smtp_settings.html; admin/webhooks.html; tokens/my_tokens.html; imports/index.html
- Fix: Scope the width rule: .form-group input:not([type=checkbox]):not([type=radio]); add a .form-check {display:flex;gap:.5rem;align-items:center} component and use it for every boolean field.
- Verifier (confirmed): Verified by (a) fetching the live stylesheet from https://localhost:8443/static/css/style.css (200) and confirming lines 177-181 are the unscoped `.form-group input, .form-group select, .form-group textarea { width: 100%; padding: 0.5rem 0.75rem; border: ... }` rule, identical to the repo; (b) reading the templates: settings.html:31-37, admin/smtp_settings.html:47/50/65, admin/webhooks.html:82 and

## F273 [sev 2] [design] Page h1 squeezed into two lines because description paragraph shares the flex header (Content Packs, Risk Appetite)
- Route: `/admin/content-packs, /admin/risk-appetite` · area: visual-design
- Actual: .page-header is display:flex; space-between, so the <p> intro competes with the h1 for width (h1 measured 121x102px, 2 lines).
- Expected: Title on its own line, intro paragraph below it (as on /registers, /answers, /evidence).
- Code: src/grcen/templates/admin/content_packs.html:5-15; admin/risk_appetite.html:5-12; style.css:139 (.page-header)
- Fix: Standardise a page-header component: <h1> + optional .page-lede paragraph in a column, with .actions as the only flex sibling. Give h1 white-space handling and the lede max-width ~70ch.
- Verifier (confirmed): Reproduced the mechanism independently. Rendered the exact `.page-header` markup from admin/content_packs.html (lines 5-15) and admin/risk_appetite.html (lines 5-11) inside base.html's `<main class="container">` with the real src/grcen/static/css/style.css in headless Chromium at 1440x900 (offline render, no login, no files written because the session is in plan mode). Measurements match the repor

## F274 [sev 2] [design] Page action bars disagree: button group vs pipe-separated link list vs floating '|' in the header
- Route: `/assets/{id} vs /frameworks/{id} vs /frameworks/{id}/soa vs /reports/executive vs /controls` · area: visual-design
- Actual: Four different compositions; the executive report pipe is a stray text node spaced out by justify-content:space-between.
- Expected: One header pattern: title left, primary action right, secondary actions as uniform buttons; back-navigation via breadcrumb.
- Code: frameworks/detail.html:5-28; frameworks/soa.html:5-11; reports/executive.html:5-13; frameworks/controls.html:10-15; assets/detail.html:4-41
- Fix: Extract partials/page_header.html(title, lede, breadcrumb, actions) and a .btn-group; ban literal '|' separators. Period picker on the board pack belongs in a toolbar below the header.
- Verifier (confirmed): Verified by reading the templates and stylesheet (read-only; this run was constrained to inspection, so no fresh screenshots were captured — the tester's shots are consistent with what the source renders). All four compositions exist as described:

1. /assets/{id} (assets/detail.html:4-41): .page-header > h1 + div.actions containing uniform .btn buttons/forms (Watch, View Graph, Download PDF, Edit

## F275 [sev 2] [design] Button hierarchy is undefined: 19 renderings, Save is secondary on some admin pages and primary on others, btn-xs/btn-secondary classes do not exist
- Route: `/admin/workflow and /admin/organization (Save = plain .btn) vs /admin/tokens, /admin/rate-limits, /settings (Save = btn-primary); /reports/executive ('Save overview' btn-sm); /frameworks/{id}/soa (per-row 'Save' btn-sm); /assets/new ('Cancel' .btn same weight as Create)` · area: visual-design
- Actual: Across the sweep: 238 btn-sm (24px), 60 a.btn-sm (29px), 8 btn-sm at 38px (wrapping), 40 btn-primary at 34px and 5 at 40px, 40 a.btn at 40px vs 29 button.btn at 34px (anchor vs button render at different heights), 12 btn-xs at 16px and 6 at 34px.
- Expected: One primary per form, consistent sizes (default/sm), consistent secondary/ghost/danger styles.
- Code: style.css:155-172 (no btn-xs, no btn-secondary, no line-height so <a> and <button> differ); admin/workflow_settings.html (Save .btn); admin/organization.html; partials/saved_searches.html, answers/questionnaire_detail.html, assets/form.html (btn-xs)
- Fix: Define .btn with explicit line-height/height tokens (--btn-h-sm 28px, --btn-h 36px), variants primary/secondary/ghost/danger, sizes sm/md; add the missing btn-xs/btn-secondary or remove their uses; rule: exactly one btn-primary per form, placed bottom-left of forms and top-right of list pages.
- Verifier (confirmed): Reproduced as admin03 via Playwright at 1440x900, measuring getBoundingClientRect/getComputedStyle on every .btn per route (plan mode was active, so results were captured to stdout rather than screenshots; the repo was only read). Observed: /admin/workflow and /admin/organization — the only submit is a plain `.btn` Save (white bg rgb(255,255,255), 34px tall). /admin/tokens, /admin/rate-limits, /se

## F276 [sev 2] [design] Dates wrap mid-value ('2026-10-' / '10') and the risk name column wraps to 3-4 lines in dense tables
- Route: `/assets (page 1-27 with long requirement names), /controls, /assets?type=control, /risk-management register` · area: visual-design
- Actual: 50 wrapped date cells measured on /assets; 11 columns squeezed into an 830px card on risk management; Score pill is centred in a column labelled by a left-aligned header.
- Expected: Dates and numbers never wrap (nowrap, tabular figures, right-aligned numbers); name column gets the flexible width; empty columns collapse or are hidden.
- Code: style.css:194-198 (no td.num / td.date rules); assets/list.html render_cell macro; risks/index.html table (11 columns)
- Fix: Add td.date/td.num {white-space:nowrap; font-variant-numeric:tabular-nums; text-align:right} and a .table-wrap with horizontal scroll; set min-width on name columns; hide/collapse columns that are '—' for every row; consider a compact density toggle.
- Verifier (confirmed): Reproduced via Playwright DOM measurement (admin04, 1440x900 and 1280x800). /assets?type=control: 15 columns, table 1653px wide inside a 1200px card (overflow-x: visible), 50 of 50 Created/Updated cells ('2026-10-10') render on two line boxes; 5 columns are '—' in every row (Automation Level, Last Tested, Next Test Due, Evidence URL, Framework References). /risk-management: 11 columns, table 1254p

## F277 [sev 2] [design] Status vocabulary is rendered five different ways (pill, coloured text, plain text, transparent badge, Yes/No)
- Route: `/reviews and /assets (pills) vs /frameworks/{id} and /frameworks/{id}/soa (coloured text '✓ satisfied' / 'gap') vs / Recent Assets (plain 'Active') vs /admin/users (role in .badge with no colour, Active = 'Yes') vs /alerts (Enabled = 'Yes')` · area: visual-design
- Actual: Measured: users .badge bg rgba(0,0,0,0); framework detail uses span style=color:#991b1b / #166534 / #1d4ed8 / #b45309 per row (191 rows).
- Expected: One badge component with semantic tones (success/warning/danger/neutral/info) used for every status, boolean and coverage state; booleans shown as a toggle/pill, not 'Yes'.
- Code: frameworks/detail.html:125-134; frameworks/soa.html; dashboard.html Recent Assets table; admin/users.html:29-31; alerts/list.html:21; style.css:200-205 (badge variants only for active/inactive/draft/archived)
- Fix: Create a status_badge(tone, label, icon) macro and tone tokens (--tone-success-bg/fg etc.); map asset status, review state, coverage state (satisfied/weak/crosswalk/gap/N-A), test result, role and booleans onto it; delete the per-row inline colours.
- Verifier (confirmed): Verified read-only from source (plan mode was active, so no Playwright run/screenshots; every cited rendering is unconditional template markup, not data-dependent, so the described state necessarily exists on the live pages). All five vocabularies are present exactly as described: (1) /assets renders `<span class="badge badge-{{ status }}">` (assets/list.html:141) and /reviews uses `.badge.review-

## F278 [sev 2] [design] Flash/alert banners are hand-styled differently on each page; 26 distinct inline hex colours across routes
- Route: `/settings (Preferences saved), /frameworks/{id}, /frameworks/{id}/soa, /controls, /reports/executive, /login notice, /admin/smtp-settings, /admin/risk-appetite, /admin/encryption` · area: visual-design
- Actual: At least 6 variants: alert-success class exists but every flash uses inline background:#dcfce7;color:#166534 (sometimes with a border, sometimes not, padding 0.5/0.6/0.75rem, radius 6/8px). 526 style= attributes across 60 templates; 26 unique inline hex colours (#374151, #6b7280, #b45309, #1d4ed8, #c0392b ... none of which are tokens).
- Expected: One .alert component with .alert-success/.alert-warning/.alert-error (which already exist in style.css) and identical spacing everywhere.
- Code: settings.html:10-19; frameworks/detail.html:30-33; frameworks/soa.html:13-16; frameworks/controls.html:18-21; reports/executive.html:15-17; auth/login.html:9-11; style.css:224-228 (unused .alert-* classes)
- Fix: partials/flash.html rendering {{ flash.ok }} to .alert-success/.alert-error; add tokens --success-bg/fg, --warning-bg/fg, --danger-bg/fg, --info-bg/fg, --text-secondary; lint rule: no hex colours in templates.
- Verifier (confirmed): Verified by source inspection (plan mode was active, so I did not drive the browser; for a design finding about template markup the templates are the authoritative evidence and the cited lines match exactly). Every code pointer checks out: settings.html:10 and :16 (success/flash with inline bg/color/border, no padding override), frameworks/detail.html:30-32 (padding 0.5rem 0.9rem, radius 6px, Jinj

## F279 [sev 2] [design] Textareas render in monospace and inputs in Arial, neither matching the body system font
- Route: `/assets/new and /assets/{id}/edit (Description), /relationships/{id}/edit, /questionnaires/{id} (answer boxes), /reports/executive (narrative), /admin/rate-limits (per-route overrides)` · area: visual-design
- Actual: Computed textarea font-family = monospace; input/select = Arial/14px (vendor campaigns 13.33px); body = -apple-system/Segoe UI. No font: inherit rule.
- Expected: All form controls inherit the page font and size.
- Code: style.css:177-183 (.form-group input/select/textarea lacks font-family); questionnaire_detail.html:86
- Fix: Add button, input, select, textarea { font: inherit; color: inherit } to the reset; set --font-mono only for code/token displays.
- Verifier (confirmed): Verified against the source and the CSS actually served by the running instance (curl https://localhost:8443/static/css/style.css matches src/grcen/static/css/style.css). The stylesheet sets the system font only on `body` (line 17); the universal reset at line 14 is `* { margin:0; padding:0; box-sizing:border-box }` with no `font: inherit`, and the only `font: inherit` in the whole file is on `.le

## F280 [sev 2] [design] Vendor Campaigns, Risk Appetite and Board Pack forms use unstyled native inputs beside styled buttons
- Route: `/vendor-campaigns (New campaign), /admin/risk-appetite (Set a band), /reports/executive (Period)` · area: visual-design
- Actual: Inputs are bare <input style="width:18rem"> outside .form-group so they get browser defaults; labels are separated with <br>.
- Expected: Inline forms use the same input component as every other form.
- Code: vendor_campaigns/index.html:41-48; admin/risk_appetite.html (Set a band); reports/executive.html:7-9
- Fix: Apply base input styling to input/select/textarea globally (not only inside .form-group) and add an .form-inline row component with aligned labels.
- Verifier (confirmed): Reproduced on all three routes as admin08 via Playwright computed-style measurements (no edits made; plan mode was active so no screenshots were written, but the numbers are unambiguous).

/vendor-campaigns "New campaign": Name input 19px tall, 0px radius, 13.33px font, 2px inset rgb(118,118,118) border (Chromium UA default), 0 padding; Vendor select 19px, square; Due date 21px, square; sitting ne

## F281 [sev 2] [content] Raw snake_case identifiers exposed in human-facing copy (workflow, evidence, exports, sensitive overrides)
- Route: `/admin/workflow, /relationships/{id}/evidence, /exports, /assets/{id} (Sensitive field overrides), /answers intro` · area: visual-design
- Actual: Same value is titled in one cell and raw in the next; relationship verbs keep underscores on the evidence page but are humanised on the asset detail table.
- Expected: Human labels ('Data Category', 'mitigated by'), with machine keys only in tooltips or code chips where a developer needs them.
- Code: admin/workflow_settings.html:48; relationships/evidence.html; exports/index.html; assets/detail.html:61; answers/list.html intro
- Fix: Add a humanize filter (replace('_',' ') | title / verb maps) and apply it everywhere a value is displayed; show the raw key in a <code> only on admin config pages, in a secondary colour.
- Verifier (partially): Plan mode was active for this agent, so verification was done against the template source (the rendered strings in a content finding like this are fully determined by the templates; no scripts/screenshots were written). Three of the five cited locations are real, deterministic defects: (1) /admin/workflow — src/grcen/templates/admin/workflow_settings.html line 25 renders `{{ cfg.asset_type | repla

## F282 [sev 2] [design] Asset detail Alerts table and Alerts list show raw timestamps and cron strings
- Route: `/assets/{id} (Alerts section, e.g. CI/CD Pipeline), /alerts (Cron column)` · area: visual-design
- Actual: Datetime object printed with str(); cron expression shown verbatim. (Dashboard raw timestamps are already known; this adds asset detail + alerts list.)
- Expected: A formatted date ('15 Oct 2026, 05:06 UTC' or relative 'in 5 days') and a humanised schedule ('Weekly on Sunday at 03:00').
- Code: assets/detail.html:282-283; alerts/list.html:19-20; dashboard.html:25
- Fix: Register Jinja filters fmt_dt / fmt_date / humanize_cron (croniter/cron-descriptor) and use them in every template; drop the Cron column in favour of a 'Schedule' column.
- Verifier (confirmed): Verified against the templates and by rendering the exact template expressions through Jinja2 with a sample tz-aware alert (the Alert model's next_fire_at is a datetime from a TIMESTAMPTZ column, so asyncpg hands the template a tz-aware value with microseconds). Output reproduced the reported strings verbatim: assets/detail.html Alerts table -> Schedule "recurring (0 3 * * 0)", Next Fire "2026-10-

## F283 [sev 2] [design] Date/time formats differ on every page (8 formats) and there is no relative time
- Route: `/alerts ('2026-10-13 05:06'), /settings sessions ('2026-10-10 05:07 UTC'), /admin/audit ('2026-10-10 05:07:48'), /reviews ('2026-08-25'), /assets ('2026-10-10'), /controls ('tested never', 'due —'), /frameworks/{id} ('never'), / and asset Alerts (raw ISO with microseconds)` · area: visual-design
- Actual: Eight formats; only the sessions tables say UTC; audit log shows seconds; others do not.
- Expected: One date style and one datetime style app-wide, with timezone handled consistently (UTC suffix everywhere or nowhere) and relative phrasing for due dates ('Overdue by 46 days').
- Code: Per-template strftime calls: alerts/list.html:20, settings.html sessions table, admin/audit_log.html, reviews/index.html, assets/detail.html:105-106, frameworks/controls.html
- Fix: Central date formatting filters with a single org timezone setting; <time datetime> elements with a title showing the absolute value.
- Verifier (confirmed): Verified against template source (read-only; plan mode prevented running Playwright/screenshots, but strftime strings fully determine the rendered output). There is no central date filter: `grep filters[` across src/grcen finds zero Jinja filters, zero `<time>` elements in any template, and zero toLocale/Intl date formatting in static JS. Every timestamp is a per-template strftime (41 call sites o

## F284 [sev 2] [design] Every content link is default blue-underlined; list tables carry two underlined links per row (Type column is a link)
- Route: `/assets, /controls (285 underlined links), /frameworks/{id} (316), /answers, /registers/vendors` · area: visual-design
- Actual: style.css has no rule for <a>; only nav/admin-bar links are styled, so content uses the browser default #0000EE underline.
- Expected: Links styled with the primary token, underline on hover only inside tables, type shown as a neutral chip/filter rather than a link, so the primary action (open the asset) stands out.
- Code: style.css (no a {} rule); assets/list.html:137-139 (type cell as link)
- Fix: a {color:var(--primary); text-decoration:none} + a:hover underline; table a.primary-link for the row's main link; render type/owner secondary values as text or chips with a filter icon.
- Verifier (confirmed): Described state exists. Code: /home/t3rmit3/gits/GRCen/src/grcen/static/css/style.css has no bare `a {}` / `a:hover` rule at all — the only anchor rules are scoped (.nav-brand a, .nav-links a, .admin-bar a, .pagination a, .autocomplete-results a, th a, .heatmap-cell a, .sort-link), and base.html adds no inline anchor styling, so every content link falls through to the UA default (#0000EE, underlin

## F285 [sev 2] [design] Heading scale is browser-default and card titles alternate between h2 (24px) and h3 (18.7px)
- Route: `/ (card titles are h2 24px) vs /settings, /my-work, /admin/content-packs, /admin/webhooks (card titles h3 18.72px) vs /registers (h2 overridden inline to 1.1rem); /admin/workflow and /imports h1 have UA margins while .page-header h1 has margin 0` · area: visual-design
- Actual: h1 = UA 2em (32px) incl. on the login page; h2 = 24px; h3 = 18.72px; no line-height/letter-spacing tuning; headings used for size rather than hierarchy.
- Expected: A defined type scale (e.g. 28/22/18/16/14/12) with semantic classes (.page-title, .section-title, .card-title) independent of heading level.
- Code: style.css:326-327 (only h2/h4 margins), :139-140; dashboard.html:7-8; settings.html:22; registers/index.html:12
- Fix: Add --fs-* and --lh-* tokens, style h1-h4 explicitly, add .card-title; make every page use .page-header so title spacing is identical.
- Verifier (confirmed): Reproduced live as admin13 at https://localhost:8443 via computed styles (getComputedStyle + bounding boxes; plan mode was active so no screenshots/files were written). Type scale: /static/css/style.css contains no font-size rule for h1-h4, so every heading renders at the UA default: h1 = 32px on /login, /, /settings, /my-work, /admin/*, /imports, /assets; h2 = 24px; h3 = 18.72px; h4 = 16px; line-

## F286 [sev 2] [design] Very long per-row-form pages (SoA 9.6k px, framework detail 15.8k px, controls 12.8k px, sensitive fields 10.3k px) with no sticky header, filter or pagination
- Route: `/frameworks/{id}, /frameworks/{id}/soa, /controls, /admin/sensitive-fields, /admin/access-log` · area: visual-design
- Actual: Flat tables with one form per row and a tiny btn-sm 'Save' each; no filters; the gap count in the Requirements heading is not a filter link.
- Expected: Sticky column headers, a filter bar (status: gaps / satisfied / N-A; search by id), grouping by control family, and one Save for the whole SoA (or autosave per row with inline feedback).
- Code: frameworks/soa.html; frameworks/detail.html:117-140; frameworks/controls.html; admin/sensitive_fields.html
- Fix: thead {position:sticky; top:0}; a .table-toolbar with status filter chips and search; group rows under h3 per family with collapse; SoA as a single form with a sticky 'Save N changes' bar.
- Verifier (confirmed): Verified from the full-page captures (PIL-measured: SoA 1712x9593, framework detail 1712x15751, controls 1712x12834, sensitive-fields 1712x10303) plus source inspection; cropped mid-page views saved under scratchpad/pw/work/verify/F286_*.png. Mid-page crops of the SoA (y=4500-5400) and framework detail (y=8000-8900) show rows with checkbox/select/text/btn-sm 'Save' and status/requirement columns w

## F287 [sev 2] [design] Org chart renders at ~3px label height inside a mostly empty 680px canvas
- Route: `/org-views (Org Chart tab)` · area: visual-design
- Actual: Names are ~3px tall; 60% of the canvas is blank.
- Expected: Fit to a readable minimum scale with pan/zoom controls, or a collapsed tree with expandable levels; show hints and a legend.
- Code: src/grcen/static/js/org_tree.js (fit/scale), org_views.html, style.css:241 (.org-tree-container height calc)
- Fix: Set a minimum node scale and allow horizontal scrolling/zoom controls (+/−/fit), collapse below depth 2 by default, and add a zoom hint overlay like the graph page.
- Verifier (confirmed): Plan mode blocked writing a new Playwright script, so verification used the prior tester's screenshot (work/visual-design/shots/25_org_views.png, 1440x900 viewport) cross-checked against the renderer source; the arithmetic is deterministic and matches the screenshot exactly. The screenshot shows the Org Chart tab: a 5-level tree of ~44 Person cards rendered as ~40x14px boxes with ~3px names, occup

## F293 [sev 2] [design] Export page asks for comma-separated type and column names typed by hand
- Route: `/exports` · area: visual-design
- Actual: Users must type 'data_category,intellectual_property' exactly.
- Expected: Checkbox group / multi-select for types and a checklist of columns with Select all.
- Code: exports/index.html
- Fix: Render the same type list as a checkbox grid (permission-grid component) and columns as a checklist; keep a hidden comma-joined value for the API.
- Verifier (confirmed): Confirmed from source (the session is in plan mode, which forbids writing/running a Playwright script, so no fresh screenshot was taken; the template is unambiguous and is the exact file served at /exports). /home/t3rmit3/gits/GRCen/src/grcen/templates/exports/index.html renders `<input type="text" name="types" placeholder="e.g. person,system,risk">` with the label "Asset Types (comma-separated, o

## F318 [sev 1] [a11y] Toggle controls expose no state: Advanced filters (aria-expanded), legend type filters and Link Mode (aria-pressed), saved-searches <details> menu
- Route: `/assets, /graph/{id}` · area: a11y-keyboard
- Actual: probe2.json advanced_before.expanded=null; probe3.json graph_legend.pressed=null, graph_link_mode.pressed=null.
- Expected: aria-expanded="true|false" + aria-controls on disclosure buttons and aria-pressed on toggle buttons (WCAG 4.1.2 Name, Role, Value).
- Code: src/grcen/templates/assets/list.html:28 and :190-194 (toggleAdvanced); src/grcen/static/js/graph.js:251-279 (renderLegend), :281-290 (toggleLinkMode)
- Fix: Set/flip aria-expanded and aria-controls="advanced-filters" in toggleAdvanced; set aria-pressed="true|false" in the legend click handler and toggleLinkMode.
- Verifier (partially): Reproduced live as admin14 (inline Playwright probe, no files written because plan mode is active). On /assets the #advanced-toggle button has aria-expanded=null and aria-controls=null before and after click. On /graph/{id} the 17 legend buttons flip only the CSS class ('legend-item' -> 'legend-item legend-off') with aria-pressed=null both states; #link-mode-btn also has aria-pressed=null. `grep -

## F319 [sev 1] [a11y] Form-field focus indicator is a 1px border-colour change plus a 10% alpha ring after outline:none, and no prefers-reduced-motion rule exists for the 400ms graph animations
- Route: `/assets/new (form fields), /graph/{id}` · area: a11y-keyboard
- Actual: probe4.json tab_focus_name_input: outline 'none 0px', shadow rgba(37,99,235,0.1) 0 0 0 3px; probe1 _css reduced_motion=0; graph.js animate:true at lines 131 and 199.
- Expected: A focus indicator with at least 3:1 contrast and ~2px thickness (WCAG 2.4.7; 2.4.11/2.4.13 in 2.2) consistent with the 2px solid outline used on the nav search, and animations disabled/instant under prefers-reduced-motion (WCAG 2.3.3 best practice).
- Code: src/grcen/static/css/style.css:184-188 (.form-group input:focus outline:none + 0.1 alpha shadow); src/grcen/static/js/graph.js:131,199
- Fix: Use :focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; } for inputs/selects/textareas/buttons/links globally instead of outline:none, raise the shadow alpha to >=0.4 if kept; add @media (prefers-reduced-motion: reduce) { * { transition: none !important; animation: none !important; } } and pass animate: !matchMedia('(prefers-reduced-motion: reduce)').matches to Cytoscape layou
- Verifier (confirmed): Reproduced live as admin15 (plan mode was active, so probes were run inline and no scripts/screenshots were written; the reporter's existing p4-focus-form-input.png was inspected instead). Part 1, /assets/new: focusing #name gives computed outline 'none 0px', box-shadow 'rgba(37,99,235,0.1) 0 0 0 3px', border 1px rgb(37,99,235) (unfocused rgb(226,232,240)), :focus-visible matches. Contrast arithme

## F233 [sev 1] [bug] ITSM integration can be enabled with an empty Base URL
- Route: `/admin/itsm-settings` · area: admin-settings
- Actual: Enabled with no endpoint; 'Push to ticket' on findings will fail at use time.
- Expected: Refuse to enable without base URL/project key/credentials.
- Code: src/grcen/routers/admin_pages.py:761-784
- Fix: Require base_url (and project_key for jira/servicenow) when enabled=true.
- Verifier (partially): Did not perform the Save step: it mutates a global setting (task forbids this) and the session is in read-only plan mode, so I judged from a read-only page inspection plus code. Page inspection (GET /admin/itsm-settings as admin09, 200): #base_url has no `required` attribute (type=url permits an empty value), Enabled checkbox and base_url are currently blank/unchecked. Code confirms the described 

## F243 [sev 1] [ux] One-time secrets (invite link, OAuth client secret, calendar URL) have no Copy control
- Route: `/admin/invitations, /admin/oauth-clients, /settings` · area: admin-settings
- Actual: Manual selection only.
- Expected: A Copy button (working under CSP) next to every once-only value.
- Code: src/grcen/templates/admin/invitations.html:18-24; admin/oauth_clients.html:20-27; settings.html:126-127
- Fix: Shared nonce'd copy-to-clipboard helper on data-copy-target.
- Verifier (confirmed): Verified against the templates (plan mode was active in this session, so I did not drive the browser to create new invitations/OAuth clients/calendar tokens; the rendering is unconditional in the templates, so a live screenshot would not change the outcome). All three once-only values are rendered as a bare <p><code>...</code></p> with no button, no id, no data-copy-* hook, and no script: src/grce

## F174 [sev 1] [ux] Submitting the create form with an empty name shows a bare 'Name is required' text page and loses the form
- Route: `/questionnaires and /vendor-campaigns (POST)` · area: answers-questionnaires
- Actual: HTTP 400 JSON-style page containing only 'Name is required' with no navigation, styling or way back; Source/Due date input is lost. Identical on /vendor-campaigns.
- Expected: Re-render the form with an inline error and the other fields preserved.
- Code: src/grcen/routers/answer_pages.py:75-77; src/grcen/routers/vendor_campaigns.py:78-80 (raise HTTPException(400)).
- Fix: Redirect back with ?flash=fail:Name is required (pattern already used in vendor_campaigns) or re-render the template with the error.
- Verifier (partially): Verified by code inspection only. This session is in plan mode, which forbids writing the verify script or logging in via Playwright (login creates a session row), so I did not capture a live screenshot; the judgement below rests on the deterministic code path, which has no DB or state dependency before the raise.

Mechanism is exactly as described. Both POST handlers strip `name` and `raise HTTPE

## F186 [sev 1] [design] Questionnaire status dropdown is in arbitrary order and 'Submitted' does not lock editing
- Route: `/questionnaires/{id}` · area: answers-questionnaires
- Actual: Order comes from iterating a Python set; nothing changes on Submitted.
- Expected: Lifecycle order (Draft -> In Progress -> Submitted) and a read-only (or warn-on-edit) state once submitted to the customer.
- Code: src/grcen/services/questionnaire_service.py:14 (VALID_STATUS is a set); routers/answer_pages.py:119 list(...); template questionnaire_detail.html:47-51.
- Fix: Make VALID_STATUS an ordered tuple and gate the per-row forms/import card on status != 'submitted' (with an explicit 'Reopen').
- Verifier (confirmed): Observed live as admin10 on the only existing questionnaire (/questionnaires/fdfa34fd-f122-41b9-a1cd-983e8f21eaa7, status In Progress): the Status select renders options ['Draft', 'Submitted', 'In Progress'], not the lifecycle order; the page shows 6 per-row Save buttons and 1 import form. I did not flip the questionnaire to Submitted (shared data / plan-mode read-only constraint), but the second 

## F187 [sev 1] [design] Export filenames are UUIDs instead of the questionnaire name
- Route: `/questionnaires/{id}/export.csv, export.pdf` · area: answers-questionnaires
- Actual: questionnaire-4ce8d5c5-b616-40c7-ba72-41231f48971a.csv / .pdf.
- Expected: questionnaire-Acme-Corp-Vendor-Security-Assessment-2026.csv or similar.
- Code: src/grcen/routers/answer_pages.py:247 and :273
- Fix: Slugify q['name'] for the filename (the access log already records f"{q['name']}.csv").
- Verifier (confirmed): Confirmed from source (plan mode prevented driving the browser, which would have written access-log rows and files; the code path is deterministic so this is sufficient). /home/t3rmit3/gits/GRCen/src/grcen/routers/answer_pages.py:247 builds `attachment; filename="questionnaire-{questionnaire_id}.csv"` and :273 builds `attachment; filename="questionnaire-{questionnaire_id}.pdf"`, where questionnair

## F188 [sev 1] [ux] Campaign 'Import' with no file selected shows a green 'Imported 0 question(s)' success message
- Route: `/vendor-campaigns/{id}` · area: answers-questionnaires
- Actual: Redirect with ok-flash 'Imported 0 question(s)'.
- Expected: Browser-level required on the file input, or a failure flash 'Choose a CSV file first'.
- Code: src/grcen/templates/vendor_campaigns/detail.html:61 (input has no required); routers/vendor_campaigns.py:139-149.
- Fix: Add required to the file input and treat n == 0 as a fail flash.
- Verifier (confirmed): Plan mode was active for this subagent, so I could not execute the Playwright repro (creating a campaign and posting the form are non-readonly actions). Verification is by deterministic code trace plus the reporter's URL evidence, which matches the trace exactly.

Trace: the template at /home/t3rmit3/gits/GRCen/src/grcen/templates/vendor_campaigns/detail.html line 61 renders `<input type="file" na

## F189 [sev 1] [design] 404 for a deleted/unknown questionnaire renders as bare text with no navigation
- Route: `/questionnaires/{id}` · area: answers-questionnaires
- Actual: Plain page containing only 'Questionnaire not found'.
- Expected: The app's styled 404 with nav and a link back to /questionnaires.
- Code: src/grcen/routers/answer_pages.py:101-102 (HTTPException 404 on an HTML route).
- Fix: Render a templated 404 (or redirect to /questionnaires?flash=fail:Not found).
- Verifier (confirmed): Reproduced live as admin13 without needing to create/delete anything: GET /questionnaires/<random uuid> returns HTTP 404, content-type text/html, a 62-byte body whose entire visible content is the string "Questionnaire not found" -- no <nav>, no <a href>, no stylesheet (screenshot: /tmp/claude-1000/-home-t3rmit3-gits-GRCen/7ebb0b49-1352-428e-a038-c28a4c71f5b6/scratchpad/pw/work/verify/F189_questio

## F080 [sev 1] [design] Evidence page 'Kind' column and detail-page attachment 'Kind' show raw enum values; Evidence '+ add' is shown to read-only users
- Route: `/relationships/{id}/evidence, /assets/{id}` · area: asset-crud
- Actual: Raw att.kind.value; '+ add' label independent of permission.
- Expected: Humanised kinds ('URL', 'Document reference', 'File') and a neutral 'none' / '0 files' label for users without CREATE.
- Code: src/grcen/templates/relationships/evidence.html:32, src/grcen/templates/assets/detail.html:178-180, 233
- Fix: Add a kind_label filter/mapping and gate the '+ add' text on has_perm(user.role, Permission.CREATE).
- Verifier (confirmed): Verified by reading the templates (plan mode blocked running the browser helper, so no fresh screenshot; the rendering path is static Jinja with no JS post-processing, so the source is conclusive). (1) Raw enum kinds: `src/grcen/templates/relationships/evidence.html:32` and `src/grcen/templates/assets/detail.html:233` both emit `{{ att.kind.value }}`; `AttachmentKind` is a StrEnum with values "fil

## F082 [sev 1] [ux] Owner autocomplete only fires on keyup, so pasted or autofilled owner names never search; request carries a duplicate q param
- Route: `/assets/new, /assets/{id}/edit` · area: asset-crud
- Actual: hx-trigger="keyup changed delay:300ms" misses non-keyboard input; hx-include duplicates q.
- Expected: Search triggers on input (covers paste/autofill); a single q parameter.
- Code: src/grcen/templates/assets/form.html:46-51
- Fix: Give the visible input name="q", drop the hidden #owner_q and hx-include, and use hx-trigger="input changed delay:300ms".
- Verifier (partially): Verified from source only (plan mode was active for this agent, so no browser run was executed; the repo was read, nothing written). The mechanism the finding describes is real and exactly as reported in the template: src/grcen/templates/assets/form.html:46-51 has the visible #owner_search input with no name attribute and hx-trigger="keyup changed delay:300ms", a hidden <input name="q" id="owner_q

## F054 [sev 1] [ux] Register metric cards (e.g. '0 OVERDUE ASSESSMENTS', '3 OPEN') are not clickable and do not filter the list
- Route: `/registers/incidents` · area: assets-list
- Actual: Plain divs; nothing happens.
- Expected: Clicking a metric filters the table to those rows (as the heatmap cells do on /risk-management).
- Code: src/grcen/templates/assets/list.html:18-27; src/grcen/registers.py MetricDef
- Fix: Give each MetricDef an optional filter query (status=/meta_key=/meta_value=) and render the card as an <a> to the register with that filter.
- Verifier (confirmed): Reproduced as admin06 in headless Chromium. /registers/incidents redirects to /assets?type=incident&columns=curated&sort=meta.detected_at&order=desc and renders 3 metric cards ("3 | TOTAL", "3 | OPEN", "0 | CRITICAL"); /registers/vendors renders 4 ("20 | TOTAL", "0 | OVERDUE ASSESSMENTS", "0 | CRITICAL VENDORS", "0 | APPROVAL GAPS"). Every card is a DIV with no enclosing or inner <a>, no onclick, 

## F055 [sev 1] [ux] Metadata-field filter with a key but no value (or value but no key) is silently ignored
- Route: `/assets?type=risk&meta_key=likelihood` · area: assets-list
- Actual: All 21 risks are returned with the field still populated in the form; no message.
- Expected: Either match 'field is set' or show a validation hint.
- Code: src/grcen/routers/asset_pages.py:65-67
- Fix: Treat key-without-value as 'has field' (metadata ? key) or render an inline hint next to the inputs.
- Verifier (confirmed): Verified by code inspection (plan mode was active for this session, so I did not launch the Playwright browser or capture a screenshot; the behaviour is fully determined by the code below, so the live result cannot differ). /home/t3rmit3/gits/GRCen/src/grcen/routers/asset_pages.py:66-67 builds `metadata_filters` only `if meta_key and meta_value:`, so a key with no value (or value with no key) yiel

## F056 [sev 1] [design] Filter form submission pollutes the URL with every empty parameter
- Route: `/assets` · area: assets-list
- Actual: /assets?q=okta&type=&status=&owner=&created_after=&created_before=&meta_key=&meta_value=&tag=&sort=name&order=asc - ugly to share and inconsistent with the clean links the sort headers and /tags produce.
- Expected: /assets?q=okta
- Code: src/grcen/templates/assets/list.html:31-118 (single GET form incl. hidden sort/order)
- Fix: Strip empty fields on submit (tiny nonce'd script: formdata event deleting blank entries) or redirect server-side to the canonical filter_params URL.
- Verifier (confirmed): Verified from the source (plan mode in this session forbade writing/running the Playwright repro script, so no fresh screenshot; the behaviour is deterministic HTML form serialization and matches the reporter's captured URL). /home/t3rmit3/gits/GRCen/src/grcen/templates/assets/list.html lines 31-118 is a single `<form method="get" action="/assets">` containing text inputs q/owner/meta_key/meta_val

## F057 [sev 1] [design] Tags page: rename input has only a placeholder (no label), rename needs no confirmation, same-name rename reports 'Renamed 'x' -> 'x' on 0 asset(s)'
- Route: `/tags` · area: assets-list
- Actual: As described; the merge case ('Renamed a -> b on 2 asset(s)') does not say it merged into an existing tag.
- Expected: Labelled input, a no-op rejected with a hint, and a confirm when renaming merges into an existing tag (it silently merges).
- Code: src/grcen/templates/tags/index.html:19-41; src/grcen/routers/tag_pages.py:50-74
- Fix: Add aria-label/visually-hidden label, reject old==new with a 'fail' flash, and warn when new already exists ('merge into X?').
- Verifier (confirmed): Verified against source (plan mode was active for this agent, so the live Playwright repro — which requires writing a script and issuing the mutating POST /tags/{old}/rename — was not run; every claim in the finding is directly observable in the template and router, and the described state cannot fail to exist at render time).

1. No label: src/grcen/templates/tags/index.html:39 renders `<input ty

## F058 [sev 1] [ux] Two unrelated export surfaces: list buttons (CSV/JSON/PDF honoring filters) and the nav 'Export' page with its own filter set
- Route: `/exports vs /assets` · area: assets-list
- Actual: Users must learn two export models; the nav page cannot express owner/tag/unlinked filters that the list can.
- Expected: One export entry point, or the nav page pre-filled from the last list filters.
- Code: src/grcen/routers/exports.py (/api/exports/assets) vs src/grcen/routers/asset_pages.py:319-367 (/assets/export); src/grcen/templates/exports/
- Fix: Make /exports a column-picker step that accepts the list's query string (link 'Export...' from the list carries ?{filters}) and retire the parallel filter UI.
- Verifier (confirmed): Reproduced in the live app as admin10. /assets?type=vendor&owner=Carol shows 4 rows and its header buttons link to /assets/export?type=vendor&owner=Carol&format=csv|json (4-row vendor.csv, filename confirmed) and /exports/assets.pdf?type=vendor&owner=Carol. The nav 'Export' link goes to /exports, a separate 'Export Assets' form posting to /api/exports/assets with only format / types (free-text com

## F032 [sev 1] [ux] Every login that trips the session cap creates a new 'Session signed out' notification, spamming the inbox with identical rows
- Route: `/notifications` · area: dashboard-mywork
- Actual: Low-value duplicates crowd out real alert notifications and inflate the unread badge (badge went 9 → 11 during the session purely from these).
- Expected: At most one such notice, or a notice shown once on the Settings/sessions page rather than as a persistent inbox item; grammar should read '1 of your older sessions was'.
- Code: src/grcen/services/session_service.py:76-86
- Fix: Upsert/dedupe on (user_id, title) within a window, or surface the eviction as a one-time flash on the next page load instead of a stored notification; fix the pluralisation of 'session'.
- Verifier (confirmed): Verified from the reporter's screenshot plus the code path (this session was in plan mode, so I did not run the live login loop against the container, which would have mutated sessions/notifications; the behaviour is fully deterministic from the code and the existing evidence shows it). Evidence screenshot /tmp/claude-1000/-home-t3rmit3-gits-GRCen/7ebb0b49-1352-428e-a038-c28a4c71f5b6/scratchpad/pw

## F033 [sev 1] [design] Timestamps on notifications and alerts are UTC with no timezone indicator, so the displayed date can be 'tomorrow' for the user
- Route: `/notifications, /alerts, /` · area: dashboard-mywork
- Actual: Server-side strftime of a TIMESTAMPTZ in UTC; users in other zones read wrong days for review due dates.
- Expected: Times rendered in the user's/org's timezone or at least suffixed 'UTC'.
- Code: src/grcen/templates/alerts/notifications.html:16, src/grcen/templates/alerts/list.html:20, src/grcen/templates/dashboard.html:25
- Fix: Add a shared Jinja datetime filter that appends 'UTC' (or converts using an org timezone setting) and use it everywhere a datetime is rendered.
- Verifier (partially): The described state exists in the code and is deterministic (template-static): /home/t3rmit3/gits/GRCen/src/grcen/templates/alerts/notifications.html:16 renders `notif.created_at.strftime('%Y-%m-%d %H:%M')` and /home/t3rmit3/gits/GRCen/src/grcen/templates/alerts/list.html:20 renders `alert.next_fire_at.strftime('%Y-%m-%d %H:%M')` with no 'UTC' suffix or column-header marker. Both columns are TIMES

## F034 [sev 1] [design] Dashboard card grid is unbalanced: 5 cards in a 3-column grid leave an empty slot, with a 17-item Asset Types list stretching the top row
- Route: `/` · area: dashboard-mywork
- Actual: Layout is driven by auto-fit grid and template order, not importance.
- Expected: Attention-first hierarchy: overdue reviews, answers needing review, unread notifications and upcoming alerts at the top; the asset-type directory lower or collapsed.
- Code: src/grcen/templates/dashboard.html:6-56, src/grcen/static/css/style.css:151 (.grid)
- Fix: Reorder cards (Reviews, Answers, Alerts, Notifications/My Work first), render asset types as a compact chip row with counts, and consider a 4-column stat strip like /risk-management uses.
- Verifier (confirmed): Judged from the existing full-page capture at 1440x900 (/tmp/claude-1000/-home-t3rmit3-gits-GRCen/7ebb0b49-1352-428e-a038-c28a4c71f5b6/scratchpad/pw/work/dashboard-mywork/01_dashboard.png) plus the template and CSS; plan mode prevented running a fresh Playwright script, but the described state is unambiguous in the screenshot. The top row is Assets / Asset Types / Upcoming Alerts, with the Asset T

## F151 [sev 1] [ux] Board-pack 'Download PDF' opens inline in the same tab, navigating away from the editor; framework PDFs download as attachments
- Route: `/reports/executive -> /reports/board-pack.pdf` · area: frameworks-controls
- Actual: Inconsistent label/behaviour; unsaved narrative text is lost if clicked mid-edit.
- Expected: Consistent behaviour: either both download, or 'Open PDF' with target=_blank.
- Code: src/grcen/routers/reports_pages.py:83; src/grcen/templates/reports/executive.html:12
- Fix: Use attachment disposition like the other PDFs, or label it 'Open PDF' and add target="_blank" rel="noopener".
- Verifier (confirmed): Verified from source (plan mode restricted this session to read-only actions, so no browser run; the finding is fully determined by static template/router code). /home/t3rmit3/gits/GRCen/src/grcen/routers/reports_pages.py:81-83 returns the board pack with `Content-Disposition: inline; filename="board-pack-{period}.pdf"`, while every other PDF route uses `attachment` (framework_pages.py:450 gap-rep

## F152 [sev 1] [design] Executive page header has a stray '|' separator and a Trend column that is always '—' with no explanation
- Route: `/reports/executive` · area: frameworks-controls
- Actual: Visual glitch and an unexplained empty column.
- Expected: Grouped actions on the right; trend shows 'no snapshots yet' or hides the column until data exists.
- Code: src/grcen/templates/reports/executive.html:5-13 and :40-47
- Fix: Wrap form + button in a div.actions; render a tooltip/empty-state text for Trend when delta is None.
- Verifier (confirmed): Both observations hold. (1) The evidence screenshot (/tmp/claude-1000/.../work/frameworks-controls/13_reports_executive.png) shows the header row at 1440px with h1 at left, the Period form roughly centred, a lone '|' glyph floating at ~x=1030 and the Download PDF button hard right. The template explains it exactly: executive.html lines 5-13 put h1, <form style="display:inline">, a bare text node '

## F155 [sev 1] [design] 'Record result' column header is shown to read-only roles with an empty column
- Route: `/controls (as auditor2 / viewer)` · area: frameworks-controls
- Actual: Empty column.
- Expected: Omit the column for users without EDIT.
- Code: src/grcen/templates/frameworks/controls.html:42 and :84-96
- Fix: Wrap the <th> and <td> in the same has_perm(EDIT) check (as soa.html already does).
- Verifier (confirmed): The described state is real and deterministic from the template. In /home/t3rmit3/gits/GRCen/src/grcen/templates/frameworks/controls.html the `<th>Record result</th>` (line 42) is emitted unconditionally, while the per-row `<td>` (lines 84-96) only renders the test form inside `{% if has_perm(user.role, Permission.EDIT) %}` — so any role without EDIT gets a header over a column of empty cells. per

## F156 [sev 1] [content] Framework certification status is never populated and cannot be set from the framework pages
- Route: `/frameworks, /frameworks/{id}` · area: frameworks-controls
- Actual: A prominent column/field that is always empty on an otherwise fully seeded instance.
- Expected: Inline status setter (not started / in progress / certified) with audit date on the framework page, and the column hidden or hinted when empty.
- Code: src/grcen/templates/frameworks/index.html:53-60, detail.html:45-48
- Fix: Add a small POST form on the detail header to set certification_status/cert date, and seed packs with 'not_started'.
- Verifier (confirmed): Verified read-only (plan mode prevented launching a browser or writing scripts/screenshots). (1) The tester's evidence screenshots 01_frameworks_index.png and 02a_iso_top.png show the described state exactly: a prominent "Certification" column reading "—" for all 8 frameworks on /frameworks, and the detail header's "Status" cell reading "—" on ISO/IEC 27001:2022 Annex A. (2) A read-only SELECT on 

## F157 [sev 1] [ux] 'Suggest mappings (AI)' is silently absent when no API key is configured - nothing tells the user the feature exists
- Route: `/frameworks/{id}` · area: frameworks-controls
- Actual: Feature is undiscoverable; the task's 'what happens when no AI configured' answer is 'nothing is shown'.
- Expected: A disabled button with a tooltip 'Set ANTHROPIC_API_KEY to enable' (admins) or a short note in the header.
- Code: src/grcen/templates/frameworks/detail.html:20-26; src/grcen/routers/framework_pages.py:206-210
- Fix: Render the button disabled with an explanatory title when ai_mapping_enabled is false and the user has EDIT.
- Verifier (confirmed): Reproduced as admin13 (Admin, has EDIT) on all 8 frameworks at /frameworks/{id}: the page header renders only "All Frameworks | View as asset | SoA | Download PDF | Evidence Package"; locator count for "Suggest mappings" is 0 on every framework, and the pages have no text matching /anthropic|AI mapping/i. All frameworks have open gaps (coverage < 100%, 3-81 "gap" mentions per page), so the only fa

## F158 [sev 1] [design] Gap CSV / Gap PDF buttons are split across the card header by flex space-between
- Route: `/frameworks/{id}` · area: frameworks-controls
- Actual: Three-way space-between layout with the heading, one button in the middle, one on the right.
- Expected: Both export buttons grouped at the right (and ideally alongside SoA/PDF/ZIP in the page header so all exports are in one place).
- Code: src/grcen/templates/frameworks/detail.html:100-116
- Fix: Wrap the two links in a div.actions inside the flex header.
- Verifier (confirmed): Reproduced as admin14 on /frameworks/2285820f-ac21-4a2e-a431-03927fab43a0 at 1440x900. The Requirements card header div (style="display:flex;justify-content:space-between;align-items:center;") has exactly 3 direct flex children: the h3 (x 169-551), the "Gap CSV" link (x 818-911) and the "Gap PDF" link (x 1179-1271). Space-between therefore distributes them evenly: ~267px of empty space between the

## F159 [sev 1] [bug] Order of 'Requirements covered' per control changes between page loads
- Route: `/controls` · area: frameworks-controls
- Actual: Unordered aggregation; scanning for a specific requirement is harder and diffs of two CSV exports are noisy.
- Expected: Stable ordering (framework then natural requirement order).
- Code: src/grcen/services/framework_service.py list_controls_with_coverage (no ORDER BY in the requirement aggregation)
- Fix: Sort requirements per control by framework_name then natural requirement key before rendering/exporting.
- Verifier (partially): Observation: logged in as admin15 and fetched /controls 8 times in a row and /controls/export.csv 4 times; the per-control requirement order was identical across all 8 page loads (0 of 47 controls varied) and all 4 CSV pulls were byte-identical. Running the service's SQL directly against the DB 5 times also returned identical row order. So the claim "changes between page loads" does not reproduce 

## F160 [sev 1] [bug] Evidence package silently ignores an invalid ?since/?until date
- Route: `/frameworks/{id}/evidence-package.zip?since=garbage&until=2026-01-01` · area: frameworks-controls
- Actual: Partial filter applied silently (until honoured, since dropped); note the UI offers no date range control at all for the ZIP.
- Expected: 400 with a message, or the UI should expose a date picker so bad values cannot be typed.
- Code: src/grcen/routers/framework_pages.py:278-288 (_pdate swallows ValueError)
- Fix: Return 400 on unparsable dates and add since/until inputs next to the 'Evidence Package' button.
- Verifier (confirmed): Confirmed from the source (plan mode was active for this session, so I verified by reading the code path rather than by driving the browser; the mechanism is unambiguous and matches the reporter's observed output exactly). In /home/t3rmit3/gits/GRCen/src/grcen/routers/framework_pages.py the evidence-package route defines a local `_pdate` that does `try: return _date.fromisoformat(v) if v else None

## G3-18 [sev 1] [ux] Send Ping delivers to a disabled webhook and reports success
- Route: `/admin/webhooks (Send Ping on an Enabled: no row)` · area: gap-alerts-notifications-delivery
- Actual: A disabled endpoint still receives traffic from the admin UI, which is surprising when 'disabled' was chosen to stop sending to a decommissioned URL.
- Expected: Either the ping is blocked ('webhook is disabled') or the UI makes clear that ping ignores the enabled flag.
- Code: src/grcen/routers/admin_pages.py:1009-1028 (no enabled check)
- Fix: Label the button 'Test (ignores Enabled)' or skip sending with a flash when the hook is disabled.
- Verifier (confirmed): Confirmed by static trace of the exact request path; the live browser repro (create webhook, untick Enabled, click Send Ping) was not executed because plan mode forbids state-changing actions in this session. The path is deterministic with no enabled-dependent branch, so the reported behaviour follows necessarily:

1. Template /home/t3rmit3/gits/GRCen/src/grcen/templates/admin/webhooks.html:32-35 

## G5-21 [sev 1] [design] Revoking the token that minted a child token does not revoke the child
- Route: `POST /api/tokens/ then revoke parent` · area: gap-api-machine-client
- Actual: Child tokens are fully independent of the token that created them, so revoking a leaked token leaves any tokens it minted alive with no linkage shown in the UI. (Both are owned by the same user, so an admin can still find them, but nothing associates them.)
- Expected: At minimum this independence should be documented; operators often assume revoking a compromised token also kills anything it created.
- Code: src/grcen/services/token_service.py create_token (no parent linkage)
- Fix: Record the minting token/actor on each token and offer 'revoke cascade', or document that tokens are independent.
- Verifier (confirmed): Verified from code (plan mode was active, so I did not mint/revoke live tokens; the prior tester's s2_results.json already shows the mechanic: 'revoked parent token' -> 401, 'child token after parent revoked' -> 200, and the code path is deterministic). The described state exists exactly as reported: (1) the api_tokens table (src/grcen/database.py:230-241, plus the allowed_ips/organization_id ALTE

## G5-22 [sev 1] [ux] dry_run accepts 'yes' as true but 422s on 'maybe'; bulk accepts 1200 rows with no limit
- Route: `POST /api/imports/assets/bulk?dry_run=...` · area: gap-api-machine-client
- Actual: FastAPI's loose bool coercion means 'yes'/'true'/'1' work but 'maybe' 422s (fine, but undocumented), and there is no cap on bulk array length so a client can submit arbitrarily large batches in one request.
- Expected: Consistent, documented boolean parsing and a documented maximum batch size.
- Code: src/grcen/routers/imports.py bulk_assets (dry_run:bool, rows:list[dict] with no max length)
- Fix: Document the accepted boolean values and enforce a max rows-per-request (returning 413 over the limit).
- Verifier (confirmed): Both halves of the described state exist. (1) `dry_run: bool = False` on bulk_assets/bulk_relationships is a bare FastAPI query param, so pydantic's standard bool coercion applies: a local TypeAdapter(bool) check in the project venv gives 'yes'/'true'/'1'/'on'/'y' -> True, 'no'/'off' -> False, and 'maybe' and '' -> bool_parsing error (FastAPI renders that as 422 on loc ['query','dry_run']). The Op

## G1-18 [sev 1] [bug] Settings and Admin Sessions flash banners are reflected verbatim from the URL
- Route: `/settings?flash=..., /admin/sessions?flash=...` · area: gap-auth-lifecycle-mfa-sso
- Actual: Any ok:/fail: text in the query string renders as a green/red banner and persists on every reload; the MFA-enabled success message ('Two-factor authentication is now required on login.') stays in the URL after the action.
- Expected: Flash messages should be one-shot (session-stored) and never sourced from a user-controlled query string.
- Code: src/grcen/routers/settings_pages.py:90-93; src/grcen/routers/admin_pages.py:1267-1270
- Fix: Store the flash in request.session and pop it on render; drop the flash query parameter (same fix as the known /tags issue).
- Verifier (confirmed): Verified by code inspection (this session is in plan mode, so I could not write/run the Playwright script or perform a login; only read-only checks were made). The mechanism is unambiguous and deterministic: both handlers declare `flash: str | None = None` as a plain GET query parameter and build `flash_ctx = {"ok": ok == "ok", "message": message or flash}` from `flash.partition(":")`, then the te

## G4-17 [sev 1] [ux] Admin 'New User' form has no Linked Person field, so attaching a login to an HRIS-synced person takes a second edit step
- Route: `/admin/users/new -> /admin/users/{id}/edit` · area: gap-ingress-sync-to-ui
- Actual: The select is absent on create (the route does not pass person_assets), appears only on edit, and there is no hint that linking is what makes HRIS leaver deactivation work.
- Expected: The create form offers the same Linked Person select as the edit form (the leaver-deprovisioning flow depends on this link).
- Code: src/grcen/routers/admin_pages.py:57-125 (create route ignores person_asset_id); src/grcen/templates/admin/user_form.html:49-55
- Fix: Pass person_assets to the create template and read person_asset_id in admin_user_create_submit; add helper text 'Link to a Person so HRIS/SCIM deprovisioning disables this login'.
- Verifier (confirmed): Observed as admin06 (read-only page loads only; no user was created). GET /admin/users/new → 200, form labels are exactly ['Username', 'Password', 'Role'] with zero `#person_asset_id` elements and no mention of Person/HRIS/SCIM anywhere inside the form (the only HRIS/SCIM strings on the page are the admin nav links). GET /admin/users/{id}/edit for an existing user → 200 with a `#person_asset_id` s

## G2-11 [sev 1] [ux] POST /switch-org with an empty organization_id silently redirects to /settings with no message; /settings and /admin/orgs render arbitrary flash text from the URL
- Route: `POST /switch-org, /settings?flash=, /admin/orgs?flash=` · area: gap-multi-org-superadmin
- Actual: Reflected-flash pattern (already known on /tags) also applies to the two org-management routes; the empty case has no message at all.
- Expected: Empty selection gives feedback; flash messages come from the session, not the query string.
- Code: src/grcen/routers/settings_pages.py:40-41 and 69-72; src/grcen/routers/admin_pages.py:318-322
- Fix: Store flashes in request.session and pop them on render.
- Verifier (confirmed): Verified by code inspection (plan mode was active for this session, so I did not drive the browser or write scripts; every step is a deterministic code path, not a timing/rendering question). (1) /home/t3rmit3/gits/GRCen/src/grcen/routers/settings_pages.py:40-41: `if not target_id: return RedirectResponse("/settings", status_code=302)` — the empty case redirects with no flash, while the invalid-UU

## G2-12 [sev 1] [bug] /api/graph/{id} answers 200 with an empty graph for an asset in another org (or an unknown id) while every sibling endpoint returns 404
- Route: `/api/graph/{asset_id}` · area: gap-multi-org-superadmin
- Actual: The graph API treats a foreign/unknown root as an empty subgraph, so API clients cannot distinguish 'no relationships' from 'not yours'.
- Expected: Consistent 404 for ids that do not resolve in the active tenant.
- Code: src/grcen/routers/graph.py (subgraph endpoint); src/grcen/services/graph.py
- Fix: Resolve the root asset within organization_id first and raise 404 when missing.
- Verifier (confirmed): Reproduced live as admin03 (Org A) against https://localhost:8443 using a CSRF-aware curl session (no files written): GET /api/graph/00000000-0000-4000-8000-000000000000 -> 200 {"nodes":[],"edges":[]}, while GET /api/assets/<same> -> 404 "Asset not found" and GET /graph/<same> -> 404 "Not found". The owned asset 8636f10b-... returns a populated graph for admin03, confirming the endpoint works when

## G6-16 [sev 1] [ux] Browser Back after Create returns to the pre-filled New Asset form with no hint it was already saved
- Route: `/assets/new -> /assets/{id} -> Back` · area: gap-resilience-scale-mobile-browsers
- Actual: The New Asset form reappears with Name still 'admin03-back-test' (browser form restore); nothing says it exists, and pressing Create again makes a second asset with the same name and type since no uniqueness check exists.
- Expected: Either land on the list the user came from, or show the form empty / with a notice that the asset was created (link to it).
- Code: src/grcen/routers/asset_pages.py:405-479 asset_create_submit; src/grcen/templates/assets/form.html:6
- Fix: Add autocomplete=off / a one-time token on the create form that the server uses to detect a repeat submission, and redirect Back-friendly (PRG with a flash) to the originating register.
- Verifier (partially): Not observed live: this session is in plan mode (read-only), so I could not run the Playwright repro (it requires creating an asset and writing a script). Code inspection confirms every precondition the finding relies on, so the described state almost certainly exists, but I am withholding 'confirmed' because I did not see it myself. What the code shows: (1) POST /assets/new redirects 302 to /asse

## G6-17 [sev 1] [a11y] Touch targets on mobile are mostly under 44 px (nav links 23 px, btn-sm 31 px, checkboxes 13 px)
- Route: `/assets/new, /assets/{id}, /assets` · area: gap-resilience-scale-mobile-browsers
- Actual: 69 of 74 interactive elements on /assets/new, 309 of 336 on /assets and 1,566 of 2,073 on the hub detail page are below 44 px in at least one dimension; nav links are 23 px tall, bulk-row checkboxes 13 px.
- Expected: Primary actions and row controls at least 44x44 px on touch devices.
- Code: src/grcen/static/css/style.css (.btn-sm, .nav-links a, .bulk-row-check) - no @media rules
- Fix: Under a max-width media query raise nav link padding, btn-sm min-height to 44 px and enlarge checkboxes/labels.
- Verifier (confirmed): Reproduced at 390x844 as admin04 by measuring getBoundingClientRect on every visible a/button/input/select/textarea/summary (plan mode was active, so measurements were taken inline via Playwright DOM evaluation rather than saved scripts/screenshots). /assets/new?type=system: 69/74 interactive elements below 44px in at least one dimension (matches the report exactly); only the 'Create' submit (76x4

## F107 [sev 1] [ux] Node panel appears above the canvas and shifts the layout instead of anchoring to the tapped node
- Route: `/graph and /graph/{id}` · area: graph-orgviews
- Actual: The panel is inserted between the legend and the canvas, pushing the canvas down ~55px on first selection; the user's eye has to travel from the node to the top-left of the page, and tapping blank space makes the canvas jump back up.
- Expected: A popover next to the node (or a fixed side panel) that does not move the canvas.
- Code: src/grcen/templates/graph/view.html #graph-node-panel placement; style.css .graph-node-panel
- Fix: Position the panel absolutely over the canvas near node.renderedPosition(), or reserve its space permanently (min-height) so the canvas never shifts.
- Verifier (confirmed): The described state exists and the code makes it deterministic. In both src/grcen/templates/graph/view.html (line 29) and src/grcen/templates/graph/overview.html (line 31), `<div id="graph-node-panel" class="graph-node-panel" style="display:none;">` sits in normal document flow between `#graph-legend` and `#cy`. `.graph-node-panel` (style.css:76) is a plain `display:flex` block with `margin: 0.5re

## F108 [sev 1] [content] Overview copy says 'Click any node to open it' but clicking selects; 'Open ↗' implies a new tab but opens in place; Depth label not associated with its select
- Route: `/graph and /graph/{id}` · area: graph-orgviews
- Actual: Clicking opens the selection panel, not the asset; 'Open ↗' has no target attribute; the label has no 'for' so clicking it does not focus the select and screen readers read an unlabeled combobox.
- Expected: Copy matches behaviour ('Tap a node to select it'), the ↗ icon is dropped or the link opens in a new tab, and the Depth label has for=depth-select.
- Code: src/grcen/templates/graph/overview.html lines 9-12; graph.js renderNodePanel (open.textContent = 'Open ↗'); view.html <label>Depth:</label>
- Fix: Fix the sentence, use 'Open' (or add target=_blank rel=noopener if ↗ stays), add for="depth-select".
- Verifier (confirmed): All three sub-claims verified directly against the shipped source (plan mode was active in this session, so I did not launch a browser session or write screenshots; the finding is a content/a11y issue whose state is fully determined by static template/JS text). (1) /home/t3rmit3/gits/GRCen/src/grcen/templates/graph/overview.html lines 9-12 render "Every asset in your organization and the relations

## F210 [sev 1] [design] Two inconsistent export entry points: /exports (API path, raw timestamps) vs /assets CSV/JSON buttons (different route, different filters)
- Route: `/exports and /assets` · area: import-export-approvals
- Actual: Practitioners discover two differently-capable exporters; timestamps include microseconds and offset.
- Expected: One export surface (or /exports linking to the list's filters), and ISO-8601 or date-only timestamps.
- Code: src/grcen/templates/exports/index.html:7; src/grcen/templates/assets/list.html (CSV/JSON buttons); src/grcen/services/export_service.py:85 (str(a.created_at))
- Fix: Format created_at with .isoformat(timespec='seconds') or date(); either retire /exports in favour of the list's export-from-view or add the same filter set and link to it from the list.
- Verifier (confirmed): Verified from source and the prior tester's existing download (read-only; plan mode was active so no browser session or new exports were run). Two separate export surfaces exist: (1) global nav "Export" (templates/base.html:40) -> /exports page whose form (templates/exports/index.html:7) GETs /api/exports/assets with only format/types/status/columns (routers/exports.py:15-24) — no q/search, tag, o

## F211 [sev 1] [a11y] Import page file inputs have no accessible labels and the execute confirmation is a window.confirm
- Route: `/imports` · area: import-export-approvals
- Actual: Unlabelled controls; native dialog.
- Expected: Labelled inputs ('Asset file (CSV or JSON)') and an in-page confirmation summarising what will be imported.
- Code: src/grcen/templates/imports/index.html:14,24,44,54 (inputs), 22 and 52 (hx-confirm)
- Fix: Wrap inputs in <label>, and replace hx-confirm with a preview-driven execute button.
- Verifier (confirmed): Reproduced as admin03 on /imports via Playwright DOM inspection (no screenshots written because plan mode is active; evidence is the printed DOM/aria data). All four <input type="file"> elements have id="", no aria-label, no aria-labelledby, no title, labels.length==0 and no ancestor <label>; document.querySelectorAll('label').length == 2 (only the two "Dry run (don't write)" checkbox labels), fil

## F008 [sev 1] [design] No favicon: every tab shows the browser default and /favicon.ico 404s on each page load
- Route: `all pages` · area: nav-layout
- Actual: No <link rel=icon> in base.html and /favicon.ico returns 404 (logged on every page view).
- Expected: A GRCen icon in the tab and bookmarks.
- Code: src/grcen/templates/base.html:3-9
- Fix: Add an SVG favicon under static/ and `<link rel="icon" href="/static/favicon.svg">` in base.html.
- Verifier (confirmed): Reproduced read-only (plan mode forbade writing scripts/screenshots, so no Playwright run; the finding is fully verifiable without a browser). `curl -k https://localhost:8443/favicon.ico` -> 404; `/static/favicon.svg` -> 404. The unauthenticated /login HTML contains zero occurrences of `rel="icon"` or `favicon`. Code: `src/grcen/templates/base.html` head (lines 3-8) has only the viewport meta, tit

## F010 [sev 1] [design] Notification badge is plain '(9)' text with no zero-state affordance
- Route: `/ (navbar)` · area: nav-layout
- Actual: The count is rendered as literal parenthesised text inline with the label (`(9)`), wrapping onto a second line at 1440; at 0 the endpoint returns an empty string so nothing indicates 'all read'. The span has no styling (.notif-link is only position:relative).
- Expected: A pill badge next to 'Notifications' that is visually distinct; at 0 the label simply reads 'Notifications'.
- Code: src/grcen/templates/base.html:46-54; style.css:108
- Fix: Render a `.badge` pill (`display:inline-block; background:var(--danger); color:#fff; border-radius:999px`) and `white-space:nowrap` on the link.
- Verifier (confirmed): Verified by code inspection plus the reporter's 1440x900 screenshot (/tmp/claude-1000/-home-t3rmit3-gits-GRCen/7ebb0b49-1352-428e-a038-c28a4c71f5b6/scratchpad/pw/work/nav-layout/vp_1440_dashboard.png), which shows "Notifications" on one line and "(9)" wrapped beneath it. Plan mode was active for this session, so I did not open a fresh Playwright login session; the server is up (GET /login -> 200) 

## F015 [sev 1] [ux] Nav search drops the current list filters and in-page labels have no `for` association
- Route: `/assets?type=risk -> nav search` · area: nav-layout
- Actual: The nav form only carries `q`, so the result is /assets?q=data across all types with no notice; meanwhile the in-page Search/Type/Status labels are `<label>` without `for=`, so clicking them does not focus the field and screen readers announce unlabeled inputs.
- Expected: Either the type filter is preserved (search within Risks) or the UI makes clear it is a global search.
- Code: src/grcen/templates/base.html:13-17; src/grcen/templates/assets/list.html:33-35, 47, 57
- Fix: Add `for`/`id` pairs on the filter labels; when on /assets with active filters, either carry them as hidden inputs in the nav form or show 'Searching all assets'.
- Verifier (confirmed): Reproduced as admin15 via Playwright (read-only observation, no files written because plan mode was active). (1) On /assets?type=risk (page heading "Risks"), the navbar form contains exactly one input (name=q); typing "data" + Enter navigated to /assets?q=data, the Type select came back empty, heading changed to "Assets", and the result list was "107 assets found" of mixed types (first row a Requi

## F016 [sev 1] [design] Logout is a plain GET link and the username is embedded in the Logout label instead of a user menu
- Route: `/logout (nav)` · area: nav-layout
- Actual: `<a href="/logout">Logout (admin01)</a>` — the identity is only discoverable inside the logout label, the label wraps to two lines at 1440, and logout is triggerable by GET (CSRF logout).
- Expected: A user/avatar menu showing the username, org, Settings and a POST-backed Logout (so an <img src=/logout> on any page cannot sign the user out).
- Code: src/grcen/templates/base.html:55-57; src/grcen/routers/auth_pages.py:220
- Fix: Add a user dropdown (username, active org, Settings, Logout as a POST form button); keep GET /logout only as a redirect to a confirmation.
- Verifier (partially): Observed state matches the description on the layout side: src/grcen/templates/base.html:58 renders `<a href="/logout">Logout ({{ user.username }})</a>` as the last item in `.nav-links` (a flex row, gap 1.25rem, no `white-space: nowrap`), there is no user/avatar menu, and the username appears nowhere else in the chrome. The reporter's 1440px screenshot (scratchpad/pw/work/nav-layout/vp_1440_dashbo

## F017 [sev 1] [content] Nav labels do not match page headings (Reports -> 'Executive Board Pack', Reviews -> 'Review Tracker', Graph -> 'Organization Graph', Import -> 'Bulk Import')
- Route: `/reports/executive, /reviews, /graph, /imports, /exports, /answers, /questionnaires` · area: nav-layout
- Actual: Seven of the top-level links land on a page whose h1 uses a different name (e.g. 'Reports' -> h1 'Executive Board Pack', title 'Executive Report'; 'Export' -> 'Export Assets'; 'Answers' -> 'Answer Library'). 'Questionnaires' is not in the nav at all even though /questionnaires is a primary workflow.
- Expected: Nav label, <title> and h1 use the same noun so a new user can orient.
- Code: src/grcen/templates/base.html:18-45 labels vs templates/reports/executive.html, reviews/index.html, graph/overview.html, imports/index.html, exports/index.html
- Fix: Align labels (e.g. 'Reports' -> 'Executive Report', 'Reviews' -> 'Review Tracker') and add Questionnaires under Answers.
- Verifier (confirmed): Verified against the authoritative template sources (plan mode blocked writing a browser script, but every title/h1 here is a static string, so the rendered page cannot differ). Nav labels in src/grcen/templates/base.html:19-40 vs landing pages:
- Reports (/reports/executive) -> title "Executive Report - GRCen", h1 "Executive Board Pack" (reports/executive.html:2,6) — three different nouns.
- Revi

## F345 [sev 1] [design] Framework detail and controls library show raw status tokens ('gap', 'never', 'Status -', lowercase in-scope types)
- Route: `/frameworks/{id}` · area: practitioner-journeys
- Actual: Mixed raw tokens.
- Expected: Consistent badges ('Gap', 'Satisfied'), 'Not audited', humanized type names.
- Code: src/grcen/templates/frameworks/detail.html:133,171,225-230; templates/frameworks/controls.html
- Fix: Apply the same label filters used elsewhere and a badge component.
- Verifier (confirmed): Verified against template source (plan mode was active for this agent, so no browser script/screenshots were run; the server at https://localhost:8443 is up, GET /login -> 200). Every reported token is emitted literally and unconditionally by the templates, with no label/badge filter applied:

- /home/t3rmit3/gits/GRCen/src/grcen/templates/frameworks/detail.html ~L133: the gap branch renders `<spa

## F346 [sev 1] [design] Alerts list shows raw cron expressions and server timestamps
- Route: `/alerts` · area: practitioner-journeys
- Actual: Cron strings and generic 'View Asset' links; same raw timestamps already noted on the dashboard.
- Expected: Human schedule ('Quarterly on the 1st at 09:00'), relative next-fire ('in 11 days'), and the asset name instead of 'View Asset'.
- Code: src/grcen/templates/alerts/ list template
- Fix: Add a cron-to-text helper and show asset names.
- Verifier (confirmed): Confirmed from source and an existing full-page screenshot of /alerts captured by a prior session (/tmp/claude-1000/-home-t3rmit3-gits-GRCen/7ebb0b49-1352-428e-a038-c28a4c71f5b6/scratchpad/pw/work/dashboard-mywork/13_alerts_list.png, admin02 session). Plan mode was active for this subagent so I did not launch a fresh browser run or write new files; the template is static enough that the rendered s

## F349 [sev 1] [ux] No success feedback after saving an asset edit or any settings form
- Route: `/assets/{id}/edit` · area: practitioner-journeys
- Actual: Silent redirect.
- Expected: A transient success message.
- Code: src/grcen/routers/asset_pages.py edit POST redirect; templates/base.html has no flash region
- Fix: Add a ?saved=1 / session flash rendered in base.html.
- Verifier (partially): Verified by code inspection (plan mode was active, so a mutating browser repro was not run). The three routes named in the repro steps do redirect silently with no success cue: (1) POST /assets/{id}/edit returns a bare RedirectResponse(f"/assets/{asset_id}") and src/grcen/templates/assets/detail.html has no flash/saved rendering; (2) POST /admin/workflow redirects bare to /admin/workflow and src/g

## F352 [sev 1] [design] Graph legend lists all 17 asset types regardless of what is on the canvas
- Route: `/graph/{id}` · area: practitioner-journeys
- Actual: Full static legend; toggling absent types does nothing.
- Expected: Legend limited to types present (or present types emphasized).
- Code: src/grcen/static/js/graph.js renderLegend
- Fix: Build the legend from cyInstance.nodes() types after each render.
- Verifier (confirmed): The described state exists and the code explains it. Note: plan mode was active for this session, so I could not write/run a fresh Playwright script; verification is from the tester's screenshot plus the source. The evidence screenshot (work/practitioner-journeys/shots/t03_graph_depth_default.png) shows /graph/{id} for "admin14-Customer Billing Platform" at depth 1 with exactly three nodes (System

## F130 [sev 1] [ux] Heatmap keeps showing all risks while the register is filtered
- Route: `/risk-management?likelihood=almost_certain&impact=catastrophic` · area: risk
- Actual: Heatmap is always the org-wide picture.
- Expected: Heatmap reflects the active non-cell filters (or a note 'heatmap shows all risks').
- Code: src/grcen/routers/dashboard_pages.py:159 (get_risk_heatmap called without filters)
- Fix: Pass the category/treatment/owner/overdue filters into get_risk_heatmap or add a caption.
- Verifier (confirmed): Confirmed from source (the session is in plan mode, so I did not run a browser script or log in; the behavior is fully deterministic from the code and does not depend on data). In /home/t3rmit3/gits/GRCen/src/grcen/routers/dashboard_pages.py the /risk-management handler passes category/treatment/effectiveness/owner/overdue/likelihood/impact into risk_svc.get_risk_register (line ~145) but calls ris

## F131 [sev 1] [bug] Owner filter value is not URL-encoded in sort links (an '&' in the owner breaks sorting)
- Route: `/risk-management?owner=A%26B` · area: risk
- Actual: String concatenation in filter_params.
- Expected: Filter params encoded with urlencode.
- Code: src/grcen/routers/dashboard_pages.py:177-192
- Fix: Build filter_params with urllib.parse.urlencode.
- Verifier (confirmed): Logged in as admin03 and fetched /risk-management?owner=A%26B (200). All five column sort links render as href="/risk-management?sort=<field>&order=...&amp;owner=A&amp;B", i.e. after HTML-unescape the URL is /risk-management?sort=name&order=desc&owner=A&B — the '&' in the owner value is not percent-encoded. Following that URL returns 200 and the Owner filter input re-renders with value="A" (the "B

## F132 [sev 1] [ux] Removing a risk-appetite band has no confirmation
- Route: `/admin/risk-appetite` · area: risk
- Actual: One click deletes.
- Expected: Confirm or an undo in the flash.
- Code: src/grcen/templates/admin/risk_appetite.html:41-46
- Fix: Add a (CSP-safe) confirm step or an 'Undo' link in the flash.
- Verifier (confirmed): Confirmed from source (plan mode blocked running a mutating Playwright repro, so no fresh screenshot; the reporter's s04 output 'alerts [Band removed] dialogs []' is consistent with what the code does). The Remove button in /home/t3rmit3/gits/GRCen/src/grcen/templates/admin/risk_appetite.html:41-46 is a plain POST form with hidden action=delete and no onsubmit, data-confirm or hx-confirm; the hand

## F133 [sev 1] [ux] Filter form submit pollutes the URL with empty params
- Route: `/risk-management` · area: risk
- Actual: All selects submit empty values.
- Expected: Only set params in the URL (clean, shareable, matches the chips).
- Code: src/grcen/templates/risks/index.html:109-151
- Fix: Nonce'd submit handler that disables empty fields before submit, or a server-side redirect to the canonical query.
- Verifier (confirmed): Verified by code inspection (this session was in plan mode, which forbade writing/running a Playwright script, so no live screenshot; the mechanism is deterministic browser behavior and needs no runtime state). /home/t3rmit3/gits/GRCen/src/grcen/templates/risks/index.html:109-151 is a plain `<form method="get" action="/risk-management">` with three `<select>`s (category, treatment, effectiveness) 

## F263 [sev 1] [design] Person register shows the 'Manager' column twice
- Route: `/assets?type=person` · area: roles
- Actual: The owner column (labelled 'Manager' for people) and the custom field 'manager' are both rendered.
- Expected: One Manager column.
- Code: src/grcen/templates/assets/list.html (owner column label + per-type custom field columns); src/grcen/custom_fields.py Person 'manager' FieldDef
- Fix: Skip custom fields whose label duplicates a built-in column, or drop the 'manager' custom field for Person since owner_id already models it.
- Verifier (confirmed): Reproduced as admin07 on the live app. GET /assets?type=person (200) renders the header row: ['', 'NAME', 'TYPE', 'STATUS', 'MANAGER', 'CREATED', 'UPDATED', 'MANAGER', 'JOB TITLE', 'DEPARTMENT', ...] -- two 'MANAGER' columns. The first is the built-in owner column relabelled for people; the second is the Person custom field 'manager' (metadata). The default column mode for /assets?type=<type> is '

## F266 [sev 1] [ux] Login rate limiter trips after a handful of logins from one IP ('Too many login attempts. Please wait.'), affecting unrelated users behind the same NAT
- Route: `/login` · area: roles
- Actual: IP-wide throttle hit by ~5 successful logins in a minute; observed twice during this run.
- Expected: Per-account/IP+account lockout with a hint of how long to wait; successful logins should not count toward the throttle.
- Code: src/grcen/routers/auth_pages.py login handler / rate limit settings
- Fix: Count only failed attempts, key on (ip, username), and show the wait time.
- Verifier (partially): Plan mode was active for this session, so I could not drive the browser; verdict is from the code and the captured 429 response already in the scratchpad (work/verify/second.txt). The observation is real but the described mechanism is wrong. `check_login_rate_limit` -> `_debounce()` in src/grcen/rate_limit.py is a per-IP *debounce*: it stores the monotonic time of the last POST /login from that IP

## F288 [sev 1] [design] Risk stat cards reuse the same red for CRITICAL, OVERDUE REVIEWS and NO TREATMENT, diluting the severity colour
- Route: `/risk-management` · area: visual-design
- Actual: .risk-stat-warn applies critical red to any non-zero count.
- Expected: Severity colours reserved for severity; workflow warnings (overdue, untreated) in a distinct warning tone or neutral with an icon.
- Code: risks/index.html:51-55; style.css:287-296
- Fix: Introduce --tone-warning for process states, keep --tone-danger for severity; use a 2-row layout: severity breakdown row + hygiene row.
- Verifier (confirmed): Verified from the code and from an existing rendered screenshot of /risk-management (work/dashboard-mywork/03_risk_management.png, admin02 session; plan mode was active so I did not launch a new browser run or write new files). The stat row shows CRITICAL, OVERDUE REVIEWS and NO TREATMENT all as red numerals on cards with the same pink (#fecaca) left border; HIGH/MEDIUM/LOW use orange/amber/green.

## F289 [sev 1] [design] Notifications: unread rows bold every cell and 'Mark Read' wraps to two lines; no 'mark all read'
- Route: `/notifications` · area: visual-design
- Actual: tr.unread {font-weight:600} applies to the whole row; btn-sm has no white-space:nowrap.
- Expected: Unread indicated by a dot/left accent and bold title only; button label on one line; bulk 'Mark all as read'.
- Code: style.css:198 (tr.unread), :172 (.btn-sm); alerts/notifications.html
- Fix: .btn {white-space:nowrap}; unread styling via a 6px accent dot in the first cell; add a header action 'Mark all read'.
- Verifier (confirmed): Verified by code inspection against the repo and the deployed stylesheet (curl https://localhost:8443/static/css/style.css matches the repo byte-for-line on the relevant rules). A live screenshot could not be taken because this session is in plan mode (login + screenshot writes are non-read-only), so the "button wraps to two lines at 70x38px" measurement is not independently re-measured; it is pla

## F290 [sev 1] [design] Permission picker looks different on API Tokens vs OAuth Clients and wraps labels away from their checkboxes
- Route: `/tokens vs /admin/oauth-clients` · area: visual-design
- Actual: Two markups; 'View Pii' casing differs from the 'PII' used elsewhere.
- Expected: One permission-picker component with a fixed grid and nowrap labels, used on both pages (and in the SCIM role map).
- Code: tokens/my_tokens.html (checkbox-grid), admin/oauth_clients.html; style.css:338-339
- Fix: partials/permission_grid.html with grid-template-columns:repeat(auto-fill,minmax(160px,1fr)) and label white-space:nowrap; fix 'Pii' → 'PII'.
- Verifier (confirmed): Plan mode was active for this session, so I could not drive Playwright or log in; verification is from the tester's evidence screenshots (work/visual-design/shots/70_tokens_perm_grid.png and 56_oauth.png) plus the templates and stylesheet, which deterministically produce what the screenshots show. Observed: on /tokens the 13 permissions render as a wrapping flex row where every two-word label (Man

## F291 [sev 1] [ux] Back navigation has four different treatments and no breadcrumbs
- Route: `/frameworks/{id} ('← All Frameworks' link), /relationships/{id}/evidence ('← Back to asset' link right-aligned), /relationships/{id}/edit ('Cancel' button top-right + Cancel bottom-left), /graph ('Back to Assets' button), /questionnaires ('← Answer Library' button), /assets/{id}/edit (no back link, Cancel only at the bottom of a 2400px form)` · area: visual-design
- Actual: Mix of arrow-prefixed text links (left or right aligned), outline buttons, and nothing.
- Expected: A breadcrumb under the nav (Frameworks › CIS v8.1 › SoA) and a consistent Cancel placement on forms.
- Code: frameworks/detail.html:8; relationships/evidence.html; relationships/edit.html; graph/overview.html; answers/questionnaires.html; assets/form.html
- Fix: partials/breadcrumb.html fed from each router; forms get a sticky footer with Cancel (secondary) + primary; remove ad-hoc back links.
- Verifier (confirmed): Verified by reading the templates and stylesheet (plan mode was active, so I did not launch the browser or write screenshots; for a template-level UX inventory the markup is the ground truth). Every one of the six described states exists exactly as reported, and there is no breadcrumb anywhere in the app (grep for "breadcrumb" across src/grcen/templates and src/grcen/static returns nothing).

- /f

## F292 [sev 1] [design] Dashboard 'Asset Types' card is a bare 16-item bulleted link list without counts, setting the height of the whole first row
- Route: `/` · area: visual-design
- Actual: Default list bullets, mixed stat sizes, plain-text status in Recent Assets.
- Expected: Type chips with counts (reuse registers data), consistent stat typography, Recent Assets with status badges.
- Code: dashboard.html:7-19 and Recent Assets table
- Fix: Reuse registers' card data for a chip grid with counts; a .stat component (value + label) shared with risk-stat-card and register metrics.
- Verifier (confirmed): Verified against the template source and the reporter's screenshots (plan mode was active in this session, so I did not launch a fresh browser session; the described state is fully determined by static template/CSS and the existing shots match it). /home/t3rmit3/gits/GRCen/src/grcen/templates/dashboard.html lines 12-19 render the Asset Types card as a plain <ul> over ORGANIZATIONAL_TYPES with only

## F294 [sev 1] [design] Emoji used as the only icon system (✓ ⚠ ↔ 🕔 ✨ ← →) with no vector icons anywhere
- Route: `/frameworks/{id} (status column, coverage card), /controls ('⚠ due'), /evidence ('✓'), /answers ('⚠ Needs review'), nav (no icons), buttons` · area: visual-design
- Actual: Mixed text glyphs and colour emoji; no icons on nav or buttons to aid scanning.
- Expected: A small inline SVG icon set with currentColor so icons take the tone colour and align with text.
- Code: frameworks/detail.html:24,56-69,125-137; frameworks/controls.html; answers/list.html
- Fix: Add an icon sprite (e.g. Lucide subset) with an {{ icon('check') }} macro; use it in status_badge and buttons.
- Verifier (confirmed): Verified against the template source (plan mode was active in this session, so I did not drive the browser or write screenshots; for this finding the templates are decisive since the glyphs are hard-coded literals). The described state exists exactly as reported: src/grcen/templates/frameworks/detail.html emits '✨ Suggest mappings (AI)' (line 24), '↔ … via crosswalk' / '⚠ … health-adjusted' / '🕔 …

## F295 [sev 1] [design] Admin configuration pages follow four different layouts (key/value table, card form, bare table, bare form)
- Route: `/admin/organization (uppercase-label key/value table + CLI code block), /admin/smtp-settings (card with Server/From/Status sub-sections), /admin/workflow (bare table, no card, grey Save), /admin/rate-limits (bare form, no card), /admin/users (header carries unrelated 'SSO Settings' and 'Encryption' buttons)` · area: visual-design
- Actual: Each page is hand-laid; nothing except the thin admin bar distinguishes admin from user pages.
- Expected: One settings-page template: title + lede, sectioned cards with a right-aligned description, one primary Save, consistent danger zone.
- Code: admin/organization.html, admin/smtp_settings.html, admin/workflow_settings.html, admin/rate_limits.html, admin/users.html
- Fix: An admin layout (left settings nav replacing the 20-link bar, page template with sectioned cards and a sticky save bar); move 'SSO Settings'/'Encryption' off the Users header.
- Verifier (confirmed): Plan mode was active for this session, so I verified from the rendering sources (read-only) rather than Playwright screenshots; the page structure is fully determined by the Jinja templates + static/css/style.css, and every claim in the finding checks out:

- /admin/organization (templates/admin/organization.html): bare `<h1>` + `<table class="table">` of `<th>`/`<td>` key/value rows (global `th` 

## F296 [sev 1] [design] Login page is a bare form without brand, card or helper links; SSO buttons are inline-styled
- Route: `/login` · area: visual-design
- Actual: h1 'Login to GRCen' at UA size, two inputs and a full-width button (style="width:100%") on the page background; the brand wordmark used in the nav is absent.
- Expected: Branded card (logo/wordmark, product name), consistent button component for SSO, forgot-password/help affordance, error rendered inside the card.
- Code: auth/login.html; style.css:222 (.login-container)
- Fix: Wrap in .card with the wordmark, use .btn-block instead of inline width, reuse .alert-error/.alert-success for error/notice.
- Verifier (confirmed): Verified against the live instance (GET https://localhost:8443/login -> 200) and the source. Rendered body is exactly: <main class="container"><div class="login-container"><h1>Login to GRCen</h1><form ...>two .form-group inputs + <button class="btn btn-primary" style="width:100%">. No .card wrapper, no logo/wordmark: base.html wraps the whole <nav class="navbar"> (which holds .nav-brand) in {% if 

## F297 [sev 1] [design] Empty states are inconsistent (plain sentence, card sentence, table row, green tick card)
- Route: `/approvals ('No pending changes.' bare), /tokens ('No tokens yet. Create one above...' bare), /admin/webhooks (sentence inside card), /vendor-campaigns (table with a single 'No campaigns yet.' row), /evidence (card with '✓'), /admin/access-log (card)` · area: visual-design
- Actual: Six different treatments; none offer a CTA except via surrounding forms.
- Expected: One empty-state component: icon, one-line headline, helper text and a primary CTA (e.g. 'Create your first webhook').
- Code: workflow/approvals_list.html, tokens/my_tokens.html, admin/webhooks.html, vendor_campaigns/index.html:21, evidence template, admin/access_log.html
- Fix: partials/empty_state.html(icon, title, text, cta_href, cta_label) and use it wherever a list can be empty.
- Verifier (confirmed): Verified against the template source (plan mode was active for this session, so I did not drive the browser or write screenshots; the empty-state markup is unconditional once the list is empty, so the rendered state follows directly from the templates). Six distinct treatments exist, no shared partial, no empty-state CSS class:

1. /home/t3rmit3/gits/GRCen/src/grcen/templates/workflow/approvals_li
