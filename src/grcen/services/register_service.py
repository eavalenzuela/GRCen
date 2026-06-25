"""Register metric aggregates and per-type counts (Slice 1).

Pure SQL aggregates driven by ``MetricDef`` config in ``grcen.registers``. Every
query is org-scoped and pinned to the register's asset type. Metric ``field``
values come only from trusted config, but are still identifier-guarded before
interpolation.
"""
from __future__ import annotations

import re
from uuid import UUID

import asyncpg

from grcen.custom_fields import INCIDENT_TERMINAL_STATUSES
from grcen.registers import MetricDef, RegisterDef
from grcen.services import review_service

_IDENT = re.compile(r"^[a-zA-Z0-9_\-]+$")


async def register_counts(pool: asyncpg.Pool, *, organization_id: UUID | None = None) -> dict[str, int]:
    """One round-trip: count of assets per type for the org (for the index page)."""
    rows = await pool.fetch(
        """SELECT type, count(*) AS c FROM assets
           WHERE ($1::uuid IS NULL OR organization_id = $1)
           GROUP BY type""",
        organization_id,
    )
    return {r["type"]: r["c"] for r in rows}


async def _count(pool, type_val, organization_id, extra_sql="", extra_vals=()) -> int:
    val = await pool.fetchval(
        f"""SELECT count(*) FROM assets
            WHERE type = $1 AND ($2::uuid IS NULL OR organization_id = $2){extra_sql}""",
        type_val, organization_id, *extra_vals,
    )
    return val or 0


async def _metric_value(pool, type_val: str, m: MetricDef, organization_id) -> int:
    if m.kind == "total":
        return await _count(pool, type_val, organization_id)
    if m.kind == "status_eq":
        return await _count(pool, type_val, organization_id, " AND status = $3", (m.value,))
    if m.kind == "incident_open":
        # Open = not in a terminal lifecycle state. When incident_status is set,
        # use it; when it's unset (incidents predating the field), fall back to
        # resolved_at so a genuinely-resolved legacy incident isn't miscounted as
        # open. The $3 array is a non-empty, NULL-free literal (terminal states).
        return await _count(
            pool, type_val, organization_id,
            " AND (CASE WHEN NULLIF(metadata->>'incident_status', '') IS NOT NULL"
            "           THEN metadata->>'incident_status' <> ALL($3::text[])"
            "           ELSE NULLIF(metadata->>'resolved_at', '') IS NULL END)",
            (list(INCIDENT_TERMINAL_STATUSES),),
        )
    if m.kind == "overdue_reviews":
        reviews = await review_service.get_reviews(
            pool, asset_type=type_val, status_filter="overdue", organization_id=organization_id
        )
        return len(reviews)
    if m.kind in ("meta_eq", "meta_in", "meta_sum"):
        if not m.field or not _IDENT.match(m.field):
            return 0
        if m.kind == "meta_eq":
            return await _count(
                pool, type_val, organization_id,
                f" AND metadata->>'{m.field}' = $3", (m.value,)
            )
        if m.kind == "meta_in":
            return await _count(
                pool, type_val, organization_id,
                f" AND metadata->>'{m.field}' = ANY($3)", (list(m.values or ()),)
            )
        # meta_sum — per-value numeric cast so one dirty row can't abort the sum
        val = await pool.fetchval(
            f"""SELECT COALESCE(SUM(
                    CASE WHEN metadata->>'{m.field}' ~ '^-?[0-9.]+$'
                         THEN (metadata->>'{m.field}')::numeric ELSE 0 END), 0)
                FROM assets
                WHERE type = $1 AND ($2::uuid IS NULL OR organization_id = $2)""",
            type_val, organization_id,
        )
        return int(val or 0)
    return 0


async def build_metrics(
    pool: asyncpg.Pool, register: RegisterDef, *, organization_id: UUID | None = None
) -> list[dict]:
    """Compute the register's stat cards: ``[{label, value, warn}]``."""
    out: list[dict] = []
    for m in register.metrics:
        value = await _metric_value(pool, register.type.value, m, organization_id)
        out.append({"label": m.label, "value": value, "warn": m.warn and bool(value)})
    return out
