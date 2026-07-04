"""Tests for webhook CRUD, HMAC signing, dispatch, and alert fan-out."""

import contextlib
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


_RealAsyncClient = httpx.AsyncClient


def _patch_httpx_with_transport(transport: httpx.MockTransport):
    """Return a patch() context that makes webhook_service use a MockTransport.

    The service uses ``async with httpx.AsyncClient(timeout=...) as client: ...``
    so we replace AsyncClient with a factory that ignores its args and builds a
    real client (captured before patching) backed by ``transport``.
    """
    @contextlib.asynccontextmanager
    async def factory(*args, **kwargs):
        async with _RealAsyncClient(transport=transport) as client:
            yield client

    return patch("grcen.services.webhook_service.httpx.AsyncClient", factory)


@pytest.fixture(autouse=True)
def _no_retry_backoff():
    """Zero the retry backoff so failure-path tests don't sleep for real."""
    from grcen.config import settings

    old = settings.WEBHOOK_RETRY_BACKOFF_SECONDS
    settings.WEBHOOK_RETRY_BACKOFF_SECONDS = 0.0
    yield
    settings.WEBHOOK_RETRY_BACKOFF_SECONDS = old

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

    with _patch_httpx_with_transport(transport):
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

    with _patch_httpx_with_transport(transport):
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
    with _patch_httpx_with_transport(transport):
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
    with _patch_httpx_with_transport(transport):
        ok, status, err = await webhook_service.send_to_webhook(pool, hook, "ping", {})

    assert ok is False
    assert status is None
    assert err and "ConnectError" in err


# ── retry / backoff ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_send_to_webhook_retries_transient_failures(pool):
    """5xx responses are retried; each attempt gets its own delivery row."""
    hook = await webhook_service.create_webhook(
        pool, name="h", url="https://hooks.test/x"
    )
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(500, text="boom")
        return httpx.Response(200, text="ok")

    transport = httpx.MockTransport(handler)
    with _patch_httpx_with_transport(transport):
        ok, status, err = await webhook_service.send_to_webhook(pool, hook, "ping", {})

    assert ok is True
    assert status == 200
    assert err is None
    assert calls["n"] == 3
    rows = await pool.fetch(
        "SELECT attempt, status_code FROM webhook_deliveries ORDER BY attempt"
    )
    assert [r["attempt"] for r in rows] == [1, 2, 3]
    assert [r["status_code"] for r in rows] == [500, 500, 200]


@pytest.mark.asyncio
async def test_send_to_webhook_gives_up_after_max_attempts(pool):
    hook = await webhook_service.create_webhook(
        pool, name="h", url="https://hooks.test/x"
    )
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(503, text="still down")

    transport = httpx.MockTransport(handler)
    with _patch_httpx_with_transport(transport):
        ok, status, err = await webhook_service.send_to_webhook(pool, hook, "ping", {})

    from grcen.config import settings

    assert ok is False
    assert status == 503
    assert calls["n"] == settings.WEBHOOK_MAX_ATTEMPTS
    count = await pool.fetchval("SELECT count(*) FROM webhook_deliveries")
    assert count == settings.WEBHOOK_MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_send_to_webhook_does_not_retry_4xx(pool):
    """A 4xx is a hard rejection — retrying won't heal it."""
    hook = await webhook_service.create_webhook(
        pool, name="h", url="https://hooks.test/x"
    )
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(404, text="no such hook")

    transport = httpx.MockTransport(handler)
    with _patch_httpx_with_transport(transport):
        ok, status, err = await webhook_service.send_to_webhook(pool, hook, "ping", {})

    assert ok is False
    assert status == 404
    assert calls["n"] == 1
    count = await pool.fetchval("SELECT count(*) FROM webhook_deliveries")
    assert count == 1


# ── asset lifecycle events ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rest_asset_create_and_delete_dispatch_events(auth_client):
    """The single-asset REST routes fire asset.created / asset.deleted."""
    with patch(
        "grcen.services.webhook_service.dispatch", new=AsyncMock(return_value=1)
    ) as mock_dispatch:
        resp = await auth_client.post(
            "/api/assets/",
            json={"type": "system", "name": "Webhooked System"},
        )
        assert resp.status_code == 201
        asset_id = resp.json()["id"]

        resp = await auth_client.delete(f"/api/assets/{asset_id}")
        assert resp.status_code == 204

        # dispatch_background schedules tasks on the running loop; yield so
        # they run before we assert.
        import asyncio as _asyncio
        await _asyncio.sleep(0)

    events = [call.args[1] for call in mock_dispatch.await_args_list]
    assert events == ["asset.created", "asset.deleted"]
    created_payload = mock_dispatch.await_args_list[0].args[2]
    assert created_payload["name"] == "Webhooked System"
    assert created_payload["type"] == "system"
    assert created_payload["id"] == asset_id


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
    with _patch_httpx_with_transport(transport):
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
    with _patch_httpx_with_transport(transport):
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
