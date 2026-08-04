"""Sync a vulnerability-findings export into GRCen's graph (roadmap I4).

The sibling **uvt** (Universal Vulnerability Tracker) is the system-of-record for
vulnerabilities — it aggregates and de-duplicates raw scanner output (Qualys,
Nessus/Tenable, Trivy, …). GRCen consumes uvt's *triaged* findings and adds the
org-graph context: each Finding is linked to the System it affects, so a
vulnerability shows up next to its owner, its controls, and its risk. (Shops not
running uvt can point a per-scanner exporter at the same contract — the ``source``
tag just becomes ``qualys``/``tenable`` instead of ``uvt``.)

Mapping to the graph:

    finding             → asset(type=finding)   source_ref finding:<finding_id>
    finding.affects     → System --affected_by--> Finding   (resolved by host/id)

Idempotency & lifecycle: every synced Finding carries ``(source, source_ref)`` so
re-running upserts in place. The affected-System is *matched* against existing
assets (external_id → hostname → fqdn → ip_address → name); GRCen does not invent
Systems (that's the CMDB connector's job) — an unresolved target is a warning,
not a failure, and the Finding is still recorded. A finding that drops out of the
export is treated as remediated: its ``finding_status`` is set to ``remediated``
and the asset archived (``resolve_missing``, default on). Runs in one
transaction; ``dry_run`` rolls back.

Aggregating findings into rolled-up Risk assets is a deliberate follow-up (the
grouping — per-System, per-CVE, per-severity — is a policy choice); this slice
lands Findings + their System links + lifecycle.

## Export contract

    {
      "source": "uvt",               // optional; default "uvt". The source tag.
      "findings": [
        {
          "finding_id": "F-123",     // REQUIRED, stable — the upsert key
          "title": "OpenSSL RCE (CVE-2024-1234)",   // REQUIRED → asset name
          "description": "...",
          "severity": "critical",    // critical|high|medium|low
          "status": "open",          // open|in_remediation|remediated|verified|
                                      //   risk_accepted|closed → finding_status
          "cve_id": "CVE-2024-1234",
          "cvss_score": "9.8",
          "cvss_vector": "CVSS:3.1/AV:N/...",
          "scanner": "qualys",       // producing tool (uvt aggregates several)
          "scan_id": "QID-370588",
          "first_seen": "2024-01-01",
          "last_seen": "2024-06-01",
          "due_date": "2024-07-01",
          "affects": {               // optional — how to find the System asset
            "external_id": "sys_abc",  // tried in this priority order:
            "hostname": "web01",
            "fqdn": "web01.acme.com",
            "ip_address": "10.0.0.5",
            "name": "Prod Web"
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

DEFAULT_SOURCE = "uvt"

# Finding custom fields (custom_fields.py) a scanner record can populate.
_FINDING_FIELDS = (
    "severity", "cve_id", "cvss_score", "cvss_vector",
    "scanner", "scan_id", "first_seen", "last_seen", "due_date",
)

# How an ``affects`` block resolves to an existing System/Device, in priority
# order. Each is matched against the asset's metadata JSON (except name).
_AFFECTS_KEYS = ("external_id", "hostname", "fqdn", "ip_address")


@dataclass
class VulnSyncResult:
    source: str = DEFAULT_SOURCE
    findings_created: int = 0
    findings_updated: int = 0
    findings_resolved: int = 0        # dropped out of the export → archived
    affects_edges: int = 0
    edges_pruned: int = 0
    resolved: list[str] = field(default_factory=list)          # finding source_refs
    unresolved_assets: list[str] = field(default_factory=list)  # finding_ids w/o a System
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    dry_run: bool = False


def validate_export(export: object) -> list[str]:
    """Structural validation. Returns human-readable errors (empty = ok)."""
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
    # These findings originate from an automated scanner — set the Finding
    # `source` category accordingly (distinct from the provenance `source` tag).
    meta: dict = {"source": "automated", "finding_status": f.get("status") or "open"}
    for key in _FINDING_FIELDS:
        val = f.get(key)
        if val not in (None, ""):
            meta[key] = val
    if f.get("first_seen"):
        meta.setdefault("identified_date", f["first_seen"])
    affects = f.get("affects") or {}
    display = (
        affects.get("hostname") or affects.get("fqdn") or affects.get("name")
        or affects.get("ip_address") or affects.get("external_id")
    )
    if display:
        meta["affected_asset"] = display
    return meta


async def _resolve_affected(conn, organization_id: UUID, affects: dict) -> UUID | None:
    """Find the System/Device the finding affects, or None. Matches external_id
    → hostname → fqdn → ip_address (metadata) then name."""
    for key in _AFFECTS_KEYS:
        val = affects.get(key)
        if not val:
            continue
        row = await conn.fetchrow(
            """SELECT id FROM assets
               WHERE organization_id = $1 AND type IN ('system', 'device')
                 AND metadata ->> $2 = $3
               ORDER BY created_at LIMIT 1""",
            organization_id, key, str(val),
        )
        if row:
            return row["id"]
    name = affects.get("name")
    if name:
        row = await conn.fetchrow(
            """SELECT id FROM assets
               WHERE organization_id = $1 AND type IN ('system', 'device') AND name = $2
               ORDER BY created_at LIMIT 1""",
            organization_id, name,
        )
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
) -> VulnSyncResult:
    """Project a vuln-findings ``export`` into the asset graph for one org."""
    result = VulnSyncResult(dry_run=dry_run)
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
                    description=f.get("description"), status="active",
                    metadata=_finding_metadata(f),
                )
                seen_finding_refs.append(sref)
                if r.inserted:
                    result.findings_created += 1
                else:
                    result.findings_updated += 1

                affects = f.get("affects") or {}
                if affects:
                    system_id = await _resolve_affected(conn, org, affects)
                    if system_id is not None:
                        esref = f"affects:{fid}"
                        # System --affected_by--> Finding (reads "affects" from
                        # the Finding side, "affected by" from the System side).
                        await external_sync.upsert_external_edge(
                            conn, organization_id=org, source=src, source_ref=esref,
                            source_asset_id=system_id, target_asset_id=r.id,
                            relationship_type="affected_by",
                        )
                        seen_edge_refs.append(esref)
                        result.affects_edges += 1
                    else:
                        result.unresolved_assets.append(fid)
                        result.warnings.append(
                            f"finding '{fid}' affects an unresolved asset "
                            f"({', '.join(f'{k}={v}' for k, v in affects.items())}); edge skipped"
                        )

            # Prune stale affects edges (a finding that moved to another host).
            result.edges_pruned = await external_sync.prune_stale_edges(
                conn, organization_id=org, source=src, seen_refs=seen_edge_refs,
            )

            # Findings that dropped out of the export are remediated.
            stale = await conn.fetch(
                """SELECT id, source_ref FROM assets
                   WHERE organization_id = $1 AND source = $2 AND type = 'finding'
                     AND source_ref <> ALL($3::text[])""",
                org, src, seen_finding_refs,
            )
            result.resolved = [r["source_ref"] for r in stale]
            if resolve_missing and stale:
                updated = await conn.execute(
                    """UPDATE assets
                       SET metadata = jsonb_set(
                               coalesce(metadata, '{}'::jsonb),
                               '{finding_status}', $1::jsonb),
                           status = 'archived', updated_at = now()
                       WHERE id = ANY($2::uuid[]) AND status <> 'archived'""",
                    '"remediated"', [r["id"] for r in stale],
                )
                result.findings_resolved = int(updated.split()[-1])

            if dry_run:
                await tx.rollback()
            else:
                await tx.commit()
        except Exception:
            await tx.rollback()
            raise

    return result
