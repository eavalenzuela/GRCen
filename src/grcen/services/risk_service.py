import json
from datetime import UTC, datetime
from uuid import UUID

import asyncpg

# Likelihood and impact scales (index = numeric value 1-5)
LIKELIHOOD_LEVELS = ["rare", "unlikely", "possible", "likely", "almost_certain"]
IMPACT_LEVELS = ["insignificant", "minor", "moderate", "major", "catastrophic"]


def likelihood_value(level: str) -> int:
    """Return 1-5 numeric value for a likelihood level, or 0 if unknown."""
    try:
        return LIKELIHOOD_LEVELS.index(level) + 1
    except ValueError:
        return 0


def impact_value(level: str) -> int:
    """Return 1-5 numeric value for an impact level, or 0 if unknown."""
    try:
        return IMPACT_LEVELS.index(level) + 1
    except ValueError:
        return 0


def compute_risk_score(likelihood: str | None, impact: str | None) -> int | None:
    """Compute risk score (1-25) from likelihood x impact. Returns None if either is missing."""
    if not likelihood or not impact:
        return None
    l = likelihood_value(likelihood)
    i = impact_value(impact)
    if l == 0 or i == 0:
        return None
    return l * i


def score_color(score: int) -> str:
    """Return a CSS class suffix for a risk score."""
    if score >= 20:
        return "critical"
    if score >= 12:
        return "high"
    if score >= 6:
        return "medium"
    return "low"


async def get_risk_heatmap(pool: asyncpg.Pool) -> dict[tuple[int, int], list[dict]]:
    """Query active risk assets and bucket them by (likelihood_val, impact_val).

    Returns a dict mapping (likelihood_idx, impact_idx) -> list of {id, name, score}.
    Indices are 1-based (1=lowest, 5=highest).
    """
    rows = await pool.fetch(
        """
        SELECT id, name, metadata
        FROM assets
        WHERE type = 'risk' AND status = 'active'
        """
    )

    heatmap: dict[tuple[int, int], list[dict]] = {}
    for row in rows:
        meta = row["metadata"]
        if isinstance(meta, str):
            meta = json.loads(meta)
        if not meta:
            continue
        l = likelihood_value(meta.get("likelihood", ""))
        i = impact_value(meta.get("impact", ""))
        if l == 0 or i == 0:
            continue
        score = l * i
        key = (l, i)
        heatmap.setdefault(key, [])
        heatmap[key].append({"id": row["id"], "name": row["name"], "score": score})
    return heatmap


async def get_top_risks(pool: asyncpg.Pool, limit: int = 5) -> list[dict]:
    """Return top N active risks sorted by computed score descending."""
    rows = await pool.fetch(
        """
        SELECT a.id, a.name, COALESCE(o.name, a.owner) AS owner, a.metadata
        FROM assets a LEFT JOIN assets o ON o.id = a.owner_id
        WHERE a.type = 'risk' AND a.status = 'active'
        """
    )

    risks = []
    for row in rows:
        meta = row["metadata"]
        if isinstance(meta, str):
            meta = json.loads(meta)
        if not meta:
            continue
        score = compute_risk_score(meta.get("likelihood"), meta.get("impact"))
        if score is None:
            continue
        risks.append({
            "id": row["id"],
            "name": row["name"],
            "owner": row["owner"],
            "score": score,
            "color": score_color(score),
            "likelihood": meta.get("likelihood", ""),
            "impact": meta.get("impact", ""),
            "risk_category": meta.get("risk_category", ""),
            "treatment": meta.get("treatment", ""),
        })
    risks.sort(key=lambda r: r["score"], reverse=True)
    return risks[:limit]


def _parse_meta(row) -> dict:
    meta = row["metadata"]
    if isinstance(meta, str):
        meta = json.loads(meta)
    return meta or {}


# Weight each control effectiveness level into a 0–1 score.
_EFFECTIVENESS_WEIGHT = {
    "effective": 1.0,
    "partially_effective": 0.5,
    "ineffective": 0.0,
    "not_tested": 0.25,
}


def _effectiveness_label(score: float | None, control_count: int) -> str:
    if control_count == 0:
        return "none"
    if score is None:
        return "unknown"
    if score >= 0.8:
        return "strong"
    if score >= 0.5:
        return "adequate"
    if score >= 0.25:
        return "weak"
    return "none"


