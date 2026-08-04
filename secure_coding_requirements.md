# Secure Coding Requirements

Rules for writing new code in GRCen. Every pull request must conform to these requirements.

---

## 1. Input Validation (Pydantic Schemas)

Every request model in `src/grcen/schemas/` must use `Field()` constraints:

| Field type | Required constraints |
|---|---|
| Short strings (names, titles) | `Field(min_length=1, max_length=255)` |
| Long text (descriptions, messages) | `Field(default=None, max_length=10000)` |
| Usernames | `Field(min_length=3, max_length=150, pattern=r"^[a-zA-Z0-9_.\-]+$")` |
| Passwords | `Field(min_length=8, max_length=128)` |
| URLs/paths | `Field(max_length=2048)` |
| Lists | `field_validator` enforcing max items and per-item max length |
| Enums/choices | `field_validator` checking against an explicit set of allowed values |

Never trust that a query parameter or path parameter is safe. Validate at the boundary.

---

## 2. CSRF Protection

**HTML form pages** (the `pages` router) have CSRF enforcement via a router-level dependency. To add a new form:

1. Include the hidden input in your `<form method="post">`:
   ```html
   {% include "partials/csrf.html" %}
   ```
2. The existing `_csrf_check` dependency on the pages router handles verification automatically.

**JSON API routes** (`/api/*`) skip CSRF — they rely on `SameSite=Lax` cookies and Bearer tokens.

**Never** remove or weaken the CSRF check. If a new router serves HTML forms, it must have an equivalent dependency.

---

## 3. CSP Nonces for Inline Scripts

The CSP header allows inline `<script>` blocks **only** with a per-request nonce. Every inline script tag must include it:

```html
<script nonce="{{ request.state.csp_nonce }}">
    // your code here
</script>
```

External scripts loaded from `'self'` (e.g. `<script src="/static/js/foo.js">`) do not need a nonce.

**Never** add `'unsafe-inline'` to `script-src`. If you need inline JS, use the nonce.

`style-src` currently allows `'unsafe-inline'` because inline `style=""` attributes are used throughout. Avoid adding `<style>` blocks; prefer CSS classes in static stylesheets.

---

## 4. Authentication & Authorization

Every route that accesses data must declare a permission dependency:

```python
from grcen.routers.deps import require_permission
from grcen.permissions import Permission

@router.get("/")
async def list_things(
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    ...
```

Available permissions: see `Permission` in `permissions.py` — currently `VIEW`, `CREATE`,
`EDIT`, `DELETE`, `MANAGE_USERS`, `VIEW_AUDIT`, `VIEW_PII`, `APPROVE`, `MANAGE_ORGS`, `SYNC`.

The only unauthenticated routes are `/health`, `/login`, `/api/auth/login`, and the OIDC callback.

**Role is not the whole check.** When a request arrives on an API token, the caller's
reach is the *intersection* of their role and that token's declared scope.
`require_permission` enforces both; anything deciding access from a bare `User` must
consult `user.token_permissions` too (`None` means a session caller, so role alone
applies). Getting this wrong is silent: `redaction.can_view_pii` checked only the role,
so a token minted without `view_pii` still received unmasked PII, and token creation
bounded new scopes by role alone, so a read-only token could mint a full-role one.

---

## 5. IDOR Prevention

When a route takes both a parent ID and a child ID (e.g. `/assets/{asset_id}/attachments/{att_id}`), **always verify ownership** after fetching:

```python
obj = await svc.get_thing(pool, child_id)
if not obj or obj.parent_id != parent_id:
    raise HTTPException(status_code=404, detail="Not found")
```

Use 404, not 403, to avoid confirming existence of other resources.

---

## 6. SQL Safety

**All queries must use parameterized placeholders** (`$1`, `$2`, …). Never interpolate user input into SQL strings.

**Tenant scoping still belongs in every query.** Postgres RLS (`src/grcen/tenancy.py`) backstops
a forgotten `organization_id` filter, but it is the second layer, not a licence to drop the
first: RLS can't express field-level redaction or per-asset overrides, and it doesn't apply on
the paths that legitimately run privileged. If you write `tenancy.system_context()`, you have
switched the backstop off for that block — scope the query yourself, and keep the block as small
as the lookup that needs it (`routers/vendor_campaigns.py` shows the pattern: bypass the token
lookup, then narrow to the org it returned).

