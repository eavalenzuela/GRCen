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
    import json

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
    import json

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
