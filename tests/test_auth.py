import pytest


@pytest.mark.asyncio
async def test_unauthenticated_access(client):
    resp = await client.get("/api/assets/")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_logout(client, pool):
    from grcen.services.auth import create_user

    await create_user(pool, "testuser", "testpass123")

    # Login via API
    resp = await client.post(
        "/api/auth/login",
        json={"username": "testuser", "password": "testpass123"},
    )
    assert resp.status_code == 200
    assert resp.json()["username"] == "testuser"

    # Now authenticated
    resp = await client.get("/api/assets/")
    assert resp.status_code == 200

    # Logout
    resp = await client.post("/api/auth/logout")
    assert resp.status_code == 200

    # Back to unauthenticated
    resp = await client.get("/api/assets/")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_bad_credentials(client, pool):
    from grcen.services.auth import create_user

    await create_user(pool, "gooduser", "goodpass")

    resp = await client.post(
        "/api/auth/login",
        json={"username": "gooduser", "password": "wrongpass"},
    )
    assert resp.status_code == 401