async def get_risk_control_rollup(
    pool: asyncpg.Pool, risk_ids: list[UUID]
) -> dict[UUID, dict]:
    """Return {risk_id: {control_count, effectiveness_label, score}}.

    Looks at outbound ``mitigated_by`` edges from each risk, keeps only
    targets whose asset type is ``control``, and averages their
    ``metadata.effectiveness`` weight.  Non-control mitigators (policies,
    processes, etc.) are counted in ``mitigator_count`` but not scored.
    """
    if not risk_ids:
        return {}
    rows = await pool.fetch(
        """SELECT r.source_asset_id AS risk_id,
                  a.type::text       AS target_type,
                  a.metadata         AS target_meta
           FROM relationships r
           JOIN assets a ON a.id = r.target_asset_id
           WHERE r.source_asset_id = ANY($1::uuid[])
             AND r.relationship_type = 'mitigated_by'""",
        risk_ids,
    )

    by_risk: dict[UUID, dict] = {
        rid: {"control_count": 0, "mitigator_count": 0, "score_sum": 0.0}
        for rid in risk_ids
    }
    for row in rows:
        bucket = by_risk[row["risk_id"]]
        bucket["mitigator_count"] += 1
        if row["target_type"] != "control":
            continue
        meta = row["target_meta"]
        if isinstance(meta, str):
            meta = json.loads(meta or "{}") or {}
        elif not meta:
            meta = {}
        eff = meta.get("effectiveness")
        if eff not in _EFFECTIVENESS_WEIGHT:
            continue
        bucket["control_count"] += 1
        bucket["score_sum"] += _EFFECTIVENESS_WEIGHT[eff]

    result: dict[UUID, dict] = {}
    for rid, b in by_risk.items():
        score = (b["score_sum"] / b["control_count"]) if b["control_count"] else None
        result[rid] = {
            "control_count": b["control_count"],
            "mitigator_count": b["mitigator_count"],
            "score": round(score, 2) if score is not None else None,
            "effectiveness_label": _effectiveness_label(score, b["control_count"]),
        }
    return result


async def get_risk_register(
    pool: asyncpg.Pool,
    *,
    category: str | None = None,
    treatment: str | None = None,
    effectiveness: str | None = None,
    owner: str | None = None,
    overdue: bool = False,
    likelihood_filter: str | None = None,
    impact_filter: str | None = None,
    sort: str = "score",
    order: str = "desc",
) -> list[dict]:
    """Return all active risks with computed fields, supporting filters and sorting."""
    rows = await pool.fetch(
        """
        SELECT a.id, a.name, a.description, a.status,
               COALESCE(o.name, a.owner) AS owner, a.metadata
        FROM assets a LEFT JOIN assets o ON o.id = a.owner_id
        WHERE a.type = 'risk' AND a.status = 'active'
        """
    )

    today = datetime.now(UTC).date()
    risks = []
    for row in rows:
        meta = _parse_meta(row)
        score = compute_risk_score(meta.get("likelihood"), meta.get("impact"))

        review_due = meta.get("review_date") or meta.get("last_reviewed")
        is_overdue = False
        if review_due:
            try:
                from datetime import date as date_type
                if isinstance(review_due, str):
                    due_date = date_type.fromisoformat(review_due)
                else:
                    due_date = review_due
                is_overdue = due_date < today
            except (ValueError, TypeError):
                pass

        risk = {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"] or "",
            "owner": row["owner"],
            "score": score or 0,
            "color": score_color(score) if score else "low",
            "likelihood": meta.get("likelihood", ""),
            "impact": meta.get("impact", ""),
            "risk_category": meta.get("risk_category", ""),
            "treatment": meta.get("treatment", ""),
            "control_effectiveness": meta.get("control_effectiveness", ""),
            "inherent_risk_score": meta.get("inherent_risk_score"),
            "residual_risk_score": meta.get("residual_risk_score"),
            "treatment_plan": meta.get("treatment_plan", ""),
            "review_date": meta.get("review_date", ""),
            "last_reviewed": meta.get("last_reviewed", ""),
            "is_overdue": is_overdue,
        }

        # Apply filters
        if category and risk["risk_category"] != category:
            continue
        if treatment and risk["treatment"] != treatment:
            continue
        if effectiveness and risk["control_effectiveness"] != effectiveness:
            continue
        if owner and owner.lower() not in (risk["owner"] or "").lower():
            continue
        if overdue and not risk["is_overdue"]:
            continue
        if likelihood_filter and risk["likelihood"] != likelihood_filter:
            continue
        if impact_filter and risk["impact"] != impact_filter:
            continue

        risks.append(risk)

    # Control-effectiveness rollup across all mitigated_by edges.
    rollup = await get_risk_control_rollup(pool, [r["id"] for r in risks])
    for r in risks:
        r["control_rollup"] = rollup.get(r["id"], {
            "control_count": 0,
            "mitigator_count": 0,
            "score": None,
            "effectiveness_label": "none",
        })

    # Sort
    sort_key = sort if sort in ("score", "name", "risk_category", "treatment", "owner") else "score"
    reverse = order == "desc"
    risks.sort(key=lambda r: (r.get(sort_key) or ""), reverse=reverse)

    return risks


