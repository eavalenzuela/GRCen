"""Simple in-memory per-IP login rate limiter."""

import time
from collections import defaultdict

from fastapi import HTTPException, Request

from grcen.config import settings

# ip -> timestamp of last login attempt
_last_attempt: dict[str, float] = defaultdict(float)


async def check_login_rate_limit(request: Request) -> None:
    """Raise 429 if this IP is sending login requests too fast."""
    ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    elapsed = now - _last_attempt[ip]
    if elapsed < settings.LOGIN_RATE_LIMIT_SECONDS:
        raise HTTPException(status_code=429, detail="Too many login attempts. Please wait.")
    _last_attempt[ip] = now


def _reset() -> None:
    """Clear state — for tests only."""
    _last_attempt.clear()
