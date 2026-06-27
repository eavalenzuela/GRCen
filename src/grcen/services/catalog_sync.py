"""Sync an external controls catalog into GRCen's asset graph.

The controls catalog — frameworks, the requirements under each, and the
controls that satisfy those requirements — is owned by an external
system-of-record (autocomply). GRCen is a *consumer*: this service projects a
catalog export into GRCen assets and relationships so the ``/frameworks``
coverage dashboard lights up, while leaving the org-graph layer a human wires
up (owners, the systems a control protects, audits) untouched.

The same projection also powers GRCen's bundled *content packs* (see
``services/content_packs.py``), which ship ready-to-install catalogs so a fresh
org isn't stranded on an empty register.

See ``GRCEN_CATALOG_EXPORT.md`` in the autocomply repo for the export contract
this consumes.

Mapping to the graph (the edges ``framework_service`` keys off):

    framework            → asset(type=framework)
    requirement          → asset(type=requirement)   parent_of  ← framework
    control              → asset(type=control)        satisfies  → requirement
    crosswalk            → requirement --cross_maps--> requirement  (cross-framework)

The optional top-level ``crosswalks`` list maps one requirement to an
*equivalent* requirement in another framework, giving GRCen a home for the
relationship/confidence the contract previously could only stash in a control's
``metadata.crosswalk``. Both endpoints must be requirements present in the same
catalog (resolved in-run, like ``control.satisfies``).

Idempotency: every synced asset/relationship carries ``(source, source_ref)``.
A stable ``source_ref`` is derived from the catalog's own refs, so re-running
upserts in place — no duplicates. Stale synced *relationships* (mappings
removed upstream) are pruned on every run. Stale synced *assets* are only
deleted when ``prune=True``, because deleting an asset cascades to any
human-authored edges hanging off it; by default they're reported as stale and
left in place.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from uuid import UUID

import asyncpg

from grcen.services import organization_service

DEFAULT_SOURCE = "autocomply"

# Crosswalk relationship vocabulary (mirrors the contract's metadata.crosswalk
# values). ``relationship`` is optional on a crosswalk; absent ⇒ "related".
CROSSWALK_RELATIONSHIPS = frozenset(
    {"equivalent", "superset", "subset", "partial", "related"}
)


@dataclass
class CatalogSyncResult:
    frameworks: int = 0
    requirements: int = 0
    controls: int = 0
    crosswalks: int = 0
    assets_created: int = 0
    assets_updated: int = 0
    edges_created: int = 0
    edges_updated: int = 0
    edges_pruned: int = 0
    assets_pruned: int = 0
    # source_refs of synced assets no longer present in the catalog. Deleted
    # when prune=True; otherwise left in place and surfaced here.
    stale_assets: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    dry_run: bool = False


def validate_catalog(catalog: object) -> list[str]:
    """Structural validation. Returns a list of human-readable errors."""
    errors: list[str] = []
    if not isinstance(catalog, dict):
        return ["catalog must be a JSON object"]

    frameworks = catalog.get("frameworks")
    if not isinstance(frameworks, list):
        errors.append("'frameworks' must be a list")
        frameworks = []
    controls = catalog.get("controls", [])
    if not isinstance(controls, list):
        errors.append("'controls' must be a list")
        controls = []

    fw_refs: set[str] = set()
    req_refs: set[str] = set()
    for i, fw in enumerate(frameworks):
        if not isinstance(fw, dict):
            errors.append(f"framework[{i}] must be an object")
            continue
        ref = fw.get("ref")
        if not ref:
            errors.append(f"framework[{i}] missing 'ref'")
        elif ref in fw_refs:
            errors.append(f"duplicate framework ref '{ref}'")
        else:
            fw_refs.add(ref)
        if not fw.get("name"):
            errors.append(f"framework '{ref or i}' missing 'name'")
        reqs = fw.get("requirements", [])
        if not isinstance(reqs, list):
            errors.append(f"framework '{ref or i}' requirements must be a list")
            continue
        for j, req in enumerate(reqs):
            if not isinstance(req, dict):
                errors.append(f"framework '{ref or i}' requirement[{j}] must be an object")
                continue
            rref = req.get("ref")
            if not rref:
                errors.append(f"framework '{ref or i}' requirement[{j}] missing 'ref'")
            elif rref in req_refs:
                errors.append(f"duplicate requirement ref '{rref}'")
            else:
                req_refs.add(rref)
            if not req.get("name"):
                errors.append(f"requirement '{rref or j}' missing 'name'")

    ctrl_refs: set[str] = set()
    for i, ctrl in enumerate(controls):
        if not isinstance(ctrl, dict):
            errors.append(f"control[{i}] must be an object")
            continue
        ref = ctrl.get("ref")
        if not ref:
            errors.append(f"control[{i}] missing 'ref'")
        elif ref in ctrl_refs:
            errors.append(f"duplicate control ref '{ref}'")
        else:
            ctrl_refs.add(ref)
        if not ctrl.get("name"):
            errors.append(f"control '{ref or i}' missing 'name'")
        for tref in ctrl.get("satisfies", []) or []:
            if tref not in req_refs:
                errors.append(
                    f"control '{ref or i}' satisfies unknown requirement '{tref}'"
                )

    crosswalks = catalog.get("crosswalks", [])
    if not isinstance(crosswalks, list):
        errors.append("'crosswalks' must be a list")
        crosswalks = []
    seen_pairs: set[tuple[str, str]] = set()
    for i, x in enumerate(crosswalks):
        if not isinstance(x, dict):
            errors.append(f"crosswalk[{i}] must be an object")
            continue
        frm, to = x.get("from"), x.get("to")
        if not frm or not to:
            errors.append(f"crosswalk[{i}] needs both 'from' and 'to'")
            continue
        if frm == to:
            errors.append(f"crosswalk[{i}] maps requirement '{frm}' to itself")
        if frm not in req_refs:
            errors.append(f"crosswalk[{i}] 'from' is unknown requirement '{frm}'")
        if to not in req_refs:
            errors.append(f"crosswalk[{i}] 'to' is unknown requirement '{to}'")
        rel = x.get("relationship")
        if rel is not None and rel not in CROSSWALK_RELATIONSHIPS:
            errors.append(
                f"crosswalk[{i}] relationship '{rel}' not one of "
                f"{sorted(CROSSWALK_RELATIONSHIPS)}"
            )
        # Crosswalks are symmetric: A↔B and B↔A are the same mapping.
        pair = (frm, to) if frm <= to else (to, frm)
        if pair in seen_pairs:
            errors.append(
                f"crosswalk[{i}] duplicates the mapping between '{frm}' and '{to}'"
            )
        else:
            seen_pairs.add(pair)
    return errors


_ASSET_UPSERT = """
    INSERT INTO assets (type, name, description, status, metadata,
                        organization_id, source, source_ref)
    VALUES ($1, $2, $3, 'active', $4, $5, $6, $7)
    ON CONFLICT (organization_id, source, source_ref) WHERE source IS NOT NULL
    DO UPDATE SET name = EXCLUDED.name,
                  description = EXCLUDED.description,
                  metadata = EXCLUDED.metadata,
                  updated_at = now()
    RETURNING id, (xmax = 0) AS inserted
