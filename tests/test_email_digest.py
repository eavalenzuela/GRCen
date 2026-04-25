"""Email digest queue + hourly flush."""
import uuid

import pytest
import pytest_asyncio

from grcen.services import auth as auth_svc
from grcen.services import digest_service, organization_service
from grcen.permissions import UserRole


@pytest_asyncio.fixture
async def user_in_digest_mode(pool):
    user = await auth_svc.create_user(
        pool, f"d_{uuid.uuid4().hex[:8]}", "x", role=UserRole.ADMIN
    )
    await pool.execute(
        """UPDATE users
              SET email = 'u@example.com',
                  email_notifications_enabled = true,
                  email_notification_mode = 'digest'
            WHERE id = $1""",
        user.id,
    )
    return user


@pytest.mark.asyncio
async def test_queue_for_digest_inserts_pending_row(pool, user_in_digest_mode):
    org_id = await organization_service.get_default_org_id(pool)
    await digest_service.queue_for_digest(
        pool,
        user_id=user_in_digest_mode.id,
        organization_id=org_id,
        alert_id=None,
        asset_id=None,
        asset_name="A",
        title="t",
        message="m",
        link="https://x",
    )
    count = await pool.fetchval(
        "SELECT count(*) FROM pending_email_digest WHERE user_id = $1 AND sent_at IS NULL",
        user_in_digest_mode.id,
    )
    assert count == 1


@pytest.mark.asyncio
async def test_flush_groups_per_user(pool, monkeypatch, user_in_digest_mode):
    """Two queued items for one user produce one outbound envelope."""
    captured = []

    async def fake_send(_pool, **kwargs):
        captured.append(kwargs)
        return True, None

    monkeypatch.setattr(
        "grcen.services.email_service.send_email", fake_send
    )

    org_id = await organization_service.get_default_org_id(pool)
    for i in range(3):
        await digest_service.queue_for_digest(
            pool,
            user_id=user_in_digest_mode.id,
            organization_id=org_id,
            alert_id=None,
            asset_id=None,
            asset_name=f"asset-{i}",
            title=f"item-{i}",
            message=None,
            link=None,
        )

    sent = await digest_service.flush_digests(pool)
    assert sent == 1
    assert len(captured) == 1
    body = captured[0]["body"]
    for i in range(3):
        assert f"item-{i}" in body
    # Subject reflects the count.
    assert "3 pending notification" in captured[0]["subject"]

    # Pending rows are now marked sent.
    remaining = await pool.fetchval(
        "SELECT count(*) FROM pending_email_digest WHERE user_id = $1 AND sent_at IS NULL",
        user_in_digest_mode.id,
    )
    assert remaining == 0


@pytest.mark.asyncio
async def test_flush_skips_users_who_disabled_email(pool, monkeypatch, user_in_digest_mode):
    """If a user toggles notifications off after queueing, the pending rows are
    discarded silently — never emailed."""
    captured = []

    async def fake_send(_pool, **kwargs):
        captured.append(kwargs)
        return True, None

    monkeypatch.setattr("grcen.services.email_service.send_email", fake_send)

    org_id = await organization_service.get_default_org_id(pool)
    await digest_service.queue_for_digest(
        pool,
        user_id=user_in_digest_mode.id,
        organization_id=org_id,
        alert_id=None,
        asset_id=None,
        asset_name=None,
        title="t",
        message=None,
        link=None,
    )
    # Opt out before the flush fires.
    await pool.execute(
        "UPDATE users SET email_notifications_enabled = false WHERE id = $1",
        user_in_digest_mode.id,
    )
    sent = await digest_service.flush_digests(pool)
    assert sent == 0
    assert captured == []
    remaining = await pool.fetchval(
        "SELECT count(*) FROM pending_email_digest WHERE sent_at IS NULL"
    )
    assert remaining == 0


@pytest.mark.asyncio
async def test_settings_form_persists_mode(auth_client, pool):
    """The /settings form writes email_notification_mode."""
    user_row = await pool.fetchrow(
        "SELECT id FROM users WHERE email_notifications_enabled = true LIMIT 1"
    )
    # Make sure the current admin has an email + notifications on so the
    # form accepts the change.
    await pool.execute(
        "UPDATE users SET email = 'admin@x.com', email_notifications_enabled = true"
    )
    resp = await auth_client.post(
        "/settings",
        data={
            "email_notifications_enabled": "on",
            "email_notification_mode": "digest",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    mode = await pool.fetchval(
        "SELECT email_notification_mode FROM users LIMIT 1"
    )
    assert mode == "digest"
