"""In-memory rate limiters.

Two limiters live here:

- ``check_login_rate_limit`` — per-IP debounce on login attempts (~1/2s).
- ``check_api_rate_limit`` — sliding-window-per-minute limiter applied to every
  request via middleware. The bucket is keyed by (caller, method-class) where
  caller is the API-token-id, then session-id, then client-IP (best available),
  and method-class is 'read' for GET/HEAD/OPTIONS or 'write' otherwise. Two
  separate budgets keep a write spammer from drowning the read budget.

This is process-local; a multi-worker deployment will undercount. Move to a
shared Redis backend before scaling out, but keep this fallback for the
single-process default and tests.
"""

import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

from grcen.config import settings

# Login throttle state
_last_attempt: dict[str, float] = defaultdict(float)


async def check_login_rate_limit(request: Request) -> None:
    """Raise 429 if this IP is sending login requests too fast."""
    ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    elapsed = now - _last_attempt[ip]
    if elapsed < settings.LOGIN_RATE_LIMIT_SECONDS:
        raise HTTPException(status_code=429, detail="Too many login attempts. Please wait.")
    _last_attempt[ip] = now


# General API limiter state: (key, bucket) -> deque[timestamps within last 60s]
_api_window: dict[tuple[str, str], deque[float]] = defaultdict(deque)
_WINDOW_SECONDS = 60.0
_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _classify(method: str) -> str:
    return "read" if method.upper() in _READ_METHODS else "write"


def _api_caller_key(request: Request) -> str:
    """Pick the most specific identity available for rate-limit accounting.

    Token id beats session id beats IP. This means a token shared between two
    machines hits one shared budget — which is what we want for service
    accounts — while session-authed users get per-session budgets.
    """
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        return f"token:{auth[7:]}"
    sid = request.session.get("session_id") if hasattr(request, "session") else None
    if sid:
        return f"session:{sid}"
    ip = request.client.host if request.client else "unknown"
    return f"ip:{ip}"


def check_api_rate_limit(request: Request) -> tuple[int, int, float] | None:
    """Record a hit and decide whether to allow it.

    Returns ``(remaining, limit, retry_after_seconds)`` on rejection (use the
    retry hint for ``Retry-After``); returns None when the request is allowed.
    Side-effect-free callers can use :func:`peek_api_rate_limit` instead.
    """
    if not settings.RATE_LIMIT_ENABLED:
        return None
    bucket = _classify(request.method)
    limit = (
        settings.RATE_LIMIT_READ_PER_MINUTE
        if bucket == "read"
        else settings.RATE_LIMIT_WRITE_PER_MINUTE
    )
    if limit <= 0:
        return None
    key = (_api_caller_key(request), bucket)
    now = time.monotonic()
    window = _api_window[key]
    cutoff = now - _WINDOW_SECONDS
    while window and window[0] < cutoff:
        window.popleft()
    if len(window) >= limit:
        # Oldest hit drops out at window[0] + 60s.
        retry_after = max(1.0, window[0] + _WINDOW_SECONDS - now)
        return (0, limit, retry_after)
    window.append(now)
    return None


def _reset() -> None:
    """Clear state — for tests only."""
    _last_attempt.clear()
    _api_window.clear()
