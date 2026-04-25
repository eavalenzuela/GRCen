"""Workflow extensions: comment threads + multi-step approvals."""
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from grcen.main import app
from grcen.models.asset import AssetType
from grcen.permissions import UserRole
from grcen.services import workflow_service
from grcen.services.auth import create_user
from tests.conftest import login_with_csrf


@pytest_asyncio.fixture
async def two_admins_and_editor(pool):
    a1 = await create_user(
        pool, f"a1_{uuid.uuid4().hex[:8]}", "x", role=UserRole.ADMIN
    )
    a2 = await create_user(
        pool, f"a2_{uuid.uuid4().hex[:8]}", "x", role=UserRole.ADMIN
    )
    editor = await create_user(
        pool, f"e_{uuid.uuid4().hex[:8]}", "x", role=UserRole.EDITOR
    )

    async def make_client(u):
        c = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        await login_with_csrf(c, u.username, "x")
        return c

    ca1 = await make_client(a1)
    ca2 = await make_client(a2)
    ced = await make_client(editor)
    try:
        yield {"a1": a1, "a2": a2, "editor": editor, "ca1": ca1, "ca2": ca2, "ced": ced}
    finally:
        await ca1.aclose()
        await ca2.aclose()
        await ced.aclose()


# ── comments ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_and_list_comments(pool, two_admins_and_editor):
    """Service-level: add three comments, list returns them in order."""
    fx = two_admins_and_editor
    await workflow_service.upsert_config(
        pool, AssetType.POLICY, require_approval_create=True,
        require_approval_update=False, require_approval_delete=False,
    )
    submit = await fx["ced"].post(
        "/api/assets/", json={"type": "policy", "name": "P"}
    )
    change_id = submit.json()["pending_change_id"]
    change = await workflow_service.get(pool, uuid.UUID(change_id))

    await workflow_service.add_comment(pool, change, fx["a1"], "Looks fine to me")
    await workflow_service.add_comment(pool, change, fx["editor"], "Thanks!")

    comments = await workflow_service.list_comments(pool, change.id)
    assert [c.body for c in comments] == ["Looks fine to me", "Thanks!"]
    assert comments[0].author_username == fx["a1"].username


@pytest.mark.asyncio
async def test_post_comment_endpoint(pool, two_admins_and_editor):
    fx = two_admins_and_editor
    await workflow_service.upsert_config(
        pool, AssetType.POLICY, require_approval_create=True,
        require_approval_update=False, require_approval_delete=False,
    )
    submit = await fx["ced"].post(
        "/api/assets/", json={"type": "policy", "name": "P"}
    )
    change_id = submit.json()["pending_change_id"]
    resp = await fx["ca1"].post(
        f"/approvals/{change_id}/comment",
        data={"body": "Question: why now?"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    detail = await fx["ca1"].get(f"/approvals/{change_id}")
    assert "Question: why now?" in detail.text


@pytest.mark.asyncio
async def test_empty_comment_is_a_noop(pool, two_admins_and_editor):
    """Submitting blank text shouldn't error or create a row."""
    fx = two_admins_and_editor
    await workflow_service.upsert_config(
        pool, AssetType.POLICY, require_approval_create=True,
        require_approval_update=False, require_approval_delete=False,
    )
    submit = await fx["ced"].post(
        "/api/assets/", json={"type": "policy", "name": "P"}
    )
    change_id = submit.json()["pending_change_id"]
    await fx["ca1"].post(
        f"/approvals/{change_id}/comment",
        data={"body": "   "}, follow_redirects=False,
    )
    count = await pool.fetchval(
        "SELECT count(*) FROM pending_change_comments WHERE pending_change_id = $1",
        uuid.UUID(change_id),
    )
    assert count == 0


# ── multi-step approvals ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_single_approval_still_applies_immediately(pool, two_admins_and_editor):
    """required_approvals=1 (the default) keeps the original one-shot behaviour."""
    fx = two_admins_and_editor
    await workflow_service.upsert_config(
        pool, AssetType.POLICY, require_approval_create=True,
        require_approval_update=False, require_approval_delete=False,
        required_approvals=1,
    )
    submit = await fx["ced"].post(
        "/api/assets/", json={"type": "policy", "name": "Solo"}
    )
    change_id = submit.json()["pending_change_id"]
    resp = await fx["ca1"].post(f"/api/approvals/{change_id}/approve", json={})
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"
    listing = (await fx["ca1"].get("/api/assets/?type=policy")).json()
    assert any(a["name"] == "Solo" for a in listing["items"])


@pytest.mark.asyncio
async def test_two_step_approval_holds_until_threshold(pool, two_admins_and_editor):
    fx = two_admins_and_editor
    await workflow_service.upsert_config(
        pool, AssetType.POLICY, require_approval_create=True,
        require_approval_update=False, require_approval_delete=False,
        required_approvals=2,
    )
    submit = await fx["ced"].post(
        "/api/assets/", json={"type": "policy", "name": "TwoStep"}
    )
    change_id = submit.json()["pending_change_id"]

    # First admin approves — change stays pending, asset not yet created.
    first = await fx["ca1"].post(f"/api/approvals/{change_id}/approve", json={})
    assert first.status_code == 200
    assert first.json()["status"] == "pending"
    assert not any(
        a["name"] == "TwoStep"
        for a in (await fx["ca1"].get("/api/assets/?type=policy")).json()["items"]
    )

    # Second admin approves — asset now exists.
    second = await fx["ca2"].post(f"/api/approvals/{change_id}/approve", json={})
    assert second.status_code == 200
    assert second.json()["status"] == "approved"
    assert any(
        a["name"] == "TwoStep"
        for a in (await fx["ca1"].get("/api/assets/?type=policy")).json()["items"]
    )


@pytest.mark.asyncio
async def test_same_approver_cannot_double_count(pool, two_admins_and_editor):
    fx = two_admins_and_editor
    await workflow_service.upsert_config(
        pool, AssetType.POLICY, require_approval_create=True,
        require_approval_update=False, require_approval_delete=False,
        required_approvals=2,
    )
    submit = await fx["ced"].post(
        "/api/assets/", json={"type": "policy", "name": "OneVote"}
    )
    change_id = submit.json()["pending_change_id"]

    first = await fx["ca1"].post(f"/api/approvals/{change_id}/approve", json={})
    assert first.status_code == 200
    second = await fx["ca1"].post(f"/api/approvals/{change_id}/approve", json={})
    # Same approver again → 400 with a clear reason.
    assert second.status_code == 400


@pytest.mark.asyncio
async def test_approvals_list_records_each_approver(pool, two_admins_and_editor):
    fx = two_admins_and_editor
    await workflow_service.upsert_config(
        pool, AssetType.POLICY, require_approval_create=True,
        require_approval_update=False, require_approval_delete=False,
        required_approvals=2,
    )
    submit = await fx["ced"].post(
        "/api/assets/", json={"type": "policy", "name": "Track"}
    )
    change_id = uuid.UUID(submit.json()["pending_change_id"])
    await fx["ca1"].post(f"/api/approvals/{change_id}/approve", json={"note": "ok"})
    await fx["ca2"].post(f"/api/approvals/{change_id}/approve", json={})

    approvals = await workflow_service.list_approvals(pool, change_id)
    usernames = {a.approver_username for a in approvals}
    assert usernames == {fx["a1"].username, fx["a2"].username}
