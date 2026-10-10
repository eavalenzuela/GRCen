"""Phase 0 of the UI improvement plan: restore interactivity the CSP had
silently broken, make htmx actions update the page, and close the security
defects the audit found on the way. See ui_audit_findings.md."""

import io
import re
import uuid
from pathlib import Path

import pyotp
import pytest
from httpx import ASGITransport, AsyncClient

from grcen.main import app
from grcen.models.asset import AssetType
from grcen.models.attachment import AttachmentKind
from grcen.permissions import UserRole
from grcen.services import (
    alert_service,
    asset as asset_svc,
    attachment as att_svc,
    oauth_service,
    totp_service,
    vendor_campaign_service as vc,
)
from grcen.services.auth import create_user
from tests.conftest import login_with_csrf

TEMPLATES = Path(__file__).resolve().parents[1] / "src" / "grcen" / "templates"
ROUTERS = Path(__file__).resolve().parents[1] / "src" / "grcen" / "routers"


# ── CSP-safe templates ────────────────────────────────────────────────────


def test_no_inline_event_handlers_anywhere():
    """script-src 'self' 'nonce-…' blocks inline on*= and hx-on:: — a handler
    written that way is dead code that only fails in the browser."""
    inline = re.compile(r"""\son[a-z]+\s*=\s*["']""")
    offenders = []
    for path in list(TEMPLATES.rglob("*.html")) + list(ROUTERS.glob("*.py")):
        text = path.read_text()
        for n, line in enumerate(text.splitlines(), 1):
            if inline.search(line) or "hx-on" in line:
                offenders.append(f"{path.relative_to(TEMPLATES.parent)}:{n}: {line.strip()[:90]}")
    assert not offenders, "inline handlers are blocked by CSP:\n" + "\n".join(offenders)


@pytest.mark.asyncio
async def test_base_loads_app_js_and_flash_region(auth_client):
    resp = await auth_client.get("/")
    assert resp.status_code == 200
    assert '<script src="/static/js/app.js"></script>' in resp.text
    assert 'id="flash-region"' in resp.text
    assert 'notif-changed from:body' in resp.text
    js = await auth_client.get("/static/js/app.js")
    assert js.status_code == 200
    assert "htmx.config.responseHandling" in js.text


@pytest.mark.asyncio
async def test_owner_search_renders_buttons_not_inline_js(auth_client, pool):
    await asset_svc.create_asset(pool, type=AssetType.PERSON, name="Picker Pat", status="active")
    resp = await auth_client.get("/api/owner-search?q=Picker")
    assert resp.status_code == 200
    assert 'data-pick-owner' in resp.text
    assert "onclick" not in resp.text


@pytest.mark.asyncio
async def test_delete_forms_carry_data_confirm(auth_client, pool):
    asset = await asset_svc.create_asset(pool, type=AssetType.SYSTEM, name="Confirm Me", status="active")
    resp = await auth_client.get(f"/assets/{asset.id}")
    assert resp.status_code == 200
    assert 'data-confirm="Delete this asset?"' in resp.text
    assert 'hx-ext="json-enc"' in resp.text  # URL attachment form talks JSON now


