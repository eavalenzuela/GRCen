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

    # Sort
    sort_key = sort if sort in ("score", "name", "risk_category", "treatment", "owner") else "score"
    reverse = order == "desc"
    risks.sort(key=lambda r: (r.get(sort_key) or ""), reverse=reverse)

    return risks


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
