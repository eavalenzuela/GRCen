# GRCen Usability Testing Plan

**Status:** Draft v1 · **Owner:** GRCen team · **Date:** 2026-06-05

This plan defines how we will evaluate the real-world usability of GRCen by standing
up a live instance on AWS and running moderated, task-based sessions with GRC
practitioners. It is the companion to `feature_roadmap.md` (what we built) — this is
about *whether people can actually use it*.

---

## 1. Goals & Research Questions

**Primary goal:** Find the friction. Identify where a competent GRC practitioner gets
confused, blocked, slowed, or surprised — and rank those issues by severity so we know
what to fix before a wider release.

**Secondary goal:** Validate that the core value proposition — *"map assets and their
relationships as a graph, searchable from any node"* — is legible and useful to people
who do GRC work for a living.

### Research questions

1. **Onboarding** — Can a new user get from a fresh instance to a useful, populated
   workspace without hand-holding? Where do they stall?
2. **Mental model** — Does the asset/relationship *graph* model match how practitioners
   think about their compliance estate, or do they fight it? Do the 17 asset types map
   to concepts they recognize?
3. **Core workflows** — Can users complete the canonical journeys (create→link→graph,
   import, risk register, framework dashboard, questionnaire fill, approvals) without
   moderator intervention?
4. **Findability** — Is the navigation discoverable? Can users find features (export,
   saved searches, tags, evidence attachment) without being told where they live?
5. **Trust & comprehension** — Do users understand what the system is telling them
   (coverage bars, gap highlighting, risk heatmap, freshness flags, redaction)?
6. **GRC-domain fit** — Does this feel credible to an auditor/risk manager, or does it
   miss table-stakes expectations from tools like a real GRC suite?

---

## 2. Methodology

**Moderated think-aloud, task-based usability testing**, run against a live AWS
instance seeded with realistic synthetic data.

