"""Tests for the external ticketing integration (roadmap E3)."""
import contextlib
import json
import uuid
from unittest.mock import patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from grcen.main import app
from grcen.models.asset import AssetType
from grcen.permissions import UserRole
from grcen.services import asset as asset_svc
from grcen.services import itsm_settings, organization_service, ticketing_service
from grcen.services.auth import create_user
from grcen.services.token_service import create_token


_RealAsyncClient = httpx.AsyncClient


def _patch_httpx(transport: httpx.MockTransport):
    @contextlib.asynccontextmanager
    async def factory(*args, **kwargs):
        async with _RealAsyncClient(transport=transport) as client:
            yield client

    return patch("grcen.services.ticketing_service.httpx.AsyncClient", factory)


async def _org(pool):
    return await organization_service.get_default_org_id(pool)


async def _configure(pool, *, provider="jira", base_url="https://acme.example.com",
                     project_key="SEC", enabled="true"):
    await itsm_settings.update_settings(
        pool, provider=provider, base_url=base_url, project_key=project_key,
        issue_type="Task", auth_user="u@acme.com", auth_token="tok", enabled=enabled)


async def _finding(pool, org, name="SQLi in login", **meta):
    return await asset_svc.create_asset(
        pool, type=AssetType.FINDING, name=name, metadata_=meta or {"severity": "high"},
        organization_id=org)


async def _meta(pool, asset_id):
    raw = await pool.fetchval("SELECT metadata FROM assets WHERE id = $1", asset_id)
    return json.loads(raw) if isinstance(raw, str) else raw


# ── config ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_settings_roundtrip(pool):
    cfg = await itsm_settings.get_settings(pool)
    assert cfg.is_enabled is False  # defaults: disabled
    await _configure(pool, provider="jira")
    cfg = await itsm_settings.get_settings(pool)
    assert cfg.provider == "jira"
    assert cfg.is_enabled is True
    # unknown provider normalizes to generic
    await itsm_settings.update_settings(pool, provider="bogus")
    assert (await itsm_settings.get_settings(pool)).provider == "generic"


# ── outbound push ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_push_creates_jira_ticket_and_stamps_finding(pool):
    org = await _org(pool)
    await _configure(pool, provider="jira")
    finding = await _finding(pool, org, name="SQLi", severity="high", finding_status="open")

    captured = {}

    def handler(req):
        captured["url"] = str(req.url)
        captured["body"] = json.loads(req.content)
        return httpx.Response(201, json={"key": "SEC-42", "id": "1001"})

    with _patch_httpx(httpx.MockTransport(handler)):
        result = await ticketing_service.push_finding(pool, finding.id, organization_id=org)

    assert result["ticket_key"] == "SEC-42"
    assert result["ticket_url"] == "https://acme.example.com/browse/SEC-42"
    assert captured["url"].endswith("/rest/api/2/issue")
    assert captured["body"]["fields"]["summary"] == "SQLi"
    assert captured["body"]["fields"]["project"]["key"] == "SEC"

    meta = await _meta(pool, finding.id)
    assert meta["external_ticket"] == "SEC-42"
    assert meta["external_ticket_url"] == "https://acme.example.com/browse/SEC-42"
    assert meta["external_ticket_status"] == "created"


@pytest.mark.asyncio
async def test_push_servicenow(pool):
    org = await _org(pool)
    await _configure(pool, provider="servicenow", project_key="incident")
    finding = await _finding(pool, org)

    def handler(req):
        return httpx.Response(201, json={"result": {"number": "INC0010042", "sys_id": "abc"}})

    with _patch_httpx(httpx.MockTransport(handler)):
        result = await ticketing_service.push_finding(pool, finding.id, organization_id=org)
    assert result["ticket_key"] == "INC0010042"
    assert "sys_id=abc" in result["ticket_url"]


@pytest.mark.asyncio
async def test_push_generic_uses_bearer(pool):
    org = await _org(pool)
    await _configure(pool, provider="generic", base_url="https://hook.example.com/tickets")
    finding = await _finding(pool, org)

    captured = {}

    def handler(req):
        captured["auth"] = req.headers.get("authorization")
        return httpx.Response(200, json={"id": "T-9", "url": "https://hook.example.com/T-9"})

    with _patch_httpx(httpx.MockTransport(handler)):
        result = await ticketing_service.push_finding(pool, finding.id, organization_id=org)
    assert result["ticket_key"] == "T-9"
    assert captured["auth"] == "Bearer tok"


