"""DB-backed rate-limit overrides via /admin/rate-limits."""
import pytest

from grcen.config import settings
from grcen import rate_limit as rl


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    monkeypatch.setattr(settings, "RATE_LIMIT_READ_PER_MINUTE", 600)
    monkeypatch.setattr(settings, "RATE_LIMIT_WRITE_PER_MINUTE", 120)
    monkeypatch.setattr(settings, "RATE_LIMIT_ROUTE_OVERRIDES", "")
    rl._reset()
    rl.invalidate_settings_cache()
    yield
    rl._reset()
    rl.invalidate_settings_cache()


@pytest.mark.asyncio
async def test_form_persists_db_overrides(auth_client, pool):
    resp = await auth_client.post(
        "/admin/rate-limits",
        data={"read": "200", "write": "30", "overrides": "/api/exports/:5:5"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    rows = await pool.fetch(
        "SELECT key, value FROM app_settings WHERE key LIKE 'rate_limit_%'"
    )
    by_key = {r["key"]: r["value"] for r in rows}
    assert by_key["rate_limit_read_per_minute"] == "200"
    assert by_key["rate_limit_write_per_minute"] == "30"
    assert by_key["rate_limit_route_overrides"] == "/api/exports/:5:5"


@pytest.mark.asyncio
async def test_db_override_takes_effect_via_middleware(auth_client, pool):
    """Saving via the form should bind the new budget on subsequent requests."""
    # First, default 600/120 — request passes.
    assert (await auth_client.get("/api/assets/")).status_code == 200
    # Drop the read budget to 1 via the admin form.
    await auth_client.post(
        "/admin/rate-limits",
        data={"read": "1", "write": "1", "overrides": ""},
        follow_redirects=False,
    )
    # The middleware refresh + budget=1 means the next read passes (1 of 1)
    # then trips. /admin/rate-limits POST counted toward writes already.
    rl._reset()  # zero out the deque so the test isn't tainted by prior calls
    assert (await auth_client.get("/api/assets/")).status_code == 200
    assert (await auth_client.get("/api/assets/")).status_code == 429


@pytest.mark.asyncio
async def test_blank_field_clears_db_override(auth_client, pool):
    await auth_client.post(
        "/admin/rate-limits",
        data={"read": "10", "write": "10", "overrides": "/api/exports/:1:1"},
        follow_redirects=False,
    )
    # Now blank the read field — the row should be deleted, not persisted as "".
    await auth_client.post(
        "/admin/rate-limits",
        data={"read": "", "write": "10", "overrides": "/api/exports/:1:1"},
        follow_redirects=False,
    )
    has_read = await pool.fetchval(
        "SELECT 1 FROM app_settings WHERE key = 'rate_limit_read_per_minute'"
    )
    assert has_read is None


@pytest.mark.asyncio
async def test_admin_page_renders(auth_client):
    resp = await auth_client.get("/admin/rate-limits")
    assert resp.status_code == 200
    assert "Rate Limit Budgets" in resp.text


# --- limiter identity must not be attacker-controlled ----------------------


def _req(headers=None, session=None):
    """Minimal Request stand-in for the key function."""
    from starlette.datastructures import Headers
    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/assets/",
        "headers": Headers(headers or {}).raw,
        "client": ("203.0.113.9", 1234),
        "session": session if session is not None else {},
    }
    return Request(scope)


def test_unrecognised_bearer_falls_back_to_ip():
    """Otherwise any caller gets a fresh budget per request just by varying it.

    The key was the raw header value, so an unauthenticated client could rotate
    it to sidestep the limiter entirely — and permanently add a never-evicted
    entry to the window map on every request while doing so.
    """
    from grcen.rate_limit import _api_caller_key, _reset

    _reset()
    k1 = _api_caller_key(_req({"authorization": "Bearer made-up-aaa"}))
    k2 = _api_caller_key(_req({"authorization": "Bearer made-up-bbb"}))

    assert k1 == k2 == "ip:203.0.113.9"


def test_recognised_bearer_gets_its_own_bucket():
    from grcen.rate_limit import _api_caller_key, _reset, note_valid_token

    _reset()
    note_valid_token("real-token-value")

    key = _api_caller_key(_req({"authorization": "Bearer real-token-value"}))

    assert key.startswith("token:")
    assert "real-token-value" not in key, "raw credential held in limiter state"
    # A different token is a different bucket.
    note_valid_token("other-token-value")
    other = _api_caller_key(_req({"authorization": "Bearer other-token-value"}))
    assert other != key


def test_stale_windows_are_evicted():
    """The window map is a defaultdict; without eviction it grows forever."""
    import time

    from grcen.rate_limit import _WINDOW_SECONDS, _api_window, _evict_stale_windows, _reset

    _reset()
    now = time.monotonic()
    for i in range(50):
        _api_window[(f"ip:10.0.0.{i}", "read", "")].append(now - _WINDOW_SECONDS - 10)
    _api_window[("ip:10.1.1.1", "read", "")].append(now)
    assert len(_api_window) == 51

    _evict_stale_windows(now)

    assert list(_api_window) == [("ip:10.1.1.1", "read", "")]
