"""Outbound email delivery.

Reads SMTP settings from the database, sends via aiosmtplib, and records every
attempt in ``notification_deliveries``. Failures are logged and returned as a
status — callers (e.g. alert firing) should never crash because email was down.

Messages are sent as ``multipart/alternative`` with both a plain-text body
(for terminal mail clients) and an HTML body (for everyone else). The
:func:`render_alert_email` helper wraps the alert templates and the
unsubscribe-link footer.
"""

import logging
import uuid
from email.message import EmailMessage
from uuid import UUID

import aiosmtplib
import asyncpg
from jinja2 import Environment, FileSystemLoader, select_autoescape

from grcen.config import settings as app_settings
from grcen.services import encryption_config
from grcen.services import smtp_settings as smtp_svc
from grcen.services.encryption import decrypt_field

log = logging.getLogger(__name__)

_email_env = Environment(
    loader=FileSystemLoader("src/grcen/templates"),
    autoescape=select_autoescape(["html"]),
)


def render_alert_email(alert, asset_name: str, link: str) -> tuple[str, str]:
    """Render the (text, html) bodies for an alert notification."""
    base_url = app_settings.APP_BASE_URL.rstrip("/")
    ctx = {
        "alert": alert,
        "asset_name": asset_name,
        "link": link,
        "app_name": app_settings.APP_NAME,
        "subject": f"[{app_settings.APP_NAME}] {alert.title}",
        "unsubscribe_url": f"{base_url}/settings",
    }
    text = _email_env.get_template("emails/alert.txt").render(**ctx)
    html = _email_env.get_template("emails/alert.html").render(**ctx)
    return text, html


async def send_email(
    pool: asyncpg.Pool,
    *,
    to: str,
    subject: str,
    body: str,
    html_body: str | None = None,
    alert_id: UUID | None = None,
    user_id: UUID | None = None,
) -> tuple[bool, str | None]:
    """Send an email (plain-text by default, multipart when ``html_body`` is set)."""
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
    if html_body:
        msg.add_alternative(html_body, subtype="html")
        # Use a stable List-Unsubscribe header so MUAs surface a one-click
        # unsubscribe action. The URL points at /settings where the user can
        # toggle email_notifications_enabled.
        unsub = f"{app_settings.APP_BASE_URL.rstrip('/')}/settings"
        msg["List-Unsubscribe"] = f"<{unsub}>"
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"

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
    org_id = None
    if alert_id is not None:
        row = await pool.fetchrow(
            "SELECT organization_id FROM alerts WHERE id = $1", alert_id
        )
        if row:
            org_id = row["organization_id"]
    if org_id is None and user_id is not None:
        row = await pool.fetchrow(
            "SELECT organization_id FROM users WHERE id = $1", user_id
        )
        if row:
            org_id = row["organization_id"]
    if org_id is None:
        from grcen.services import organization_service
        org_id = await organization_service.get_default_org_id(pool)
    await pool.execute(
        """INSERT INTO notification_deliveries
               (id, alert_id, user_id, email, status, error, organization_id)
           VALUES ($1, $2, $3, $4, $5, $6, $7)""",
        uuid.uuid4(),
        alert_id,
        user_id,
        email,
        status,
        error,
        org_id,
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