# ── htmx feedback ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mark_read_returns_row_for_htmx_and_204_otherwise(auth_client, pool):
    asset = await asset_svc.create_asset(pool, type=AssetType.SYSTEM, name="Notif Sys", status="active")
    alert = await alert_service.create_alert(
        pool, asset_id=asset.id, title="Ping", schedule_type="once",
    )
    await alert_service.fire_alert(pool, alert.id)
    notif = (await alert_service.list_notifications(pool, True, organization_id=None))[0]

    resp = await auth_client.post(
        f"/api/alerts/notifications/{notif.id}/read", headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    assert resp.headers.get("hx-trigger") == "notif-changed"
    assert "<tr" in resp.text and "Read</td>" in resp.text
    assert "Mark Read" not in resp.text

    # A second, plain request still gets the API contract.
    resp2 = await auth_client.post(f"/api/alerts/notifications/{notif.id}/read")
    assert resp2.status_code == 204
    assert resp2.headers.get("hx-trigger") == "notif-changed"


# ── exports ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_export_tolerates_form_defaults_and_rejects_bad_values(auth_client):
    ok = await auth_client.get("/api/exports/assets?format=csv&types=&status=&columns=")
    assert ok.status_code == 200
    spaced = await auth_client.get("/api/exports/assets?format=json&types=Vendor,%20system&status=Active")
    assert spaced.status_code == 200
    bad_type = await auth_client.get("/api/exports/assets?format=csv&types=bogus")
    assert bad_type.status_code == 400
    bad_status = await auth_client.get("/api/exports/assets?format=csv&status=nope")
    assert bad_status.status_code == 400


# ── attachment security ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_api_cannot_create_file_kind_attachments(auth_client, pool):
    asset = await asset_svc.create_asset(pool, type=AssetType.SYSTEM, name="Att Sys", status="active")
    resp = await auth_client.post(
        f"/api/assets/{asset.id}/attachments/",
        json={"kind": "file", "name": "passwd", "url_or_path": "/etc/passwd"},
    )
    assert resp.status_code == 400
    ok = await auth_client.post(
        f"/api/assets/{asset.id}/attachments/",
        json={"kind": "url", "name": "Runbook", "url_or_path": "https://example.com/runbook"},
    )
    assert ok.status_code == 201


@pytest.mark.asyncio
async def test_download_refuses_paths_outside_upload_dir(auth_client, pool):
    asset = await asset_svc.create_asset(pool, type=AssetType.SYSTEM, name="Dl Sys", status="active")
    att = await att_svc.create_attachment(
        pool, asset_id=asset.id, kind=AttachmentKind.FILE, name="hostname", url_or_path="/etc/hostname",
    )
    resp = await auth_client.get(f"/api/assets/{asset.id}/attachments/{att.id}/download")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_upload_checks_content_against_declared_type(auth_client, pool):
    asset = await asset_svc.create_asset(pool, type=AssetType.SYSTEM, name="Up Sys", status="active")
    fake_png = await auth_client.post(
        f"/api/assets/{asset.id}/attachments/upload",
        files={"file": ("evil.png", io.BytesIO(b"MZ\x90\x00 not a png"), "image/png")},
    )
    assert fake_png.status_code == 415
    real_png = await auth_client.post(
        f"/api/assets/{asset.id}/attachments/upload",
        files={"file": ("ok.png", io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16), "image/png")},
    )
    assert real_png.status_code == 201
    binary_text = await auth_client.post(
        f"/api/assets/{asset.id}/attachments/upload",
        files={"file": ("notes.txt", io.BytesIO(b"hello\x00world"), "text/plain")},
    )
    assert binary_text.status_code == 415


@pytest.mark.asyncio
async def test_upload_without_content_type_is_refused(auth_client, pool):
    asset = await asset_svc.create_asset(pool, type=AssetType.SYSTEM, name="NoCT Sys", status="active")
    resp = await auth_client.post(
        f"/api/assets/{asset.id}/attachments/upload",
        files={"file": ("blob.bin", io.BytesIO(b"\x00\x01\x02"), "")},
    )
    assert resp.status_code == 415


@pytest.mark.asyncio
async def test_text_uploads_accept_utf16_and_cp1252(auth_client, pool):
    asset = await asset_svc.create_asset(pool, type=AssetType.SYSTEM, name="Txt Sys", status="active")
    utf16 = await auth_client.post(
        f"/api/assets/{asset.id}/attachments/upload",
        files={"file": ("notes.txt", io.BytesIO("héllo".encode("utf-16")), "text/plain")},
    )
    assert utf16.status_code == 201
    cp1252 = await auth_client.post(
        f"/api/assets/{asset.id}/attachments/upload",
        files={"file": ("export.csv", io.BytesIO("a,b\ncafé,1".encode("cp1252")), "text/csv")},
    )
    assert cp1252.status_code == 201


