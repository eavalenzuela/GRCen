"""Tests for admin user management."""

import pytest


@pytest.mark.asyncio
async def test_admin_can_list_users(auth_client):
    resp = await auth_client.get("/admin/users")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_viewer_cannot_access_admin(viewer_client):
    resp = await viewer_client.get("/admin/users")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_editor_cannot_access_admin(editor_client):
    resp = await editor_client.get("/admin/users")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_can_create_user(auth_client):
    resp = await auth_client.post(
        "/admin/users/new",
        data={"username": "newuser", "password": "pass123", "role": "viewer"},
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/admin/users"

    # Verify user appears in the list
    resp = await auth_client.get("/admin/users")
    assert b"newuser" in resp.content


@pytest.mark.asyncio
async def test_admin_can_change_user_role(auth_client, pool):
    from grcen.permissions import UserRole
    from grcen.services.auth import create_user, get_user_by_id

    target = await create_user(pool, "rolechange", "pass123", role=UserRole.VIEWER)
    resp = await auth_client.post(
        f"/admin/users/{target.id}/edit",
        data={"role": "editor", "password": ""},
    )
    assert resp.status_code == 302

    updated = await get_user_by_id(pool, target.id)
    assert updated.role == UserRole.EDITOR


@pytest.mark.asyncio
async def test_admin_can_deactivate_user(auth_client, pool):
    from grcen.permissions import UserRole
    from grcen.services.auth import create_user, get_user_by_id

    target = await create_user(pool, "deactivateme", "pass123", role=UserRole.VIEWER)
    assert target.is_active

    resp = await auth_client.post(f"/admin/users/{target.id}/toggle-active")
    assert resp.status_code == 302

    updated = await get_user_by_id(pool, target.id)
    assert not updated.is_active


@pytest.mark.asyncio
async def test_admin_cannot_deactivate_self(auth_client, pool):
    from grcen.services.auth import get_user_by_id

    # Get the admin's own user ID from the session
    resp = await auth_client.get("/api/assets/")
    assert resp.status_code == 200

    # Find the admin user
    from grcen.services.auth import list_users
    users = await list_users(pool)
    admin = [u for u in users if u.is_admin][0]

    resp = await auth_client.post(f"/admin/users/{admin.id}/toggle-active")
    assert resp.status_code == 302

    updated = await get_user_by_id(pool, admin.id)
    assert updated.is_active  # Should still be active


@pytest.mark.asyncio
async def test_admin_can_delete_user(auth_client, pool):
    from grcen.permissions import UserRole
    from grcen.services.auth import create_user, get_user_by_id

    target = await create_user(pool, "deleteme", "pass123", role=UserRole.VIEWER)
    resp = await auth_client.post(f"/admin/users/{target.id}/delete")
    assert resp.status_code == 302

    deleted = await get_user_by_id(pool, target.id)
    assert deleted is None


@pytest.mark.asyncio
async def test_admin_cannot_delete_self(auth_client, pool):
    from grcen.services.auth import list_users

    users = await list_users(pool)
    admin = [u for u in users if u.is_admin][0]

    resp = await auth_client.post(f"/admin/users/{admin.id}/delete")
    assert resp.status_code == 302

    from grcen.services.auth import get_user_by_id
    still_exists = await get_user_by_id(pool, admin.id)
    assert still_exists is not None
