"""Tests for the general API rate limiter middleware."""
import pytest

from grcen.config import settings
from grcen.rate_limit import _reset


@pytest.fixture(autouse=True)
def _tighten_limits(monkeypatch):
    """Drop the limits to small numbers so tests can exhaust them quickly."""
    monkeypatch.setattr(settings, "RATE_LIMIT_READ_PER_MINUTE", 5)
    monkeypatch.setattr(settings, "RATE_LIMIT_WRITE_PER_MINUTE", 3)
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)
    _reset()
    yield
    _reset()


@pytest.mark.asyncio
async def test_read_limit_returns_429_after_budget(auth_client):
    # Budget = 5 reads/min. The 6th read trips.
    for _ in range(5):
        resp = await auth_client.get("/api/assets/")
        assert resp.status_code == 200
    blocked = await auth_client.get("/api/assets/")
    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers
    assert blocked.headers.get("X-RateLimit-Limit") == "5"


@pytest.mark.asyncio
async def test_write_limit_returns_429_after_budget(auth_client):
    # Budget = 3 writes/min. Each successful create burns one.
    for i in range(3):
        resp = await auth_client.post(
            "/api/assets/", json={"type": "policy", "name": f"P{i}"}
        )
        assert resp.status_code == 201
    blocked = await auth_client.post(
        "/api/assets/", json={"type": "policy", "name": "blocked"}
    )
    assert blocked.status_code == 429


@pytest.mark.asyncio
async def test_read_and_write_buckets_are_independent(auth_client):
    # Burn the write budget.
    for i in range(3):
        resp = await auth_client.post(
            "/api/assets/", json={"type": "policy", "name": f"P{i}"}
        )
        assert resp.status_code == 201
    # Reads should still work.
    resp = await auth_client.get("/api/assets/")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_health_is_exempt(auth_client):
    # Even if reads are exhausted, /health stays open.
    for _ in range(5):
        await auth_client.get("/api/assets/")
    for _ in range(20):
        resp = await auth_client.get("/health")
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_per_caller_isolation(auth_client, editor_client):
    # Burn the read budget on auth_client; editor_client is an independent session.
    for _ in range(5):
        assert (await auth_client.get("/api/assets/")).status_code == 200
    assert (await auth_client.get("/api/assets/")).status_code == 429
    # Editor's first read still passes — separate session id, separate budget.
    assert (await editor_client.get("/api/assets/")).status_code == 200


@pytest.mark.asyncio
async def test_token_bucket_distinct_from_session(pool, auth_client):
    """A bearer token gets its own budget, distinct from the session that minted it."""
    from grcen.permissions import Permission
    from grcen.services import token_service

    # Create a token for the auth_client's user. We can't read the user id from
    # the session cookie, but the token will key off its own raw value.
    user_row = await pool.fetchrow("SELECT id FROM users LIMIT 1")
    _, raw = await token_service.create_token(
        pool, user_row["id"], "rl-test", [Permission.VIEW.value],
    )

    # Burn the session's read budget.
    for _ in range(5):
        await auth_client.get("/api/assets/")
    assert (await auth_client.get("/api/assets/")).status_code == 429

    # The token has its own bucket — first call goes through.
    headers = {"Authorization": f"Bearer {raw}"}
    resp = await auth_client.get("/api/assets/", headers=headers)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_route_override_uses_smaller_budget(auth_client, monkeypatch):
    """An entry like '/api/assets:2:1' caps that prefix tighter than the global."""
    monkeypatch.setattr(
        settings, "RATE_LIMIT_ROUTE_OVERRIDES", "/api/assets/:2:1"
    )
    monkeypatch.setattr(settings, "RATE_LIMIT_READ_PER_MINUTE", 100)
    monkeypatch.setattr(settings, "RATE_LIMIT_WRITE_PER_MINUTE", 100)
    _reset()
    # Two reads pass…
    assert (await auth_client.get("/api/assets/")).status_code == 200
    assert (await auth_client.get("/api/assets/")).status_code == 200
    # …third trips the per-route override.
    assert (await auth_client.get("/api/assets/")).status_code == 429


@pytest.mark.asyncio
async def test_route_override_does_not_steal_global_budget(
    auth_client, monkeypatch
):
    """The override gets its own counter, distinct from the global bucket."""
    monkeypatch.setattr(
        settings, "RATE_LIMIT_ROUTE_OVERRIDES", "/api/assets/:2:1"
    )
    monkeypatch.setattr(settings, "RATE_LIMIT_READ_PER_MINUTE", 5)
    monkeypatch.setattr(settings, "RATE_LIMIT_WRITE_PER_MINUTE", 5)
    _reset()
    # Burn the override's read budget on /api/assets/.
    await auth_client.get("/api/assets/")
    await auth_client.get("/api/assets/")
    assert (await auth_client.get("/api/assets/")).status_code == 429
    # /api/tags/ falls back to the global budget — still has all 5 reads.
    assert (await auth_client.get("/api/tags/")).status_code == 200


@pytest.mark.asyncio
async def test_longest_matching_prefix_wins(auth_client, monkeypatch):
    """When multiple overrides match, the longest prefix takes precedence."""
    monkeypatch.setattr(
        settings, "RATE_LIMIT_ROUTE_OVERRIDES",
        "/api:50:50,/api/assets/:2:1",
    )
    monkeypatch.setattr(settings, "RATE_LIMIT_READ_PER_MINUTE", 100)
    monkeypatch.setattr(settings, "RATE_LIMIT_WRITE_PER_MINUTE", 100)
    _reset()
    # /api/assets/ should match the longer (tighter) override.
    await auth_client.get("/api/assets/")
    await auth_client.get("/api/assets/")
    assert (await auth_client.get("/api/assets/")).status_code == 429


@pytest.mark.asyncio
async def test_disabled_setting_skips_check(auth_client, monkeypatch):
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", False)
    _reset()
    # Way past the configured limit, but disabled means no 429.
    for _ in range(20):
        resp = await auth_client.get("/api/assets/")
        assert resp.status_code == 200
