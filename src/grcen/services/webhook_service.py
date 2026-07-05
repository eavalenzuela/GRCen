"""Outbound webhook management and delivery.

Each webhook has a URL and an optional shared secret.  Delivery POSTs a JSON
envelope to the URL; if the secret is non-empty, the body is HMAC-SHA256 signed
and the signature is sent in ``X-GRCen-Signature: sha256=<hex>``.  Every attempt
is recorded in ``webhook_deliveries`` — failures never propagate.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import asyncpg
import httpx

from grcen.services import encryption_config
from grcen.services.encryption import decrypt_field, encrypt_field

log = logging.getLogger(__name__)

_SCOPE = "webhook_secrets"
_TIMEOUT_SECONDS = 10.0
_RESPONSE_BODY_LIMIT = 2000  # chars stored per delivery row

# Canonical catalogue of outbound event types → human labels. Single source of
# truth for the admin UI's "supported events" list and the chat formatter; emit
# sites use the string keys directly.
WEBHOOK_EVENTS: dict[str, str] = {
    "alert.fired": "Alert fired",
    "asset.created": "Asset created",
    "asset.updated": "Asset updated",
    "asset.deleted": "Asset deleted",
    "ping": "Test ping",
}

# Per-webhook delivery formats. 'grcen' = signed JSON envelope (machine/SIEM);
# 'slack'/'teams' = chat-ready message posted to an incoming-webhook URL.
FORMATS = ("grcen", "slack", "teams")


def normalize_format(value: str | None) -> str:
    return value if value in FORMATS else "grcen"


@dataclass
class Webhook:
    id: UUID
    name: str
    url: str
    secret: str
    enabled: bool
    event_filter: list[str]
    created_at: datetime
    updated_at: datetime
    format: str = "grcen"

    @classmethod
    def from_row(cls, row) -> Webhook:
        return cls(
            id=row["id"],
            name=row["name"],
            url=row["url"],
            secret=row["secret"],
            enabled=row["enabled"],
            event_filter=list(row["event_filter"] or []),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            format=row.get("format") or "grcen",
        )


# ── secret handling ──────────────────────────────────────────────────────


async def _secrets_encrypted(pool: asyncpg.Pool) -> bool:
    return await encryption_config.is_scope_active(pool, _SCOPE)


def _decrypt(secret: str, encrypted: bool) -> str:
    if encrypted and secret:
        return decrypt_field(secret, _SCOPE)
    return secret


def _encrypt(secret: str, encrypted: bool) -> str:
    if encrypted and secret:
        return encrypt_field(secret, _SCOPE)
    return secret


# ── CRUD ─────────────────────────────────────────────────────────────────


async def list_webhooks(
    pool: asyncpg.Pool,
    enabled_only: bool = False,
    *,
    organization_id: UUID | None = None,
) -> list[Webhook]:
    where: list[str] = []
    vals: list = []
    idx = 1
    if enabled_only:
        where.append("enabled = true")
    if organization_id is not None:
        where.append(f"organization_id = ${idx}")
        vals.append(organization_id)
        idx += 1
    sql = "SELECT * FROM webhooks"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY name"
    rows = await pool.fetch(sql, *vals)
    encrypted = await _secrets_encrypted(pool)
    hooks: list[Webhook] = []
    for r in rows:
        h = Webhook.from_row(r)
        h.secret = _decrypt(h.secret, encrypted)
        hooks.append(h)
    return hooks


async def get_webhook(
    pool: asyncpg.Pool, webhook_id: UUID, *, organization_id: UUID | None = None
) -> Webhook | None:
    row = await pool.fetchrow(
        """SELECT * FROM webhooks WHERE id = $1
           AND ($2::uuid IS NULL OR organization_id = $2)""",
        webhook_id, organization_id,
    )
    if not row:
        return None
    h = Webhook.from_row(row)
    h.secret = _decrypt(h.secret, await _secrets_encrypted(pool))
    return h


async def create_webhook(
    pool: asyncpg.Pool,
    *,
    organization_id: UUID | None = None,
    name: str,
    url: str,
    secret: str = "",
    enabled: bool = True,
    event_filter: list[str] | None = None,
    format: str = "grcen",
) -> Webhook:
    encrypted = await _secrets_encrypted(pool)
    if organization_id is None:
        from grcen.services import organization_service
        organization_id = await organization_service.get_default_org_id(pool)
    row = await pool.fetchrow(
        """INSERT INTO webhooks (id, name, url, secret, enabled, event_filter, format, organization_id)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
           RETURNING *""",
        uuid.uuid4(),
        name,
        url,
        _encrypt(secret, encrypted),
        enabled,
        event_filter or [],
        normalize_format(format),
        organization_id,
    )
    h = Webhook.from_row(row)
    h.secret = secret
    return h


async def update_webhook(
    pool: asyncpg.Pool,
    webhook_id: UUID,
    *,
    name: str | None = None,
    url: str | None = None,
    secret: str | None = None,
    enabled: bool | None = None,
    event_filter: list[str] | None = None,
    format: str | None = None,
) -> Webhook | None:
    sets: list[str] = []
    vals: list[Any] = []
    idx = 1
    encrypted = await _secrets_encrypted(pool)
    for col, val in [
        ("name", name),
        ("url", url),
        ("secret", _encrypt(secret, encrypted) if secret is not None else None),
        ("enabled", enabled),
        ("event_filter", event_filter),
        # None (not supplied) or an unknown value is skipped, leaving format as-is.
        ("format", format if format in FORMATS else None),
    ]:
        if val is not None:
            sets.append(f"{col} = ${idx}")
            vals.append(val)
            idx += 1
    if not sets:
        return await get_webhook(pool, webhook_id)
    sets.append("updated_at = now()")
    vals.append(webhook_id)
    row = await pool.fetchrow(
        f"UPDATE webhooks SET {', '.join(sets)} WHERE id = ${idx} RETURNING *", *vals
    )
    if not row:
        return None
    h = Webhook.from_row(row)
    h.secret = _decrypt(h.secret, encrypted)
    return h


async def delete_webhook(
    pool: asyncpg.Pool, webhook_id: UUID, *, organization_id: UUID | None = None
) -> bool:
    if organization_id is not None:
        result = await pool.execute(
            "DELETE FROM webhooks WHERE id = $1 AND organization_id = $2",
            webhook_id, organization_id,
        )
    else:
        result = await pool.execute("DELETE FROM webhooks WHERE id = $1", webhook_id)
    return result == "DELETE 1"


# ── delivery ─────────────────────────────────────────────────────────────


def sign_payload(secret: str, body: bytes) -> str:
    """Compute the signature header value for a payload."""
    mac = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={mac}"


def _matches_filter(hook: Webhook, event: str) -> bool:
    if not hook.event_filter:
        return True
    return event in hook.event_filter


def _is_transient(status_code: int | None) -> bool:
    """Whether a failed attempt is worth retrying.

    Network errors (no status code) and 5xx responses are transient; a 4xx
    means the receiver rejected the request outright and a retry won't heal it.
    """
    return status_code is None or status_code >= 500


# ── chat message formatting (Slack / Teams) ──────────────────────────────


_EVENT_EMOJI = {
    "alert.fired": ":rotating_light:",
    "asset.created": ":new:",
    "asset.updated": ":pencil2:",
    "asset.deleted": ":wastebasket:",
    "ping": ":satellite_antenna:",
}


def _summary_parts(event: str, data: dict[str, Any]) -> tuple[str, str, str, str | None]:
    """Return (label, emoji, detail, link) for a human-readable chat message."""
    label = WEBHOOK_EVENTS.get(event, event)
    emoji = _EVENT_EMOJI.get(event, ":bell:")
    link = data.get("link")
    if event == "alert.fired":
        detail = str(data.get("title") or "Alert")
        if data.get("message"):
            detail += f" — {data['message']}"
        if data.get("asset_name"):
            detail += f" (on {data['asset_name']})"
    elif event.startswith("asset."):
        detail = f"{data.get('name', '?')} ({data.get('type', '?')})"
        if data.get("actor"):
            detail += f" — by {data['actor']}"
        if not link and data.get("id") and event != "asset.deleted":
            from grcen.config import settings
            link = f"{settings.APP_BASE_URL.rstrip('/')}/assets/{data['id']}"
    elif event == "ping":
        detail = str(data.get("message") or "(no message)")
    else:
        detail = ", ".join(
            f"{k}={v}" for k, v in data.items()
            if k != "link" and not isinstance(v, (dict, list))
        )
    return label, emoji, detail, link


def _slack_payload(event: str, data: dict[str, Any]) -> dict[str, Any]:
    """A Slack incoming-webhook message (mrkdwn)."""
    label, emoji, detail, link = _summary_parts(event, data)
    text = f"{emoji} *{label}*"
    if detail:
        text += f" — {detail}"
    if link:
        text += f"\n<{link}|View in GRCen>"
    return {"text": text}


def _teams_payload(event: str, data: dict[str, Any]) -> dict[str, Any]:
    """A Microsoft Teams MessageCard."""
    label, _emoji, detail, link = _summary_parts(event, data)
    text = f"**{label}**"
    if detail:
        text += f" — {detail}"
    card: dict[str, Any] = {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "summary": label,
        "themeColor": "0F62FE",
        "title": f"GRCen: {label}",
        "text": text,
    }
    if link:
        card["potentialAction"] = [{
            "@type": "OpenUri",
            "name": "View in GRCen",
            "targets": [{"os": "default", "uri": link}],
        }]
    return card


def _build_request(
    hook: Webhook, event: str, data: dict[str, Any],
    delivery_id: str, timestamp: str,
) -> tuple[bytes, dict[str, str]]:
    """Serialise the request body + headers for this webhook's format.

    'slack'/'teams' emit a chat-ready message and are unsigned — the secret
    incoming-webhook URL is the credential and the receiver ignores our headers.
    'grcen' (default) emits the signed JSON envelope for machine/SIEM consumers.
    """
    base = {"Content-Type": "application/json", "User-Agent": "GRCen-Webhook/1.0"}
    if hook.format == "slack":
        body = json.dumps(_slack_payload(event, data), separators=(",", ":"), default=str)
        return body.encode("utf-8"), base
    if hook.format == "teams":
        body = json.dumps(_teams_payload(event, data), separators=(",", ":"), default=str)
        return body.encode("utf-8"), base
    envelope = {
        "event": event, "delivery_id": delivery_id,
        "timestamp": timestamp, "data": data,
    }
    body_bytes = json.dumps(envelope, separators=(",", ":"), default=str).encode("utf-8")
    headers = {
        **base,
        "X-GRCen-Event": event,
        "X-GRCen-Delivery": delivery_id,
        "X-GRCen-Timestamp": timestamp,
    }
    if hook.secret:
        headers["X-GRCen-Signature"] = sign_payload(hook.secret, body_bytes)
    return body_bytes, headers


async def send_to_webhook(
    pool: asyncpg.Pool,
    hook: Webhook,
    event: str,
    data: dict[str, Any],
    alert_id: UUID | None = None,
) -> tuple[bool, int | None, str | None]:
    """Send one webhook, retrying transient failures with exponential backoff.

    Up to ``settings.WEBHOOK_MAX_ATTEMPTS`` total tries; every attempt is
    recorded in ``webhook_deliveries`` with its 1-based ``attempt`` number.
    Returns (ok, status_code, error) from the final attempt.
    """
    from grcen.config import settings

    delivery_id = str(uuid.uuid4())
    timestamp = datetime.now(UTC).isoformat()
    body, headers = _build_request(hook, event, data, delivery_id, timestamp)

    org_row = await pool.fetchrow(
        "SELECT organization_id FROM webhooks WHERE id = $1", hook.id
    )
    organization_id = org_row["organization_id"] if org_row else None

    max_attempts = max(1, settings.WEBHOOK_MAX_ATTEMPTS)
    status_code: int | None = None
    response_body: str | None = None
    error: str | None = None
    ok = False
    for attempt in range(1, max_attempts + 1):
        status_code = None
        response_body = None
        error = None
        ok = False
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
                resp = await client.post(hook.url, content=body, headers=headers)
            status_code = resp.status_code
            response_body = resp.text[:_RESPONSE_BODY_LIMIT]
            ok = 200 <= resp.status_code < 300
            if not ok:
                error = f"HTTP {resp.status_code}"
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            log.warning(
                "Webhook POST to %s failed (attempt %d/%d): %s",
                hook.url, attempt, max_attempts, error,
            )

        await _log_delivery(
            pool,
            webhook_id=hook.id,
            alert_id=alert_id,
            event=event,
            url=hook.url,
            status_code=status_code,
            response_body=response_body,
            error=error,
            attempt=attempt,
            organization_id=organization_id,
        )
        if ok or not _is_transient(status_code) or attempt == max_attempts:
            break
        backoff = settings.WEBHOOK_RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1))
        if backoff > 0:
            await asyncio.sleep(backoff)
    return ok, status_code, error


async def dispatch(
    pool: asyncpg.Pool,
    event: str,
    data: dict[str, Any],
    alert_id: UUID | None = None,
    *,
    organization_id: UUID | None = None,
) -> int:
    """Send ``event`` to every enabled webhook whose filter matches.

    Returns the number of webhooks notified.  Each send is awaited serially so
    slow hooks don't starve the pool; if that becomes a problem, move to
    ``asyncio.gather`` with a semaphore.
    """
    hooks = await list_webhooks(pool, enabled_only=True, organization_id=organization_id)
    sent = 0
    for hook in hooks:
        if not _matches_filter(hook, event):
            continue
        try:
            await send_to_webhook(pool, hook, event, data, alert_id=alert_id)
        except Exception:
            log.exception("Unexpected webhook dispatch error for %s", hook.id)
            continue
        sent += 1
    return sent


# Keep strong references to in-flight background dispatch tasks so the event
# loop doesn't garbage-collect them mid-send.
_background_tasks: set[asyncio.Task] = set()


def dispatch_background(
    pool: asyncpg.Pool,
    event: str,
    data: dict[str, Any],
    *,
    organization_id: UUID | None = None,
) -> None:
    """Fire-and-forget :func:`dispatch` off the request path.

    Request handlers (e.g. asset create/delete) use this so a slow or retrying
    webhook never delays the HTTP response. Errors are already swallowed and
    logged inside ``dispatch``/``send_to_webhook``.
    """
    task = asyncio.get_running_loop().create_task(
        dispatch(pool, event, data, organization_id=organization_id)
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def asset_event_payload(asset, *, actor: str = "") -> dict[str, Any]:
    """Envelope ``data`` for ``asset.created`` / ``asset.deleted`` events."""
    return {
        "id": str(asset.id),
        "type": asset.type.value,
        "name": asset.name,
        "status": asset.status.value,
        "criticality": asset.criticality,
        "tags": asset.tags or [],
        "actor": actor,
    }


async def _log_delivery(
    pool: asyncpg.Pool,
    *,
    webhook_id: UUID,
    alert_id: UUID | None,
    event: str,
    url: str,
    status_code: int | None,
    response_body: str | None,
    error: str | None,
    attempt: int = 1,
    organization_id: UUID | None = None,
) -> None:
    if organization_id is None:
        from grcen.services import organization_service
        organization_id = await organization_service.get_default_org_id(pool)
    await pool.execute(
        """INSERT INTO webhook_deliveries
               (id, webhook_id, alert_id, event, url, status_code, response_body, error, attempt, organization_id)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)""",
        uuid.uuid4(),
        webhook_id,
        alert_id,
        event,
        url,
        status_code,
        response_body,
        error,
        attempt,
        organization_id,
    )
