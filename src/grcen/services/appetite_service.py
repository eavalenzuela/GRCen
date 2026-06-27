"""Risk appetite: per-category thresholds and per-risk in/near/out evaluation.

Appetite defines, per org (and optionally per ``risk_category``), the residual
score a risk may carry before it is "near" (amber) or "out of" (red) appetite.
``risk_category = ''`` is the org default applied when no category-specific band
exists. The headline number — "N risks out of appetite" — is the most-requested
board artifact.
"""
from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import asyncpg

from grcen.services import risk_service

DEFAULT_CATEGORY = ""


def _parse(raw) -> dict:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    try:
        return json.loads(raw) or {}
    except (TypeError, ValueError):
        return {}


def _risk_score(meta: dict) -> int | None:
    """Residual score if recorded, else the computed likelihood×impact score."""
    resid = meta.get("residual_risk_score")
    try:
        if resid is not None and int(resid) > 0:
            return int(resid)
    except (TypeError, ValueError):
        pass
    return risk_service.compute_risk_score(meta.get("likelihood"), meta.get("impact"))


async def get_appetite(
    pool: asyncpg.Pool, *, organization_id: UUID
) -> dict[str, dict[str, int]]:
    """{risk_category: {max_score, warn_score}}; '' is the org default."""
    rows = await pool.fetch(
        "SELECT risk_category, max_score, warn_score FROM risk_appetite WHERE organization_id = $1",
        organization_id,
    )
    return {
        r["risk_category"]: {"max_score": r["max_score"], "warn_score": r["warn_score"]}
        for r in rows
    }


async def set_appetite(
    pool: asyncpg.Pool, *, organization_id: UUID, risk_category: str,
    max_score: int, warn_score: int,
) -> None:
    await pool.execute(
        """INSERT INTO risk_appetite (organization_id, risk_category, max_score, warn_score)
           VALUES ($1, $2, $3, $4)
           ON CONFLICT (organization_id, risk_category) DO UPDATE SET
               max_score = EXCLUDED.max_score,
               warn_score = EXCLUDED.warn_score,
               updated_at = now()""",
        organization_id, risk_category or DEFAULT_CATEGORY, max_score, warn_score,
    )


async def delete_appetite(
    pool: asyncpg.Pool, *, organization_id: UUID, risk_category: str
) -> None:
    await pool.execute(
        "DELETE FROM risk_appetite WHERE organization_id = $1 AND risk_category = $2",
        organization_id, risk_category or DEFAULT_CATEGORY,
    )


async def evaluate_risks(
    pool: asyncpg.Pool, *, organization_id: UUID | None = None
) -> list[dict[str, Any]]:
    """Each active risk tagged within | near | out | unknown (no band/score)."""
    appetite = (
        await get_appetite(pool, organization_id=organization_id)
        if organization_id is not None else {}
    )
    default = appetite.get(DEFAULT_CATEGORY)
    rows = await pool.fetch(
        """SELECT id, name, metadata FROM assets
           WHERE type = 'risk' AND status = 'active'
             AND ($1::uuid IS NULL OR organization_id = $1)
           ORDER BY name""",
        organization_id,
    )
    out: list[dict[str, Any]] = []
    for r in rows:
        meta = _parse(r["metadata"])
        score = _risk_score(meta)
        category = meta.get("risk_category") or ""
        band = appetite.get(category) or default
        status = "unknown"
        if score is not None and band:
            if score > band["max_score"]:
                status = "out"
            elif score > band["warn_score"]:
                status = "near"
            else:
                status = "within"
        out.append({
            "id": str(r["id"]), "name": r["name"], "score": score,
            "risk_category": category, "status": status,
        })
    return out


async def breach_summary(
    pool: asyncpg.Pool, *, organization_id: UUID
) -> dict[str, Any]:
    """{out, near, out_risks} — the dashboard / board headline."""
    evals = await evaluate_risks(pool, organization_id=organization_id)
    out_risks = [e for e in evals if e["status"] == "out"]
    return {
        "out": len(out_risks),
        "near": sum(1 for e in evals if e["status"] == "near"),
        "out_risks": out_risks,
    }
