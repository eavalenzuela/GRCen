"""Per-role session caps + admin sessions page + eviction notification."""
import uuid

import pytest

from grcen.config import settings
from grcen.permissions import UserRole
from grcen.services import session_service
from grcen.services.auth import create_user


@pytest.fixture(autouse=True)
def _set_caps(monkeypatch):
    # Make the cap math obvious in tests.
    monkeypatch.setattr(settings, "SESSION_MAX_CONCURRENT", 5)
    monkeypatch.setattr(settings, "SESSION_MAX_CONCURRENT_ADMIN", 2)
    monkeypatch.setattr(settings, "SESSION_MAX_CONCURRENT_AUDITOR", 5)
    monkeypatch.setattr(settings, "SESSION_MAX_CONCURRENT_EDITOR", 5)
    monkeypatch.setattr(settings, "SESSION_MAX_CONCURRENT_VIEWER", 5)


@pytest.mark.asyncio
async def test_admin_role_uses_lower_cap(pool):
    """Admin's tighter override should kick in before the global cap."""
    admin = await create_user(
        pool, f"a_{uuid.uuid4().hex[:8]}", "x", role=UserRole.ADMIN
    )
    s1 = await session_service.create_session(pool, admin.id)
    s2 = await session_service.create_session(pool, admin.id)
    s3 = await session_service.create_session(pool, admin.id)
    rows = await pool.fetch(
        "SELECT session_id FROM sessions WHERE user_id = $1", admin.id
    )
    ids = {r["session_id"] for r in rows}
    # Cap is 2, so the third login evicts the oldest.
    assert len(ids) == 2
    assert s1 not in ids
    assert {s2, s3}.issubset(ids)


@pytest.mark.asyncio
async def test_viewer_role_uses_global_cap(pool, monkeypatch):
    """When the per-role override is the same as global, the global rules."""
    monkeypatch.setattr(settings, "SESSION_MAX_CONCURRENT", 4)
    monkeypatch.setattr(settings, "SESSION_MAX_CONCURRENT_VIEWER", -1)  # not set
    viewer = await create_user(
        pool, f"v_{uuid.uuid4().hex[:8]}", "x", role=UserRole.VIEWER
    )
    for _ in range(6):
        await session_service.create_session(pool, viewer.id)
    count = await pool.fetchval(
        "SELECT count(*) FROM sessions WHERE user_id = $1", viewer.id
    )
    assert count == 4


@pytest.mark.asyncio
async def test_eviction_creates_notification(pool):
    """When a session is bumped by the cap, drop a targeted notification."""
    admin = await create_user(
        pool, f"a_{uuid.uuid4().hex[:8]}", "x", role=UserRole.ADMIN
    )
    # Cap = 2; create 3 to force one eviction.
    for _ in range(3):
        await session_service.create_session(pool, admin.id)
    notif = await pool.fetchrow(
        """SELECT title, user_id FROM notifications
           WHERE user_id = $1 ORDER BY created_at DESC LIMIT 1""",
        admin.id,
    )
    assert notif is not None
    assert "signed out" in notif["title"].lower()


@pytest.mark.asyncio
async def test_admin_sessions_page_requires_manage_users(viewer_client):
    resp = await viewer_client.get("/admin/sessions")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_sessions_page_lists_other_users_sessions(
    pool, auth_client
):
    other = await create_user(pool, f"o_{uuid.uuid4().hex[:8]}", "x")
    await session_service.create_session(pool, other.id, user_agent="Firefox/Test")
    resp = await auth_client.get("/admin/sessions")
    assert resp.status_code == 200
    assert other.username in resp.text
    assert "Firefox/Test" in resp.text


@pytest.mark.asyncio
async def test_admin_can_revoke_other_users_session(pool, auth_client):
    other = await create_user(pool, f"o_{uuid.uuid4().hex[:8]}", "x")
    sid = await session_service.create_session(pool, other.id)
    resp = await auth_client.post(
        f"/admin/sessions/{sid}/revoke", follow_redirects=False
    )
    assert resp.status_code == 302
    gone = await pool.fetchval(
        "SELECT 1 FROM sessions WHERE session_id = $1", sid
    )
    assert gone is None


@pytest.mark.asyncio
async def test_admin_cannot_revoke_session_from_another_org(
    pool, auth_client
):
    """Cross-org revocation must be blocked."""
    from grcen.services import organization_service
    other_org = await organization_service.create_organization(
        pool, slug=f"o_{uuid.uuid4().hex[:6]}", name="Other"
    )
    foreign = await create_user(
        pool, f"f_{uuid.uuid4().hex[:8]}", "x", organization_id=other_org.id
    )
    sid = await session_service.create_session(pool, foreign.id)
    await auth_client.post(f"/admin/sessions/{sid}/revoke")
    still = await pool.fetchval(
        "SELECT 1 FROM sessions WHERE session_id = $1", sid
    )
    assert still == 1
