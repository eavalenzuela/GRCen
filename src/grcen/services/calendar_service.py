"""Per-user iCal feed of upcoming review dates.

Review and due dates matter in calendars, not dashboards — this service turns
the review engine's output into an iCalendar (RFC 5545) document served at a
per-user capability URL (``/calendar/{token}/grcen.ics``), consumable by
Outlook / Google Calendar / Thunderbird as a subscribed calendar.

The token follows the vendor-portal precedent: an unguessable random string
IS the authorization. It is stored on ``users.calendar_token`` (NULL = feed
disabled), can be regenerated (invalidating the old URL) and revoked from
/settings, and grants exactly one read-only view: the review dates already
visible to that user's active org.
"""

from __future__ import annotations

import secrets
from datetime import UTC, date, datetime
from uuid import UUID

import asyncpg

from grcen.services import review_service


def _generate_token() -> str:
    return secrets.token_urlsafe(32)


async def get_token(pool: asyncpg.Pool, user_id: UUID) -> str | None:
    return await pool.fetchval(
        "SELECT calendar_token FROM users WHERE id = $1", user_id
    )


async def enable_feed(pool: asyncpg.Pool, user_id: UUID) -> str:
    """Create (or rotate) the user's feed token and return it."""
    token = _generate_token()
    await pool.execute(
        "UPDATE users SET calendar_token = $1, updated_at = now() WHERE id = $2",
        token, user_id,
    )
    return token


async def disable_feed(pool: asyncpg.Pool, user_id: UUID) -> None:
    await pool.execute(
        "UPDATE users SET calendar_token = NULL, updated_at = now() WHERE id = $1",
        user_id,
    )


async def user_for_token(pool: asyncpg.Pool, token: str):
    """Resolve a feed token to (user_id, organization_id), or None.

    Inactive users lose their feed along with their login.
    """
    if not token:
        return None
    return await pool.fetchrow(
        """SELECT id, organization_id FROM users
           WHERE calendar_token = $1 AND is_active = true""",
        token,
    )


def _escape(text: str) -> str:
    """Escape iCalendar TEXT values (RFC 5545 §3.3.11)."""
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def _fold(line: str) -> str:
    """Fold content lines longer than 75 octets (RFC 5545 §3.1)."""
    out = []
    while len(line.encode("utf-8")) > 75:
        # Cut at 74 characters — close enough for our ASCII-heavy content and
        # always under the 75-octet ceiling for the continuation marker.
        out.append(line[:74])
        line = " " + line[74:]
    out.append(line)
    return "\r\n".join(out)


def build_ics(reviews: list[dict], *, base_url: str, calendar_name: str = "GRCen Reviews") -> str:
    """Render review rows (from review_service.get_reviews) as an iCalendar doc.

    Each review becomes an all-day VEVENT on its due date, so calendar apps
    show "Policy review due: Acceptable Use Policy" on the right day.
    """
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    lines: list[str] = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//GRCen//Review Calendar//EN",
        "CALSCALE:GREGORIAN",
        f"X-WR-CALNAME:{_escape(calendar_name)}",
    ]
    for r in reviews:
        due: date = r["due_date"]
        type_label = str(r["type"]).replace("_", " ").title()
        summary = f"{type_label} review due: {r['name']}"
        desc_bits = [f"Status: {r['status']}"]
        if r.get("owner"):
            desc_bits.append(f"Owner: {r['owner']}")
        desc_bits.append(f"Due field: {r['due_field']}")
        lines += [
            "BEGIN:VEVENT",
            f"UID:{r['id']}-{r['due_field']}@grcen",
            f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{due.strftime('%Y%m%d')}",
            f"SUMMARY:{_escape(summary)}",
            f"DESCRIPTION:{_escape('; '.join(desc_bits))}",
            f"URL:{base_url}/assets/{r['id']}",
            "TRANSP:TRANSPARENT",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(ln) for ln in lines) + "\r\n"


async def feed_for_token(pool: asyncpg.Pool, token: str, *, base_url: str) -> str | None:
    """The full .ics document for a feed token, or None if the token is dead."""
    row = await user_for_token(pool, token)
    if row is None:
        return None
    reviews = await review_service.get_reviews(
        pool, organization_id=row["organization_id"]
    )
    return build_ics(reviews, base_url=base_url.rstrip("/"))
