"""Outbound webhook management and delivery.

Each webhook has a URL and an optional shared secret.  Delivery POSTs a JSON
envelope to the URL; if the secret is non-empty, the body is HMAC-SHA256 signed
and the signature is sent in ``X-GRCen-Signature: sha256=<hex>``.  Every attempt
is recorded in ``webhook_deliveries`` — failures never propagate.
"""

from __future__ import annotations

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


async def list_webhooks(pool: asyncpg.Pool, enabled_only: bool = False) -> list[Webhook]:
    sql = "SELECT * FROM webhooks"
    if enabled_only:
        sql += " WHERE enabled = true"
    sql += " ORDER BY name"
    rows = await pool.fetch(sql)
    encrypted = await _secrets_encrypted(pool)
    hooks: list[Webhook] = []
    for r in rows:
        h = Webhook.from_row(r)
        h.secret = _decrypt(h.secret, encrypted)
        hooks.append(h)
    return hooks


async def get_webhook(pool: asyncpg.Pool, webhook_id: UUID) -> Webhook | None:
    row = await pool.fetchrow("SELECT * FROM webhooks WHERE id = $1", webhook_id)
    if not row:
        return None
    h = Webhook.from_row(row)
    h.secret = _decrypt(h.secret, await _secrets_encrypted(pool))
    return h


async def create_webhook(
    pool: asyncpg.Pool,
    *,
    name: str,
    url: str,
    secret: str = "",
    enabled: bool = True,
    event_filter: list[str] | None = None,
) -> Webhook:
    encrypted = await _secrets_encrypted(pool)
    row = await pool.fetchrow(
        """INSERT INTO webhooks (id, name, url, secret, enabled, event_filter)
           VALUES ($1, $2, $3, $4, $5, $6)
           RETURNING *""",
        uuid.uuid4(),
        name,
        url,
        _encrypt(secret, encrypted),
        enabled,
        event_filter or [],
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


async def delete_webhook(pool: asyncpg.Pool, webhook_id: UUID) -> bool:
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


async def send_to_webhook(
    pool: asyncpg.Pool,
    hook: Webhook,
    event: str,
    data: dict[str, Any],
    alert_id: UUID | None = None,
) -> tuple[bool, int | None, str | None]:
    """Send one webhook.  Returns (ok, status_code, error)."""
    delivery_id = str(uuid.uuid4())
    envelope = {
        "event": event,
        "delivery_id": delivery_id,
        "timestamp": datetime.now(UTC).isoformat(),
        "data": data,
    }
    body = json.dumps(envelope, separators=(",", ":"), default=str).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "GRCen-Webhook/1.0",
        "X-GRCen-Event": event,
        "X-GRCen-Delivery": delivery_id,
        "X-GRCen-Timestamp": envelope["timestamp"],
    }
    if hook.secret:
        headers["X-GRCen-Signature"] = sign_payload(hook.secret, body)

    status_code: int | None = None
    response_body: str | None = None
    error: str | None = None
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
        log.warning("Webhook POST to %s failed: %s", hook.url, error)

    await _log_delivery(
        pool,
        webhook_id=hook.id,
        alert_id=alert_id,
        event=event,
        url=hook.url,
        status_code=status_code,
        response_body=response_body,
        error=error,
    )
    return ok, status_code, error


async def dispatch(
    pool: asyncpg.Pool,
    event: str,
    data: dict[str, Any],
    alert_id: UUID | None = None,
) -> int:
    """Send ``event`` to every enabled webhook whose filter matches.

    Returns the number of webhooks notified.  Each send is awaited serially so
    slow hooks don't starve the pool; if that becomes a problem, move to
    ``asyncio.gather`` with a semaphore.
    """
    hooks = await list_webhooks(pool, enabled_only=True)
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
) -> None:
    await pool.execute(
        """INSERT INTO webhook_deliveries
               (id, webhook_id, alert_id, event, url, status_code, response_body, error)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
        uuid.uuid4(),
        webhook_id,
        alert_id,
        event,
        url,
        status_code,
        response_body,
        error,
    )
