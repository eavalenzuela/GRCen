import pytest


@pytest.mark.asyncio
async def test_create_and_list_assets(auth_client):
    # Create an asset
    resp = await auth_client.post(
        "/api/assets/",
        json={
            "type": "person",
            "name": "Alice",
            "description": "Test person",
            "status": "active",
        },
    )
    assert resp.status_code == 201
    asset = resp.json()
    assert asset["name"] == "Alice"
    assert asset["type"] == "person"
    asset_id = asset["id"]

    # Get by ID
    resp = await auth_client.get(f"/api/assets/{asset_id}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Alice"

    # List
    resp = await auth_client.get("/api/assets/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1

    # Filter by type
    resp = await auth_client.get("/api/assets/?type=person")
    assert resp.status_code == 200
    assert all(a["type"] == "person" for a in resp.json()["items"])


@pytest.mark.asyncio
async def test_update_asset(auth_client):
    resp = await auth_client.post(
        "/api/assets/",
        json={"type": "system", "name": "Old Name"},
    )
    asset_id = resp.json()["id"]

    resp = await auth_client.put(
        f"/api/assets/{asset_id}",
        json={"name": "New Name"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "New Name"


@pytest.mark.asyncio
async def test_delete_asset(auth_client):
    resp = await auth_client.post(
        "/api/assets/",
        json={"type": "risk", "name": "Temp Risk"},
    )
    asset_id = resp.json()["id"]

    resp = await auth_client.delete(f"/api/assets/{asset_id}")
    assert resp.status_code == 204

    resp = await auth_client.get(f"/api/assets/{asset_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_search_assets(auth_client):
    await auth_client.post("/api/assets/", json={"type": "person", "name": "Searchable Bob"})
    resp = await auth_client.get("/api/assets/search?q=Searchable")
    assert resp.status_code == 200
    assert any(a["name"] == "Searchable Bob" for a in resp.json())
