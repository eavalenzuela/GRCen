#!/usr/bin/env python3
"""Seed the asset graph from the bundled sample CSVs (assets then relationships).

This is the scripted equivalent of walking the `/imports` web UI by hand. It
reuses the same import service the UI calls, so the result is identical to a
manual import — just reproducible, which matters when you need to reset a shared
test instance to a known state between usability-test participants.

Order matters: assets are loaded before relationships, because relationship rows
resolve their endpoints by (name, type). Owner resolution within the asset load
is best-effort (same limitation as the UI importer): an asset whose owner appears
later in the file than the asset itself will be left unowned.

Usage:
    python sample_data/seed_data.py [DATABASE_URL]

Env:
    DATABASE_URL      Postgres DSN (default: postgresql://grcen:grcen@localhost:5432/grcen)
    GRCEN_ORG_SLUG    Target org slug (default: the instance's default org)

Run alerts afterwards with: python sample_data/seed_alerts.py [DATABASE_URL]
"""
import asyncio
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import asyncpg

from grcen.services import import_service, organization_service

HERE = Path(__file__).resolve().parent
DATABASE_URL = sys.argv[1] if len(sys.argv) > 1 else os.getenv(
    "DATABASE_URL", "postgresql://grcen:grcen@localhost:5432/grcen"
)
# import_service wants a plain asyncpg DSN, not the SQLAlchemy "+asyncpg" form.
DATABASE_URL = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")


async def main() -> int:
    pool = await asyncpg.create_pool(DATABASE_URL)
    try:
        org_slug = os.getenv("GRCEN_ORG_SLUG", "").strip()
        org_id = None
        if org_slug:
            org = await organization_service.get_by_slug(pool, org_slug)
            if org is None:
                print(f"Organization '{org_slug}' not found. Run `grcen createorg` first.")
                return 1
            org_id = org.id
        else:
            org_id = await organization_service.get_default_org_id(pool)
        print(f"Seeding into organization_id={org_id}")

        assets_csv = (HERE / "assets.csv").read_text()
        a = await import_service.execute_asset_import(
            pool, assets_csv, "csv", organization_id=org_id
        )
        print(f"Assets: created {a.created}, errors {len(a.errors)}")
        for e in a.errors[:10]:
            print(f"  ! {e}")

        rels_csv = (HERE / "relationships.csv").read_text()
        r = await import_service.execute_relationship_import(
            pool, rels_csv, "csv", organization_id=org_id
        )
        print(f"Relationships: created {r.created}, errors {len(r.errors)}")
        for e in r.errors[:10]:
            print(f"  ! {e}")

        await enrich(pool, org_id)
        return 0
    finally:
        await pool.close()


# Likelihood/impact pairs spread across the 5x5 heatmap so the register and heatmap
# have positioned risks (low → critical) instead of an all-unscored corner. Cycled
# over the seeded risks in name order. Values must match risk_service.*_LEVELS.
_RISK_SPREAD = [
    ("almost_certain", "catastrophic"), ("likely", "major"), ("almost_certain", "major"),
    ("possible", "moderate"), ("likely", "catastrophic"), ("unlikely", "major"),
    ("possible", "major"), ("rare", "moderate"), ("likely", "moderate"),
    ("almost_certain", "moderate"), ("possible", "minor"), ("unlikely", "moderate"),
    ("likely", "minor"), ("rare", "minor"), ("possible", "catastrophic"),
    ("unlikely", "insignificant"),
]
# review_date offsets (days from today): negative = overdue, positive = upcoming.
_REVIEW_OFFSETS = [-45, -10, 30, 90, -20, 60, 180, -5, 120, 15, 250, -30, 45, 200, -15, 300]

# Keyword → tags, matched against each asset's lowercased name + description. Gives
# /tags and the ?tag= filter real data (usability-test Task 9) without hardcoding names.
_TAG_RULES = [
    (("pci", "payment", "billing", "card", "stripe"), ["pci"]),
    (("pii", "gdpr", "privacy", "personal", "customer data", "dsar"), ["gdpr", "pii"]),
    (("portal", "production", "k8s", "kubernetes", "warehouse", "database", "identity"),
     ["crown-jewel"]),
    (("soc 2", "soc2", "iso 27001", "audit"), ["soc2"]),
]


async def enrich(pool: asyncpg.Pool, org_id) -> None:
    """Post-import enrichment: score the seeded risks and tag assets so the heatmap
    and tag filter have data (the bundled CSV carries neither)."""
    from grcen.services.risk_service import compute_risk_score

    risks = await pool.fetch(
        "SELECT id, metadata FROM assets WHERE type = 'risk' AND organization_id = $1 ORDER BY name",
        org_id,
    )
    today = date.today()
    scored = 0
    for i, row in enumerate(risks):
        likelihood, impact = _RISK_SPREAD[i % len(_RISK_SPREAD)]
        meta = row["metadata"]
        meta = json.loads(meta) if isinstance(meta, str) else dict(meta or {})
        meta["likelihood"] = likelihood
        meta["impact"] = impact
        meta["inherent_risk_score"] = compute_risk_score(likelihood, impact)
        meta["review_date"] = (today + timedelta(days=_REVIEW_OFFSETS[i % len(_REVIEW_OFFSETS)])).isoformat()
        await pool.execute(
            "UPDATE assets SET metadata = $2 WHERE id = $1", row["id"], json.dumps(meta)
        )
        scored += 1
    print(f"Enriched: scored {scored} risks (likelihood/impact + review_date)")

    assets = await pool.fetch(
        "SELECT id, name, description FROM assets WHERE organization_id = $1 AND type <> 'answer'",
        org_id,
    )
    tagged = 0
    for row in assets:
        hay = f"{row['name']} {row['description'] or ''}".lower()
        tags: list[str] = []
        for needles, labels in _TAG_RULES:
            if any(n in hay for n in needles):
                tags.extend(t for t in labels if t not in tags)
        if tags:
            await pool.execute("UPDATE assets SET tags = $2 WHERE id = $1", row["id"], tags)
            tagged += 1
    print(f"Enriched: tagged {tagged} assets ({', '.join(sorted({t for _, ls in _TAG_RULES for t in ls}))})")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
