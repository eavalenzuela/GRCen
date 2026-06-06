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
import os
import sys
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

        return 0
    finally:
        await pool.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