"""

_EDGE_UPSERT = """
    INSERT INTO relationships (source_asset_id, target_asset_id,
                              relationship_type, description,
                              organization_id, source, source_ref)
    VALUES ($1, $2, $3, $4, $5, $6, $7)
    ON CONFLICT (organization_id, source, source_ref) WHERE source IS NOT NULL
    DO UPDATE SET source_asset_id = EXCLUDED.source_asset_id,
                  target_asset_id = EXCLUDED.target_asset_id,
                  relationship_type = EXCLUDED.relationship_type,
                  description = EXCLUDED.description,
                  updated_at = now()
    RETURNING (xmax = 0) AS inserted
"""


async def sync_catalog(
    pool: asyncpg.Pool,
    catalog: dict,
    *,
    organization_id: UUID | None = None,
    source: str = DEFAULT_SOURCE,
    dry_run: bool = False,
    prune: bool = False,
) -> CatalogSyncResult:
    """Project ``catalog`` into the asset graph for one organization.

    Runs in a single transaction; on ``dry_run`` the transaction is rolled
    back after counts are tallied, so the returned result reflects exactly what
    a real run would do without persisting anything.
    """
    result = CatalogSyncResult(dry_run=dry_run)
    errors = validate_catalog(catalog)
    if errors:
        result.errors = errors
        return result

    org = organization_id or await organization_service.get_default_org_id(pool)

    async with pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        try:
            # ids of every asset we touch, keyed by source_ref, so edges can
            # resolve endpoints without a second lookup.
            asset_ids: dict[str, UUID] = {}
            seen_asset_refs: list[str] = []
            seen_edge_refs: list[str] = []

            async def upsert_asset(atype, name, description, metadata, sref):
                row = await conn.fetchrow(
                    _ASSET_UPSERT, atype, name[:255], description or "",
                    json.dumps(metadata), org, source, sref,
                )
                asset_ids[sref] = row["id"]
                seen_asset_refs.append(sref)
                if row["inserted"]:
                    result.assets_created += 1
                else:
                    result.assets_updated += 1
                return row["id"]

            async def upsert_edge(src_id, tgt_id, rtype, sref, description=""):
                row = await conn.fetchrow(
                    _EDGE_UPSERT, src_id, tgt_id, rtype, description, org, source, sref,
                )
                seen_edge_refs.append(sref)
                if row["inserted"]:
                    result.edges_created += 1
                else:
                    result.edges_updated += 1

            for fw in catalog["frameworks"]:
                fw_ref = fw["ref"]
                fw_sref = f"framework:{fw_ref}"
                fw_meta = dict(fw.get("metadata") or {})
                fw_id = await upsert_asset(
                    "framework", fw["name"], fw.get("description"), fw_meta, fw_sref
                )
                result.frameworks += 1

                for req in fw.get("requirements", []):
                    req_ref = req["ref"]
                    req_sref = f"requirement:{req_ref}"
                    req_meta = dict(req.get("metadata") or {})
                    # Convenience fields the requirement custom-field set knows
                    # about, derived so the export doesn't have to repeat them.
                    req_meta.setdefault("framework", fw["name"])
                    if req.get("reference_id"):
                        req_meta.setdefault("reference_id", req["reference_id"])
                    if req.get("category"):
                        req_meta.setdefault("category", req["category"])
                    req_id = await upsert_asset(
                        "requirement", req["name"], req.get("description"),
                        req_meta, req_sref,
                    )
                    result.requirements += 1
                    # framework --parent_of--> requirement
                    await upsert_edge(
                        fw_id, req_id, "parent_of",
                        f"parent_of:{fw_ref}:{req_ref}",
                    )

            for ctrl in catalog.get("controls", []):
                ctrl_ref = ctrl["ref"]
                ctrl_sref = f"control:{ctrl_ref}"
                ctrl_meta = dict(ctrl.get("metadata") or {})
                ctrl_id = await upsert_asset(
                    "control", ctrl["name"], ctrl.get("description"),
                    ctrl_meta, ctrl_sref,
                )
                result.controls += 1
                for req_ref in ctrl.get("satisfies", []) or []:
                    tgt_id = asset_ids.get(f"requirement:{req_ref}")
                    if tgt_id is None:
                        # validate_catalog already rejected dangling refs, but
                        # guard against a requirement that failed to upsert.
                        continue
                    # control --satisfies--> requirement
                    await upsert_edge(
                        ctrl_id, tgt_id, "satisfies",
                        f"satisfies:{ctrl_ref}:{req_ref}",
                    )

            for x in catalog.get("crosswalks", []) or []:
                frm, to = x["from"], x["to"]
                src_id = asset_ids.get(f"requirement:{frm}")
                tgt_id = asset_ids.get(f"requirement:{to}")
                if src_id is None or tgt_id is None:
                    # validate_catalog already rejected dangling refs; guard
                    # against an endpoint that failed to upsert.
                    continue
                rel = x.get("relationship") or "related"
                bits = [rel]
                if x.get("confidence"):
                    bits.append(f"confidence: {x['confidence']}")
                if x.get("note"):
                    bits.append(str(x["note"]))
                # requirement --cross_maps--> requirement (cross-framework)
                await upsert_edge(
                    src_id, tgt_id, "cross_maps",
                    f"cross_maps:{frm}:{to}", " · ".join(bits),
                )
                result.crosswalks += 1

            # Prune synced edges that are no longer in the catalog. Safe: only
            # touches rows with our source tag, never human-authored edges.
            pruned = await conn.execute(
                """DELETE FROM relationships
                   WHERE organization_id = $1 AND source = $2
                     AND source_ref <> ALL($3::text[])""",
                org, source, seen_edge_refs,
            )
            result.edges_pruned = int(pruned.split()[-1])

            # Assets orphaned upstream. Deleting cascades to human edges, so
            # only delete when explicitly asked; otherwise just report them.
            stale = await conn.fetch(
                """SELECT source_ref FROM assets
                   WHERE organization_id = $1 AND source = $2
                     AND source_ref <> ALL($3::text[])""",
                org, source, seen_asset_refs,
            )
            result.stale_assets = [r["source_ref"] for r in stale]
            if prune and result.stale_assets:
                deleted = await conn.execute(
                    """DELETE FROM assets
                       WHERE organization_id = $1 AND source = $2
                         AND source_ref <> ALL($3::text[])""",
                    org, source, seen_asset_refs,
                )
                result.assets_pruned = int(deleted.split()[-1])

            if dry_run:
                await tx.rollback()
            else:
                await tx.commit()
        except Exception:
            await tx.rollback()
            raise

    return result
