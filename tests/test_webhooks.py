"""Tests for webhook CRUD, HMAC signing, dispatch, and alert fan-out."""

import hashlib
import hmac
import json
import uuid
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from grcen.models.asset import AssetType
from grcen.permissions import UserRole
from grcen.services import alert_service, webhook_service
from grcen.services import asset as asset_svc
from grcen.services.auth import create_user

# ── CRUD ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_and_list_webhook(pool):
    hook = await webhook_service.create_webhook(
        pool, name="Slack", url="https://example.com/hook", secret="s3cret"
    )
    assert hook.id is not None
    assert hook.secret == "s3cret"

    hooks = await webhook_service.list_webhooks(pool)
    assert len(hooks) == 1
    assert hooks[0].name == "Slack"
    assert hooks[0].secret == "s3cret"


@pytest.mark.asyncio
async def test_update_and_delete_webhook(pool):
    hook = await webhook_service.create_webhook(
        pool, name="H", url="https://example.com/a"
    )
    updated = await webhook_service.update_webhook(
        pool, hook.id, name="H2", enabled=False
    )
    assert updated is not None
    assert updated.name == "H2"
    assert updated.enabled is False

    assert await webhook_service.delete_webhook(pool, hook.id) is True
    assert await webhook_service.get_webhook(pool, hook.id) is None


@pytest.mark.asyncio
async def test_list_enabled_only(pool):
    await webhook_service.create_webhook(
        pool, name="on", url="https://e.com/a", enabled=True
    )
    await webhook_service.create_webhook(
        pool, name="off", url="https://e.com/b", enabled=False
    )
    all_hooks = await webhook_service.list_webhooks(pool)
    on_only = await webhook_service.list_webhooks(pool, enabled_only=True)
    assert len(all_hooks) == 2
    assert len(on_only) == 1
    assert on_only[0].name == "on"


# ── signing ──────────────────────────────────────────────────────────────


def test_sign_payload_matches_expected_hmac():
    secret = "abc"
    body = b'{"x":1}'
    expected = "sha256=" + hmac.new(
        secret.encode(), body, hashlib.sha256
    ).hexdigest()
    assert webhook_service.sign_payload(secret, body) == expected


# ── delivery ─────────────────────────────────────────────────────────────