@pytest.mark.asyncio
async def test_push_not_enabled_raises_409(pool):
    org = await _org(pool)
    finding = await _finding(pool, org)
    with pytest.raises(ticketing_service.TicketingError) as ei:
        await ticketing_service.push_finding(pool, finding.id, organization_id=org)
    assert ei.value.status == 409


@pytest.mark.asyncio
async def test_push_finding_not_found_raises_404(pool):
    org = await _org(pool)
    await _configure(pool)
    with pytest.raises(ticketing_service.TicketingError) as ei:
        await ticketing_service.push_finding(pool, uuid.uuid4(), organization_id=org)
    assert ei.value.status == 404


@pytest.mark.asyncio
async def test_push_already_ticketed_raises(pool):
    org = await _org(pool)
    await _configure(pool)
    finding = await _finding(pool, org, external_ticket="SEC-1")
    with pytest.raises(ticketing_service.TicketingError) as ei:
        await ticketing_service.push_finding(pool, finding.id, organization_id=org)
    assert ei.value.status == 400
    assert "SEC-1" in ei.value.detail


@pytest.mark.asyncio
async def test_push_provider_error_raises_502(pool):
    org = await _org(pool)
    await _configure(pool)
    finding = await _finding(pool, org)

    def handler(req):
        return httpx.Response(400, text="bad request")

    with _patch_httpx(httpx.MockTransport(handler)):
        with pytest.raises(ticketing_service.TicketingError) as ei:
            await ticketing_service.push_finding(pool, finding.id, organization_id=org)
    assert ei.value.status == 502
    # nothing stamped on failure
    assert "external_ticket" not in (await _meta(pool, finding.id))


# ── inbound status sync ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sync_maps_status_to_finding_status(pool):
    org = await _org(pool)
    f1 = await _finding(pool, org, external_ticket="SEC-1", finding_status="open")
    f2 = await _finding(pool, org, external_ticket="SEC-2", finding_status="open")

    result = await ticketing_service.sync_ticket_statuses(
        pool,
        [{"ticket_key": "SEC-1", "status": "Done"},
         {"ticket_key": "SEC-2", "status": "In Progress"},
         {"ticket_key": "SEC-9", "status": "Done"}],
        organization_id=org,
    )
    assert result.updated == 2
    assert result.unmatched == ["SEC-9"]

    m1 = await _meta(pool, f1.id)
    assert m1["finding_status"] == "remediated"
    assert m1["external_ticket_status"] == "Done"
    assert (await _meta(pool, f2.id))["finding_status"] == "in_remediation"


@pytest.mark.asyncio
async def test_sync_unknown_status_keeps_finding_status(pool):
    org = await _org(pool)
    f = await _finding(pool, org, external_ticket="SEC-1", finding_status="open")
    result = await ticketing_service.sync_ticket_statuses(
        pool, [{"ticket_key": "SEC-1", "status": "Groovy"}], organization_id=org)
    assert result.updated == 1
    m = await _meta(pool, f.id)
    assert m["finding_status"] == "open"           # unchanged
    assert m["external_ticket_status"] == "Groovy"  # recorded


@pytest.mark.asyncio
async def test_sync_invalid_input(pool):
    org = await _org(pool)
    result = await ticketing_service.sync_ticket_statuses(pool, "nope", organization_id=org)
    assert result.errors


