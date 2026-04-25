"""Workflow / approval gate tests.

Covers:
- Toggling per-type workflow config
- HTML and REST asset writes routing to pending_changes when gated
- Approve / reject / withdraw transitions and their RBAC checks
- Self-approval is blocked
- Approval applies the recorded payload
- Audit log records the approver with submitter noted
"""
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from grcen.main import app
from grcen.permissions import UserRole
from grcen.services.auth import create_user
from tests.conftest import login_with_csrf


@pytest_asyncio.fixture
async def admin_and_editor(pool):
    admin = await create_user(
        pool, f"admin_{uuid.uuid4().hex[:8]}", "testpass", role=UserRole.ADMIN
    )
    editor = await create_user(
        pool, f"editor_{uuid.uuid4().hex[:8]}", "testpass", role=UserRole.EDITOR
    )

    async def make_client(user):
        transport = ASGITransport(app=app)
        c = AsyncClient(transport=transport, base_url="http://test")
        await login_with_csrf(c, user.username, "testpass")
        return c

    admin_c = await make_client(admin)
    editor_c = await make_client(editor)
    try:
        yield admin, editor, admin_c, editor_c
    finally:
        await admin_c.aclose()
        await editor_c.aclose()


async def _enable_gate(pool, asset_type: str, *, create=False, update=False, delete=False):
    await pool.execute(
        """INSERT INTO workflow_config (asset_type, require_approval_create,
            require_approval_update, require_approval_delete, updated_at)
           VALUES ($1, $2, $3, $4, now())
           ON CONFLICT (asset_type) DO UPDATE SET
               require_approval_create=EXCLUDED.require_approval_create,
               require_approval_update=EXCLUDED.require_approval_update,
               require_approval_delete=EXCLUDED.require_approval_delete""",
        asset_type, create, update, delete,
    )