- Participants narrate their thoughts continuously ("I'm looking for… I expected this
  to…"). The moderator stays quiet except to probe ("What did you expect to happen?")
  and to unblock only after a genuine struggle (see *intervention protocol*, §7).
- Each session is screen-shared and recorded (with consent), ~60 minutes.
- We capture both **performance metrics** (success, time, errors) and **qualitative
  insight** (confusion points, verbatim quotes, severity).

### Three-stage rollout

| Stage | Who | Purpose |
|-------|-----|---------|
| **0 — Expert self-eval** | You (moderator, practicing GRC/audit pro) | Heuristic + dogfood pass against the full task suite. Shakes out blocking bugs and obvious UX defects *before* spending recruited-participant time. Doubles as a moderator dry-run. |
| **1 — Pilot** | 1 external GRC pro | Validate the script, tasks, timing, and the AWS environment with a real outside user. Fix the plan, then proceed. |
| **2 — Main round** | 4–7 external GRC pros | The real study. Run until findings saturate (new sessions stop surfacing new issues — typically ~5 users catch the large majority of problems). |

You occupy a dual role: you are both the **moderator** and **Participant 0**. Because you
do GRC/audit work professionally, Stage 0 is a legitimate expert evaluation, not just a
smoke test — log your own findings with the same rigor (and the same severity scale) as
the recruited sessions. Keep a separate hat on: when moderating others, suppress your own
"I'd do it this way" instinct and let them struggle.

---

## 3. Participants

### Target profile

Practicing GRC / security / audit professionals who would plausibly *use* a tool like
this. Mix of:

- **Compliance Manager** — owns frameworks, evidence, audit readiness
- **Risk Analyst / Manager** — lives in the risk register and heatmap
- **Auditor (internal or external)** — read-heavy; cares about evidence chains, audit trail, gap reports
- **Security Engineer / GRC Engineer** — imports data, builds the asset graph, integrates
- **IT / Admin** — user mgmt, SSO, workflow gating, tenancy

### Sample

- **Stage 0:** you.
- **Stage 1:** 1 pilot participant.
- **Stage 2:** 4–7 participants, ideally ≥1 from each of the roles above, weighted toward
  Compliance Manager and Auditor (the heaviest day-to-day users).

### Screening

Recruit people who: (a) have done GRC/compliance/audit/risk work in the last ~3 years,
(b) have used at least one GRC, ticketing, or asset-inventory tool, (c) have **not**
seen GRCen before. Capture years of experience, primary role, and tools they use today
in a short pre-screen.

---

## 4. Test Environment (AWS)

> Detailed stand-up steps live in §11. This section is the *requirements* the environment
> must satisfy for valid testing.

- **Live, internet-reachable instance** over HTTPS (TLS — a GRC tool over plain HTTP
  poisons trust and skews feedback). Real domain or at least a valid cert.
- **Realistic seeded data**, not an empty instance — load `sample_data/` so dashboards,
  graphs, risk heatmap, and framework coverage are populated. An empty instance tests
  onboarding only; a populated one tests the actual work. (We test *both* — see Task 1.)
- **One test account per role** (Admin, Editor, Viewer, Auditor) with known credentials,
  plus the ability to mint a fresh account for the onboarding task.
- **Synthetic data only.** No real customer/employee/compliance data — avoids any privacy
  exposure and lets us share screens freely. `sample_data/` is already synthetic.
- **Isolated, disposable, and reset-able** between participants so each user starts from a
  known state (one participant's edits shouldn't confuse the next). Plan a reset step.
- **Pre-configured optional features** so they're testable without setup burden:
  workflow gating enabled on at least one asset type, at least one alert scheduled,
  a framework present (via `grcen sync-catalog` or seeded), and a couple of answer-library
  entries + one inbound questionnaire.
- **Monitoring:** tail app logs during sessions to correlate user confusion with errors,
  and to catch 500s the user might not report.

**Cost/teardown:** size it small (single small EC2 / lightweight container host +
managed Postgres or a co-located Postgres container). Tear it down between rounds; it
does not need to be long-lived.

---

## 5. Task Scenarios

Tasks are written as **realistic goals**, not click-by-click instructions — we want to
see how users *find* the path. Each has a defined start state, success criterion, and the
underlying route(s) for the moderator's reference. Scenarios escalate from simple to
complex. Aim for ~7–9 tasks per 60-min session; pick per participant's role.

> Moderator note: give the participant the *scenario narrative* only. The routes/criteria
> columns are your cheat-sheet, not theirs.

### Task 1 — First contact / onboarding (fresh account)
**Scenario:** "You've just been given login credentials to a brand-new GRCen instance.
Get oriented and tell me what you think this tool is for and what you'd do first."
- **Start:** fresh login, empty or near-empty workspace.
- **Success:** user articulates the asset/graph concept and identifies a sensible first
  action (create or import an asset) unaided.
- **Watch for:** Do they understand the empty dashboard? Do they find a way to add data?
- **Routes:** `/login` → `/` → `/assets/new` or `/imports`.

### Task 2 — Create an asset and link a relationship
**Scenario:** "Add a new *System* called 'Customer Billing Platform.' Then record that
it's owned by an existing person and that it processes an existing data category."
- **Success:** asset created; ≥1 relationship created to existing assets.
- **Watch for:** Do they discover relationships at all? Drag-to-link on the graph vs. form?
  Do the relationship types/descriptions make sense to them?
- **Routes:** `/assets/new?type=system` → `/assets/{id}` → `/graph/{id}` (drag-to-link) or
  relationship form.

### Task 3 — Explore the graph
**Scenario:** "Starting from that system, show me everything it's connected to, two hops
out. Find a related asset you didn't expect and open it."
- **Success:** navigates the Cytoscape graph, expands, opens a connected node.
- **Watch for:** graph legibility, controls discoverability, orientation/getting lost.
- **Routes:** `/graph/{asset_id}`.

### Task 4 — Bulk import
**Scenario:** "You have a spreadsheet of vendors to load. Import them, check the preview
looks right, and commit."
- **Start:** provide a small synthetic vendor CSV.
- **Success:** preview reviewed, import executed, vendors appear in `/assets`.
- **Watch for:** Do they trust the preview? Do they understand errors/validation? Column
  mapping confusion?
- **Routes:** `/imports` → preview → execute → `/assets`.

### Task 5 — Risk register & heatmap
**Scenario:** "Record a new risk: 'Vendor outage disrupts billing,' likely and
high-impact. Then find your top risks and tell me which one needs attention first."
- **Success:** risk created with likelihood/impact; user reads the heatmap and identifies
  top/overdue risks.
- **Watch for:** Is the 5×5 heatmap legible? Do they understand inherent vs. control-
  adjusted scoring and the trend arrows? Bulk-update discoverability.
- **Routes:** `/assets/new?type=risk` → `/risk-management`.

### Task 6 — Framework coverage & gap report
**Scenario:** "An auditor asks how you're tracking against [seeded framework]. Show
coverage, find a gap, and produce something you could hand to the auditor."
- **Success:** opens framework detail, interprets coverage bars + gap highlighting,
  exports the gap report (CSV or PDF).
- **Watch for:** Do they understand what "covered" means and what satisfies a requirement?
  Do they find the export?
- **Routes:** `/frameworks` → `/frameworks/{id}` → gap-report.csv/.pdf.

### Task 7 — Evidence attachment
**Scenario:** "Attach a piece of evidence (a document or a URL) proving that a control
satisfies a requirement."
- **Success:** attaches evidence to a relationship (or asset).
- **Watch for:** Do they realize *relationships* can hold evidence, not just assets? Is
  the attachment UI discoverable from where they expect it?
- **Routes:** `/relationships/{rel_id}/evidence`.

### Task 8 — Inbound questionnaire (answer library)
**Scenario:** "A prospect sent you a security questionnaire. Import it, answer two
questions using your existing answer library, and export a response."
- **Success:** questionnaire imported, ≥2 questions filled (≥1 mapped to a library
  answer with auto-fill), response exported.
- **Watch for:** Do they grasp the canonical-answer / reuse concept? Freshness flags?
- **Routes:** `/questionnaires` → `/questionnaires/{id}` → import → fill → export.

### Task 9 — Search & findability (open-ended)
**Scenario:** "Find every asset tagged 'pci' that you own and that's overdue for review.
Save this search so you can come back to it."
- **Success:** uses filters/tags, creates a saved search.
- **Watch for:** filter discoverability, tag system comprehension, saved-search feature
  discovery.
- **Routes:** `/assets?tag=pci...` → saved search.

### Task 10 — Admin / governance (Admin-role participants only)
**Scenario:** "Set it so that deleting a *Policy* requires approval. Then, as an approver,
review a pending change someone submitted."
- **Success:** enables workflow gating; processes an item in the approvals queue.
- **Watch for:** Is gating config understandable? Is the approvals queue obvious? Self-
  approval block surprising?
- **Routes:** `/admin/workflow` → `/approvals` → `/approvals/{id}`.

**Stretch tasks** (if time / by role): configure an alert (`/alerts`), invite a user
(`/admin/users/new`), read the audit log (`/admin/audit`), switch org (`/switch-org`),
export filtered assets (`/exports`).

---

## 6. Metrics & Instruments

### Quantitative (per task)
- **Task success** — Complete / Partial / Fail (with reason).
- **Time on task** — start to success or give-up.
- **Errors** — wrong turns, dead ends, mis-clicks; note recoverable vs. fatal.
- **Assists** — number of moderator nudges needed (0 = ideal).
- **Single Ease Question (SEQ)** — after each task: *"Overall, how difficult or easy was
  that task?"* (1 = very difficult … 7 = very easy).

### Quantitative (per session)
- **SUS (System Usability Scale)** — the 10-item standard questionnaire at session end.
  Gives a 0–100 score benchmarkable against the ~68 industry average.
- **Net top issues** — participant's self-reported biggest frustration and favorite thing.

### Qualitative
- Think-aloud verbatim quotes (tag confusion, delight, expectation mismatches).
- Mental-model mismatches ("I thought X would be under Y").
- Domain-credibility gaps ("a real GRC tool would also…").
- Moderator observations of non-verbal struggle (hesitation, backtracking).

### Pre/post
- **Pre-test:** role, years of GRC experience, tools used today, expectations.
- **Post-test:** SUS, would-you-use-this, top 3 changes you'd make.

---

## 7. Session Protocol

**Duration:** ~60 min. **Structure:**

1. **Intro (5 min)** — Purpose, consent to record, "we're testing the software, not you,"
   think-aloud instructions, "there are no wrong answers, getting stuck is *useful data*."
2. **Pre-test questions (3 min).**
3. **Warm-up (2 min)** — "Log in and just look around for a minute — what do you think
   this is?" (Captures first impressions = Task 1.)
4. **Tasks (40 min)** — Work the scenario list, SEQ after each.
5. **Post-test (8 min)** — SUS + wrap-up questions + biggest frustration / favorite.
6. **Thanks (2 min).**

### Intervention protocol
Let participants struggle — struggle is the finding. Escalate only when truly stuck:
1. Stay silent ~30–60s after they go quiet.
2. Reflective prompt: *"What are you trying to do right now? What did you expect?"*
3. Only if still blocked and it threatens the rest of the session: give the minimal hint
   to unblock, and **log it as a task failure / assist**.

### Moderator discipline (important — you are a domain expert)
Because you do this work professionally, you will instinctively know where to click. When
moderating others, **do not lead**. Don't finish their sentences, don't react to wrong
turns, don't defend the design. Your expert opinions belong in your Stage-0 notes, not in
participants' sessions.

---

## 8. Data Capture

- **Live note template** per session: task grid (success / time / errors / assists / SEQ)
  + a running quote/observation log. One file per participant.
- **Screen recording** (with consent) for later review of anything missed live.
- **App logs** captured server-side for the session window, to correlate confusion with
  actual errors/500s.
- Store all session artifacts as synthetic-data-only and access-controlled.

A suggested per-session note skeleton:

```
Participant: P_  | Role: ____ | Date: ____
Pre-test: years GRC __ | tools today: ____ | expectation: ____
--- Tasks ---
T1 onboarding   success/partial/fail  time __  errors __  assists __  SEQ _  notes:
T2 create+link  ...
...
--- Post ---
SUS: __/100   Would use? __   Top issues: 1)__ 2)__ 3)__   Loved: ____
Key quotes:
```

---

## 9. Analysis & Severity

After each session, write up notes within 24h while fresh. After the round:

1. **Affinity-map** all observations into issue clusters.
2. **Rate each issue's severity** (Nielsen-style 0–4):
   - **4 Catastrophic** — blocks task completion / data loss / breaks trust. Fix before release.
   - **3 Major** — serious difficulty, many users hit it. High priority.
   - **2 Minor** — slows users, recoverable. Fix if cheap.
   - **1 Cosmetic** — annoyance only. Backlog.
   - **0 Not a problem.**
3. **Frequency × severity** — count how many participants hit each issue; an issue 5/5
   users hit, even if "minor," ranks up.
4. **Output:** a prioritized findings report — each issue with severity, frequency,
   evidence (quote/clip/route), and a concrete recommendation. Feed the catastrophic/major
   items into `feature_roadmap.md` or an issue tracker.
5. **Benchmark:** report aggregate SUS and per-task success rate / mean SEQ to track
   progress across future rounds.

---

## 10. Timeline (indicative)

| Phase | Effort |
|-------|--------|
| Finalize plan, write task sheets, build note templates | 0.5 day |
| Stand up + seed AWS environment (§11) | 0.5–1 day |
| Stage 0: your expert self-eval pass | 0.5 day |
| Fix blocking bugs found in Stage 0 | as needed |
| Stage 1: pilot session + plan fixes | 0.5 day |
| Stage 2: 4–7 sessions (~1–1.5 hr each incl. notes) | 2–3 days |
| Analysis, severity, findings report | 1 day |

Recruiting external GRC pros runs in parallel and is usually the long pole — start it
early.

---

## 11. AWS Stand-Up Checklist (Phase 2)

Concrete steps to produce the environment §4 requires. (We'll execute this next.)

1. **Provision host** — small EC2 (or container host) + Postgres (managed RDS or a
   co-located Postgres container via `docker-compose.prod.yml`).
2. **DNS + TLS** — point a hostname at it, terminate HTTPS (ACM + ALB, or Caddy/nginx +
   Let's Encrypt in front of the app). No plain HTTP.
3. **Configure secrets** — `ENCRYPTION_KEY`, DB creds, session secret, etc. via the prod
   compose/env. Review `deploy/` and `docker-compose.prod.yml`.
4. **Bootstrap** — run `grcen createadmin` (and `grcen createorg` if non-default) to make
   the first admin + org.
5. **Seed data** — import `sample_data/assets.csv` + `relationships.csv` (via `/imports`
   or CLI), run `sample_data/seed_alerts.py`, and `grcen sync-catalog` a framework so the
   frameworks dashboard lights up. Add a couple of answer-library entries + one inbound
   questionnaire.
6. **Create per-role test accounts** — Admin / Editor / Viewer / Auditor with known creds;
   pre-enable workflow gating on one asset type and queue a pending change for Task 10.
7. **Smoke test** — walk every task path yourself (this *is* Stage 0).
8. **Reset mechanism** — snapshot the seeded DB state so you can restore between
   participants (e.g. `grcen backup` / `restore`, or a DB snapshot).
9. **Log tailing** — confirm you can watch app logs live during sessions.
10. **Teardown plan** — tear down or stop between rounds to control cost; nothing here is
    long-lived.

---

## 12. Out of Scope

- Performance/load testing (separate effort).
- Security testing/pentest (see `security_features_and_requirements.md`).
- Accessibility audit (worth doing — flag as a follow-up).
- Quantitative A/B testing (sample too small; this is qualitative discovery).

---

## 13. Open Decisions

- Final participant count and role mix (recruiting-dependent).
- Incentives for external participants.
- Recording/consent specifics per participant's employer policy.
- Whether to run a second round after fixes (recommended for catastrophic/major issues).
