import logging
import uuid
from uuid import UUID

import asyncpg

from grcen.config import settings
from grcen.models.alert import Alert
from grcen.models.notification import Notification

log = logging.getLogger(__name__)


async def create_alert(
    pool: asyncpg.Pool,
    *,
    asset_id: UUID,
    title: str,
    message: str | None = None,
    schedule_type: str,
    cron_expression: str | None = None,
    next_fire_at=None,
    enabled: bool = True,
    organization_id: UUID | None = None,
) -> Alert:
    if organization_id is None:
        # Derive from the owning asset so the alert lands in the same tenant.
        owner = await pool.fetchrow(
            "SELECT organization_id FROM assets WHERE id = $1", asset_id
        )
        if owner is None:
            raise ValueError("Asset not found")
        organization_id = owner["organization_id"]
    row = await pool.fetchrow(
        """
        INSERT INTO alerts
            (id, asset_id, title, message, schedule_type, cron_expression, next_fire_at, enabled, organization_id)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        RETURNING *
        """,
        uuid.uuid4(),
        asset_id,
        title,
        message,
        schedule_type,
        cron_expression,
        next_fire_at,
        enabled,
        organization_id,
    )
    return Alert.from_row(row)


async def get_alert(
    pool: asyncpg.Pool, alert_id: UUID, *, organization_id: UUID | None = None
) -> Alert | None:
    row = await pool.fetchrow(
        """SELECT * FROM alerts WHERE id = $1
           AND ($2::uuid IS NULL OR organization_id = $2)""",
        alert_id, organization_id,
    )
    return Alert.from_row(row) if row else None


async def list_alerts(
    pool: asyncpg.Pool,
    asset_id: UUID | None = None,
    *,
    organization_id: UUID | None = None,
) -> list[Alert]:
    where = []
    vals = []
    idx = 1
    if asset_id:
        where.append(f"asset_id = ${idx}")
        vals.append(asset_id)
        idx += 1
    if organization_id is not None:
        where.append(f"organization_id = ${idx}")
        vals.append(organization_id)
        idx += 1
    sql = "SELECT * FROM alerts"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY next_fire_at"
    rows = await pool.fetch(sql, *vals)
    return [Alert.from_row(r) for r in rows]


async def update_alert(
    pool: asyncpg.Pool,
    alert_id: UUID,
    *,
    title: str | None = None,
    message: str | None = None,
    schedule_type: str | None = None,
    cron_expression: str | None = None,
    next_fire_at=None,
    enabled: bool | None = None,
) -> Alert | None:
    sets: list[str] = []
    vals: list = []
    idx = 1
    for col, val in [
        ("title", title),
        ("message", message),
        ("schedule_type", schedule_type),
        ("cron_expression", cron_expression),
        ("next_fire_at", next_fire_at),
        ("enabled", enabled),
    ]:
        if val is not None:
            sets.append(f"{col} = ${idx}")
            vals.append(val)
            idx += 1
    if not sets:
        return await get_alert(pool, alert_id)
    sets.append("updated_at = now()")
    vals.append(alert_id)
    row = await pool.fetchrow(
        f"UPDATE alerts SET {', '.join(sets)} WHERE id = ${idx} RETURNING *", *vals
    )
    return Alert.from_row(row) if row else None


async def delete_alert(pool: asyncpg.Pool, alert_id: UUID) -> bool:
    result = await pool.execute("DELETE FROM alerts WHERE id = $1", alert_id)
    return result == "DELETE 1"


async def fire_alert(pool: asyncpg.Pool, alert_id: UUID) -> None:
    alert = await get_alert(pool, alert_id)
    if not alert:
        return
    org_row = await pool.fetchrow(
        "SELECT organization_id FROM alerts WHERE id = $1", alert_id
    )
    org_id = org_row["organization_id"]
    await pool.execute(
        """
        INSERT INTO notifications (id, alert_id, title, message, organization_id)
        VALUES ($1, $2, $3, $4, $5)
        """,
        uuid.uuid4(),
        alert.id,
        alert.title,
        alert.message,
        org_id,
    )
    asset_row = await pool.fetchrow(
        "SELECT name FROM assets WHERE id = $1", alert.asset_id
    )
    asset_name = asset_row["name"] if asset_row else str(alert.asset_id)
    link = f"{settings.APP_BASE_URL.rstrip('/')}/assets/{alert.asset_id}"

    try:
        await _deliver_email(pool, alert, asset_name, link)
    except Exception:
        # Delivery must never break alert firing or the scheduler.
        log.exception("Email delivery failed for alert %s", alert.id)

    try:
        await _deliver_webhooks(pool, alert, asset_name, link)
    except Exception:
        log.exception("Webhook delivery failed for alert %s", alert.id)


