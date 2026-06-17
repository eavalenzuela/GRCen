"""Tests for the unlinked-asset filter (S2) and the relationship edit UI (R1)."""
import uuid

import pytest

from grcen.models.asset import AssetType
from grcen.permissions import UserRole
from grcen.services import asset as asset_svc
from grcen.services import relationship as rel_svc
from grcen.services.auth import create_user


@pytest.mark.asyncio
async def test_list_assets_unlinked_filter(pool):
    admin = await create_user(pool, f"u_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.ADMIN)
    a = await asset_svc.create_asset(pool, type=AssetType.SYSTEM, name="Linked A", updated_by=admin.id)
    b = await asset_svc.create_asset(pool, type=AssetType.SYSTEM, name="Linked B", updated_by=admin.id)
    await asset_svc.create_asset(pool, type=AssetType.SYSTEM, name="Lonely", updated_by=admin.id)
    await rel_svc.create_relationship(
        pool, source_asset_id=a.id, target_asset_id=b.id, relationship_type="depends_on"
    )

    unlinked, _ = await asset_svc.list_assets(pool, unlinked=True)
    names = {x.name for x in unlinked}
    assert "Lonely" in names
    assert "Linked A" not in names
    assert "Linked B" not in names

    # Without the filter, all three are present.
    all_assets, _ = await asset_svc.list_assets(pool)
    all_names = {x.name for x in all_assets}
    assert {"Linked A", "Linked B", "Lonely"} <= all_names


@pytest.mark.asyncio
async def test_assets_page_unlinked_filter(auth_client):
    a = (await auth_client.post("/api/assets/", json={"type": "system", "name": "P-Linked-A"})).json()["id"]
    b = (await auth_client.post("/api/assets/", json={"type": "system", "name": "P-Linked-B"})).json()["id"]
    await auth_client.post("/api/assets/", json={"type": "system", "name": "P-Lonely"})
    await auth_client.post(
        "/api/relationships/",
        json={"source_asset_id": a, "target_asset_id": b, "relationship_type": "depends_on"},
    )
    page = await auth_client.get("/assets?unlinked=on")
    assert page.status_code == 200
    assert "P-Lonely" in page.text
    assert "P-Linked-A" not in page.text


@pytest.mark.asyncio
async def test_relationship_edit_page_and_submit(auth_client):
    a1 = (await auth_client.post("/api/assets/", json={"type": "system", "name": "E1"})).json()["id"]
    a2 = (await auth_client.post("/api/assets/", json={"type": "system", "name": "E2"})).json()["id"]
    rid = (await auth_client.post(
        "/api/relationships/",
        json={"source_asset_id": a1, "target_asset_id": a2, "relationship_type": "depends_on"},
    )).json()["id"]

    # Edit page renders with the current type.
    page = await auth_client.get(f"/relationships/{rid}/edit?from={a1}")
    assert page.status_code == 200
    assert "Edit Relationship" in page.text
    assert "depends_on" in page.text

    # The detail page exposes an Edit link.
    detail = await auth_client.get(f"/assets/{a1}")
    assert f"/relationships/{rid}/edit" in detail.text

    # Submit an edit → redirect back to the referring asset, change persisted.
    resp = await auth_client.post(
        f"/relationships/{rid}/edit",
        data={"relationship_type": "processed_by", "description": "fixed typo", "return_to": a1},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == f"/assets/{a1}"

    updated = (await auth_client.get(f"/api/relationships/{rid}")).json()
    assert updated["relationship_type"] == "processed_by"
    assert updated["description"] == "fixed typo"


@pytest.mark.asyncio
async def test_relationship_edit_requires_type(auth_client):
    a1 = (await auth_client.post("/api/assets/", json={"type": "system", "name": "E3"})).json()["id"]
    a2 = (await auth_client.post("/api/assets/", json={"type": "system", "name": "E4"})).json()["id"]
    rid = (await auth_client.post(
        "/api/relationships/",
        json={"source_asset_id": a1, "target_asset_id": a2, "relationship_type": "depends_on"},
    )).json()["id"]
    resp = await auth_client.post(
        f"/relationships/{rid}/edit",
        data={"relationship_type": "", "description": "x", "return_to": a1},
        follow_redirects=False,
    )
    assert resp.status_code == 400
