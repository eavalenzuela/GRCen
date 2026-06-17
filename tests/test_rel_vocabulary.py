"""Tests that the canonical relationship vocabulary is offered for input (R2/R5)."""
import pytest


@pytest.mark.asyncio
async def test_relationship_types_endpoint_merges_vocabulary(auth_client):
    a = (await auth_client.post("/api/assets/", json={"type": "system", "name": "V1"})).json()["id"]
    b = (await auth_client.post("/api/assets/", json={"type": "system", "name": "V2"})).json()["id"]
    # A bespoke type the canonical list doesn't contain.
    await auth_client.post(
        "/api/relationships/",
        json={"source_asset_id": a, "target_asset_id": b, "relationship_type": "frobnicates"},
    )
    resp = await auth_client.get("/api/relationships/types")
    assert resp.status_code == 200  # /types is not shadowed by /{rel_id}
    types = resp.json()
    assert "manages" in types          # canonical
    assert "mitigated_by" in types     # canonical, not in use
    assert "frobnicates" in types      # in use, not canonical
    assert types == sorted(types)


@pytest.mark.asyncio
async def test_detail_form_offers_canonical_vocabulary(auth_client):
    a = (await auth_client.post("/api/assets/", json={"type": "system", "name": "Fresh"})).json()["id"]
    # No relationships exist yet, but the add-relationship datalist should still
    # offer canonical types (not just types already in the DB).
    page = await auth_client.get(f"/assets/{a}")
    assert page.status_code == 200
    assert '<option value="mitigated_by">' in page.text
    assert '<option value="depends_on">' in page.text


@pytest.mark.asyncio
async def test_edit_form_offers_canonical_vocabulary(auth_client):
    a = (await auth_client.post("/api/assets/", json={"type": "system", "name": "Ed1"})).json()["id"]
    b = (await auth_client.post("/api/assets/", json={"type": "system", "name": "Ed2"})).json()["id"]
    rid = (await auth_client.post(
        "/api/relationships/",
        json={"source_asset_id": a, "target_asset_id": b, "relationship_type": "depends_on"},
    )).json()["id"]
    page = await auth_client.get(f"/relationships/{rid}/edit?from={a}")
    assert page.status_code == 200
    assert '<option value="mitigated_by">' in page.text