async def _deliver_email(
    pool: asyncpg.Pool, alert: Alert, asset_name: str, link: str
) -> None:
    from grcen.services import email_service
    from grcen.services import smtp_settings as smtp_svc

    smtp = await smtp_svc.get_settings(pool)
    if not smtp.is_enabled:
        return

    recipients = await email_service.resolve_alert_recipients(pool, alert.asset_id)
    if not recipients:
        return

    # Resolve the alert's owning org so the email gets that tenant's branding.
    from grcen.services import organization_service
    org_row = await pool.fetchrow(
        "SELECT organization_id FROM alerts WHERE id = $1", alert.id
    )
    org = None
    if org_row:
        org = await organization_service.get_by_id(pool, org_row["organization_id"])
    brand_name = (
        org.email_from_name if org and org.email_from_name else "GRCen"
    )
    subject = f"[{brand_name}] {alert.title}"
    text_body, html_body = email_service.render_alert_email(
        alert, asset_name, link, org=org,
    )

    from grcen.services import digest_service
    for user_id, email, mode in recipients:
        if mode == "digest":
            await digest_service.queue_for_digest(
                pool,
                user_id=user_id,
                organization_id=org.id if org else org_row["organization_id"],
                alert_id=alert.id,
                asset_id=alert.asset_id,
                asset_name=asset_name,
                title=alert.title,
                message=alert.message,
                link=link,
            )
            continue
        await email_service.send_email(
            pool,
            to=email,
            subject=subject,
            body=text_body,
            html_body=html_body,
            alert_id=alert.id,
            user_id=user_id,
        )


async def _deliver_webhooks(
    pool: asyncpg.Pool, alert: Alert, asset_name: str, link: str
) -> None:
    from grcen.services import webhook_service

    data = {
        "alert_id": str(alert.id),
        "asset_id": str(alert.asset_id),
        "asset_name": asset_name,
        "title": alert.title,
        "message": alert.message,
        "link": link,
    }
    org_row = await pool.fetchrow(
        "SELECT organization_id FROM alerts WHERE id = $1", alert.id
    )
    org_id = org_row["organization_id"] if org_row else None
    await webhook_service.dispatch(
        pool, "alert.fired", data, alert_id=alert.id, organization_id=org_id
    )


async def list_notifications(
    pool: asyncpg.Pool,
    unread_only: bool = False,
    *,
    organization_id: UUID | None = None,
) -> list[Notification]:
    where = []
    vals = []
    idx = 1
    if unread_only:
        where.append("read = false")
    if organization_id is not None:
        where.append(f"organization_id = ${idx}")
        vals.append(organization_id)
        idx += 1
    sql = "SELECT * FROM notifications"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC"
    rows = await pool.fetch(sql, *vals)
    return [Notification.from_row(r) for r in rows]


async def count_unread_notifications(
    pool: asyncpg.Pool, *, organization_id: UUID | None = None
) -> int:
    return await pool.fetchval(
        """SELECT count(*) FROM notifications WHERE read = false
           AND ($1::uuid IS NULL OR organization_id = $1)""",
        organization_id,
    )


async def mark_notification_read(
    pool: asyncpg.Pool, notif_id: UUID, *, organization_id: UUID | None = None
) -> bool:
    if organization_id is not None:
        result = await pool.execute(
            "UPDATE notifications SET read = true, updated_at = now() WHERE id = $1 AND organization_id = $2",
            notif_id, organization_id,
        )
    else:
        result = await pool.execute(
            "UPDATE notifications SET read = true, updated_at = now() WHERE id = $1", notif_id
        )
    return result == "UPDATE 1"
