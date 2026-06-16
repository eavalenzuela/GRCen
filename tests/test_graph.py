import pytest


@pytest.mark.asyncio
async def test_graph_endpoint(auth_client):
    # Create assets and a relationship
    r1 = await auth_client.post("/api/assets/", json={"type": "person", "name": "Node A"})
    r2 = await auth_client.post("/api/assets/", json={"type": "system", "name": "Node B"})
    a1 = r1.json()["id"]
    a2 = r2.json()["id"]

    await auth_client.post(
        "/api/relationships/",
        json={
            "source_asset_id": a1,
            "target_asset_id": a2,
            "relationship_type": "connects_to",
        },
    )

    resp = await auth_client.get(f"/api/graph/{a1}?depth=1")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["nodes"]) == 2
    assert len(data["edges"]) == 1


@pytest.mark.asyncio
async def test_org_graph_endpoint_and_page(auth_client):
    """Whole-org graph: API returns the org's nodes/edges; the page renders."""
    r1 = await auth_client.post("/api/assets/", json={"type": "person", "name": "Whole A"})
    r2 = await auth_client.post("/api/assets/", json={"type": "system", "name": "Whole B"})
    a1 = r1.json()["id"]
    a2 = r2.json()["id"]
    await auth_client.post(
        "/api/relationships/",
        json={"source_asset_id": a1, "target_asset_id": a2, "relationship_type": "connects_to"},
    )

    api = await auth_client.get("/api/graph")
    assert api.status_code == 200
    data = api.json()
    ids = {n["id"] for n in data["nodes"]}
    assert {a1, a2} <= ids
    assert any(e["source"] == a1 and e["target"] == a2 for e in data["edges"])

    page = await auth_client.get("/graph")
    assert page.status_code == 200
    assert "Organization Graph" in page.text