**Three tables have no backstop at all.** `users`, `user_organizations` and `api_tokens` are
outside RLS by design (they're read before a tenant exists). A missing filter there is a
straight cross-tenant read or write with nothing underneath to catch it — that is how
`/admin/users/{id}/edit` let an admin of one org reset the password of an admin of another.
Prefer putting the filter in the SQL via a **required** `organization_id` parameter rather
than an ownership check in the route, so it can't be omitted at a new call site.

Scheduler jobs are the other place to watch: they run inside `system_context()`, so a service
they call has neither layer. `email_service.resolve_alert_recipients` requires its
`organization_id` for that reason — without it, the admin fallback mailed one tenant's alert
to every other tenant's admins.

**Claim before you act.** There are 2+ worker processes and every scheduled job runs in all of
them, so a `SELECT` that picks up work, followed by side effects, followed by a status write is
executed concurrently by each worker. Anything that sends mail, calls a webhook, or hits a
third-party API must claim its row first, in one statement:

```python
rows = await pool.fetch(
    """UPDATE queue_table SET claimed_at = now()
       WHERE id IN (SELECT id FROM queue_table WHERE claimed_at IS NULL
                    FOR UPDATE SKIP LOCKED)
       RETURNING ..."""
)
```

The same applies within a request: a check and the write that depends on it are not atomic if
an `await` separates them. Use a conditional `UPDATE ... WHERE <still-in-the-expected-state>`
and treat 0 rows as "someone got there first" — and never pass that 0-row result into a
`from_row()`, which turns a lost race into a 500.

**Never write back a whole JSONB blob you read earlier.** Merge the keys you actually changed
(`metadata = coalesce(metadata,'{}'::jsonb) || $1::jsonb`); rewriting the blob reverts every
concurrent edit to keys you never touched.

For dynamic `WHERE` / `SET` clauses, use the index-counting pattern:

```python
parts, vals, idx = [], [], 1
if name:
    parts.append(f"name = ${idx}")
    vals.append(name)
    idx += 1
# ...
await pool.fetch(f"SELECT * FROM t WHERE {' AND '.join(parts)}", *vals)
```

For `ORDER BY`, validate the column name against an allowlist:

```python
allowed_sorts = {"name": "a.name", "created_at": "a.created_at"}
sort_col = allowed_sorts.get(user_input, "a.name")
```

If user input appears in a JSON operator (e.g. `metadata->>'key'`), validate the key:

```python
if not re.match(r"^[a-zA-Z0-9_\-]+$", key):
    continue  # skip unsafe keys
```

---

## 7. HTML Output Safety

**Jinja2 templates** auto-escape by default. Never use `| safe`, `{% autoescape false %}`, or `Markup()` unless the content is known-safe static HTML.

**When building HTML in Python** (e.g. `HTMLResponse` endpoints), always escape user-supplied values:

```python
from html import escape

safe_name = escape(user_input, quote=True)
html = f'<div data-value="{safe_name}">{safe_name}</div>'
```

Prefer `data-*` attributes + `dataset` access over injecting values into `onclick` or other event handler attributes.

---

## 8. File Uploads

All file upload endpoints must enforce:

1. **Content-type allowlist** — reject files not in `settings.ALLOWED_UPLOAD_TYPES` (415)
2. **Size limit** — chunked read, reject if total exceeds `settings.MAX_UPLOAD_SIZE_MB` (413)
3. **Filename sanitization** — `os.path.basename()` + strip non-alphanumeric characters
4. **Path traversal check** — `os.path.realpath(filepath)` must start with `os.path.realpath(upload_dir)`

See `src/grcen/routers/attachments.py` for the reference implementation.

---

## 9. Session Management

Sessions are server-side (database-backed). The cookie stores only a `session_id`.

On login, always:
1. Check account lockout (`check_lockout`)
2. Authenticate
3. Record success/failure (`record_successful_login` / `record_failed_login`)
4. Clear the old session (`request.session.clear()`) — session fixation prevention
5. Create a new server-side session (`session_service.create_session`)
6. Store only `session_id` in the cookie

On logout, always:
1. Invalidate the server-side session (`session_service.invalidate_session`)
2. Clear the cookie (`request.session.clear()`)

Never store `user_id` or sensitive data directly in the cookie.

---

## 10. Audit Logging

Every create, edit, and delete operation must log an audit event:

```python
await audit_svc.log_audit_event(
    pool,
    user_id=user.id,
    username=user.username,
    action="create",       # "create", "edit", "delete", "login", etc.
    entity_type="thing",   # matches audit_config table
    entity_id=obj.id,
    entity_name=obj.name,
    changes=audit_svc.create_snapshot(obj.__dict__, FIELD_LIST),
)
```

For edits, use `compute_diff(old.__dict__, new.__dict__, FIELD_LIST)`.
For deletes, use `delete_snapshot(old.__dict__, FIELD_LIST)`.

If you add a new entity type, insert a default row into `audit_config` in the migration SQL.

---

## 11. Rate Limiting

Login endpoints must include the rate limit dependency:

```python
from grcen.rate_limit import check_login_rate_limit

@router.post("/login", dependencies=[Depends(check_login_rate_limit)])
```

If you add a new login endpoint (e.g. API key exchange), apply the same dependency.

---

## 12. Error Handling

- Return generic error messages to users. Never expose stack traces, SQL errors, or internal file paths.
- Use `HTTPException` with a short `detail` string.
- For HTML pages, the global exception handler in `main.py` redirects 401→`/login` and renders a 403 template. New error codes may need similar handling.

---

## 13. Secrets & Configuration

- Never hardcode secrets. All sensitive values come from environment variables or `.env` (loaded by `pydantic-settings`).
- `.env` is gitignored. Certificate files (`*.pem`, `*.key`, `*.crt`) and `deploy/ssl/` are gitignored.
- The `SECRET_KEY` setting must be overridden in production — the default is intentionally insecure.

---

## 14. Tests

When writing tests that call login:
- Use `login_with_csrf()` from `tests/conftest.py` — it handles CSRF token extraction and header setup.
- If a test makes multiple rapid login attempts, call `_reset_rate_limit()` between them.
- Run tests with `.venv/bin/pytest`.

Security-related tests go in `tests/test_security.py`.

---

## Quick Checklist for New Features

- [ ] Request model has `Field()` constraints on all string/list fields
- [ ] Route handler declares `Depends(require_permission(...))`
- [ ] Object access verifies parent ownership (IDOR check)
- [ ] SQL uses parameterized queries; dynamic columns validated against allowlist
- [ ] HTML forms include `{% include "partials/csrf.html" %}`
- [ ] Inline `<script>` tags have `nonce="{{ request.state.csp_nonce }}"`
- [ ] Any HTML built in Python uses `html.escape()`
- [ ] Create/edit/delete actions log an audit event
- [ ] File uploads enforce type, size, name, and path checks
- [ ] Test coverage for the new endpoint (auth, validation, edge cases)
