"""Tests for SMTP settings, email delivery, and alert-firing email fan-out."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from grcen.models.asset import AssetType
from grcen.permissions import UserRole
from grcen.services import alert_service, email_service, smtp_settings
from grcen.services import asset as asset_svc
from grcen.services.auth import create_user, set_email_notifications_enabled


async def _enable_smtp(pool):
    await smtp_settings.update_settings(
        pool,
        host="smtp.test",
        port="587",
        username="u",
        password="p",
        from_address="grcen@test",
        from_name="GRCen",
        use_starttls="true",
        use_ssl="false",
        enabled="true",
    )
    return await smtp_settings.reload(pool)


# ── settings service ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_smtp_settings_defaults_disabled(pool):
    cfg = await smtp_settings.reload(pool)
    assert cfg.is_enabled is False
    assert cfg.port_int == 587


@pytest.mark.asyncio
async def test_smtp_settings_update_and_reload(pool):
    cfg = await _enable_smtp(pool)
    assert cfg.is_enabled is True
    assert cfg.host == "smtp.test"
    assert cfg.from_address == "grcen@test"
    assert cfg.starttls is True
    assert cfg.ssl is False


# ── email_service.send_email ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_send_email_skipped_when_smtp_disabled(pool):
    ok, err = await email_service.send_email(
        pool, to="x@test", subject="s", body="b"
    )
    assert ok is False
    assert err == "smtp_not_configured"
    rows = await pool.fetch("SELECT status FROM notification_deliveries")
    assert [r["status"] for r in rows] == ["skipped"]


@pytest.mark.asyncio
async def test_send_email_success_logs_sent(pool):
    await _enable_smtp(pool)
    with patch("grcen.services.email_service.aiosmtplib.send", new=AsyncMock(return_value=None)):
        ok, err = await email_service.send_email(
            pool, to="x@test", subject="s", body="b"
        )
    assert ok is True
    assert err is None
    rows = await pool.fetch("SELECT status, email FROM notification_deliveries")
    assert len(rows) == 1
    assert rows[0]["status"] == "sent"
    assert rows[0]["email"] == "x@test"


@pytest.mark.asyncio
async def test_send_email_failure_logs_failed(pool):
    await _enable_smtp(pool)
    with patch(
        "grcen.services.email_service.aiosmtplib.send",
        new=AsyncMock(side_effect=ConnectionRefusedError("boom")),
    ):
        ok, err = await email_service.send_email(
            pool, to="x@test", subject="s", body="b"
        )
    assert ok is False
    assert err and "ConnectionRefusedError" in err
    row = await pool.fetchrow("SELECT status, error FROM notification_deliveries")
    assert row["status"] == "failed"
    assert "ConnectionRefusedError" in row["error"]


# ── recipient resolution ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_recipients_prefers_asset_owner(pool):
    owner = await create_user(
        pool, f"owner_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.EDITOR
    )
    await pool.execute(
        "UPDATE users SET email = $1, email_notifications_enabled = true WHERE id = $2",
        "owner@test",
        owner.id,
    )
    person = await asset_svc.create_asset(
        pool, type=AssetType.PERSON, name="Owner", status="active", updated_by=owner.id
    )
    await pool.execute(
        "UPDATE users SET person_asset_id = $1 WHERE id = $2", person.id, owner.id
    )
    system = await asset_svc.create_asset(
        pool,
        type=AssetType.SYSTEM,
        name="App",
        status="active",
        owner_id=person.id,
        updated_by=owner.id,
    )

    recipients = await email_service.resolve_alert_recipients(
        pool, system.id, organization_id=owner.organization_id
    )
    assert recipients == [(owner.id, "owner@test", "immediate")]


@pytest.mark.asyncio
async def test_resolve_recipients_falls_back_to_admins(pool):
    admin = await create_user(
        pool, f"admin_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.ADMIN
    )
    await pool.execute(
        "UPDATE users SET email = $1, email_notifications_enabled = true WHERE id = $2",
        "admin@test",
        admin.id,
    )
    system = await asset_svc.create_asset(
        pool, type=AssetType.SYSTEM, name="Orphan", status="active", updated_by=admin.id
    )

    recipients = await email_service.resolve_alert_recipients(
        pool, system.id, organization_id=admin.organization_id
    )
    assert recipients == [(admin.id, "admin@test", "immediate")]


@pytest.mark.asyncio
async def test_resolve_recipients_respects_opt_out(pool):
    admin = await create_user(
        pool, f"admin_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.ADMIN
    )
    # Admin has email but has NOT opted in.
    await pool.execute(
        "UPDATE users SET email = $1, email_notifications_enabled = false WHERE id = $2",
        "admin@test",
        admin.id,
    )
    system = await asset_svc.create_asset(
        pool, type=AssetType.SYSTEM, name="A", status="active", updated_by=admin.id
    )

    recipients = await email_service.resolve_alert_recipients(
        pool, system.id, organization_id=admin.organization_id
    )
    assert recipients == []


# ── fire_alert integration ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fire_alert_no_email_when_smtp_disabled(pool):
    admin = await create_user(
        pool, f"admin_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.ADMIN
    )
    await pool.execute(
        "UPDATE users SET email = $1, email_notifications_enabled = true WHERE id = $2",
        "admin@test",
        admin.id,
    )
    asset = await asset_svc.create_asset(
        pool, type=AssetType.SYSTEM, name="A", status="active", updated_by=admin.id
    )
    alert = await alert_service.create_alert(
        pool, asset_id=asset.id, title="Review", schedule_type="once"
    )

    await alert_service.fire_alert(pool, alert.id)

    notif_count = await pool.fetchval("SELECT count(*) FROM notifications")
    delivery_count = await pool.fetchval("SELECT count(*) FROM notification_deliveries")
    assert notif_count == 1  # in-app still works
    assert delivery_count == 0  # no email attempt since SMTP disabled


@pytest.mark.asyncio
async def test_fire_alert_sends_email_when_configured(pool):
    admin = await create_user(
        pool, f"admin_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.ADMIN
    )
    await pool.execute(
        "UPDATE users SET email = $1, email_notifications_enabled = true WHERE id = $2",
        "admin@test",
        admin.id,
    )
    await _enable_smtp(pool)
    asset = await asset_svc.create_asset(
        pool, type=AssetType.SYSTEM, name="A", status="active", updated_by=admin.id
    )
    alert = await alert_service.create_alert(
        pool, asset_id=asset.id, title="Review", schedule_type="once"
    )

    with patch(
        "grcen.services.email_service.aiosmtplib.send", new=AsyncMock(return_value=None)
    ) as mock_send:
        await alert_service.fire_alert(pool, alert.id)

    assert mock_send.await_count == 1
    row = await pool.fetchrow(
        "SELECT status, email, alert_id FROM notification_deliveries"
    )
    assert row["status"] == "sent"
    assert row["email"] == "admin@test"
    assert row["alert_id"] == alert.id


@pytest.mark.asyncio
async def test_fire_alert_email_failure_does_not_crash(pool):
    admin = await create_user(
        pool, f"admin_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.ADMIN
    )
    await pool.execute(
        "UPDATE users SET email = $1, email_notifications_enabled = true WHERE id = $2",
        "admin@test",
        admin.id,
    )
    await _enable_smtp(pool)
    asset = await asset_svc.create_asset(
        pool, type=AssetType.SYSTEM, name="A", status="active", updated_by=admin.id
    )
    alert = await alert_service.create_alert(
        pool, asset_id=asset.id, title="Review", schedule_type="once"
    )

    with patch(
        "grcen.services.email_service.aiosmtplib.send",
        new=AsyncMock(side_effect=RuntimeError("nope")),
    ):
        # Must not raise
        await alert_service.fire_alert(pool, alert.id)

    # In-app notification still recorded
    assert await pool.fetchval("SELECT count(*) FROM notifications") == 1
    # Failure row logged
    row = await pool.fetchrow("SELECT status FROM notification_deliveries")
    assert row["status"] == "failed"


# ── admin / self-service pages ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_admin_smtp_settings_requires_manage_users(viewer_client):
    resp = await viewer_client.get("/admin/smtp-settings")
    assert resp.status_code in (302, 403)


@pytest.mark.asyncio
async def test_admin_smtp_settings_page_renders(auth_client):
    resp = await auth_client.get("/admin/smtp-settings")
    assert resp.status_code == 200
    assert "SMTP" in resp.text


@pytest.mark.asyncio
async def test_user_settings_opt_in_requires_email(pool, client):
    user = await create_user(
        pool, f"u_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.VIEWER
    )
    # No email on file.
    from tests.conftest import _extract_csrf_from_session_cookie, login_with_csrf

    await login_with_csrf(client, user.username, "pw")
    resp = await client.post(
        "/settings",
        data={
            "email_notifications_enabled": "on",
            "csrf_token": _extract_csrf_from_session_cookie(client),
        },
    )
    assert resp.status_code in (302, 303)

    fresh = await pool.fetchrow(
        "SELECT email_notifications_enabled FROM users WHERE id = $1", user.id
    )
    assert fresh["email_notifications_enabled"] is False  # suppressed: no email


@pytest.mark.asyncio
async def test_user_settings_opt_in_saves_when_email_present(pool, client):
    user = await create_user(
        pool, f"u_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.VIEWER
    )
    await pool.execute("UPDATE users SET email = $1 WHERE id = $2", "u@test", user.id)
    from tests.conftest import _extract_csrf_from_session_cookie, login_with_csrf

    await login_with_csrf(client, user.username, "pw")
    resp = await client.post(
        "/settings",
        data={
            "email_notifications_enabled": "on",
            "csrf_token": _extract_csrf_from_session_cookie(client),
        },
    )
    assert resp.status_code in (302, 303)

    fresh = await pool.fetchrow(
        "SELECT email_notifications_enabled FROM users WHERE id = $1", user.id
    )
    assert fresh["email_notifications_enabled"] is True


@pytest.mark.asyncio
async def test_set_email_notifications_enabled_service(pool):
    user = await create_user(
        pool, f"u_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.VIEWER
    )
    updated = await set_email_notifications_enabled(pool, user.id, True)
    assert updated is not None
    assert updated.email_notifications_enabled is True


@pytest.mark.asyncio
async def test_admin_fallback_does_not_cross_organizations(pool):
    """An alert must never be mailed to another tenant's admins.

    The fallback selected every admin with opt-in on, org-wide-blind. `users`
    is outside RLS and alerts fire from the scheduler inside system_context(),
    so neither isolation layer covered this.
    """
    from grcen.services import organization_service

    org_a = await organization_service.create_organization(
        pool, slug=f"a_{uuid.uuid4().hex[:6]}", name="Org A"
    )
    org_b = await organization_service.create_organization(
        pool, slug=f"b_{uuid.uuid4().hex[:6]}", name="Org B"
    )
    admin_a = await create_user(
        pool, f"aa_{uuid.uuid4().hex[:8]}", "pw",
        role=UserRole.ADMIN, organization_id=org_a.id,
    )
    admin_b = await create_user(
        pool, f"bb_{uuid.uuid4().hex[:8]}", "pw",
        role=UserRole.ADMIN, organization_id=org_b.id,
    )
    for u, email in ((admin_a, "a@test"), (admin_b, "b@test")):
        await pool.execute(
            "UPDATE users SET email = $1, email_notifications_enabled = true "
            "WHERE id = $2",
            email, u.id,
        )

    # An org-A asset with no owner — the admin fallback path.
    system = await asset_svc.create_asset(
        pool, organization_id=org_a.id, type=AssetType.SYSTEM,
        name="A app", status="active", updated_by=admin_a.id,
    )

    recipients = await email_service.resolve_alert_recipients(
        pool, system.id, organization_id=org_a.id
    )

    emails = {e for _uid, e, _mode in recipients}
    assert "a@test" in emails
    assert "b@test" not in emails, "alert leaked to another tenant's admins"


# ── the ticker must claim its work before firing ─────────────────────────


async def _due_alert(pool, admin, *, schedule_type="once", cron=None):
    from grcen.services import alert_service
    from datetime import UTC, datetime, timedelta

    asset = await asset_svc.create_asset(
        pool, type=AssetType.SYSTEM, name="Ticker asset", status="active",
        updated_by=admin.id,
    )
    alert = await alert_service.create_alert(
        pool, asset_id=asset.id, title="Due now", schedule_type=schedule_type,
        cron_expression=cron,
    )
    await pool.execute(
        "UPDATE alerts SET next_fire_at = $1 WHERE id = $2",
        datetime.now(UTC) - timedelta(minutes=1), alert.id,
    )
    return alert


@pytest.mark.asyncio
async def test_concurrent_tickers_fire_each_alert_once(pool):
    """The scheduler runs in every gunicorn worker.

    Selecting due alerts and firing them before claiming meant each worker
    delivered the same alert — duplicate notifications, emails and webhook
    POSTs, none of which can be taken back.
    """
    import asyncio

    from grcen.main import _tick_alerts

    admin = await create_user(
        pool, f"admin_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.ADMIN
    )
    alert = await _due_alert(pool, admin)

    # Two workers' tickers firing at the same instant.
    await asyncio.gather(_tick_alerts(), _tick_alerts())

    fired = await pool.fetchval(
        "SELECT count(*) FROM notifications WHERE alert_id = $1", alert.id
    )
    assert fired == 1, f"alert fired {fired} times"


@pytest.mark.asyncio
async def test_one_shot_alert_does_not_refire(pool):
    """`once` alerts never had next_fire_at advanced, so they re-fired forever."""
    from grcen.main import _tick_alerts

    admin = await create_user(
        pool, f"admin_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.ADMIN
    )
    alert = await _due_alert(pool, admin, schedule_type="once")

    await _tick_alerts()
    await _tick_alerts()
    await _tick_alerts()

    fired = await pool.fetchval(
        "SELECT count(*) FROM notifications WHERE alert_id = $1", alert.id
    )
    assert fired == 1, f"one-shot alert fired {fired} times"
    assert await pool.fetchval(
        "SELECT next_fire_at FROM alerts WHERE id = $1", alert.id
    ) is None


@pytest.mark.asyncio
async def test_recurring_alert_is_rescheduled_not_retired(pool):
    from grcen.main import _tick_alerts

    admin = await create_user(
        pool, f"admin_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.ADMIN
    )
    alert = await _due_alert(pool, admin, schedule_type="recurring", cron="0 9 * * *")

    await _tick_alerts()

    next_at = await pool.fetchval(
        "SELECT next_fire_at FROM alerts WHERE id = $1", alert.id
    )
    assert next_at is not None, "recurring alert was retired instead of rescheduled"
    fired = await pool.fetchval(
        "SELECT count(*) FROM notifications WHERE alert_id = $1", alert.id
    )
    assert fired == 1
