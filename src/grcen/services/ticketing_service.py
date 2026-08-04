"""External ticketing integration — push findings to an ITSM, sync status back (E3).

Outbound: create a Jira / ServiceNow / generic ticket from a finding and stamp
the ticket key + URL onto the finding (``metadata.external_ticket*``), so a
remediation ticket lives next to the finding that raised it. Inbound: a
``sync``-scoped status feed (the ITSM, or a poller) reports ticket statuses and
GRCen maps them onto ``finding_status`` — closing the loop without a bespoke
webhook receiver.

Provider payloads are abstracted behind ``_build_request`` / ``_parse_response``;
'generic' POSTs a plain JSON body to ``base_url`` for anything else (Linear,
a Zapier hook, an internal queue). Config + the encrypted API token live in
``itsm_settings``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from uuid import UUID

import asyncpg
import httpx

from grcen.services import itsm_settings

_TIMEOUT_SECONDS = 15.0

# ITSM status (lower-cased) → GRCen finding_status. Unknown statuses leave
# finding_status unchanged (only external_ticket_status is recorded).
_STATUS_MAP = {
    "open": "open", "new": "open", "to do": "open", "backlog": "open", "reopened": "open",
    "in progress": "in_remediation", "in review": "in_remediation",
    "in remediation": "in_remediation", "pending": "in_remediation",
    "resolved": "remediated", "done": "remediated", "fixed": "remediated",
    "verified": "verified",
    "closed": "closed", "complete": "closed", "completed": "closed",
    "cancelled": "risk_accepted", "canceled": "risk_accepted",
    "won't do": "risk_accepted", "wontfix": "risk_accepted", "risk accepted": "risk_accepted",
}


class TicketingError(Exception):
    def __init__(self, detail: str, status: int = 400):
        super().__init__(detail)
        self.detail = detail
        self.status = status


@dataclass
class TicketSyncResult:
    updated: int = 0
    unmatched: list[str] = field(default_factory=list)   # ticket keys with no finding
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _parse_meta(raw) -> dict:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    try:
        return json.loads(raw) or {}
    except (TypeError, ValueError):
        return {}


def map_status(status: str | None) -> str | None:
    return _STATUS_MAP.get((status or "").strip().lower())


# ── outbound: build the provider request ─────────────────────────────────


def _describe_finding(name: str, description: str | None, meta: dict) -> str:
    lines = [description or "", ""]
    for label, key in (("Severity", "severity"), ("Status", "finding_status"),
                        ("CVE", "cve_id"), ("Due", "due_date"),
                        ("Affected", "affected_asset")):
        if meta.get(key):
            lines.append(f"{label}: {meta[key]}")
    lines.append("\nRaised from GRCen.")
    return "\n".join(lines).strip()


def _build_request(cfg, summary: str, description: str) -> tuple[str, dict, tuple | None, dict]:
    """Return (url, json_payload, basic_auth_or_None, headers)."""
    base = cfg.base_url.rstrip("/")
    if cfg.provider == "jira":
        return (
            f"{base}/rest/api/2/issue",
            {"fields": {
                "project": {"key": cfg.project_key},
                "summary": summary[:255],
                "description": description,
                "issuetype": {"name": cfg.issue_type or "Task"},
            }},
            (cfg.auth_user, cfg.auth_token),
            {"Content-Type": "application/json"},
        )
    if cfg.provider == "servicenow":
        table = cfg.project_key or "incident"
        return (
            f"{base}/api/now/table/{table}",
            {"short_description": summary[:160], "description": description},
            (cfg.auth_user, cfg.auth_token),
            {"Content-Type": "application/json", "Accept": "application/json"},
        )
    # generic: bearer token, simple body
    headers = {"Content-Type": "application/json"}
    if cfg.auth_token:
        headers["Authorization"] = f"Bearer {cfg.auth_token}"
    return (base, {"summary": summary, "description": description}, None, headers)


def _parse_response(cfg, data: dict) -> tuple[str, str | None]:
    """Return (ticket_key, ticket_url) from the provider's create response."""
    base = cfg.base_url.rstrip("/")
    if cfg.provider == "jira":
        key = str(data.get("key") or data.get("id") or "")
        return key, (f"{base}/browse/{key}" if key else None)
    if cfg.provider == "servicenow":
        result = data.get("result") or {}
        key = str(result.get("number") or result.get("sys_id") or "")
        sys_id = result.get("sys_id")
        table = cfg.project_key or "incident"
        url = f"{base}/nav_to.do?uri={table}.do?sys_id={sys_id}" if sys_id else None
        return key, url
    key = str(data.get("key") or data.get("id") or data.get("number") or "")
    return key, data.get("url")