@pytest.mark.asyncio
async def test_create_without_gate_is_immediate(auth_client):
    resp = await auth_client.post(
        "/api/assets/", json={"type": "policy", "name": "P1"}
    )
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_create_with_gate_is_pending(pool, admin_and_editor):
    await _enable_gate(pool, "policy", create=True)
    _, _, admin_c, editor_c = admin_and_editor
    # Editor submits — receives 202 with a pending_change_id
    resp = await editor_c.post(
        "/api/assets/", json={"type": "policy", "name": "Gated Policy"}
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "pending_approval"
    change_id = body["pending_change_id"]

    # Asset has not been created yet
    listing = await editor_c.get("/api/assets/?type=policy")
    assert listing.json()["total"] == 0

    # Pending queue shows it
    queue = await admin_c.get("/api/approvals/")
    assert queue.status_code == 200
    items = queue.json()
    assert len(items) == 1
    assert items[0]["id"] == change_id
    assert items[0]["action"] == "create"


@pytest.mark.asyncio
async def test_self_approval_blocked(pool, admin_and_editor):
    await _enable_gate(pool, "system", create=True)
    admin, _, admin_c, _ = admin_and_editor
    # Admin submits and tries to self-approve
    resp = await admin_c.post(
        "/api/assets/", json={"type": "system", "name": "Self-approve"}
    )
    assert resp.status_code == 202
    change_id = resp.json()["pending_change_id"]

    approve = await admin_c.post(f"/api/approvals/{change_id}/approve", json={})
    assert approve.status_code == 400
    assert "own pending change" in approve.text.lower()


@pytest.mark.asyncio
async def test_approve_applies_create(pool, admin_and_editor):
    await _enable_gate(pool, "system", create=True)
    _, _, admin_c, editor_c = admin_and_editor
    resp = await editor_c.post(
        "/api/assets/",
        json={
            "type": "system",
            "name": "Applied via approval",
            "description": "From queue",
        },
    )
    change_id = resp.json()["pending_change_id"]

    approve = await admin_c.post(f"/api/approvals/{change_id}/approve", json={})
    assert approve.status_code == 200
    assert approve.json()["status"] == "approved"

    # Asset now exists
    listing = await admin_c.get("/api/assets/?type=system")
    items = listing.json()["items"]
    assert any(a["name"] == "Applied via approval" for a in items)

    # Audit log records the approver and notes the submitter
    rows = await pool.fetch(
        "SELECT username, action, changes FROM audit_log WHERE entity_type='asset' AND action='create'"
    )
    assert len(rows) == 1
    assert rows[0]["username"].startswith("admin_")
    import json as _json
    changes = _json.loads(rows[0]["changes"])
    assert changes.get("_workflow", {}).get("submitted_by", "").startswith("editor_")


@pytest.mark.asyncio
async def test_approve_applies_update(pool, admin_and_editor):
    # Create an asset directly (no gate yet), then turn the gate on for updates
    _, _, admin_c, editor_c = admin_and_editor
    create = await admin_c.post(
        "/api/assets/", json={"type": "process", "name": "Original"}
    )
    asset_id = create.json()["id"]
    await _enable_gate(pool, "process", update=True)

    # Editor proposes a rename
    upd = await editor_c.put(f"/api/assets/{asset_id}", json={"name": "Renamed"})
    assert upd.status_code == 202
    change_id = upd.json()["pending_change_id"]

    # Asset still has old name
    cur = await admin_c.get(f"/api/assets/{asset_id}")
    assert cur.json()["name"] == "Original"

    # Approve
    approve = await admin_c.post(f"/api/approvals/{change_id}/approve", json={})
    assert approve.status_code == 200

    after = await admin_c.get(f"/api/assets/{asset_id}")
    assert after.json()["name"] == "Renamed"


@pytest.mark.asyncio
async def test_reject_does_not_apply(pool, admin_and_editor):
    await _enable_gate(pool, "control", create=True)
    _, _, admin_c, editor_c = admin_and_editor
    resp = await editor_c.post(
        "/api/assets/", json={"type": "control", "name": "Should not exist"}
    )
    change_id = resp.json()["pending_change_id"]

    rej = await admin_c.post(
        f"/api/approvals/{change_id}/reject", json={"note": "no thanks"}
    )
    assert rej.status_code == 200
    assert rej.json()["status"] == "rejected"
    assert rej.json()["decision_note"] == "no thanks"

    listing = await admin_c.get("/api/assets/?type=control")
    assert listing.json()["total"] == 0


@pytest.mark.asyncio
async def test_withdraw_only_by_submitter(pool, admin_and_editor):
    await _enable_gate(pool, "vendor", create=True)
    _, _, admin_c, editor_c = admin_and_editor
    resp = await editor_c.post(
        "/api/assets/", json={"type": "vendor", "name": "Withdrawable"}
    )
    change_id = resp.json()["pending_change_id"]

    # Admin (not submitter) cannot withdraw
    bad = await admin_c.post(f"/api/approvals/{change_id}/withdraw", json={})
    assert bad.status_code == 400

    # Submitter can
    ok = await editor_c.post(f"/api/approvals/{change_id}/withdraw", json={})
    assert ok.status_code == 200
    assert ok.json()["status"] == "withdrawn"


@pytest.mark.asyncio
async def test_delete_gate(pool, admin_and_editor):
    _, _, admin_c, editor_c = admin_and_editor
    create = await admin_c.post(
        "/api/assets/", json={"type": "risk", "name": "ProtectMe"}
    )
    asset_id = create.json()["id"]
    await _enable_gate(pool, "risk", delete=True)

    delete_resp = await editor_c.delete(f"/api/assets/{asset_id}")
    assert delete_resp.status_code == 202

    # Asset still exists
    still = await admin_c.get(f"/api/assets/{asset_id}")
    assert still.status_code == 200

    change_id = delete_resp.json()["pending_change_id"]
    approve = await admin_c.post(f"/api/approvals/{change_id}/approve", json={})
    assert approve.status_code == 200

    gone = await admin_c.get(f"/api/assets/{asset_id}")
    assert gone.status_code == 404


@pytest.mark.asyncio
async def test_duplicate_pending_update_blocked(pool, admin_and_editor):
    _, _, admin_c, editor_c = admin_and_editor
    create = await admin_c.post(
        "/api/assets/", json={"type": "system", "name": "Dup"}
    )
    asset_id = create.json()["id"]
    await _enable_gate(pool, "system", update=True)

    first = await editor_c.put(f"/api/assets/{asset_id}", json={"name": "v2"})
    assert first.status_code == 202
    second = await editor_c.put(f"/api/assets/{asset_id}", json={"name": "v3"})
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_editor_cannot_approve(pool, admin_and_editor):
    await _enable_gate(pool, "policy", create=True)
    admin, editor, admin_c, editor_c = admin_and_editor
    # Admin submits
    resp = await admin_c.post(
        "/api/assets/", json={"type": "policy", "name": "Editor can't touch"}
    )
    change_id = resp.json()["pending_change_id"]
    # Editor tries to approve -> 403 (lacks APPROVE permission)
    approve = await editor_c.post(f"/api/approvals/{change_id}/approve", json={})
    assert approve.status_code == 403


@pytest.mark.asyncio
async def test_workflow_admin_settings_save(pool, auth_client):
    # Admin form submit toggles three checkboxes for Policy
    resp = await auth_client.post(
        "/admin/workflow",
        data={"create_policy": "on", "update_policy": "on"},
    )
    assert resp.status_code in (200, 302)
    row = await pool.fetchrow(
        "SELECT * FROM workflow_config WHERE asset_type='policy'"
    )
    assert row is not None
    assert row["require_approval_create"] is True
    assert row["require_approval_update"] is True
    assert row["require_approval_delete"] is False


@pytest.mark.asyncio
async def test_html_create_redirects_to_approval(pool, admin_and_editor):
    """Form-based create against a gated type lands on the approval detail page."""
    await _enable_gate(pool, "policy", create=True)
    _, _, _, editor_c = admin_and_editor
    resp = await editor_c.post(
        "/assets/new",
        data={"type": "policy", "name": "Form-gated", "status": "active"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "/approvals/" in resp.headers["location"]
