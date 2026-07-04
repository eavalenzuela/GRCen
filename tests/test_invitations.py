"""Tests for self-service org invitations."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from grcen.permissions import UserRole
from grcen.services import invitation_service, organization_service
from grcen.services.auth import create_user
from tests.conftest import get_csrf_token, login_with_csrf


@pytest.fixture
async def admin(pool):
    return await create_user(
        pool, f"inv_admin_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.ADMIN
    )


async def _invite(pool, admin, role=UserRole.EDITOR, **kw):
    return await invitation_service.create_invitation(
        pool, organization_id=admin.organization_id, role=role,
        created_by=admin.id, **kw,
    )


# ── service layer ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_and_accept_invitation(pool, admin):
    row, raw = await _invite(pool, admin, role=UserRole.EDITOR)
    assert (await invitation_service.get_valid_by_token(pool, raw)) is not None

    user = await invitation_service.accept_invitation(
        pool, raw, username="new_analyst", password="hunter2hunter2"
    )
    assert user.role == UserRole.EDITOR
    assert user.organization_id == admin.organization_id

    # Single-use: the same link is dead afterward.
    assert await invitation_service.get_valid_by_token(pool, raw) is None
    with pytest.raises(invitation_service.InvitationError):
        await invitation_service.accept_invitation(
            pool, raw, username="someone_else", password="hunter2hunter2"
        )


@pytest.mark.asyncio
async def test_accept_rejects_existing_username(pool, admin):
    """create_user upserts on username — the invite path must never let an
    invitee take over an existing account."""
    _, raw = await _invite(pool, admin)
    with pytest.raises(invitation_service.InvitationError, match="taken"):
        await invitation_service.accept_invitation(
            pool, raw, username=admin.username, password="hunter2hunter2"
        )
    # Invitation stays pending after the failed attempt.
    assert await invitation_service.get_valid_by_token(pool, raw) is not None


@pytest.mark.asyncio
async def test_accept_rejects_short_password(pool, admin):
    _, raw = await _invite(pool, admin)
    with pytest.raises(invitation_service.InvitationError, match="at least"):
        await invitation_service.accept_invitation(
            pool, raw, username="shorty", password="short"
        )


@pytest.mark.asyncio
async def test_revoked_and_expired_invitations_rejected(pool, admin):
    row, raw = await _invite(pool, admin)
    await invitation_service.revoke_invitation(
        pool, row["id"], organization_id=admin.organization_id
    )
    assert await invitation_service.get_valid_by_token(pool, raw) is None

    row2, raw2 = await _invite(pool, admin)
    await pool.execute(
        "UPDATE org_invitations SET expires_at = $1 WHERE id = $2",
        datetime.now(UTC) - timedelta(days=1), row2["id"],
    )
    assert await invitation_service.get_valid_by_token(pool, raw2) is None


@pytest.mark.asyncio
async def test_list_shows_status(pool, admin):
    row, _ = await _invite(pool, admin, email="a@b.co", note="analyst")
    listing = await invitation_service.list_invitations(
        pool, organization_id=admin.organization_id
    )
    assert len(listing) == 1
    assert listing[0]["status"] == "pending"
    assert listing[0]["email"] == "a@b.co"
    assert listing[0]["created_by_username"] == admin.username


# ── public accept page ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_public_accept_flow(client, pool, admin):
    org = await organization_service.get_by_id(pool, admin.organization_id)
    _, raw = await _invite(pool, admin, role=UserRole.VIEWER)

    page = await client.get(f"/invite/{raw}")
    assert page.status_code == 200
    assert org.name in page.text
    assert "viewer" in page.text

    csrf = await get_csrf_token(client, f"/invite/{raw}")
    resp = await client.post(
        f"/invite/{raw}",
        data={"username": "invited_person", "password": "longenough1",
              "csrf_token": csrf},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "/login" in resp.headers["location"]

    # The new user can actually log in.
    await login_with_csrf(client, "invited_person", "longenough1")
    me = await client.get("/settings")
    assert me.status_code == 200


@pytest.mark.asyncio
async def test_public_page_404_for_bad_token(client):
    resp = await client.get("/invite/not-a-token")
    assert resp.status_code == 404
    assert "not valid" in resp.text


# ── admin page ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_admin_page_create_and_revoke(auth_client, pool):
    resp = await auth_client.post(
        "/admin/invitations",
        data={"role": "editor", "email": "x@y.z", "ttl_days": "3"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    page = await auth_client.get("/admin/invitations")
    assert "/invite/" in page.text  # shown-once link
    assert "pending" in page.text

    inv_id = await pool.fetchval("SELECT id FROM org_invitations LIMIT 1")
    resp = await auth_client.post(
        f"/admin/invitations/{inv_id}/revoke", follow_redirects=False
    )
    assert resp.status_code == 302
    page = await auth_client.get("/admin/invitations")
    assert "revoked" in page.text


@pytest.mark.asyncio
async def test_admin_page_requires_admin(viewer_client):
    resp = await viewer_client.get("/admin/invitations")
    assert resp.status_code == 403