def _capture_request():
    """Build a MockTransport that records the first POST and returns 200."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = request.content
        return httpx.Response(200, text="ok")

    return captured, httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_send_to_webhook_signs_and_logs(pool):
    hook = await webhook_service.create_webhook(
        pool, name="h", url="https://hooks.test/x", secret="topsecret"
    )
    captured, transport = _capture_request()

    with patch("grcen.services.webhook_service.httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value = httpx.AsyncClient(
            transport=transport
        )
        mock_client.return_value.__aexit__.return_value = None
        ok, status, err = await webhook_service.send_to_webhook(
            pool, hook, "ping", {"hello": "world"}
        )

    assert ok is True
    assert status == 200
    assert err is None

    # Signature check
    body = captured["body"]
    expected_sig = webhook_service.sign_payload("topsecret", body)
    assert captured["headers"]["x-grcen-signature"] == expected_sig
    assert captured["headers"]["x-grcen-event"] == "ping"
    assert "x-grcen-delivery" in captured["headers"]

    envelope = json.loads(body)
    assert envelope["event"] == "ping"
    assert envelope["data"] == {"hello": "world"}

    row = await pool.fetchrow(
        "SELECT event, status_code, error FROM webhook_deliveries"
    )
    assert row["event"] == "ping"
    assert row["status_code"] == 200
    assert row["error"] is None


@pytest.mark.asyncio
async def test_send_to_webhook_without_secret_omits_signature(pool):
    hook = await webhook_service.create_webhook(
        pool, name="h", url="https://hooks.test/x", secret=""
    )
    captured, transport = _capture_request()

    with patch("grcen.services.webhook_service.httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value = httpx.AsyncClient(
            transport=transport
        )
        mock_client.return_value.__aexit__.return_value = None
        await webhook_service.send_to_webhook(pool, hook, "ping", {})

    assert "x-grcen-signature" not in captured["headers"]


@pytest.mark.asyncio
async def test_send_to_webhook_logs_non_2xx_as_failure(pool):
    hook = await webhook_service.create_webhook(
        pool, name="h", url="https://hooks.test/x"
    )

    def handler(req):
        return httpx.Response(500, text="server boom")

    transport = httpx.MockTransport(handler)
    with patch("grcen.services.webhook_service.httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value = httpx.AsyncClient(
            transport=transport
        )
        mock_client.return_value.__aexit__.return_value = None
        ok, status, err = await webhook_service.send_to_webhook(pool, hook, "ping", {})

    assert ok is False
    assert status == 500
    assert err == "HTTP 500"
    row = await pool.fetchrow(
        "SELECT status_code, error, response_body FROM webhook_deliveries"
    )
    assert row["status_code"] == 500
    assert row["error"] == "HTTP 500"
    assert "server boom" in row["response_body"]


@pytest.mark.asyncio
async def test_send_to_webhook_logs_connection_error(pool):
    hook = await webhook_service.create_webhook(
        pool, name="h", url="https://hooks.test/x"
    )

    def handler(req):
        raise httpx.ConnectError("refused")

    transport = httpx.MockTransport(handler)
    with patch("grcen.services.webhook_service.httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value = httpx.AsyncClient(
            transport=transport
        )
        mock_client.return_value.__aexit__.return_value = None
        ok, status, err = await webhook_service.send_to_webhook(pool, hook, "ping", {})

    assert ok is False
    assert status is None
    assert err and "ConnectError" in err


# ── dispatch + filter ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_skips_disabled_and_filtered(pool):
    await webhook_service.create_webhook(
        pool, name="wants_alerts", url="https://e.com/1",
        event_filter=["alert.fired"],
    )
    await webhook_service.create_webhook(
        pool, name="wants_other", url="https://e.com/2",
        event_filter=["something.else"],
    )
    await webhook_service.create_webhook(
        pool, name="off", url="https://e.com/3", enabled=False,
        event_filter=["alert.fired"],
    )

    with patch(
        "grcen.services.webhook_service.send_to_webhook",
        new=AsyncMock(return_value=(True, 200, None)),
    ) as mock_send:
        count = await webhook_service.dispatch(pool, "alert.fired", {})

    assert count == 1
    assert mock_send.await_count == 1


# ── alert fan-out ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fire_alert_dispatches_webhook(pool):
    admin = await create_user(
        pool, f"admin_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.ADMIN
    )
    asset = await asset_svc.create_asset(
        pool, type=AssetType.SYSTEM, name="AppX", status="active", updated_by=admin.id
    )
    await webhook_service.create_webhook(
        pool, name="h", url="https://e.com/hook"
    )
    alert = await alert_service.create_alert(
        pool, asset_id=asset.id, title="Review", schedule_type="once"
    )

    captured, transport = _capture_request()
    with patch("grcen.services.webhook_service.httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value = httpx.AsyncClient(
            transport=transport
        )
        mock_client.return_value.__aexit__.return_value = None
        await alert_service.fire_alert(pool, alert.id)

    assert captured["url"] == "https://e.com/hook"
    envelope = json.loads(captured["body"])
    assert envelope["event"] == "alert.fired"
    assert envelope["data"]["asset_name"] == "AppX"
    assert envelope["data"]["title"] == "Review"
    assert envelope["data"]["alert_id"] == str(alert.id)

    row = await pool.fetchrow(
        "SELECT event, alert_id, status_code FROM webhook_deliveries"
    )
    assert row["event"] == "alert.fired"
    assert row["alert_id"] == alert.id
    assert row["status_code"] == 200


@pytest.mark.asyncio
async def test_fire_alert_webhook_failure_does_not_crash(pool):
    admin = await create_user(
        pool, f"admin_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.ADMIN
    )
    asset = await asset_svc.create_asset(
        pool, type=AssetType.SYSTEM, name="A", status="active", updated_by=admin.id
    )
    await webhook_service.create_webhook(
        pool, name="h", url="https://e.com/hook"
    )
    alert = await alert_service.create_alert(
        pool, asset_id=asset.id, title="Review", schedule_type="once"
    )

    def handler(req):
        raise httpx.ConnectError("nope")

    transport = httpx.MockTransport(handler)
    with patch("grcen.services.webhook_service.httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value = httpx.AsyncClient(
            transport=transport
        )
        mock_client.return_value.__aexit__.return_value = None
        # Must not raise
        await alert_service.fire_alert(pool, alert.id)

    # In-app notification still recorded, delivery logged as failure
    assert await pool.fetchval("SELECT count(*) FROM notifications") == 1
    row = await pool.fetchrow("SELECT status_code, error FROM webhook_deliveries")
    assert row["status_code"] is None
    assert "ConnectError" in row["error"]


# ── admin UI ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_admin_webhooks_page_requires_manage_users(viewer_client):
    resp = await viewer_client.get("/admin/webhooks")
    assert resp.status_code in (302, 403)


@pytest.mark.asyncio
async def test_admin_webhooks_page_renders(auth_client):
    resp = await auth_client.get("/admin/webhooks")
    assert resp.status_code == 200
    assert "Webhooks" in resp.text


@pytest.mark.asyncio
async def test_admin_create_webhook_flow(auth_client, pool):
    from tests.conftest import _extract_csrf_from_session_cookie

    resp = await auth_client.post(
        "/admin/webhooks",
        data={
            "name": "Test Hook",
            "url": "https://e.com/hook",
            "secret": "abc",
            "event_filter": "alert.fired",
            "enabled": "on",
            "csrf_token": _extract_csrf_from_session_cookie(auth_client),
        },
    )
    assert resp.status_code in (302, 303)
    hooks = await webhook_service.list_webhooks(pool)
    assert len(hooks) == 1
    assert hooks[0].name == "Test Hook"
    assert hooks[0].event_filter == ["alert.fired"]
    assert hooks[0].secret == "abc"
