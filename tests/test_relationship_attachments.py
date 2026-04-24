"""Tests for attaching evidence (URLs, documents, files) to relationships."""

import io
import uuid

import pytest

from grcen.models.asset import AssetType
from grcen.models.attachment import AttachmentKind
from grcen.permissions import UserRole
from grcen.services import asset as asset_svc
from grcen.services import attachment as att_svc
from grcen.services import relationship as rel_svc
from grcen.services.auth import create_user


@pytest.fixture
async def admin_user(pool):
    return await create_user(
        pool, f"admin_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.ADMIN
    )


@pytest.fixture
async def two_assets_and_rel(pool, admin_user):
    src = await asset_svc.create_asset(
        pool, type=AssetType.SYSTEM, name="Src", status="active", updated_by=admin_user.id
    )
    tgt = await asset_svc.create_asset(
        pool, type=AssetType.REQUIREMENT, name="Tgt", status="active", updated_by=admin_user.id
    )
    rel = await rel_svc.create_relationship(
        pool,
        source_asset_id=src.id,
        target_asset_id=tgt.id,
        relationship_type="satisfies",
        description="control satisfies req",
    )
    return src, tgt, rel


# ── service layer ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_attachment_on_relationship(pool, two_assets_and_rel):
    _, _, rel = two_assets_and_rel
    att = await att_svc.create_attachment(
        pool,
        relationship_id=rel.id,
        kind=AttachmentKind.URL,
        name="Evidence doc",
        url_or_path="https://example.com/evidence",
    )
    assert att.relationship_id == rel.id
    assert att.asset_id is None
    listed = await att_svc.list_attachments_for_relationship(pool, rel.id)
    assert len(listed) == 1
    assert listed[0].id == att.id


@pytest.mark.asyncio
async def test_create_attachment_rejects_both_or_neither_owner(pool, two_assets_and_rel):
    src, _, rel = two_assets_and_rel
    with pytest.raises(ValueError):
        await att_svc.create_attachment(
            pool,
            kind=AttachmentKind.URL,
            name="neither",
            url_or_path="x",
        )
    with pytest.raises(ValueError):
        await att_svc.create_attachment(
            pool,
            asset_id=src.id,
            relationship_id=rel.id,
            kind=AttachmentKind.URL,
            name="both",
            url_or_path="x",
        )


@pytest.mark.asyncio
async def test_db_constraint_rejects_both_owners(pool, two_assets_and_rel):
    src, _, rel = two_assets_and_rel
    with pytest.raises(Exception) as exc:
        await pool.execute(
            """INSERT INTO attachments
                   (id, asset_id, relationship_id, kind, name, url_or_path)
               VALUES ($1, $2, $3, 'url', 'bad', 'x')""",
            uuid.uuid4(),
            src.id,
            rel.id,
        )
    assert "attachments_exactly_one_owner" in str(exc.value)


@pytest.mark.asyncio
async def test_db_constraint_rejects_neither_owner(pool):
    with pytest.raises(Exception) as exc:
        await pool.execute(
            """INSERT INTO attachments (id, kind, name, url_or_path)
               VALUES ($1, 'url', 'orphan', 'x')""",
            uuid.uuid4(),
        )
    assert "attachments_exactly_one_owner" in str(exc.value)


@pytest.mark.asyncio
async def test_cascade_delete_on_relationship_removes_attachments(pool, two_assets_and_rel):
    _, _, rel = two_assets_and_rel
    await att_svc.create_attachment(
        pool, relationship_id=rel.id, kind=AttachmentKind.URL,
        name="doc", url_or_path="https://e.com",
    )
    assert await pool.fetchval("SELECT count(*) FROM attachments WHERE relationship_id = $1", rel.id) == 1

    await rel_svc.delete_relationship(pool, rel.id)
    assert await pool.fetchval("SELECT count(*) FROM attachments WHERE relationship_id = $1", rel.id) == 0


