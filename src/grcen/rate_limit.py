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

import hashlib
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

from grcen.config import settings

# Login throttle state
_last_attempt: dict[str, float] = defaultdict(float)


def _debounce(request: Request, bucket: str) -> None:
    """Raise 429 if this IP is hitting ``bucket`` faster than the debounce."""
    ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    key = f"{bucket}:{ip}"
    if now - _last_attempt[key] < settings.LOGIN_RATE_LIMIT_SECONDS:
        raise HTTPException(status_code=429, detail="Too many login attempts. Please wait.")
    _last_attempt[key] = now


async def check_login_rate_limit(request: Request) -> None:
    """Raise 429 if this IP is sending login requests too fast."""
    _debounce(request, "login")


async def check_mfa_rate_limit(request: Request) -> None:
    """Throttle second-factor submissions, in their own bucket.

    Separate from the password bucket on purpose: the MFA POST is the second
    leg of a single login, and sharing a bucket would 429 an ordinary user for
    typing their code promptly. Repeated *guesses* still get throttled, which
    is the point — the route is exempt from the general limiter because the
    ``/login`` prefix matches it.
    """
    _debounce(request, "mfa")


# General API limiter state: (key, bucket) -> deque[timestamps within last 60s]
_api_window: dict[tuple[str, str, str], deque[float]] = defaultdict(deque)
_WINDOW_SECONDS = 60.0
_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _classify(method: str) -> str:
    return "read" if method.upper() in _READ_METHODS else "write"


# DB-backed overrides for the env-default rate-limit budgets. Looked up at
# request time; cached for ~30 s to avoid hammering the DB on every request.
_db_cache: dict[str, str] | None = None
_db_cache_at: float = 0.0
_DB_CACHE_TTL = 30.0


def invalidate_settings_cache() -> None:
    """Force the next limiter check to reload DB settings.

    Tests and the admin form call this so changes are visible immediately
    instead of waiting out the TTL.
    """
    global _db_cache, _db_cache_at
    _db_cache = None
    _db_cache_at = 0.0


async def _load_db_settings(pool) -> dict[str, str]:
    """Pull rate-limit-related rows from app_settings into a dict."""
    rows = await pool.fetch(
        "SELECT key, value FROM app_settings WHERE key LIKE 'rate_limit_%'"
    )
    return {r["key"]: r["value"] for r in rows}


def _read_db_setting(name: str) -> str | None:
    if _db_cache is None:
        return None
    return _db_cache.get(name)


def _resolve_setting(name: str, env_default):
    """Prefer DB override, fall back to the env-derived setting."""
    db = _read_db_setting(name)
    if db is None:
        return env_default
    if isinstance(env_default, int):
        try:
            return int(db)
        except ValueError:
            return env_default
    if isinstance(env_default, bool):
        return db.lower() in ("1", "true", "yes")
    return db


def _parse_route_overrides(raw: str) -> list[tuple[str, int, int]]:
    """Parse the RATE_LIMIT_ROUTE_OVERRIDES setting.

    Returns ``(prefix, read_limit, write_limit)`` triples sorted longest-prefix
    first so the most specific match wins.
    """
    if not raw:
        return []
    out: list[tuple[str, int, int]] = []
    for entry in raw.split(","):
        parts = entry.strip().split(":")
        if len(parts) != 3:
            continue
        prefix, read_s, write_s = parts
        prefix = prefix.strip()
        if not prefix.startswith("/"):
            continue
        try:
            r = int(read_s)
            w = int(write_s)
        except ValueError:
            continue
        out.append((prefix, r, w))
    return sorted(out, key=lambda t: len(t[0]), reverse=True)


def _resolve_limits(path: str) -> tuple[int, int, str]:
    """Return (read_limit, write_limit, matching_prefix_or_empty) for ``path``."""
    raw = _resolve_setting(
        "rate_limit_route_overrides", settings.RATE_LIMIT_ROUTE_OVERRIDES
    )
    overrides = _parse_route_overrides(raw or "")
    for prefix, r, w in overrides:
        if path.startswith(prefix):
            return r, w, prefix
    return (
        _resolve_setting("rate_limit_read_per_minute", settings.RATE_LIMIT_READ_PER_MINUTE),
        _resolve_setting("rate_limit_write_per_minute", settings.RATE_LIMIT_WRITE_PER_MINUTE),
        "",
    )


def _matching_prefix(path: str) -> str:
    return _resolve_limits(path)[2]


