"""Sync CSPM (cloud security posture) misconfigurations into the graph (I4 2nd wave).

A CSPM tool (Wiz, Prisma Cloud, AWS Security Hub, Steampipe) continuously checks
cloud resources against benchmarks (CIS, SOC 2, …) and emits *misconfiguration*
findings. GRCen consumes them the same way it consumes uvt vuln findings
(``vuln_sync``): each becomes a Finding asset linked ``System --affected_by-->
Finding`` — but the affected asset is a **cloud resource**, resolved by its ARN /
resource id against the System's ``cloud_resource_id`` / ``external_id`` (exactly
what the CMDB / cloud-inventory sync, I3, populates). So a "S3 bucket public"
finding lands next to the very System row the inventory created, and — because
``risk_aggregation`` (I4) rolls up *all* open findings — feeds the risk register
with no extra wiring.

Idempotent via ``external_sync`` under ``source='cspm:<vendor>'``: re-running
upserts in place, prunes the affects edge when a finding moves or clears, and
archives a finding as ``remediated`` when it drops out of the export
(``resolve_missing``, default on). Runs in one transaction; ``dry_run`` rolls back.

## Export contract

    {
      "source": "cspm:wiz",          // optional; default "cspm". The source tag.
      "findings": [
        {
          "finding_id": "WIZ-00123", // REQUIRED, stable — the upsert key
          "title": "S3 bucket allows public read",  // REQUIRED
          "description": "...",
          "severity": "high",        // critical|high|medium|low
          "status": "open",          // → finding_status (open|remediated|…)
          "check_id": "CIS-2.1.5",   // the CSPM rule/benchmark check
          "framework": "CIS AWS 1.4",
          "provider": "aws",         // aws|azure|gcp
          "remediation": "Enable Block Public Access on the bucket.",
          "first_seen": "2024-01-01", "last_seen": "2024-06-01",
          "resource": {              // the affected cloud resource
            "resource_id": "arn:aws:s3:::my-bucket",  // vs System.cloud_resource_id / external_id
            "name": "my-bucket",
            "type": "AWS::S3::Bucket",
            "account": "123456789012",
            "region": "us-east-1"
          }
        }
      ]
    }
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

import asyncpg

from grcen.services import external_sync, organization_service

DEFAULT_SOURCE = "cspm"

# Finding lifecycle states that count as resolved (a dropped-out finding).
_NON_OPEN = {"remediated", "verified", "risk_accepted", "closed"}

# System/Device metadata keys the resource_id is matched against, in priority order.
_RESOURCE_KEYS = ("cloud_resource_id", "external_id")


@dataclass
class CspmSyncResult:
    source: str = DEFAULT_SOURCE
    findings_created: int = 0
    findings_updated: int = 0
    findings_resolved: int = 0
    affects_edges: int = 0
    edges_pruned: int = 0
    resolved: list[str] = field(default_factory=list)           # finding source_refs
    unresolved_assets: list[str] = field(default_factory=list)  # finding_ids w/o a System
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    dry_run: bool = False


def validate_export(export: object) -> list[str]:
    if not isinstance(export, dict):
        return ["export must be a JSON object"]
    findings = export.get("findings")
    if not isinstance(findings, list):
        return ["'findings' must be a list"]
    errors: list[str] = []
    seen: set[str] = set()
    for i, f in enumerate(findings):
        if not isinstance(f, dict):
            errors.append(f"finding[{i}] must be an object")
            continue
        fid = f.get("finding_id")
        if not fid:
            errors.append(f"finding[{i}] missing 'finding_id'")
        elif fid in seen:
            errors.append(f"duplicate finding_id '{fid}'")
        else:
            seen.add(fid)
        if not f.get("title"):
            errors.append(f"finding '{fid or i}' missing 'title'")
    return errors


def _finding_metadata(f: dict) -> dict:
    meta: dict = {"source": "automated", "finding_status": f.get("status") or "open"}
    for key in ("severity", "first_seen", "last_seen"):
        if f.get(key) not in (None, ""):
            meta[key] = f[key]
    if f.get("check_id"):
        meta["scan_id"] = f["check_id"]
    if f.get("provider"):
        meta["scanner"] = f["provider"]
    if f.get("first_seen"):
        meta.setdefault("identified_date", f["first_seen"])
    resource = f.get("resource") or {}
    display = resource.get("name") or resource.get("resource_id")
    if display:
        meta["affected_asset"] = display
    # Informational extras (persisted, not labelled custom fields).
    if f.get("framework"):
        meta["cspm_framework"] = f["framework"]
    if resource.get("account"):
        meta["cloud_account"] = resource["account"]
    return meta


def _description(f: dict) -> str:
    resource = f.get("resource") or {}
    bits = [f.get("description") or ""]
    if f.get("check_id"):
        bits.append(f"Check: {f['check_id']}")
    if f.get("framework"):
        bits.append(f"Framework: {f['framework']}")
    if resource.get("resource_id"):
        bits.append(f"Resource: {resource['resource_id']}")
    if resource.get("type"):
        bits.append(f"Type: {resource['type']}")
    if f.get("remediation"):
        bits.append(f"Remediation: {f['remediation']}")
    return "\n".join(b for b in bits if b).strip()


async def _resolve_affected(conn, organization_id: UUID, resource: dict) -> UUID | None:
    """Find the System/Device the misconfig is on. Matches the resource_id against
    cloud_resource_id / external_id, then the resource name."""
    rid = resource.get("resource_id")
    if rid:
        for key in _RESOURCE_KEYS:
            row = await conn.fetchrow(
                """SELECT id FROM assets
                   WHERE organization_id = $1 AND type IN ('system', 'device')
                     AND metadata ->> $2 = $3
                   ORDER BY created_at LIMIT 1""",
                organization_id, key, str(rid))
            if row:
                return row["id"]
    name = resource.get("name")
    if name:
        row = await conn.fetchrow(
            """SELECT id FROM assets
               WHERE organization_id = $1 AND type IN ('system', 'device') AND name = $2
               ORDER BY created_at LIMIT 1""",
            organization_id, name)
        if row:
            return row["id"]
    return None


async def sync_findings(
    pool: asyncpg.Pool,
    export: dict,
    *,
    organization_id: UUID | None = None,
    source: str | None = None,
    dry_run: bool = False,
    resolve_missing: bool = True,
    allow_empty: bool = False,
) -> CspmSyncResult:
    """Project a CSPM misconfiguration ``export`` into the asset graph."""
    result = CspmSyncResult(dry_run=dry_run)
    errors = validate_export(export)
    if errors:
        result.errors = errors
        return result

    # An empty export is indistinguishable from "everything is remediated", and
    # the resolve step would archive every open finding this source owns. A
    # scanner that fails auth, times out, or filters to nothing returns exactly
    # this shape, so silently closing the whole finding backlog — a false
    # negative in a compliance tool — has to be asked for explicitly.
    if not export["findings"] and resolve_missing and not allow_empty:
        result.errors.append(
            "export contains no findings; refusing to mark every open finding "
            "as remediated. Pass allow_empty=True if the export is genuinely empty."
        )
        return result

    src = source or export.get("source") or DEFAULT_SOURCE
    result.source = src
    org = organization_id or await organization_service.get_default_org_id(pool)
    findings: list[dict] = export["findings"]

    async with pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        try:
            seen_finding_refs: list[str] = []
            seen_edge_refs: list[str] = []

            for f in findings:
                fid = f["finding_id"]
                sref = f"finding:{fid}"
                r = await external_sync.upsert_external_asset(
                    conn, organization_id=org, source=src, source_ref=sref,
                    asset_type="finding", name=f["title"],
                    description=_description(f), status="active",
                    metadata=_finding_metadata(f))
                seen_finding_refs.append(sref)
                if r.inserted:
                    result.findings_created += 1
                else:
                    result.findings_updated += 1

                resource = f.get("resource") or {}
                if resource:
                    system_id = await _resolve_affected(conn, org, resource)
                    if system_id is not None:
                        esref = f"affects:{fid}"
                        await external_sync.upsert_external_edge(
                            conn, organization_id=org, source=src, source_ref=esref,
                            source_asset_id=system_id, target_asset_id=r.id,
                            relationship_type="affected_by")
                        seen_edge_refs.append(esref)
                        result.affects_edges += 1
                    else:
                        result.unresolved_assets.append(fid)
                        result.warnings.append(
                            f"finding '{fid}' resource "
                            f"'{resource.get('resource_id') or resource.get('name')}' "
                            f"did not match a System; edge skipped")

            result.edges_pruned = await external_sync.prune_stale_edges(
                conn, organization_id=org, source=src, seen_refs=seen_edge_refs)

            stale = await conn.fetch(
                """SELECT id, source_ref FROM assets
                   WHERE organization_id = $1 AND source = $2 AND type = 'finding'
                     AND source_ref <> ALL($3::text[])""",
                org, src, seen_finding_refs)
            result.resolved = [r["source_ref"] for r in stale]
            if resolve_missing and stale:
                updated = await conn.execute(
                    """UPDATE assets
                       SET metadata = jsonb_set(
                               coalesce(metadata, '{}'::jsonb),
                               '{finding_status}', $1::jsonb),
                           status = 'archived', updated_at = now()
                       WHERE id = ANY($2::uuid[]) AND status <> 'archived'""",
                    '"remediated"', [r["id"] for r in stale])
                result.findings_resolved = int(updated.split()[-1])

            if dry_run:
                await tx.rollback()
            else:
                await tx.commit()
        except Exception:
            await tx.rollback()
            raise

    return result
