"""Sync a CMDB / cloud-inventory into GRCen's asset graph (roadmap I3).

A CMDB (ServiceNow CMDB, Device42, Snipe-IT) or cloud inventory (AWS Config,
Azure Resource Graph, GCP Asset Inventory) is the system-of-record for what
infrastructure exists. GRCen consumes an inventory projection so Systems and
Devices are authoritative — real hostnames/IPs/resource-ids instead of
hand-typed placeholders — and carry ownership. This is the infrastructure
counterpart to the HRIS People sync; both ride the same ``external_sync`` upsert.

Mapping to the graph:

    system              → asset(type=system)   source_ref system:<ci_id>
    device              → asset(type=device)    source_ref device:<ci_id>
    ci.depends_on[]     → CI --depends_on--> CI  (source_ref depends_on:<from>:<to>)
    ci.owner (name)     → asset.owner_id (matched to a Person/OU by name)

Each asset's ``metadata.external_id`` is set to its CI id (unless the record
carries an explicit ``external_id``), so a uvt/scanner finding (I4) that
references the CI id resolves straight onto the CMDB-synced host — the two
connectors join up without extra plumbing.

Idempotency & lifecycle: every synced row carries ``(source, source_ref)`` so
re-runs upsert in place; stale dependency *edges* are pruned each run. A record
whose ``status`` is decommissioned/retired is archived, and a CI that drops out
of the inventory entirely is archived too (``archive_missing``, default on) —
but only for the CI classes the inventory actually carried (a devices-only sync
never archives Systems). Runs in one transaction; ``dry_run`` rolls back.

## Inventory contract

    {
      "source": "cmdb:servicenow",   // optional; default "cmdb". The source tag.
      "systems": [
        {
          "ci_id": "sys_abc",        // REQUIRED, stable (e.g. ServiceNow sys_id)
          "name": "Prod Web",        // REQUIRED
          "description": "...",
          "hostname": "web01",
          "ip_address": "10.0.0.5",
          "fqdn": "web01.acme.com",
          "external_id": "sys_abc",  // defaults to ci_id
          "cloud_resource_id": "arn:aws:ec2:...:instance/i-0abc",
          "environment": "production",   // production|staging|development|dr
          "hosting": "cloud",            // on_prem|cloud|hybrid|saas
          "provider": "aws", "region": "us-east-1",
          "criticality": "high",         // critical|high|medium|low
          "data_classification": "confidential",
          "url": "https://...",
          "owner": "Ada Lovelace",       // Person/OU name → owner_id
          "status": "active",            // decommissioned/retired → archived
          "depends_on": ["sys_def"]      // CI ids → depends_on edges
        }
      ],
      "devices": [
        {"ci_id": "dev_xyz", "name": "Laptop-123", "device_type": "laptop",
         "manufacturer": "Dell", "model": "XPS 13", "os": "Windows 11",
         "ip_address": "10.0.1.4", "serial_number": "SN123", "location": "London"}
      ]
    }

CI ids must be unique across systems + devices (so ``depends_on`` resolves
unambiguously). Dangling ``depends_on`` and unresolved ``owner`` names are
warnings, not errors.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

import asyncpg

from grcen.services import external_sync, organization_service

DEFAULT_SOURCE = "cmdb"

_SYSTEM_FIELDS = (
    "hostname", "ip_address", "fqdn", "cloud_resource_id", "environment",
    "hosting", "provider", "region", "criticality", "data_classification", "url",
)
_DEVICE_FIELDS = (
    "device_type", "manufacturer", "model", "os", "ip_address",
    "serial_number", "location", "cmdb_link",
)
# Export statuses that map the asset to 'archived' (vs the default 'active').
_ARCHIVED_STATUSES = {"inactive", "decommissioned", "retired", "archived", "disposed"}


@dataclass
class CmdbSyncResult:
    source: str = DEFAULT_SOURCE
    systems_created: int = 0
    systems_updated: int = 0
    systems_archived: int = 0
    devices_created: int = 0
    devices_updated: int = 0
    devices_archived: int = 0
    dependency_edges: int = 0
    edges_pruned: int = 0
    owners_linked: int = 0
    stale: list[str] = field(default_factory=list)      # decommissioned CI source_refs
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    dry_run: bool = False


def validate_inventory(inventory: object) -> list[str]:
    """Structural validation. Returns human-readable errors (empty = ok)."""
    if not isinstance(inventory, dict):
        return ["inventory must be a JSON object"]
    systems = inventory.get("systems", [])
    devices = inventory.get("devices", [])
    if not isinstance(systems, list):
        return ["'systems' must be a list"]
    if not isinstance(devices, list):
        return ["'devices' must be a list"]
    if not systems and not devices:
        return ["inventory must contain 'systems' and/or 'devices'"]

    errors: list[str] = []
    seen: set[str] = set()
    for label, items in (("system", systems), ("device", devices)):
        for i, ci in enumerate(items):
            if not isinstance(ci, dict):
                errors.append(f"{label}[{i}] must be an object")
                continue
            cid = ci.get("ci_id")
            if not cid:
                errors.append(f"{label}[{i}] missing 'ci_id'")
            elif cid in seen:
                errors.append(f"duplicate ci_id '{cid}'")
            else:
                seen.add(cid)
            if not ci.get("name"):
                errors.append(f"{label} '{cid or i}' missing 'name'")
    return errors


def _metadata(ci: dict, fields: tuple[str, ...]) -> dict:
    meta = {"external_id": ci.get("external_id") or ci["ci_id"]}
    for key in fields:
        val = ci.get(key)
        if val not in (None, ""):
            meta[key] = val
    return meta


def _asset_status(ci: dict) -> str:
    return "archived" if str(ci.get("status") or "active").lower() in _ARCHIVED_STATUSES else "active"


async def _link_owner(conn, organization_id: UUID, asset_id: UUID, ci: dict,
                      result: CmdbSyncResult) -> None:
    owner = ci.get("owner")
    if not owner:
        return
    row = await conn.fetchrow(
        """SELECT id FROM assets
           WHERE organization_id = $1 AND type IN ('person', 'organizational_unit')
             AND name = $2
           ORDER BY created_at LIMIT 1""",
        organization_id, owner,
    )
    if row:
        await conn.execute(
            "UPDATE assets SET owner_id = $1, updated_at = now() WHERE id = $2",
            row["id"], asset_id,
        )
        result.owners_linked += 1
    else:
        result.warnings.append(
            f"CI '{ci['ci_id']}' owner '{owner}' not found; ownership left unset"
        )


async def _archive_stale(conn, organization_id: UUID, source: str,
                         asset_type: str, seen_refs: list[str],
                         archive: bool) -> tuple[int, list[str]]:
    """Return (archived_count, stale_source_refs). Only writes when ``archive``."""
    stale = await conn.fetch(
        """SELECT id, source_ref FROM assets
           WHERE organization_id = $1 AND source = $2 AND type = $3
             AND source_ref <> ALL($4::text[])""",
        organization_id, source, asset_type, seen_refs,
    )
    refs = [r["source_ref"] for r in stale]
    count = 0
    if archive and stale:
        updated = await conn.execute(
            """UPDATE assets SET status = 'archived', updated_at = now()
               WHERE id = ANY($1::uuid[]) AND status <> 'archived'""",
            [r["id"] for r in stale],
        )
        count = int(updated.split()[-1])
    return count, refs


async def sync_inventory(
    pool: asyncpg.Pool,
    inventory: dict,
    *,
    organization_id: UUID | None = None,
    source: str | None = None,
    dry_run: bool = False,
    archive_missing: bool = True,
) -> CmdbSyncResult:
    """Project a CMDB/cloud ``inventory`` into the asset graph for one org."""
    result = CmdbSyncResult(dry_run=dry_run)
    errors = validate_inventory(inventory)
    if errors:
        result.errors = errors
        return result

    src = source or inventory.get("source") or DEFAULT_SOURCE
    result.source = src
    org = organization_id or await organization_service.get_default_org_id(pool)

    has_systems = isinstance(inventory.get("systems"), list)
    has_devices = isinstance(inventory.get("devices"), list)
    systems: list[dict] = inventory.get("systems") or []
    devices: list[dict] = inventory.get("devices") or []

    async with pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        try:
            ci_to_asset: dict[str, UUID] = {}
            seen_system_refs: list[str] = []
            seen_device_refs: list[str] = []
            seen_edge_refs: list[str] = []

            for ci in systems:
                cid = ci["ci_id"]
                sref = f"system:{cid}"
                r = await external_sync.upsert_external_asset(
                    conn, organization_id=org, source=src, source_ref=sref,
                    asset_type="system", name=ci["name"],
                    description=ci.get("description"), status=_asset_status(ci),
                    metadata=_metadata(ci, _SYSTEM_FIELDS),
                )
                ci_to_asset[cid] = r.id
                seen_system_refs.append(sref)
                if r.inserted:
                    result.systems_created += 1
                else:
                    result.systems_updated += 1
                await _link_owner(conn, org, r.id, ci, result)

            for ci in devices:
                cid = ci["ci_id"]
                sref = f"device:{cid}"
                r = await external_sync.upsert_external_asset(
                    conn, organization_id=org, source=src, source_ref=sref,
                    asset_type="device", name=ci["name"],
                    description=ci.get("description"), status=_asset_status(ci),
                    metadata=_metadata(ci, _DEVICE_FIELDS),
                )
                ci_to_asset[cid] = r.id
                seen_device_refs.append(sref)
                if r.inserted:
                    result.devices_created += 1
                else:
                    result.devices_updated += 1
                await _link_owner(conn, org, r.id, ci, result)

            # Dependency edges (either CI class may declare depends_on).
            for ci in [*systems, *devices]:
                cid = ci["ci_id"]
                for dep in ci.get("depends_on", []) or []:
                    if dep == cid:
                        result.warnings.append(f"CI '{cid}' depends on itself; skipped")
                        continue
                    tgt = ci_to_asset.get(dep)
                    if tgt is None:
                        result.warnings.append(
                            f"CI '{cid}' depends_on '{dep}' not in inventory; edge skipped"
                        )
                        continue
                    esref = f"depends_on:{cid}:{dep}"
                    await external_sync.upsert_external_edge(
                        conn, organization_id=org, source=src, source_ref=esref,
                        source_asset_id=ci_to_asset[cid], target_asset_id=tgt,
                        relationship_type="depends_on",
                    )
                    seen_edge_refs.append(esref)
                    result.dependency_edges += 1

            result.edges_pruned = await external_sync.prune_stale_edges(
                conn, organization_id=org, source=src, seen_refs=seen_edge_refs,
            )

            # Decommissioned CIs (dropped out of the inventory). Only archive a
            # CI class the inventory actually carried this run.
            stale: list[str] = []
            if has_systems:
                result.systems_archived, refs = await _archive_stale(
                    conn, org, src, "system", seen_system_refs, archive_missing)
                stale += refs
            if has_devices:
                result.devices_archived, refs = await _archive_stale(
                    conn, org, src, "device", seen_device_refs, archive_missing)
                stale += refs
            result.stale = stale

            if dry_run:
                await tx.rollback()
            else:
                await tx.commit()
        except Exception:
            await tx.rollback()
            raise

    return result