# ── REST API ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_api_list_empty(auth_client, pool, two_assets_and_rel):
    _, _, rel = two_assets_and_rel
    resp = await auth_client.get(f"/api/relationships/{rel.id}/attachments/")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_api_create_url_attachment(auth_client, pool, two_assets_and_rel):
    _, _, rel = two_assets_and_rel
    resp = await auth_client.post(
        f"/api/relationships/{rel.id}/attachments/",
        json={"kind": "url", "name": "ref", "url_or_path": "https://e.com"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["relationship_id"] == str(rel.id)
    assert body["asset_id"] is None
    assert body["name"] == "ref"


@pytest.mark.asyncio
async def test_api_upload_and_download(auth_client, pool, two_assets_and_rel):
    _, _, rel = two_assets_and_rel
    files = {"file": ("proof.txt", io.BytesIO(b"hello world"), "text/plain")}
    up = await auth_client.post(
        f"/api/relationships/{rel.id}/attachments/upload", files=files
    )
    assert up.status_code == 201, up.text
    att_id = up.json()["id"]

    dl = await auth_client.get(
        f"/api/relationships/{rel.id}/attachments/{att_id}/download"
    )
    assert dl.status_code == 200
    assert dl.content == b"hello world"


@pytest.mark.asyncio
async def test_api_delete_attachment(auth_client, pool, two_assets_and_rel):
    _, _, rel = two_assets_and_rel
    created = await auth_client.post(
        f"/api/relationships/{rel.id}/attachments/",
        json={"kind": "url", "name": "doomed", "url_or_path": "https://e.com"},
    )
    att_id = created.json()["id"]
    resp = await auth_client.delete(f"/api/relationships/{rel.id}/attachments/{att_id}")
    assert resp.status_code == 204
    listed = await att_svc.list_attachments_for_relationship(pool, rel.id)
    assert listed == []


@pytest.mark.asyncio
async def test_api_download_404_when_attachment_belongs_to_other_relationship(
    auth_client, pool, two_assets_and_rel, admin_user
):
    # Attach file to rel1, try to download via rel2's URL
    _, _, rel1 = two_assets_and_rel
    s2 = await asset_svc.create_asset(
        pool, type=AssetType.SYSTEM, name="S2", status="active", updated_by=admin_user.id
    )
    t2 = await asset_svc.create_asset(
        pool, type=AssetType.REQUIREMENT, name="T2", status="active", updated_by=admin_user.id
    )
    rel2 = await rel_svc.create_relationship(
        pool, source_asset_id=s2.id, target_asset_id=t2.id,
        relationship_type="satisfies", description="",
    )
    files = {"file": ("x.txt", io.BytesIO(b"a"), "text/plain")}
    up = await auth_client.post(
        f"/api/relationships/{rel1.id}/attachments/upload", files=files
    )
    att_id = up.json()["id"]

    dl = await auth_client.get(
        f"/api/relationships/{rel2.id}/attachments/{att_id}/download"
    )
    assert dl.status_code == 404


# ── Evidence page ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_evidence_page_renders(auth_client, pool, two_assets_and_rel):
    _, _, rel = two_assets_and_rel
    resp = await auth_client.get(f"/relationships/{rel.id}/evidence")
    assert resp.status_code == 200
    assert "Evidence" in resp.text
    assert "Src" in resp.text
    assert "Tgt" in resp.text


@pytest.mark.asyncio
async def test_evidence_page_create_and_delete_flow(
    auth_client, pool, two_assets_and_rel
):
    from tests.conftest import _extract_csrf_from_session_cookie

    _, _, rel = two_assets_and_rel
    csrf = _extract_csrf_from_session_cookie(auth_client)
    resp = await auth_client.post(
        f"/relationships/{rel.id}/evidence",
        data={
            "kind": "url",
            "name": "form-test",
            "url_or_path": "https://e.com/form",
            "csrf_token": csrf,
        },
    )
    assert resp.status_code in (302, 303)
    listed = await att_svc.list_attachments_for_relationship(pool, rel.id)
    assert len(listed) == 1
    att_id = listed[0].id

    resp = await auth_client.post(
        f"/relationships/{rel.id}/evidence/{att_id}/delete",
        data={"csrf_token": _extract_csrf_from_session_cookie(auth_client)},
    )
    assert resp.status_code in (302, 303)
    assert await att_svc.list_attachments_for_relationship(pool, rel.id) == []


@pytest.mark.asyncio
async def test_asset_detail_shows_evidence_count(auth_client, pool, two_assets_and_rel):
    src, _, rel = two_assets_and_rel
    # No evidence → should show "+ add"
    resp = await auth_client.get(f"/assets/{src.id}")
    assert resp.status_code == 200
    assert f"/relationships/{rel.id}/evidence" in resp.text

    # Add two and check count renders
    await att_svc.create_attachment(
        pool, relationship_id=rel.id, kind=AttachmentKind.URL,
        name="a", url_or_path="https://e.com/a",
    )
    await att_svc.create_attachment(
        pool, relationship_id=rel.id, kind=AttachmentKind.URL,
        name="b", url_or_path="https://e.com/b",
    )
    resp = await auth_client.get(f"/assets/{src.id}")
    assert "2 files" in resp.text
