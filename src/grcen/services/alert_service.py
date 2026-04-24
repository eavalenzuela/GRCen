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
) -> Alert:
    row = await pool.fetchrow(
        """
        INSERT INTO alerts
            (id, asset_id, title, message, schedule_type, cron_expression, next_fire_at, enabled)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
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
    )
    return Alert.from_row(row)


async def get_alert(pool: asyncpg.Pool, alert_id: UUID) -> Alert | None:
    row = await pool.fetchrow("SELECT * FROM alerts WHERE id = $1", alert_id)
    return Alert.from_row(row) if row else None


async def list_alerts(pool: asyncpg.Pool, asset_id: UUID | None = None) -> list[Alert]:
    if asset_id:
        rows = await pool.fetch(
            "SELECT * FROM alerts WHERE asset_id = $1 ORDER BY next_fire_at", asset_id
        )
    else:
        rows = await pool.fetch("SELECT * FROM alerts ORDER BY next_fire_at")
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
    await pool.execute(
        """
        INSERT INTO notifications (id, alert_id, title, message)
        VALUES ($1, $2, $3, $4)
        """,
        uuid.uuid4(),
        alert.id,
        alert.title,
        alert.message,
    )
    try:
        await _deliver_email(pool, alert)
    except Exception:
        # Email delivery must never break alert firing or the scheduler.
        log.exception("Email delivery failed for alert %s", alert.id)


async def _deliver_email(pool: asyncpg.Pool, alert: Alert) -> None:
    from grcen.services import email_service
    from grcen.services import smtp_settings as smtp_svc

    smtp = await smtp_svc.get_settings(pool)
    if not smtp.is_enabled:
        return

    recipients = await email_service.resolve_alert_recipients(pool, alert.asset_id)
    if not recipients:
        return

    asset_row = await pool.fetchrow(
        "SELECT name FROM assets WHERE id = $1", alert.asset_id
    )
    asset_name = asset_row["name"] if asset_row else str(alert.asset_id)
    link = f"{settings.APP_BASE_URL.rstrip('/')}/assets/{alert.asset_id}"
    subject = f"[GRCen] {alert.title}"
    body_lines = [alert.title]
    if alert.message:
        body_lines.append("")
        body_lines.append(alert.message)
    body_lines.append("")
    body_lines.append(f"Asset: {asset_name}")
    body_lines.append(f"Link:  {link}")
    body = "\n".join(body_lines)

    for user_id, email in recipients:
        await email_service.send_email(
            pool,
            to=email,
            subject=subject,
            body=body,
            alert_id=alert.id,
            user_id=user_id,
        )


async def list_notifications(
    pool: asyncpg.Pool, unread_only: bool = False
) -> list[Notification]:
    if unread_only:
        rows = await pool.fetch(
            "SELECT * FROM notifications WHERE read = false ORDER BY created_at DESC"
        )
    else:
        rows = await pool.fetch("SELECT * FROM notifications ORDER BY created_at DESC")
    return [Notification.from_row(r) for r in rows]


async def count_unread_notifications(pool: asyncpg.Pool) -> int:
    return await pool.fetchval("SELECT count(*) FROM notifications WHERE read = false")


async def mark_notification_read(pool: asyncpg.Pool, notif_id: UUID) -> bool:
    result = await pool.execute(
        "UPDATE notifications SET read = true, updated_at = now() WHERE id = $1", notif_id
    )
    return result == "UPDATE 1"
