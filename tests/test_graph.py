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

    # Per-asset graph page renders (legend + expand panel markup present).
    page = await auth_client.get(f"/graph/{a1}")
    assert page.status_code == 200
    assert 'id="graph-legend"' in page.text
    assert 'id="graph-node-panel"' in page.text


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


@pytest.mark.asyncio
async def test_get_asset_graph_caps_nodes(pool):
    import uuid

    from grcen.models.asset import AssetType
    from grcen.permissions import UserRole
    from grcen.services import asset as asset_svc
    from grcen.services import relationship as rel_svc
    from grcen.services.auth import create_user
    from grcen.services.graph import get_asset_graph

    admin = await create_user(pool, f"u_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.ADMIN)
    hub = await asset_svc.create_asset(pool, type=AssetType.SYSTEM, name="Hub", updated_by=admin.id)
    for i in range(5):
        n = await asset_svc.create_asset(pool, type=AssetType.SYSTEM, name=f"N{i}", updated_by=admin.id)
        await rel_svc.create_relationship(
            pool, source_asset_id=hub.id, target_asset_id=n.id, relationship_type="connects_to"
        )

    # depth 1 reaches 6 nodes; the cap keeps the centre + 2 nearest.
    g = await get_asset_graph(pool, hub.id, depth=1, max_nodes=3)
    assert len(g.nodes) == 3
    ids = {n.id for n in g.nodes}
    assert str(hub.id) in ids  # centre (lvl 0) is always kept
    for e in g.edges:  # no edge dangles outside the capped node set
        assert e.source in ids and e.target in ids

    # Uncapped, all 6 nodes and 5 edges come back (single-CTE correctness).
    full = await get_asset_graph(pool, hub.id, depth=1)
    assert len(full.nodes) == 6
    assert len(full.edges) == 5
