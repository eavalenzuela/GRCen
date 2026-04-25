"""Concurrent session cap + per-user session listing/revocation."""
import uuid

import pytest

from grcen.config import settings
from grcen.permissions import UserRole
from grcen.services import session_service
from grcen.services.auth import create_user


@pytest.fixture(autouse=True)
def _tighten_cap(monkeypatch):
    monkeypatch.setattr(settings, "SESSION_MAX_CONCURRENT", 3)


@pytest.mark.asyncio
async def test_cap_evicts_oldest_session(pool):
    user = await create_user(pool, f"u_{uuid.uuid4().hex[:8]}", "x")
    s1 = await session_service.create_session(pool, user.id, ip_address="1.1.1.1")
    s2 = await session_service.create_session(pool, user.id, ip_address="1.1.1.2")
    s3 = await session_service.create_session(pool, user.id, ip_address="1.1.1.3")
    # Cap = 3 — one more should evict the oldest (s1).
    s4 = await session_service.create_session(pool, user.id, ip_address="1.1.1.4")
    rows = await pool.fetch(
        "SELECT session_id FROM sessions WHERE user_id = $1 ORDER BY created_at",
        user.id,
    )
    ids = {r["session_id"] for r in rows}
    assert len(ids) == 3
    assert s1 not in ids
    assert {s2, s3, s4}.issubset(ids)


@pytest.mark.asyncio
async def test_cap_zero_disables_check(pool, monkeypatch):
    monkeypatch.setattr(settings, "SESSION_MAX_CONCURRENT", 0)
    user = await create_user(pool, f"u_{uuid.uuid4().hex[:8]}", "x")
    for _ in range(8):
        await session_service.create_session(pool, user.id)
    count = await pool.fetchval(
        "SELECT count(*) FROM sessions WHERE user_id = $1", user.id
    )
    assert count == 8


@pytest.mark.asyncio
async def test_list_and_revoke_session(pool):
    user = await create_user(pool, f"u_{uuid.uuid4().hex[:8]}", "x", role=UserRole.ADMIN)
    s1 = await session_service.create_session(pool, user.id, ip_address="1.1.1.1")
    s2 = await session_service.create_session(pool, user.id, ip_address="1.1.1.2")
    sessions = await session_service.list_sessions_for_user(pool, user.id)
    assert {s["session_id"] for s in sessions} == {s1, s2}

    await session_service.invalidate_session(pool, s1)
    after = await session_service.list_sessions_for_user(pool, user.id)
    assert {s["session_id"] for s in after} == {s2}


@pytest.mark.asyncio
async def test_settings_page_lists_sessions(auth_client):
    resp = await auth_client.get("/settings")
    assert resp.status_code == 200
    assert "Active sessions" in resp.text


@pytest.mark.asyncio
async def test_user_cannot_revoke_other_users_session(pool, auth_client):
    """A revoke request scoped to user.id won't touch a stranger's session."""
    other = await create_user(pool, f"o_{uuid.uuid4().hex[:8]}", "x")
    other_sid = await session_service.create_session(pool, other.id)
    resp = await auth_client.post(f"/settings/sessions/{other_sid}/revoke")
    # Redirects to /settings (no error) but the other user's session survives.
    assert resp.status_code in (302, 303)
    still_there = await pool.fetchval(
        "SELECT 1 FROM sessions WHERE session_id = $1", other_sid
    )
    assert still_there == 1