async def capture_risk_snapshot(pool: asyncpg.Pool, for_date=None) -> dict:
    """Write today's risk counts to risk_snapshots (idempotent: one row per date).

    Returns the snapshot row that's now in the table.
    """
    from datetime import date as date_type
    snap_date = for_date or date_type.today()
    summary = await get_risk_summary(pool)
    by_sev = summary["by_severity"]
    await pool.execute(
        """INSERT INTO risk_snapshots
               (snapshot_date, total, critical, high, medium, low, overdue, no_treatment)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
           ON CONFLICT (snapshot_date) DO UPDATE SET
               total = EXCLUDED.total,
               critical = EXCLUDED.critical,
               high = EXCLUDED.high,
               medium = EXCLUDED.medium,
               low = EXCLUDED.low,
               overdue = EXCLUDED.overdue,
               no_treatment = EXCLUDED.no_treatment,
               created_at = now()""",
        snap_date,
        summary["total"],
        by_sev["critical"],
        by_sev["high"],
        by_sev["medium"],
        by_sev["low"],
        summary["overdue"],
        summary["no_treatment"],
    )
    return {
        "snapshot_date": snap_date,
        "total": summary["total"],
        **by_sev,
        "overdue": summary["overdue"],
        "no_treatment": summary["no_treatment"],
    }


async def get_severity_trend(pool: asyncpg.Pool) -> dict:
    """Return current counts plus deltas vs. the previous snapshot (if any).

    Shape: {"current": {..}, "prior": {..}|None, "deltas": {critical: int, ...}}.
    The 'current' counts come from live data so they stay fresh between
    nightly captures; deltas compare against the most recent snapshot row
    with a date < today.
    """
    from datetime import date as date_type
    summary = await get_risk_summary(pool)
    by_sev = summary["by_severity"]
    current = {
        "total": summary["total"],
        "critical": by_sev["critical"],
        "high": by_sev["high"],
        "medium": by_sev["medium"],
        "low": by_sev["low"],
    }
    prior_row = await pool.fetchrow(
        """SELECT * FROM risk_snapshots
           WHERE snapshot_date < $1
           ORDER BY snapshot_date DESC LIMIT 1""",
        date_type.today(),
    )
    if not prior_row:
        return {"current": current, "prior": None, "deltas": {}}
    prior = {
        k: prior_row[k]
        for k in ("total", "critical", "high", "medium", "low")
    }
    deltas = {k: current[k] - prior[k] for k in current}
    return {
        "current": current,
        "prior": {**prior, "snapshot_date": prior_row["snapshot_date"]},
        "deltas": deltas,
    }


async def bulk_update_risks(
    pool: asyncpg.Pool,
    risk_ids: list[UUID],
    *,
    treatment: str | None = None,
    owner_id: UUID | None = None,
    review_date: str | None = None,
    updated_by: UUID | None = None,
) -> list[UUID]:
    """Update non-empty fields on the given risks. Returns ids actually updated.

    ``treatment`` and ``review_date`` live in metadata JSON; ``owner_id`` is
    a column on assets.  Each updated asset keeps its other metadata keys
    intact.
    """
    if not risk_ids:
        return []
    if treatment is None and owner_id is None and review_date is None:
        return []

    updated: list[UUID] = []
    async with pool.acquire() as conn:
        async with conn.transaction():
            for rid in risk_ids:
                row = await conn.fetchrow(
                    "SELECT metadata FROM assets WHERE id = $1 AND type = 'risk'",
                    rid,
                )
                if not row:
                    continue
                meta = row["metadata"]
                if isinstance(meta, str):
                    meta = json.loads(meta) or {}
                elif meta is None:
                    meta = {}
                else:
                    meta = dict(meta)

                changed = False
                if treatment is not None:
                    meta["treatment"] = treatment
                    changed = True
                if review_date is not None:
                    meta["review_date"] = review_date
                    changed = True

                if changed:
                    await conn.execute(
                        """UPDATE assets
                             SET metadata = $1::jsonb, updated_at = now(),
                                 updated_by = COALESCE($2, updated_by)
                             WHERE id = $3""",
                        json.dumps(meta),
                        updated_by,
                        rid,
                    )
                if owner_id is not None:
                    await conn.execute(
                        """UPDATE assets
                             SET owner_id = $1, updated_at = now(),
                                 updated_by = COALESCE($2, updated_by)
                             WHERE id = $3""",
                        owner_id,
                        updated_by,
                        rid,
                    )
                    changed = True
                if changed:
                    updated.append(rid)
    return updated


async def get_risk_summary(pool: asyncpg.Pool) -> dict:
    """Return summary statistics for active risks."""
    rows = await pool.fetch(
        """
        SELECT a.metadata FROM assets a
        WHERE a.type = 'risk' AND a.status = 'active'
        """
    )

    today = datetime.now(UTC).date()
    total = 0
    by_severity = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    overdue_count = 0
    no_treatment = 0

    for row in rows:
        meta = _parse_meta(row)
        score = compute_risk_score(meta.get("likelihood"), meta.get("impact"))
        if score is None:
            continue
        total += 1
        color = score_color(score)
        by_severity[color] += 1

        if not meta.get("treatment"):
            no_treatment += 1

        review_due = meta.get("review_date") or meta.get("last_reviewed")
        if review_due:
            try:
                from datetime import date as date_type
                if isinstance(review_due, str):
                    due_date = date_type.fromisoformat(review_due)
                else:
                    due_date = review_due
                if due_date < today:
                    overdue_count += 1
            except (ValueError, TypeError):
                pass

    return {
        "total": total,
        "by_severity": by_severity,
        "overdue": overdue_count,
        "no_treatment": no_treatment,
    }