# Hashes of bearer tokens that have authenticated at least once recently, with
# the time they last did. The limiter runs before authentication, so this is how
# it tells a real caller's token from an arbitrary string.
_known_tokens: dict[str, float] = {}
_KNOWN_TOKEN_TTL = 3600.0


def _token_fingerprint(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:32]


def note_valid_token(raw: str) -> None:
    """Record that ``raw`` authenticated, so its budget can be its own.

    Called from bearer resolution. Until a token is seen here it is billed to
    the caller's IP, which is what stops an unauthenticated client from minting
    a fresh budget per request by varying the header.
    """
    now = time.monotonic()
    _known_tokens[_token_fingerprint(raw)] = now
    if len(_known_tokens) > 4096:
        cutoff = now - _KNOWN_TOKEN_TTL
        for fp in [k for k, seen in _known_tokens.items() if seen < cutoff]:
            _known_tokens.pop(fp, None)


def _api_caller_key(request: Request) -> str:
    """Pick the most specific identity available for rate-limit accounting.

    Token beats session beats IP. A token shared between two machines hits one
    shared budget — what we want for service accounts — while session-authed
    users get per-session budgets.

    Only *recognised* tokens get their own bucket. Keying on the raw header
    meant any unauthenticated caller could vary it per request for an unlimited
    budget, and permanently add a never-evicted entry to the window map while
    doing it. Unrecognised bearers fall through to the IP bucket, and the key
    is a hash so raw credentials aren't held in memory.
    """
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        fp = _token_fingerprint(auth[7:])
        seen = _known_tokens.get(fp)
        if seen is not None and (time.monotonic() - seen) < _KNOWN_TOKEN_TTL:
            return f"token:{fp}"
    # Read through the scope: request.session asserts when SessionMiddleware
    # isn't installed, and hasattr() propagates that rather than returning False.
    session = request.scope.get("session")
    sid = session.get("session_id") if isinstance(session, dict) else None
    if sid:
        return f"session:{sid}"
    ip = request.client.host if request.client else "unknown"
    return f"ip:{ip}"


def _evict_stale_windows(now: float) -> None:
    """Drop windows with no hits in the last minute.

    ``_api_window`` is a defaultdict keyed by caller identity, so without this
    it grows for the life of the process — one entry per session, IP, or token
    ever seen, none of them ever removed.
    """
    cutoff = now - _WINDOW_SECONDS
    for key in [k for k, w in _api_window.items() if not w or w[-1] < cutoff]:
        del _api_window[key]


async def refresh_db_settings(pool) -> None:
    """Populate the in-memory cache from app_settings (called by middleware)."""
    global _db_cache, _db_cache_at
    _db_cache = await _load_db_settings(pool)
    import time as _t
    _db_cache_at = _t.monotonic()


def _cache_fresh() -> bool:
    if _db_cache is None:
        return False
    import time as _t
    return (_t.monotonic() - _db_cache_at) < _DB_CACHE_TTL


def check_api_rate_limit(request: Request) -> tuple[bool, int, int, float] | None:
    """Record a hit and decide whether to allow it.

    Returns ``(allowed, limit, remaining, retry_after_seconds)`` — on rejection
    ``allowed`` is False, ``remaining`` is 0, and the retry hint feeds
    ``Retry-After``; on an allowed request ``remaining`` is the budget left in
    the current window (so the middleware can advertise it in
    ``X-RateLimit-*`` headers). Returns None when limiting doesn't apply at
    all (disabled globally or a non-positive budget).
    """
    if not settings.RATE_LIMIT_ENABLED:
        return None
    bucket = _classify(request.method)
    read_lim, write_lim, prefix = _resolve_limits(request.url.path)
    limit = read_lim if bucket == "read" else write_lim
    if limit <= 0:
        return None
    # Bucket key includes the matching prefix so a tightened-prefix budget
    # gets its own counter, distinct from the global one.
    key = (_api_caller_key(request), bucket, prefix)
    now = time.monotonic()
    # Cheap amortised sweep; the map is small in normal operation.
    if len(_api_window) > 1024:
        _evict_stale_windows(now)
    window = _api_window[key]
    cutoff = now - _WINDOW_SECONDS
    while window and window[0] < cutoff:
        window.popleft()
    if len(window) >= limit:
        # Oldest hit drops out at window[0] + 60s.
        retry_after = max(1.0, window[0] + _WINDOW_SECONDS - now)
        return (False, limit, 0, retry_after)
    window.append(now)
    return (True, limit, limit - len(window), 0.0)


def _reset() -> None:
    """Clear state — for tests only."""
    _last_attempt.clear()
    _api_window.clear()
    _known_tokens.clear()