async def push_finding(
    pool: asyncpg.Pool,
    finding_id: UUID,
    *,
    organization_id: UUID,
) -> dict:
    """Create an ITSM ticket for a finding and stamp the ref onto it."""
    cfg = await itsm_settings.get_settings(pool)
    if not cfg.is_enabled:
        raise TicketingError("ITSM ticketing is not configured/enabled", status=409)

    finding = await pool.fetchrow(
        """SELECT name, description, metadata FROM assets
           WHERE id = $1 AND type = 'finding' AND organization_id = $2""",
        finding_id, organization_id,
    )
    if finding is None:
        raise TicketingError("finding not found", status=404)
    meta = _parse_meta(finding["metadata"])
    if meta.get("external_ticket"):
        raise TicketingError(f"finding already linked to ticket {meta['external_ticket']}")

    # Claim the finding before calling out. The check above and the write below
    # are separated by an HTTP round trip of up to _TIMEOUT_SECONDS, so without
    # a claim two concurrent pushes both passed the check, both created a ticket
    # in Jira/ServiceNow, and the second write buried the first ticket's ref —
    # leaving a real ticket nobody in GRCen can see. The conditional UPDATE only
    # matches while the finding is still unlinked and unclaimed.
    claimed = await pool.fetchval(
        """UPDATE assets
              SET metadata = coalesce(metadata, '{}'::jsonb)
                             || '{"external_ticket_status": "creating"}'::jsonb,
                  updated_at = now()
            WHERE id = $1 AND organization_id = $2 AND type = 'finding'
              AND coalesce(metadata->>'external_ticket', '') = ''
              AND coalesce(metadata->>'external_ticket_status', '') <> 'creating'
            RETURNING 1""",
        finding_id, organization_id,
    )
    if not claimed:
        raise TicketingError(
            "a ticket is already being created for this finding", status=409
        )

    async def _release() -> None:
        await pool.execute(
            """UPDATE assets
                  SET metadata = coalesce(metadata, '{}'::jsonb) - 'external_ticket_status',
                      updated_at = now()
                WHERE id = $1 AND organization_id = $2
                  AND coalesce(metadata->>'external_ticket', '') = ''""",
            finding_id, organization_id,
        )

    try:
        url, payload, auth, headers = _build_request(
            cfg, finding["name"], _describe_finding(finding["name"], finding["description"], meta)
        )
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
                resp = await client.post(url, json=payload, auth=auth, headers=headers)
        except Exception as exc:  # noqa: BLE001 — surface as a clean error
            raise TicketingError(f"ITSM request failed: {type(exc).__name__}", status=502) from exc
        if not (200 <= resp.status_code < 300):
            raise TicketingError(
                f"ITSM returned HTTP {resp.status_code}: {resp.text[:300]}", status=502
            )
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            data = {}
        key, ticket_url = _parse_response(cfg, data)
        if not key:
            raise TicketingError("ITSM response did not include a ticket id", status=502)
    except Exception:
        # Put the finding back the way we found it so a retry can proceed.
        await _release()
        raise

    # Merge into whatever metadata is there NOW rather than writing back the
    # blob read before the HTTP call — anything changed meanwhile (a status
    # update, an owner edit) would otherwise be silently reverted.
    stamp = {"external_ticket": key, "external_ticket_status": "created"}
    if ticket_url:
        stamp["external_ticket_url"] = ticket_url
    await pool.execute(
        """UPDATE assets
              SET metadata = coalesce(metadata, '{}'::jsonb) || $1::jsonb,
                  updated_at = now()
            WHERE id = $2 AND organization_id = $3""",
        json.dumps(stamp), finding_id, organization_id,
    )
    return {"ticket_key": key, "ticket_url": ticket_url, "provider": cfg.provider}


# ── inbound: sync ticket statuses back onto findings ─────────────────────


async def sync_ticket_statuses(
    pool: asyncpg.Pool,
    tickets: object,
    *,
    organization_id: UUID,
) -> TicketSyncResult:
    """Map external ticket statuses onto the findings they're linked to.

    ``tickets`` = ``[{"ticket_key": "...", "status": "..."}]``. A ticket key with
    no matching finding is reported (not an error); an unknown status updates
    only ``external_ticket_status``, leaving ``finding_status`` untouched.
    """
    result = TicketSyncResult()
    if not isinstance(tickets, list):
        result.errors.append("'tickets' must be a list")
        return result

    for i, t in enumerate(tickets):
        if not isinstance(t, dict) or not t.get("ticket_key"):
            result.warnings.append(f"ticket[{i}] missing 'ticket_key'; skipped")
            continue
        key = str(t["ticket_key"])
        status = t.get("status")
        mapped = map_status(status)
        rows = await pool.fetch(
            """SELECT id, metadata FROM assets
               WHERE type = 'finding' AND organization_id = $1
                 AND metadata ->> 'external_ticket' = $2""",
            organization_id, key,
        )
        if not rows:
            result.unmatched.append(key)
            continue
        for r in rows:
            meta = _parse_meta(r["metadata"])
            if status is not None:
                meta["external_ticket_status"] = str(status)
            if mapped:
                meta["finding_status"] = mapped
            await pool.execute(
                "UPDATE assets SET metadata = $1::jsonb, updated_at = now() WHERE id = $2",
                json.dumps(meta), r["id"],
            )
            result.updated += 1
    return result
