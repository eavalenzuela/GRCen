"""Sync an HRIS/HR roster into GRCen's People graph (roadmap I2).

An HRIS (Workday, BambooHR, HiBob, Rippling, ADP, …) is the system-of-record for
who works here. GRCen consumes a roster projection so People are authoritative
and — the payoff — **leavers are deprovisioned**: when an employee drops out of
the roster, their Person is archived and any linked login disabled, without
waiting for someone to notice. This is the bulk counterpart to SCIM's per-user
push (``services/scim_service``); both ride the same ``external_sync`` upsert.

Mapping to the graph:

    employee            → asset(type=person)              source_ref emp:<employee_id>
    org_unit            → asset(type=organizational_unit) source_ref ou:<ref>
    manager_employee_id → manager --manages--> report     (mover: re-pointed each run)
    org_unit_ref        → person --member_of--> org_unit
    org_unit.parent_ref → parent --parent_of--> child

Idempotency & lifecycle: every synced row carries ``(source, source_ref)`` so
re-running upserts in place. Stale *edges* (a changed manager/OU) are pruned
each run. Stale *people* (leavers) are archived and their login disabled when
``deactivate_leavers`` is set (the default) — never hard-deleted, so audit
history and any human-authored edges on the Person survive; optionally their
owned assets are reassigned to a fallback owner. Runs in one transaction;
``dry_run`` rolls back after tallying, so the result reflects a real run exactly.

## Roster contract

    {
      "source": "hris:workday",      // optional; default "hris". The source tag.
      "employees": [
        {
          "employee_id": "E123",     // REQUIRED, stable — the upsert key
          "name": "Ada Lovelace",    // REQUIRED
          "email": "ada@acme.com",
          "title": "Staff Engineer",
          "department": "Engineering",
          "manager_employee_id": "E100",   // → manages edge (must be in roster)
          "org_unit_ref": "eng",           // → member_of edge (must be in org_units)
          "manager": "Grace Hopper",       // free-text display, optional
          "location": "London",
          "cost_center": "CC-100",
          "employment_type": "full_time",
          "start_date": "2020-01-01",
          "end_date": null,
          "active": true                   // false → Person archived, login disabled
        }
      ],
      "org_units": [                        // OPTIONAL
        {"ref": "eng", "name": "Engineering", "parent_ref": "company"},
        {"ref": "company", "name": "Acme"}
      ]
    }

Dangling ``manager_employee_id`` / ``org_unit_ref`` / ``parent_ref`` are skipped
with a warning (rosters legitimately have a CEO with no manager), not a hard
error — unlike catalog_sync, which fails closed on dangling control refs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

import asyncpg

from grcen.services import external_sync, organization_service

DEFAULT_SOURCE = "hris"

# Person custom fields (custom_fields.py) an employee record can populate.
_PERSON_FIELDS = (
    "email", "title", "department", "location", "cost_center",
    "manager", "employment_type", "start_date", "end_date",
)


@dataclass
class HrisSyncResult:
    source: str = DEFAULT_SOURCE
    persons_created: int = 0
    persons_updated: int = 0
    persons_archived: int = 0          # leavers archived this run
    org_units_created: int = 0
    org_units_updated: int = 0
    org_units_archived: int = 0
    manager_edges: int = 0             # manages edges emitted
    membership_edges: int = 0          # member_of edges emitted
    hierarchy_edges: int = 0           # parent_of (OU→OU) edges emitted
    edges_pruned: int = 0
    users_deactivated: int = 0         # linked logins disabled for leavers
    assets_reassigned: int = 0         # owned by a leaver, moved to the fallback
    leavers: list[str] = field(default_factory=list)          # emp source_refs
    stale_org_units: list[str] = field(default_factory=list)  # ou source_refs
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    dry_run: bool = False


def validate_roster(roster: object) -> list[str]:
    """Structural validation. Returns human-readable errors (empty = ok)."""
    errors: list[str] = []
    if not isinstance(roster, dict):
        return ["roster must be a JSON object"]

    employees = roster.get("employees")
    if not isinstance(employees, list):
        return ["'employees' must be a list"]

    seen_emp: set[str] = set()
    for i, emp in enumerate(employees):
        if not isinstance(emp, dict):
            errors.append(f"employee[{i}] must be an object")
            continue
        eid = emp.get("employee_id")
        if not eid:
            errors.append(f"employee[{i}] missing 'employee_id'")
        elif eid in seen_emp:
            errors.append(f"duplicate employee_id '{eid}'")
        else:
            seen_emp.add(eid)
        if not emp.get("name"):
            errors.append(f"employee '{eid or i}' missing 'name'")

    org_units = roster.get("org_units", [])
    if not isinstance(org_units, list):
        errors.append("'org_units' must be a list")
    else:
        seen_ou: set[str] = set()
        for i, ou in enumerate(org_units):
            if not isinstance(ou, dict):
                errors.append(f"org_unit[{i}] must be an object")
                continue
            ref = ou.get("ref")
            if not ref:
                errors.append(f"org_unit[{i}] missing 'ref'")
            elif ref in seen_ou:
                errors.append(f"duplicate org_unit ref '{ref}'")
            else:
                seen_ou.add(ref)
            if not ou.get("name"):
                errors.append(f"org_unit '{ref or i}' missing 'name'")
    return errors


def _person_metadata(emp: dict) -> dict:
    meta = {"employee_id": emp["employee_id"]}
    for key in _PERSON_FIELDS:
        val = emp.get(key)
        if val not in (None, ""):
            meta[key] = val
    return meta


async def sync_roster(
    pool: asyncpg.Pool,
    roster: dict,
    *,
    organization_id: UUID | None = None,
    source: str | None = None,
    dry_run: bool = False,
    deactivate_leavers: bool = True,
    reassign_owned_to: UUID | None = None,
    allow_empty: bool = False,
) -> HrisSyncResult:
    """Project ``roster`` into the People graph for one organization."""
    result = HrisSyncResult(dry_run=dry_run)
    errors = validate_roster(roster)
    if errors:
        result.errors = errors
        return result

    # An empty roster is indistinguishable from "everyone left", and the leaver
    # step would archive every Person this source owns and disable their logins.
    # In practice an empty payload almost always means an upstream failure — an
    # expired credential, a filter that matched nothing, a truncated export — so
    # winding down an entire org has to be asked for explicitly. Harmless when
    # deactivate_leavers is off, since nothing is archived then.
    if not roster["employees"] and deactivate_leavers and not allow_empty:
        result.errors.append(
            "roster contains no employees; refusing to treat every synced person "
            "as a leaver. Pass allow_empty=True if the roster is genuinely empty."
        )
        return result

    src = source or roster.get("source") or DEFAULT_SOURCE
    result.source = src
    org = organization_id or await organization_service.get_default_org_id(pool)

    if reassign_owned_to is not None:
        exists = await pool.fetchrow(
            "SELECT 1 FROM assets WHERE id = $1 AND organization_id = $2",
            reassign_owned_to, org,
        )
        if not exists:
            result.errors.append(
                f"reassign_owned_to asset {reassign_owned_to} not found in this organization"
            )
            return result

    employees: list[dict] = roster["employees"]
    org_units: list[dict] = roster.get("org_units") or []

    async with pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        try:
            person_by_emp: dict[str, UUID] = {}
            ou_by_ref: dict[str, UUID] = {}
            seen_person_refs: list[str] = []
            seen_ou_refs: list[str] = []
            seen_edge_refs: list[str] = []

            # 1. Org units
            for ou in org_units:
                ref = ou["ref"]
                sref = f"ou:{ref}"
                r = await external_sync.upsert_external_asset(
                    conn, organization_id=org, source=src, source_ref=sref,
                    asset_type="organizational_unit", name=ou["name"],
                    status="active",
                )
                ou_by_ref[ref] = r.id
                seen_ou_refs.append(sref)
                if r.inserted:
                    result.org_units_created += 1
                else:
                    result.org_units_updated += 1

            # OU hierarchy edges (parent --parent_of--> child)
            for ou in org_units:
                parent = ou.get("parent_ref")
                if not parent:
                    continue
                if parent == ou["ref"]:
                    result.warnings.append(f"org_unit '{ou['ref']}' is its own parent; skipped")
                elif parent in ou_by_ref:
                    esref = f"parent_of:{parent}:{ou['ref']}"
                    await external_sync.upsert_external_edge(
                        conn, organization_id=org, source=src, source_ref=esref,
                        source_asset_id=ou_by_ref[parent],
                        target_asset_id=ou_by_ref[ou["ref"]],
                        relationship_type="parent_of",
                    )
                    seen_edge_refs.append(esref)
                    result.hierarchy_edges += 1
                else:
                    result.warnings.append(
                        f"org_unit '{ou['ref']}' parent_ref '{parent}' not in roster; edge skipped"
                    )

            # 2. Employees → Person assets
            for emp in employees:
                eid = emp["employee_id"]
                sref = f"emp:{eid}"
                active = emp.get("active", True)
                r = await external_sync.upsert_external_asset(
                    conn, organization_id=org, source=src, source_ref=sref,
                    asset_type="person", name=emp["name"],
                    status="active" if active else "archived",
                    metadata=_person_metadata(emp),
                )
                person_by_emp[eid] = r.id
                seen_person_refs.append(sref)
                if r.inserted:
                    result.persons_created += 1
                else:
                    result.persons_updated += 1

            # 3. Manager + membership edges
            for emp in employees:
                eid = emp["employee_id"]
                mgr = emp.get("manager_employee_id")
                if mgr:
                    if mgr == eid:
                        result.warnings.append(f"employee '{eid}' lists itself as manager; skipped")
                    elif mgr in person_by_emp:
                        esref = f"manages:{mgr}:{eid}"
                        await external_sync.upsert_external_edge(
                            conn, organization_id=org, source=src, source_ref=esref,
                            source_asset_id=person_by_emp[mgr],
                            target_asset_id=person_by_emp[eid],
                            relationship_type="manages",
                        )
                        seen_edge_refs.append(esref)
                        result.manager_edges += 1
                    else:
                        result.warnings.append(
                            f"employee '{eid}' manager '{mgr}' not in roster; edge skipped"
                        )
                ou_ref = emp.get("org_unit_ref")
                if ou_ref:
                    if ou_ref in ou_by_ref:
                        esref = f"member_of:{eid}:{ou_ref}"
                        await external_sync.upsert_external_edge(
                            conn, organization_id=org, source=src, source_ref=esref,
                            source_asset_id=person_by_emp[eid],
                            target_asset_id=ou_by_ref[ou_ref],
                            relationship_type="member_of",
                        )
                        seen_edge_refs.append(esref)
                        result.membership_edges += 1
                    else:
                        result.warnings.append(
                            f"employee '{eid}' org_unit_ref '{ou_ref}' not in roster; edge skipped"
                        )

            # 4. Prune stale synced edges (movers' old manager/OU edges).
            result.edges_pruned = await external_sync.prune_stale_edges(
                conn, organization_id=org, source=src, seen_refs=seen_edge_refs,
            )

            # 5. Leavers — people this source owns that dropped out of the roster.
            stale_people = await conn.fetch(
                """SELECT id, source_ref FROM assets
                   WHERE organization_id = $1 AND source = $2 AND type = 'person'
                     AND source_ref <> ALL($3::text[])""",
                org, src, seen_person_refs,
            )
            result.leavers = [r["source_ref"] for r in stale_people]
            if deactivate_leavers and stale_people:
                leaver_ids = [r["id"] for r in stale_people]
                archived = await conn.execute(
                    """UPDATE assets SET status = 'archived', updated_at = now()
                       WHERE id = ANY($1::uuid[]) AND status <> 'archived'""",
                    leaver_ids,
                )
                result.persons_archived = int(archived.split()[-1])
                deactivated = await conn.execute(
                    """UPDATE users SET is_active = false, updated_at = now()
                       WHERE person_asset_id = ANY($1::uuid[]) AND is_active = true""",
                    leaver_ids,
                )
                result.users_deactivated = int(deactivated.split()[-1])
                if reassign_owned_to is not None:
                    reassigned = await conn.execute(
                        """UPDATE assets SET owner_id = $1, updated_at = now()
                           WHERE organization_id = $2 AND owner_id = ANY($3::uuid[])""",
                        reassign_owned_to, org, leaver_ids,
                    )
                    result.assets_reassigned = int(reassigned.split()[-1])

            # 6. Stale org units (only when the roster carried org_units at all —
            # a roster that omits the section shouldn't archive existing OUs).
            if org_units:
                stale_ous = await conn.fetch(
                    """SELECT id, source_ref FROM assets
                       WHERE organization_id = $1 AND source = $2
                         AND type = 'organizational_unit'
                         AND source_ref <> ALL($3::text[])""",
                    org, src, seen_ou_refs,
                )
                result.stale_org_units = [r["source_ref"] for r in stale_ous]
                if deactivate_leavers and stale_ous:
                    archived_ous = await conn.execute(
                        """UPDATE assets SET status = 'archived', updated_at = now()
                           WHERE id = ANY($1::uuid[]) AND status <> 'archived'""",
                        [r["id"] for r in stale_ous],
                    )
                    result.org_units_archived = int(archived_ous.split()[-1])

            if dry_run:
                await tx.rollback()
            else:
                await tx.commit()
        except Exception:
            await tx.rollback()
            raise

    return result
