"""Service for querying review/due-date status across all asset types."""

import json
from datetime import date, timedelta
from uuid import UUID

import asyncpg

# Map of asset_type -> metadata field name that holds the "next due" date.
# Each type uses a different field name for historical reasons.
REVIEW_DATE_FIELDS: dict[str, str] = {
    "person": "next_review_due",
    "policy": "review_date",
    "product": "next_review_due",
    "system": "next_review_due",
    "device": "next_review_due",
    "data_category": "next_review_due",
    "requirement": "due_date",
    "process": "next_execution",
    "intellectual_property": "expiry_date",
    "risk": "review_date",
    "organizational_unit": "next_review_due",
    "vendor": "next_assessment_due",
    "control": "next_test_due",
    "framework": "certification_expiry",
    # audit uses end_date but audits are events, not recurring reviews
}


def review_status(due_date_str: str | None, today: date | None = None) -> str:
    """Return 'overdue', 'due_soon' (within 30 days), or 'on_track'."""
    if not due_date_str:
        return "no_date"
    today = today or date.today()
    try:
        due = date.fromisoformat(due_date_str)
    except (ValueError, TypeError):
        return "no_date"
    if due < today:
        return "overdue"
    if due <= today + timedelta(days=30):
        return "due_soon"
    return "on_track"


async def get_reviews(
    pool: asyncpg.Pool,
    *,
    asset_type: str | None = None,
    status_filter: str | None = None,
    organization_id=None,
) -> list[dict]:
    """Return all active assets that have a review/due date field set."""
    where = "a.status = 'active'"
    vals: list = []
    idx = 1
    if asset_type:
        where += f" AND a.type = ${idx}"
        vals.append(asset_type)
        idx += 1
    if organization_id is not None:
        where += f" AND a.organization_id = ${idx}"
        vals.append(organization_id)
        idx += 1

    rows = await pool.fetch(
        f"""SELECT a.id, a.type, a.name, COALESCE(o.name, a.owner) AS owner, a.metadata
            FROM assets a LEFT JOIN assets o ON o.id = a.owner_id
            WHERE {where} ORDER BY a.name""",
        *vals,
    )

    today = date.today()
    results: list[dict] = []
    for row in rows:
        asset_type_val = row["type"]
        field_name = REVIEW_DATE_FIELDS.get(asset_type_val)
        if not field_name:
            continue
        meta = row["metadata"]
        if isinstance(meta, str):
            meta = json.loads(meta)
        if not meta:
            continue
        due_str = meta.get(field_name)
        if not due_str:
            continue
        rs = review_status(due_str, today)
        if status_filter and rs != status_filter:
            continue
        try:
            due_date = date.fromisoformat(due_str)
        except (ValueError, TypeError):
            continue
        results.append({
            "id": row["id"],
            "name": row["name"],
            "type": asset_type_val,
            "owner": row["owner"],
            "due_date": due_date,
            "due_field": field_name,
            "status": rs,
        })

    results.sort(key=lambda r: r["due_date"])
    return results


async def get_review_counts(pool: asyncpg.Pool, *, organization_id=None) -> dict[str, int]:
    reviews = await get_reviews(pool, organization_id=organization_id)
    counts = {"overdue": 0, "due_soon": 0}
    for r in reviews:
        if r["status"] in counts:
            counts[r["status"]] += 1
    return counts
