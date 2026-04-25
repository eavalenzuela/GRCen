"""Email digest queue.

Users with ``email_notification_mode = 'digest'`` get alert emails accumulated
into ``pending_email_digest`` instead of one envelope per event. An hourly
scheduled job (see ``main._send_email_digests``) flushes the queue: one
envelope per user with all their pending entries, then marks them sent.

This trades latency for fewer interruptions. We keep the per-event row so the
UI / audit can still link back to the originating alert.
"""
from __future__ import annotations

import logging
import uuid
from typing import Iterable
from uuid import UUID

import asyncpg
from jinja2 import Environment, FileSystemLoader, select_autoescape

from grcen.config import settings as app_settings
from grcen.services import email_service, organization_service

log = logging.getLogger(__name__)

_env = Environment(
    loader=FileSystemLoader("src/grcen/templates"),
    autoescape=select_autoescape(["html"]),
)


async def queue_for_digest(
    pool: asyncpg.Pool,
    *,
    user_id: UUID,
    organization_id: UUID,
    alert_id: UUID | None,
    asset_id: UUID | None,
    asset_name: str | None,
    title: str,
    message: str | None,
    link: str | None,
) -> None:
    """Append one row to the user's pending digest queue."""
    await pool.execute(
        """INSERT INTO pending_email_digest
               (id, user_id, organization_id, alert_id, asset_id,
                asset_name, title, message, link)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)""",
        uuid.uuid4(),
        user_id,
        organization_id,
        alert_id,
        asset_id,
        (asset_name or "")[:255],
        title[:255],
        message,
        link,
    )


def _render(items: Iterable[dict], org=None) -> tuple[str, str]:
    """Render the digest envelope as (text, html)."""
    base_url = app_settings.APP_BASE_URL.rstrip("/")
    items = list(items)
    brand_name = (
        org.email_from_name if org and org.email_from_name else app_settings.APP_NAME
    )
    ctx = {
        "items": items,
        "count": len(items),
        "app_name": brand_name,
        "subject": f"[{brand_name}] {len(items)} pending notification(s)",
        "unsubscribe_url": f"{base_url}/settings",
        "brand_color": (
            org.email_brand_color if org and org.email_brand_color else "#1f2937"
        ),
        "logo_url": (org.email_logo_url if org and org.email_logo_url else ""),
    }
    text = _env.get_template("emails/digest.txt").render(**ctx)
    html = _env.get_template("emails/digest.html").render(**ctx)
    return text, html


async def flush_digests(pool: asyncpg.Pool) -> int:
    """Send one envelope per user with all their pending rows.

    Returns the count of users emailed. Each user's pending rows share a single
    org (a user belongs to one org by default; additional memberships are
    flushed in separate envelopes since branding differs per tenant).
    """
    rows = await pool.fetch(
        """SELECT id, user_id, organization_id, alert_id, asset_id,
                  asset_name, title, message, link
           FROM pending_email_digest
           WHERE sent_at IS NULL
           ORDER BY user_id, organization_id, queued_at"""
    )
    if not rows:
        return 0

    # Group by (user_id, organization_id) so each envelope reflects one org's
    # branding and goes to that user's email.
    grouped: dict[tuple[UUID, UUID], list[dict]] = {}
    for r in rows:
        grouped.setdefault((r["user_id"], r["organization_id"]), []).append(dict(r))

    sent = 0
    for (user_id, org_id), items in grouped.items():
        user_row = await pool.fetchrow(
            "SELECT email, email_notifications_enabled FROM users WHERE id = $1",
            user_id,
        )
        if not user_row or not user_row["email_notifications_enabled"]:
            # User opted out between queue and flush — discard quietly.
            await pool.execute(
                "UPDATE pending_email_digest SET sent_at = now() WHERE id = ANY($1::uuid[])",
                [i["id"] for i in items],
            )
            continue
        email = user_row["email"]
        if not email:
            await pool.execute(
                "UPDATE pending_email_digest SET sent_at = now() WHERE id = ANY($1::uuid[])",
                [i["id"] for i in items],
            )
            continue
        # PII may be encrypted at rest.
        from grcen.services import encryption_config
        from grcen.services.encryption import decrypt_field
        if await encryption_config.is_scope_active(pool, "user_pii") and email:
            email = decrypt_field(email, "user_pii")

        org = await organization_service.get_by_id(pool, org_id)
        text, html = _render(items, org=org)
        ok, _err = await email_service.send_email(
            pool,
            to=email,
            subject=(
                f"[{org.email_from_name or app_settings.APP_NAME}] "
                f"{len(items)} pending notification(s)"
            ),
            body=text,
            html_body=html,
            user_id=user_id,
        )
        if ok:
            await pool.execute(
                "UPDATE pending_email_digest SET sent_at = now() WHERE id = ANY($1::uuid[])",
                [i["id"] for i in items],
            )
            sent += 1
    return sent
