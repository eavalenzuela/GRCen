"""Security middleware for GRCen."""

import hmac
import secrets

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, RedirectResponse, Response

from grcen.rate_limit import check_api_rate_limit, refresh_db_settings, _cache_fresh

# ---------------------------------------------------------------------------
# Security headers middleware
# ---------------------------------------------------------------------------

_STATIC_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-XSS-Protection": "0",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Ensure a CSRF token exists in the session and is accessible in templates
        # via request.state.csrf_token
        csrf = request.session.get("csrf_token")
        if not csrf:
            csrf = secrets.token_urlsafe(32)
            request.session["csrf_token"] = csrf
        request.state.csrf_token = csrf

        # Generate a per-request nonce for CSP inline scripts
        nonce = secrets.token_urlsafe(16)
        request.state.csp_nonce = nonce

        response = await call_next(request)
        for header, value in _STATIC_SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)

        # Build CSP with the per-request nonce
        csp = (
            "default-src 'self'; "
            f"script-src 'self' 'nonce-{nonce}'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "font-src 'self'; "
            "connect-src 'self'"
        )
        response.headers.setdefault("Content-Security-Policy", csp)
        return response


# ---------------------------------------------------------------------------
# CSRF protection helpers
# ---------------------------------------------------------------------------


def get_csrf_token(request: Request) -> str:
    """Get or create a CSRF token stored in the session."""
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token


async def verify_csrf_token(request: Request) -> None:
    """Verify the CSRF token on form submissions.

    Checks the ``csrf_token`` form field against the session value.
    Raises 403 on mismatch.  Skips verification for JSON API requests.
    """
    content_type = request.headers.get("content-type", "")
    is_form = "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type

    if not is_form:
        return  # JSON API requests rely on SameSite cookies + Bearer tokens

    form = await request.form()
    submitted = form.get("csrf_token", "")
    expected = request.session.get("csrf_token", "")

    if not expected or not submitted or not hmac.compare_digest(str(submitted), str(expected)):
        raise HTTPException(status_code=403, detail="CSRF token mismatch")


# ---------------------------------------------------------------------------
# HTTPS redirect middleware
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# General API rate-limit middleware
# ---------------------------------------------------------------------------


# Paths exempt from the general limiter. Health is for monitors; static is
# served from disk and the login limiter already covers /login.
_RATE_LIMIT_EXEMPT_PREFIXES = (
    "/health",
    "/static/",
    "/login",
    "/logout",
)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Apply :func:`check_api_rate_limit` to every non-exempt request."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if any(path == p or path.startswith(p) for p in _RATE_LIMIT_EXEMPT_PREFIXES):
            return await call_next(request)
        # Refresh the DB-backed override cache opportunistically so admin form
        # edits propagate to all workers within the TTL.
        if not _cache_fresh():
            try:
                from grcen.database import get_pool
                await refresh_db_settings(await get_pool())
            except Exception:
                # Pool not initialised yet (test bootstrap) — fall back to env.
                pass
        result = check_api_rate_limit(request)
        if result is not None:
            _, limit, retry_after = result
            return JSONResponse(
                {"detail": "Rate limit exceeded. Please slow down."},
                status_code=429,
                headers={
                    "Retry-After": str(int(retry_after) + 1),
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": "0",
                },
            )
        return await call_next(request)


class HTTPSRedirectMiddleware(BaseHTTPMiddleware):
    """Redirect HTTP requests to HTTPS and set HSTS header.

    Respects X-Forwarded-Proto from reverse proxies so it works behind
    nginx/Caddy/load balancers without redirect loops.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        proto = request.headers.get("x-forwarded-proto", request.url.scheme)
        if proto != "https":
            url = request.url.replace(scheme="https")
            return RedirectResponse(str(url), status_code=301)
        response = await call_next(request)
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        return response
