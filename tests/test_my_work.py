"""Tests for the /my-work aggregation page."""

import uuid

import pytest

from grcen.models.asset import AssetType
from grcen.services import asset as asset_svc, watcher_service, workflow_service
from grcen.services.auth import set_person_asset_link


async def _admin_user_of(client, pool):
    """Resolve the logged-in fixture client's user row."""
    row = await pool.fetchrow(
        "SELECT id, username, organization_id FROM users ORDER BY created_at DESC LIMIT 1"
    )
    return row


@pytest.mark.asyncio
async def test_page_renders_empty_state(auth_client):
    resp = await auth_client.get("/my-work")
    assert resp.status_code == 200
    assert "My Work" in resp.text
    assert "Nothing waiting for approval" in resp.text


@pytest.mark.asyncio
async def test_shows_pending_approvals_and_own_submissions(auth_client, editor_client, pool):
    await workflow_service.upsert_config(
        pool, AssetType.POLICY, require_approval_create=True,
        require_approval_update=False, require_approval_delete=False,
    )
    submit = await editor_client.post(
        "/api/assets/", json={"type": "policy", "name": "Needs Blessing"}
    )
    assert submit.status_code == 202

    # Admin (approver, not submitter) sees it as actionable.
    resp = await auth_client.get("/my-work")
    assert "Needs Blessing" in resp.text
    assert "Review" in resp.text

    # The editor sees it under their own submissions.
    resp = await editor_client.get("/my-work")
    assert "My submissions in flight" in resp.text
    assert "Needs Blessing" in resp.text


@pytest.mark.asyncio
async def test_shows_owned_reviews_and_findings(auth_client, pool):
    me = await pool.fetchrow(
        "SELECT id, organization_id FROM users ORDER BY created_at ASC LIMIT 1"
    )
    person = await asset_svc.create_asset(
        pool, type=AssetType.PERSON, name="Me Myself", status="active",
        organization_id=me["organization_id"],
    )
    await set_person_asset_link(pool, me["id"], person.id)

    await asset_svc.create_asset(
        pool, type=AssetType.POLICY, name="Overdue AUP", status="active",
        owner_id=person.id, metadata_={"review_date": "2020-01-01"},
        organization_id=me["organization_id"],
    )
    await asset_svc.create_asset(
        pool, type=AssetType.FINDING, name="Fix the firewall", status="active",
        owner_id=person.id,
        metadata_={"finding_status": "open", "due_date": "2026-08-01"},
        organization_id=me["organization_id"],
    )

    resp = await auth_client.get("/my-work")
    assert resp.status_code == 200
    assert "Overdue AUP" in resp.text
    assert "overdue" in resp.text
    assert "Fix the firewall" in resp.text


@pytest.mark.asyncio
async def test_shows_watched_assets(auth_client, pool):
    me = await pool.fetchrow(
        "SELECT id, organization_id FROM users ORDER BY created_at ASC LIMIT 1"
    )
    asset = await asset_svc.create_asset(
        pool, type=AssetType.SYSTEM, name=f"Watched-{uuid.uuid4().hex[:6]}",
        status="active", organization_id=me["organization_id"],
    )
    await watcher_service.watch(
        pool, asset.id, me["id"], organization_id=me["organization_id"]
    )
    resp = await auth_client.get("/my-work")
    assert asset.name in resp.text


@pytest.mark.asyncio
async def test_requires_login(client):
    resp = await client.get("/my-work", follow_redirects=False)
    assert resp.status_code in (302, 401)
