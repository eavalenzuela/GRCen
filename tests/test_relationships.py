import pytest


@pytest.mark.asyncio
async def test_create_and_list_relationships(auth_client):
    # Create two assets
    r1 = await auth_client.post("/api/assets/", json={"type": "person", "name": "Person A"})
    r2 = await auth_client.post("/api/assets/", json={"type": "system", "name": "System B"})
    a1 = r1.json()["id"]
    a2 = r2.json()["id"]

    # Create relationship
    resp = await auth_client.post(
        "/api/relationships/",
        json={
            "source_asset_id": a1,
            "target_asset_id": a2,
            "relationship_type": "manages",
            "description": "Person A manages System B",
        },
    )
    assert resp.status_code == 201
    rel = resp.json()
    assert rel["relationship_type"] == "manages"

    # List relationships for asset
    resp = await auth_client.get(f"/api/relationships/?asset_id={a1}")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


@pytest.mark.asyncio
async def test_delete_relationship(auth_client):
    r1 = await auth_client.post("/api/assets/", json={"type": "policy", "name": "Policy X"})
    r2 = await auth_client.post("/api/assets/", json={"type": "process", "name": "Process Y"})

    resp = await auth_client.post(
        "/api/relationships/",
        json={
            "source_asset_id": r1.json()["id"],
            "target_asset_id": r2.json()["id"],
            "relationship_type": "governs",
        },
    )
    rel_id = resp.json()["id"]

    resp = await auth_client.delete(f"/api/relationships/{rel_id}")
    assert resp.status_code == 204
