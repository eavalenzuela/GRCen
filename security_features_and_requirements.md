# Security Features

## Implemented
* SSO/OIDC integration
* Role-based access control (RBAC) with admin, editor, viewer, auditor roles
* API key management with scoped permissions and expiry
* Audit log granularity (login/logout events, admin actions)
* Input validation (Pydantic schema constraints on all request models)
* Password policy enforcement (minimum length via Pydantic validation)
* File upload hardening (size limits, content-type allowlist, filename sanitization, path traversal prevention)
* Server-side session management with DB-backed sessions, idle/absolute timeouts
* Account lockout after failed login attempts
* Session fixation prevention (session regeneration on login)
* Security headers middleware (CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy, Permissions-Policy)
* Secure session cookies (HttpOnly, SameSite=Lax, Secure in production)
* CSRF protection on all form POST endpoints
* Login rate limiting (per-IP, 1 request per 2 seconds for spraying protection)
* XSS prevention (Jinja2 auto-escaping, HTML entity escaping in dynamic HTML, CSP with per-request nonces)
* IDOR protection (object-level authorization checks on all attachment operations)
* Metadata key validation (allowlist pattern for JSON query keys)
* SSL/TLS support (direct via SSL_CERTFILE/SSL_KEYFILE, or nginx reverse proxy with TLS 1.2+/strong ciphers)
* HTTPS redirect middleware with HSTS (max-age=2y, includeSubDomains)
* Health check endpoint (unauthenticated) with no sensitive info leakage

## Not Implemented

### Authentication & Identity
* Multi-factor authentication (TOTP, WebAuthn/FIDO2)
* SAML 2.0 support (in addition to OIDC)
* Password policy enforcement (complexity rules, breach database checks)

### Authorization
* Attribute-based access control (ABAC) for fine-grained asset-level permissions
* Delegation and impersonation with audit trail

### Audit & Logging
* Audit log shipping to remote log endpoint (syslog, SIEM integration)
* Tamper-evident audit logs (hash chaining or append-only storage)
* Data access logging (who viewed/exported what, when)

### Data Protection
* Data export controls (restrict bulk export by role, watermark exports)
* Field-level redaction in the UI based on user role
* Backup encryption at rest

### Network & Deployment
* CORS policy configuration
* Rate limiting and throttling per endpoint (login rate limiting is done; general API rate limiting is not)
* Deployment hardening guide (reverse proxy config, container security)

### Integration Security
* Webhook signature verification (HMAC)
* OAuth 2.0 client credentials flow for service-to-service integrations
* IP allowlisting for API access

---

# Security Requirements

These are controls that **must** be implemented before production use.

## Data Layer
* Database encryption: database-level or field-level is TBD
* Encryption at rest for all stored credentials, tokens, and secrets (use a KMS or vault)
* Encryption in transit for all database connections (require TLS for DB connections)
* ~~Secure credential storage: passwords hashed with bcrypt, scrypt, or Argon2id; never stored in plaintext or reversible encryption~~ **DONE**
* ~~Parameterized queries for all database access (no string concatenation of SQL)~~ **DONE**

## HTTP & Transport
* ~~HTTP security headers: `Strict-Transport-Security`, `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy`~~ **DONE**
* ~~Secure cookie settings: `HttpOnly`, `Secure`, `SameSite=Lax` (or `Strict` where possible)~~ **DONE**
* ~~HTTPS-only with HTTP-to-HTTPS redirect; HSTS preload eligible~~ **DONE**
* ~~TLS 1.2 minimum; disable SSLv3, TLS 1.0, TLS 1.1~~ **DONE** (nginx config enforces TLS 1.2+)

## Input Validation & Injection Prevention
* ~~Input validation scheme: allowlist-based validation on all user inputs with server-side enforcement~~ **DONE**
* ~~Hardening against SQL injection: parameterized queries/ORM exclusively; no raw SQL from user input~~ **DONE**
* ~~Hardening against injection attacks in POST endpoints: context-aware output encoding (HTML, JS, URL, CSS)~~ **DONE**
* ~~XSS prevention: auto-escaping templates, CSP with nonce-based script allowlisting~~ **DONE**
* ~~Hardening against IDOR: enforce authorization checks on every object access, not just UI-level hiding~~ **DONE**
* ~~CSRF protection: anti-CSRF tokens on all state-changing requests (or SameSite cookie + Origin header validation)~~ **DONE**
* ~~Path traversal prevention: sanitize file paths, restrict uploads to designated directories~~ **DONE**
* ~~Deserialization safety: avoid deserializing untrusted data; if required, use safe formats (JSON) with schema validation~~ **DONE** (JSON only, Pydantic validation on all inputs)

## Session Management
* ~~Cryptographically random session tokens (minimum 128-bit entropy)~~ **DONE**
* ~~Session invalidation on logout, password change, and privilege escalation~~ **DONE**
* ~~Absolute session timeout (e.g., 12 hours) and idle timeout (e.g., 30 minutes), configurable by admin~~ **DONE**
* Concurrent session limits (configurable)

## Access Control
* Principle of least privilege: default deny; users get no access until explicitly granted
* Object-level authorization checks on every API endpoint (not just route-level middleware)
* Admin actions require re-authentication or step-up authentication
* Separation of duties: no single role can both create and approve (e.g., policy approval, audit sign-off)

## Error Handling & Information Disclosure
* Generic error messages to end users; no stack traces, SQL errors, or internal paths in responses
* Structured internal error logging with correlation IDs
* Custom error pages (no default framework error pages in production)

## Dependency & Supply Chain
* Dependency vulnerability scanning in CI (e.g., `pip-audit`, `safety`, Dependabot)
* Pin dependency versions; review updates before merging
* SBOM generation for each release

## Secrets Management
* No secrets in source code, config files, or environment variables checked into version control
* Use a secrets manager or vault for runtime secrets
* Rotate secrets and API keys on a defined schedule
* ~~`.env` files excluded from version control via `.gitignore`~~ **DONE**
