"""Outbound email delivery.

Reads SMTP settings from the database, sends via aiosmtplib, and records every
attempt in ``notification_deliveries``. Failures are logged and returned as a
status — callers (e.g. alert firing) should never crash because email was down.
"""

import logging
import uuid
from email.message import EmailMessage
from uuid import UUID

import aiosmtplib
import asyncpg

from grcen.services import encryption_config
from grcen.services import smtp_settings as smtp_svc
from grcen.services.encryption import decrypt_field

log = logging.getLogger(__name__)


async def send_email(
    pool: asyncpg.Pool,
    *,
    to: str,
    subject: str,
    body: str,
    alert_id: UUID | None = None,
    user_id: UUID | None = None,
) -> tuple[bool, str | None]:
    """Send a plain-text email. Returns (ok, error_message)."""
    settings = await smtp_svc.get_settings(pool)
    if not settings.is_enabled:
        await _log_delivery(
            pool, alert_id, user_id, to, "skipped", "SMTP not configured"
        )
        return False, "smtp_not_configured"

    msg = EmailMessage()
    msg["From"] = (
        f"{settings.from_name} <{settings.from_address}>"
        if settings.from_name
        else settings.from_address
    )
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    try:
        await aiosmtplib.send(
            msg,
            hostname=settings.host,
            port=settings.port_int,
            username=settings.username or None,
            password=settings.password or None,
            start_tls=settings.starttls and not settings.ssl,
            use_tls=settings.ssl,
            timeout=15,
        )
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
        log.warning("SMTP send to %s failed: %s", to, err)
        await _log_delivery(pool, alert_id, user_id, to, "failed", err)
        return False, err

    await _log_delivery(pool, alert_id, user_id, to, "sent", None)
    return True, None


async def _log_delivery(
    pool: asyncpg.Pool,
    alert_id: UUID | None,
    user_id: UUID | None,
    email: str,
    status: str,
    error: str | None,
) -> None:
    await pool.execute(
        """INSERT INTO notification_deliveries
               (id, alert_id, user_id, email, status, error)
           VALUES ($1, $2, $3, $4, $5, $6)""",
        uuid.uuid4(),
        alert_id,
        user_id,
        email,
        status,
        error,
    )


async def resolve_alert_recipients(
    pool: asyncpg.Pool, asset_id: UUID
) -> list[tuple[UUID, str]]:
    """Return [(user_id, email)] who should receive email for an alert on this asset.

    Rules: the asset owner if owner_id links to a Person asset whose user has
    email_notifications_enabled=true. Otherwise all admins with opt-in on.
    Users without an email are skipped.
    """
    pii_encrypted = await encryption_config.is_scope_active(pool, "user_pii")

    def _decrypt(rows):
        out: list[tuple[UUID, str]] = []
        for r in rows:
            email = r["email"]
            if pii_encrypted and email:
                email = decrypt_field(email, "user_pii")
            if email:
                out.append((r["id"], email))
        return out

    owner_rows = await pool.fetch(
        """SELECT u.id, u.email
           FROM assets a
           JOIN users u ON u.person_asset_id = a.owner_id
           WHERE a.id = $1
             AND u.is_active = true
             AND u.email_notifications_enabled = true
             AND u.email IS NOT NULL AND u.email <> ''""",
        asset_id,
    )
    recipients = _decrypt(owner_rows)
    if recipients:
        return recipients

    admin_rows = await pool.fetch(
        """SELECT id, email FROM users
           WHERE role = 'admin'
             AND is_active = true
             AND email_notifications_enabled = true
             AND email IS NOT NULL AND email <> ''"""
    )
    return _decrypt(admin_rows)
