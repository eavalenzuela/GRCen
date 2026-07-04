# Planned Improvements & Features

Plan for this maintenance pass. Items marked with a roadmap reference close a
"Remaining:" line in `feature_roadmap.md`.

## Improvements (existing behavior / robustness / UX / docs)

1. **Webhook delivery retry with exponential backoff** — a transient 5xx or
   network blip currently loses the delivery forever; retry up to 3 attempts
   with backoff and record each attempt (`webhook_deliveries.attempt`).
   (Roadmap #2 remaining.)
2. **New webhook event types `asset.created` / `asset.deleted`** — outbound
   integrations today only hear about `alert.fired`; dispatch asset lifecycle
   events (in the background, off the request path) from the single-asset
   REST + page routes so SIEM/SOAR consumers can react. (Roadmap #2 remaining.)
3. **Control-library CSV export at `/controls/export.csv`** — the inverted
   controls view exists but can't be handed to an auditor; export every
   control with the requirements/frameworks it satisfies, recorded in the
   data-access log. (Roadmap #4 remaining.)
4. **Content-pack installed-version tracking + update indicator** — installs
   don't record which pack version seeded the org, so upgrades are invisible;
   store the version in `app_settings` per org and surface "update available"
   in `/admin/content-packs` and `grcen list-packs`. (Roadmap #4a remaining.)
5. **Workflow self-approval override** — small teams get wedged when the only
   approver is also the submitter; add `workflow_config.allow_self_approval`
   (per type, off by default) honored by approve/reject. (Roadmap #13 remaining.)
6. **Admin email template preview** — admins currently configure SMTP blind;
   render the alert/digest HTML templates with sample data at
   `/admin/smtp-settings/preview`. (Roadmap #20 remaining.)
7. **Backup format v2: per-backup random HKDF salt** — every backup today is
   keyed with the fixed salt `backup-salt`; embed a fresh random salt in the
   file header (v1 files still restore) so backups don't share derived keys.
   (Roadmap #19 remaining.)
8. **Rate-limit headers on allowed responses** — clients only learn their
   budget when already throttled (429); return `X-RateLimit-Limit` /
   `X-RateLimit-Remaining` on allowed API responses so they can self-throttle.
   (Roadmap #17 quality-of-life.)
9. **Sampled data-access logging for list endpoints** — list reads are
   currently invisible in the access log; add a config-gated sample rate
   (default 0 = off, preserving current volume) applied to the asset list
   endpoints. (Roadmap #15 remaining.)
10. **Docs refresh** — update `feature_roadmap.md` "Remaining:" lines,
    `CLAUDE.md`, and the README for everything shipped in this pass so the
    docs stay the source of truth they're treated as.

## New Features

11. **Self-service org invitations** — admins mint single-use, expiring invite
    links (`org_invitations`) for a role in their org; the invitee sets a
    username/password on a public accept page and lands in the right org.
    (Roadmap #12 remaining.)
12. **iCal review-calendar feed** — review dates and audit schedules matter in
    calendars, not dashboards; a per-user feed token exposes upcoming reviews
    as `/calendar/{token}/grcen.ics` consumable by Outlook/Google Calendar.
13. **Asset watchers** — users can watch any asset and get an in-app
    notification when it is updated or deleted, closing the "someone changed
    the policy I audit" gap without polling the audit log.
14. **OAuth 2.0 client-credentials flow** — machine integrations currently use
    long-lived static tokens; add `oauth_clients` (hashed secrets) and
    `POST /api/oauth/token` issuing short-lived scoped bearer tokens through
    the existing `api_tokens` machinery. (Roadmap #19 remaining.)
15. **"My Work" page** — a per-user `/my-work` view aggregating approvals
    waiting on me, my assets due/overdue for review, findings I own, and
    answers needing review, so daily GRC work starts from one place.