@pytest.mark.asyncio
async def test_download_confined_to_the_owning_assets_folder(auth_client, pool):
    """A file row pointing at another asset's upload is not served."""
    victim = await asset_svc.create_asset(pool, type=AssetType.SYSTEM, name="Victim", status="active")
    up = await auth_client.post(
        f"/api/assets/{victim.id}/attachments/upload",
        files={"file": ("secret.txt", io.BytesIO(b"top secret"), "text/plain")},
    )
    assert up.status_code == 201
    victim_path = (await pool.fetchrow("SELECT url_or_path FROM attachments WHERE id = $1", uuid.UUID(up.json()["id"])))["url_or_path"]
    other = await asset_svc.create_asset(pool, type=AssetType.SYSTEM, name="Other", status="active")
    att = await att_svc.create_attachment(
        pool, asset_id=other.id, kind=AttachmentKind.FILE, name="stolen", url_or_path=victim_path,
    )
    resp = await auth_client.get(f"/api/assets/{other.id}/attachments/{att.id}/download")
    assert resp.status_code == 404
    legit = await auth_client.get(f"/api/assets/{victim.id}/attachments/{up.json()['id']}/download")
    assert legit.status_code == 200
    assert "filename*=UTF-8''secret.txt" in legit.headers["content-disposition"]