# ── HTTP routes ────────────────────────────────────────────────────────────


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _sync_token(pool) -> str:
    admin = await create_user(pool, f"itsm_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.ADMIN)
    _, raw = await create_token(pool, admin.id, "itsm", ["sync"], is_service_account=True)
    return raw


@pytest.mark.asyncio
async def test_http_create_ticket(auth_client, pool):
    org = await _org(pool)
    await _configure(pool, provider="jira")
    finding = await _finding(pool, org)

    def handler(req):
        return httpx.Response(201, json={"key": "SEC-7"})

    with _patch_httpx(httpx.MockTransport(handler)):
        resp = await auth_client.post(f"/api/findings/{finding.id}/ticket")
    assert resp.status_code == 200
    assert resp.json()["ticket_key"] == "SEC-7"


@pytest.mark.asyncio
async def test_http_create_ticket_requires_config(auth_client, pool):
    org = await _org(pool)
    finding = await _finding(pool, org)
    resp = await auth_client.post(f"/api/findings/{finding.id}/ticket")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_http_sync_tickets_permission(pool):
    org = await _org(pool)
    await _finding(pool, org, external_ticket="SEC-1", finding_status="open")
    body = {"tickets": [{"ticket_key": "SEC-1", "status": "Done"}]}
    async with _client() as c:
        assert (await c.post("/api/sync/tickets", json=body)).status_code == 401
        raw = await _sync_token(pool)
        resp = await c.post("/api/sync/tickets", json=body,
                            headers={"Authorization": f"Bearer {raw}"})
        assert resp.status_code == 200
        assert resp.json()["updated"] == 1


@pytest.mark.asyncio
async def test_admin_itsm_settings_page_and_save(auth_client, pool):
    from tests.conftest import _extract_csrf_from_session_cookie

    resp = await auth_client.get("/admin/itsm-settings")
    assert resp.status_code == 200
    assert "ITSM" in resp.text

    resp = await auth_client.post("/admin/itsm-settings", data={
        "provider": "jira", "base_url": "https://acme.atlassian.net",
        "project_key": "SEC", "issue_type": "Bug", "auth_user": "u@acme.com",
        "auth_token": "secret-tok", "enabled": "on",
        "csrf_token": _extract_csrf_from_session_cookie(auth_client),
    })
    assert resp.status_code in (302, 303)
    cfg = await itsm_settings.get_settings(pool)
    assert cfg.provider == "jira"
    assert cfg.is_enabled is True
    assert cfg.auth_token == "secret-tok"


@pytest.mark.asyncio
async def test_admin_itsm_settings_requires_manage_users(viewer_client):
    resp = await viewer_client.get("/admin/itsm-settings")
    assert resp.status_code in (302, 403)


@pytest.mark.asyncio
async def test_concurrent_push_creates_one_ticket(pool):
    """The 'already linked' guard is separated from the write by an HTTP call.

    Both callers passed the check, both created a real ticket at the provider,
    and the second write buried the first ticket's ref.
    """
    import asyncio

    org = await _org(pool)
    await _configure(pool)
    finding = await _finding(pool, org)

    created: list[str] = []

    def handler(req):
        created.append(req.url.path)
        return httpx.Response(201, json={"key": f"SEC-{len(created)}"})

    with _patch_httpx(httpx.MockTransport(handler)):
        results = await asyncio.gather(
            ticketing_service.push_finding(pool, finding.id, organization_id=org),
            ticketing_service.push_finding(pool, finding.id, organization_id=org),
            return_exceptions=True,
        )

    ok = [r for r in results if not isinstance(r, Exception)]
    assert len(ok) == 1, f"{len(ok)} pushes succeeded"
    assert len(created) == 1, f"{len(created)} tickets created at the provider"

    meta = await _meta(pool, finding.id)
    assert meta["external_ticket"] == ok[0]["ticket_key"]


@pytest.mark.asyncio
async def test_push_preserves_concurrent_metadata_edits(pool):
    """The stamp must merge, not overwrite the blob read before the HTTP call."""
    org = await _org(pool)
    await _configure(pool)
    finding = await _finding(pool, org)

    def handler(req):
        # Someone edits the finding while the provider request is in flight.
        return httpx.Response(201, json={"key": "SEC-9"})

    await pool.execute(
        """UPDATE assets SET metadata = coalesce(metadata,'{}'::jsonb)
               || '{"finding_status": "in_remediation"}'::jsonb WHERE id = $1""",
        finding.id,
    )

    with _patch_httpx(httpx.MockTransport(handler)):
        await ticketing_service.push_finding(pool, finding.id, organization_id=org)

    meta = await _meta(pool, finding.id)
    assert meta["external_ticket"] == "SEC-9"
    assert meta["finding_status"] == "in_remediation", "concurrent edit was reverted"


@pytest.mark.asyncio
async def test_failed_push_releases_the_claim(pool):
    """A provider error must leave the finding pushable again."""
    org = await _org(pool)
    await _configure(pool)
    finding = await _finding(pool, org)

    with _patch_httpx(httpx.MockTransport(lambda r: httpx.Response(500, text="nope"))):
        with pytest.raises(ticketing_service.TicketingError):
            await ticketing_service.push_finding(pool, finding.id, organization_id=org)

    meta = await _meta(pool, finding.id)
    assert "external_ticket" not in meta
    assert meta.get("external_ticket_status") != "creating"

    with _patch_httpx(httpx.MockTransport(lambda r: httpx.Response(201, json={"key": "SEC-2"}))):
        out = await ticketing_service.push_finding(pool, finding.id, organization_id=org)
    assert out["ticket_key"] == "SEC-2"