@pytest.mark.asyncio
async def test_relationship_evidence_form_rejects_file_kind(auth_client, pool):
    a = await asset_svc.create_asset(pool, type=AssetType.SYSTEM, name="Rel A", status="active")
    b = await asset_svc.create_asset(pool, type=AssetType.SYSTEM, name="Rel B", status="active")
    rel = await auth_client.post("/api/relationships/", json={
        "source_asset_id": str(a.id), "target_asset_id": str(b.id), "relationship_type": "depends_on",
    })
    rel_id = rel.json()["id"]
    resp = await auth_client.post(
        f"/relationships/{rel_id}/evidence",
        data={"kind": "file", "name": "passwd", "url_or_path": "/etc/passwd"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_upload_long_filename_is_truncated_not_500(auth_client, pool):
    asset = await asset_svc.create_asset(pool, type=AssetType.SYSTEM, name="Long Sys", status="active")
    name = "x" * 300 + ".txt"
    resp = await auth_client.post(
        f"/api/assets/{asset.id}/attachments/upload",
        files={"file": (name, io.BytesIO(b"plain text"), "text/plain")},
    )
    assert resp.status_code == 201
    assert len(resp.json()["name"]) <= 255


# ── token escalation ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_oauth_access_token_cannot_mint_api_token(pool):
    from tests.conftest import get_csrf_token

    admin = await create_user(pool, f"oa_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.ADMIN)
    _, client_id, secret = await oauth_service.create_client(
        pool, organization_id=admin.organization_id, name="ci",
        permissions=["view", "manage_tokens"], token_ttl_seconds=600, created_by=admin.id,
    )
    raw, _ = await oauth_service.issue_token(pool, client_id, secret)
    before = await pool.fetchval("SELECT count(*) FROM api_tokens WHERE user_id = $1", admin.id)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        c.headers["Authorization"] = f"Bearer {raw}"
        resp = await c.post("/api/tokens/", json={"name": "forever", "permissions": ["view"]})
        assert resp.status_code == 403
        # The HTML form route is reachable with a bearer too (session cookie +
        # CSRF token can be harvested from a GET); it must refuse the same way.
        csrf = await get_csrf_token(c, "/tokens")
        page = await c.post("/tokens", data={"name": "forever", "permissions": ["view"], "csrf_token": csrf})
        assert page.status_code == 302
        shown = await c.get("/tokens")
        assert "OAuth access tokens cannot create API tokens." in shown.text
    after = await pool.fetchval("SELECT count(*) FROM api_tokens WHERE user_id = $1", admin.id)
    assert after == before


# ── MFA / auth state changes ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mfa_begin_refuses_when_already_enabled(pool, client):
    u = await create_user(pool, f"mfa_{uuid.uuid4().hex[:8]}", "testpass", role=UserRole.VIEWER)
    await login_with_csrf(client, u.username, "testpass")
    secret, _ = await totp_service.begin_enrollment(pool, u.id)
    assert await totp_service.confirm_enrollment(pool, u.id, pyotp.TOTP(secret).now())
    resp = await client.post("/settings/mfa/begin")
    assert resp.status_code == 302
    assert "already%20enabled" in resp.headers["location"]
    assert await totp_service.is_enabled(pool, u.id)


@pytest.mark.asyncio
async def test_mfa_cancel_and_logout_are_post_only(auth_client):
    assert (await auth_client.get("/settings/mfa/cancel")).status_code == 405
    assert (await auth_client.get("/logout")).status_code == 405
    resp = await auth_client.post("/settings/mfa/cancel")
    assert resp.status_code == 302
    out = await auth_client.post("/logout")
    assert out.status_code == 302
    home = await auth_client.get("/")
    # Session gone: the page either bounces to login or answers 401.
    assert home.status_code in (401, 302, 303)


def test_match_totp_step_identifies_the_codes_own_step():
    secret = pyotp.random_base32()
    step_n = 56_666_667
    now = step_n * 30 + 5.0  # five seconds into step N
    code_n = pyotp.TOTP(secret).at(int(now))
    assert totp_service.match_totp_step(secret, code_n, now=now) == step_n
    # Submitted 45s later (step N+1) the code is still inside pyotp's window,
    # but it is reported as step N, so a guard keyed on the stored step
    # rejects the replay instead of treating it as a fresh code.
    assert totp_service.match_totp_step(secret, code_n, now=now + 45) == step_n
    assert totp_service.match_totp_step(secret, code_n, now=now + 60) is None
    assert totp_service.match_totp_step(secret, "000000", now=now) in (None, step_n - 1, step_n, step_n + 1)


@pytest.mark.asyncio
async def test_begin_enrollment_never_resets_an_enabled_account(pool):
    u = await create_user(pool, f"be_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.VIEWER)
    secret, _ = await totp_service.begin_enrollment(pool, u.id)
    assert await totp_service.confirm_enrollment(pool, u.id, pyotp.TOTP(secret).now())
    assert await totp_service.begin_enrollment(pool, u.id) is None
    assert await totp_service.is_enabled(pool, u.id)


@pytest.mark.asyncio
async def test_totp_code_cannot_be_replayed(pool):
    u = await create_user(pool, f"rp_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.VIEWER)
    secret, _ = await totp_service.begin_enrollment(pool, u.id)
    code = pyotp.TOTP(secret).now()
    assert await totp_service.confirm_enrollment(pool, u.id, code)
    assert await totp_service.verify_login_code(pool, u.id, code) is True
    assert await totp_service.verify_login_code(pool, u.id, code) is False


# ── vendor portal capability URL ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_vendor_portal_url_hidden_from_viewers(pool, auth_client, viewer_client):
    org_id = await pool.fetchval("SELECT id FROM organizations ORDER BY created_at LIMIT 1")
    campaign = await vc.create_campaign(pool, organization_id=org_id, name="Acme review")
    await vc.set_status(pool, campaign["id"], "sent", organization_id=org_id)
    admin_view = await auth_client.get(f"/vendor-campaigns/{campaign['id']}")
    assert admin_view.status_code == 200 and "/vendor-portal/" in admin_view.text
    viewer_view = await viewer_client.get(f"/vendor-campaigns/{campaign['id']}")
    assert viewer_view.status_code == 200
    assert "/vendor-portal/" not in viewer_view.text


# ── links minted for humans ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_invite_link_uses_request_host_when_base_url_unset(auth_client):
    resp = await auth_client.post("/admin/invitations", data={"role": "viewer", "ttl_days": "7"})
    assert resp.status_code == 302
    page = await auth_client.get("/admin/invitations")
    assert "http://test/invite/" in page.text
    assert "localhost:8000" not in page.text


def test_public_base_url_prefers_an_explicit_setting(monkeypatch):
    from types import SimpleNamespace

    from grcen.config import settings
    from grcen.routers._pages_shared import public_base_url

    def req(headers):
        return SimpleNamespace(
            base_url="http://request.example/", headers=headers,
            url=SimpleNamespace(scheme="http", netloc="request.example"),
        )

    monkeypatch.setattr(settings, "APP_BASE_URL", "http://localhost:8000")
    assert public_base_url(req({"host": "request.example"})) == "http://request.example"
    # Behind a TLS-terminating proxy the forwarded scheme/host win.
    proxied = req({"host": "app:8000", "x-forwarded-proto": "https", "x-forwarded-host": "grc.example.com"})
    assert public_base_url(proxied) == "https://grc.example.com"
    monkeypatch.setattr(settings, "APP_BASE_URL", "https://configured.example/")
    assert public_base_url(proxied) == "https://configured.example"


# ── alerts can be created from the UI ─────────────────────────────────────


@pytest.mark.asyncio
async def test_alert_create_edit_from_page(auth_client, pool):
    asset = await asset_svc.create_asset(pool, type=AssetType.POLICY, name="Alert Policy", status="active")
    page = await auth_client.get(f"/alerts?asset_id={asset.id}")
    assert page.status_code == 200
    assert "New alert" in page.text and 'value="Alert Policy"' in page.text

    bad = await auth_client.post("/alerts", data={
        "title": "", "asset_id": str(asset.id), "schedule_type": "once", "next_fire_at": "",
    })
    assert bad.status_code == 400
    assert "A title is required." in bad.text
    assert "One-time alerts need a date and time." in bad.text

    good = await auth_client.post("/alerts", data={
        "title": "Review policy", "asset_id": str(asset.id), "schedule_type": "recurring",
        "cron_expression": "0 9 * * 1", "enabled": "1",
    })
    assert good.status_code == 302
    listing = await auth_client.get("/alerts")
    assert "Review policy" in listing.text
    assert "created for Alert Policy" in listing.text  # session flash rendered once
    again = await auth_client.get("/alerts")
    assert "created for Alert Policy" not in again.text

    alert = (await alert_service.list_alerts(pool, asset_id=asset.id))[0]
    assert alert.next_fire_at is not None  # cron schedule seeded with its first occurrence
    edit = await auth_client.get(f"/alerts/{alert.id}/edit")
    assert edit.status_code == 200 and 'value="Review policy"' in edit.text
    saved = await auth_client.post(f"/alerts/{alert.id}/edit", data={
        "title": "Review policy quarterly", "asset_id": str(asset.id), "schedule_type": "recurring",
        "cron_expression": "0 9 1 */3 *", "enabled": "1",
    })
    assert saved.status_code == 302
    updated = await alert_service.get_alert(pool, alert.id)
    assert updated.title == "Review policy quarterly"
    assert updated.cron_expression == "0 9 1 */3 *"
    assert updated.message is None  # blank message stays NULL, not ""

    # A one-time alert that already fired (next_fire_at NULL) stays editable.
    fired = await alert_service.create_alert(
        pool, asset_id=asset.id, title="Fired once", schedule_type="once", next_fire_at=None,
    )
    renamed = await auth_client.post(f"/alerts/{fired.id}/edit", data={
        "title": "Fired once (renamed)", "asset_id": str(asset.id), "schedule_type": "once", "next_fire_at": "",
    })
    assert renamed.status_code == 302
    assert (await alert_service.get_alert(pool, fired.id)).title == "Fired once (renamed)"


@pytest.mark.asyncio
async def test_viewer_cannot_create_alerts(viewer_client):
    resp = await viewer_client.post("/alerts", data={"title": "x", "asset_id": str(uuid.uuid4())})
    assert resp.status_code == 403
