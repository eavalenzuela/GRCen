"""Generic external-source upsert — the untyped sibling of the connectors (I5).

The typed connectors (``hris_sync``, ``cmdb_sync``, ``vuln_sync``,
``cspm_sync``, SCIM) each hand-write a contract for one class of source system.
That serves the sources GRCen knows about and strands everything else: a team
with a spreadsheet export, an in-house CMDB with its own shape, or a one-off ETL
has no idempotent way in. ``/api/imports`` does a blind ``INSERT`` — re-run it
and you get every asset twice — and the REST API keys on GRCen's own UUIDs, so a
script has to maintain its own external-id → GRCen-id mapping table.

This module removes that gap. The caller declares its own ``source`` tag and a
stable ``ref`` per record; GRCen reconciles on ``(organization_id, source,
source_ref)`` exactly as the typed connectors do, through the same
``external_sync`` primitives. POST the same inventory twice and the second run
updates in place. No mapping table on the client, no search-then-branch.

Prefer a typed connector when one fits — it understands its domain (leaver
deprovisioning, finding remediation, CI dependency graphs) in ways a generic
projection cannot. Reach for this when nothing fits.

## Contract

    {
      "source": "etl:asset-inventory",   // REQUIRED. Your namespace tag.
      "assets": [
        {
          "ref": "srv-001",              // REQUIRED. Stable key in YOUR system.
          "type": "system",              // REQUIRED. Any AssetType value.
          "name": "Prod Web",            // REQUIRED.
          "description": "Customer-facing web tier",
          "status": "active",            // active|inactive|draft|archived
          "owner": "Ada Lovelace",       // Person/OU name → owner_id
          "tags": ["prod", "pci"],
          "criticality": "high",
          "metadata": {"hostname": "web01"},  // explicit custom-field bag…
          "environment": "production"    // …or flat per-type custom-field keys
        }
      ],
      "relationships": [
        {
          "from": "srv-001",             // an asset `ref`…
          "to_name": "Security Team",    // …or a name + type, for an asset
          "to_type": "organizational_unit",  //  this source doesn't own
          "type": "depends_on",          // REQUIRED. Free-text edge type.
          "description": "reads session state",
          "ref": "srv-001:depends_on:db-001"   // optional; derived when absent
        }
      ],
      "on_missing": "ignore"             // ignore (default) | archive | delete
    }

Flat keys are matched against the per-type ``CUSTOM_FIELDS`` definitions and
coerced the same way a CSV import column would be, so a script that reads a CSV
row into a dict can post it almost unchanged. An explicit ``metadata`` bag wins
over a flat key of the same name. ``metadata.external_id`` defaults to the
record's ``ref`` (mirroring ``cmdb_sync``), so a uvt or CSPM finding that cites
that id resolves onto the asset without extra plumbing.

Each end of a relationship is addressed one of two ways, and exactly one:

- **By external ref** — ``from`` / ``to``. Resolved against assets in this
  payload first, then assets carrying the same ``source`` from earlier runs, so
  a relationships-only payload can wire up an inventory synced previously.
- **By name + type** — ``from_name`` + ``from_type`` (likewise ``to_*``). Reaches
  assets this source doesn't own: the hand-authored Org Unit that owns a synced
  system, an HRIS-synced Person, a Control from a content pack. Org-scoped and
  source-agnostic — the same rule ``owner`` already follows. A duplicate
  name+type resolves to the oldest match *and warns*, so the pick isn't silent.

Setting both forms for one end is an error rather than a precedence rule; there
is no silent fallback from a ref that didn't resolve to a name that might.
Nothing is ever created to satisfy a link — an end that resolves to nothing is a
warning, the edge is skipped, and the rest of the payload still lands.

Note this vocabulary differs from ``/api/imports/relationships``, which spells
the same idea ``source_name``/``target_name``. That word is already spoken for
here — ``source`` is the provenance tag — and quietly overloading it on the one
endpoint where both meanings are live would be a trap.

Edges to human-authored assets are still owned by the caller's ``source``, so
they prune when they drop out of a payload like any other synced edge. A human
who hand-edits one will see it reverted on the next sync; that is the existing
rule for every connector, not something new here.

## CSV front door

``payload_from_csv`` adapts CSV text into exactly the payload above, so
``POST /api/sync/upsert/csv`` (multipart: an ``assets`` file, a ``relationships``
file, or both) is the same code path with a different envelope — one contract,
one set of lifecycle rules. Columns are the record keys; ``tags`` is one
comma-separated cell; a ``default_type`` form field supplies ``type`` for a
homogeneous export that lacks the column. A header that isn't a known field for
its row's type comes back as a warning rather than vanishing.

Blank cells drop out rather than writing empties, so a CSV cannot clear an
existing ``tags``/``criticality``/``owner`` — post JSON with an explicit value
for that. Sending both files in one request keeps the whole sync in a single
transaction, which is what lets relationships resolve against assets created in
the same call.

## Lifecycle

``on_missing`` governs records that GRCen holds under this ``source`` but that
the payload no longer carries:

- ``ignore`` (default) — leave them. The safe choice, and the reason it is the
  default: a paginated or partial upload must not archive everything it omits.
- ``archive`` — set ``status='archived'``. The reversible tombstone.
- ``delete`` — remove the rows. Deleting an asset cascades to human-authored
  edges hanging off it, so this is opt-in and irreversible.

Staleness is scoped to the asset *types* the payload actually carried, matching
``cmdb_sync``'s per-CI-class rule: syncing ``systems.csv`` and ``devices.csv``
under one source tag won't make each run archive the other's assets. Edges are
pruned only when the payload carried a ``relationships`` key at all.

## Source namespacing

A caller-supplied ``source`` that collided with a built-in connector's tag could
archive or delete rows that connector owns — posting ``{"source": "scim",
"assets": [], "on_missing": "delete"}`` would drop your whole IdP-synced
directory. Reserved tags (and their ``tag:`` sub-namespaces) are rejected up
front; pick your own.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from uuid import UUID

import asyncpg

from grcen.custom_fields import CUSTOM_FIELDS, coerce_value
from grcen.models.asset import AssetStatus, AssetType
from grcen.services import (
    catalog_sync,
    cmdb_sync,
    cspm_sync,
    external_sync,
    hris_sync,
    organization_service,
    risk_aggregation,
    scim_service,
    vuln_sync,
)

# Source tags owned by the typed connectors. Sourced from the connectors
# themselves so a rename there tightens the guard here automatically.
RESERVED_SOURCES: frozenset[str] = frozenset({
    scim_service.SOURCE,
    hris_sync.DEFAULT_SOURCE,
    cmdb_sync.DEFAULT_SOURCE,
    cspm_sync.DEFAULT_SOURCE,
    vuln_sync.DEFAULT_SOURCE,
    catalog_sync.DEFAULT_SOURCE,
    risk_aggregation.DEFAULT_SOURCE,
})
# Content packs claim a whole namespace rather than one tag.
RESERVED_PREFIXES: tuple[str, ...] = ("grcen-pack:",)

ON_MISSING_MODES = ("ignore", "archive", "delete")

MAX_ASSETS = 5000
MAX_RELATIONSHIPS = 10000

# Top-level record keys with their own meaning — never treated as custom fields.
_RESERVED_ASSET_KEYS = frozenset({
    "ref", "type", "name", "description", "status", "owner",
    "tags", "criticality", "metadata",
})


# CSV header aliases → the contract's key. A CMDB/scanner export tends to name
# its stable key one of these; the edge type is commonly spelled the long way.
_ASSET_REF_ALIASES = ("ref", "external_id", "ci_id")
# Only the edge type gets an alias. `source_ref` is deliberately NOT aliased to
# the edge's own `ref` — a reader would just as reasonably take it to mean "the
# source asset's ref", and silently mis-keying an edge is worse than a header
# that doesn't resolve.
_EDGE_ALIASES = {"relationship_type": "type"}

# Tags arrive as one cell; comma matches the tag input on the asset form
# (routers/asset_pages.py). A tag containing a comma isn't expressible — same
# limitation the web form has.
_TAG_DELIMITER = ","


@dataclass
class GenericSyncResult:
    source: str = ""
    assets_created: int = 0
    assets_updated: int = 0
    assets_archived: int = 0
    assets_deleted: int = 0
    edges_created: int = 0
    edges_updated: int = 0
    edges_pruned: int = 0
    owners_linked: int = 0
    stale: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    dry_run: bool = False


def reserved_source_error(source: str) -> str | None:
    """Return an error if ``source`` trespasses on a connector's namespace."""
    lowered = source.strip().lower()
    root = lowered.split(":", 1)[0]
    if lowered in RESERVED_SOURCES or root in RESERVED_SOURCES:
        return (
            f"source '{source}' is reserved by a built-in connector "
            f"({', '.join(sorted(RESERVED_SOURCES))}) — choose your own tag"
        )
    for prefix in RESERVED_PREFIXES:
        if lowered.startswith(prefix):
            return f"source '{source}' is reserved by content packs — choose your own tag"
    return None


def validate_payload(payload: object) -> list[str]:
    """Structural validation. Returns human-readable errors (empty = ok)."""
    if not isinstance(payload, dict):
        return ["payload must be a JSON object"]

    errors: list[str] = []

    source = payload.get("source")
    if not source or not isinstance(source, str) or not source.strip():
        errors.append("missing 'source'")
    else:
        reserved = reserved_source_error(source)
        if reserved:
            errors.append(reserved)

    on_missing = payload.get("on_missing", "ignore")
    if on_missing not in ON_MISSING_MODES:
        errors.append(f"'on_missing' must be one of {', '.join(ON_MISSING_MODES)}")

    assets = payload.get("assets", [])
    rels = payload.get("relationships", [])
    if not isinstance(assets, list):
        return [*errors, "'assets' must be a list"]
    if not isinstance(rels, list):
        return [*errors, "'relationships' must be a list"]
    if "assets" not in payload and "relationships" not in payload:
        errors.append("payload must contain 'assets' and/or 'relationships'")
    if len(assets) > MAX_ASSETS:
        errors.append(f"'assets' exceeds the {MAX_ASSETS}-record limit; page the upload")
    if len(rels) > MAX_RELATIONSHIPS:
        errors.append(f"'relationships' exceeds the {MAX_RELATIONSHIPS}-record limit; page the upload")

    valid_status = {s.value for s in AssetStatus}
    seen_refs: set[str] = set()
    for i, record in enumerate(assets):
        if not isinstance(record, dict):
            errors.append(f"assets[{i}] must be an object")
            continue
        ref = record.get("ref")
        label = ref or i
        if not ref or not isinstance(ref, str):
            errors.append(f"assets[{i}] missing 'ref'")
        elif ref in seen_refs:
            errors.append(f"duplicate asset ref '{ref}'")
        else:
            seen_refs.add(ref)
        if not record.get("name"):
            errors.append(f"asset '{label}' missing 'name'")
        raw_type = record.get("type")
        if not raw_type:
            errors.append(f"asset '{label}' missing 'type'")
        else:
            try:
                AssetType(raw_type)
            except ValueError:
                errors.append(f"asset '{label}' has invalid type '{raw_type}'")
        status = record.get("status")
        if status is not None and status not in valid_status:
            errors.append(f"asset '{label}' has invalid status '{status}'")
        tags = record.get("tags")
        if tags is not None and not isinstance(tags, list):
            errors.append(f"asset '{label}' 'tags' must be a list")
        crit = record.get("criticality")
        if crit is not None and (not isinstance(crit, str) or len(crit) > 20):
            errors.append(f"asset '{label}' 'criticality' must be a string of at most 20 chars")
        meta = record.get("metadata")
        if meta is not None and not isinstance(meta, dict):
            errors.append(f"asset '{label}' 'metadata' must be an object")

    seen_edge_refs: set[str] = set()
    for i, edge in enumerate(rels):
        if not isinstance(edge, dict):
            errors.append(f"relationships[{i}] must be an object")
            continue
        if not edge.get("type"):
            errors.append(f"relationships[{i}] missing 'type'")
        for side in ("from", "to"):
            by_ref = bool(edge.get(side))
            by_name = bool(edge.get(f"{side}_name"))
            if by_ref and by_name:
                errors.append(
                    f"relationships[{i}] sets both '{side}' and '{side}_name' — use one"
                )
            elif by_name:
                side_type = edge.get(f"{side}_type")
                if not side_type:
                    errors.append(
                        f"relationships[{i}] has '{side}_name' but no '{side}_type'"
                    )
                else:
                    try:
                        AssetType(side_type)
                    except ValueError:
                        errors.append(
                            f"relationships[{i}] has invalid '{side}_type' '{side_type}'"
                        )
            elif not by_ref:
                errors.append(f"relationships[{i}] missing '{side}' or '{side}_name'")
        eref = edge.get("ref") or _edge_ref(edge)
        if eref in seen_edge_refs:
            errors.append(f"duplicate relationship ref '{eref}'")
        else:
            seen_edge_refs.add(eref)

    return errors


def _endpoint_token(edge: dict, side: str) -> str:
    """Stable token for one end of an edge, used to derive the edge's own ref.

    Ref-addressed ends yield the bare ref, so refs derived before name-based
    linking existed are unchanged — an existing synced edge keeps its key
    instead of being pruned and re-created on the next run.
    """
    ref = edge.get(side)
    if ref:
        return str(ref)
    return f"name:{edge.get(f'{side}_type')}:{edge.get(f'{side}_name')}"


def _endpoint_label(edge: dict, side: str) -> str:
    if edge.get(side):
        return f"ref '{edge[side]}'"
    return f"{edge.get(f'{side}_type')} named '{edge.get(f'{side}_name')}'"


def _edge_ref(edge: dict) -> str:
    return (
        f"{_endpoint_token(edge, 'from')}:{edge.get('type')}:{_endpoint_token(edge, 'to')}"
    )


def _read_csv(content: str, label: str, errors: list[str]) -> list[dict]:
    """Parse CSV into row dicts: headers normalized, empty cells dropped.

    Headers are stripped and lowercased — every custom-field name is lowercase
    snake_case, so this only ever rescues a ``Name``/``Type`` header. A leading
    BOM is dropped here as well as at decode time: Excel emits one, it would
    turn the first header into ``﻿ref``, and the parse shouldn't depend on
    the caller having picked ``utf-8-sig``. An empty cell drops its key entirely
    rather than writing a blank: a blank spreadsheet cell means "not supplied",
    and clearing a value the file didn't mean to speak to is the wrong default.
    Post JSON with an explicit ``[]`` to clear.
    """
    content = content.lstrip("﻿")
    try:
        reader = csv.DictReader(io.StringIO(content))
        if not reader.fieldnames:
            errors.append(f"{label} CSV has no header row")
            return []
        rows: list[dict] = []
        for raw in reader:
            row: dict = {}
            for key, val in raw.items():
                if key is None:      # columns beyond the header
                    continue
                name = key.strip().lower()
                value = val.strip() if isinstance(val, str) else val
                if not name or value in (None, ""):
                    continue
                row[name] = value
            if row:                  # skips blank and ",,," filler lines
                rows.append(row)
        return rows
    except csv.Error as exc:
        errors.append(f"{label} CSV could not be parsed: {exc}")
        return []


def payload_from_csv(
    *,
    source: str,
    assets_csv: str | None = None,
    relationships_csv: str | None = None,
    default_type: str | None = None,
    on_missing: str = "ignore",
) -> tuple[dict, list[str], list[str]]:
    """Adapt CSV text into a ``sync`` payload. Returns ``(payload, errors, warnings)``.

    A thin adapter, deliberately: the CSV front door and the JSON one converge
    on the same payload, so the contract, validation, and lifecycle rules have
    exactly one implementation.

    Asset columns are the record keys — ``ref`` (or ``external_id`` / ``ci_id``),
    ``type``, ``name``, ``description``, ``status``, ``owner``, ``tags``
    (comma-separated in one cell), ``criticality``, plus any per-type
    custom-field column. ``default_type`` supplies ``type`` for a homogeneous
    export that has no such column. Relationship columns are ``from``/``to`` (or
    ``from_name`` + ``from_type`` / ``to_name`` + ``to_type`` to reach an asset
    this source doesn't own), ``type`` (or ``relationship_type``),
    ``description``, ``ref``.

    A column that isn't a known field for its row's type is reported as a
    warning rather than silently dropped — a typo'd header vanishing without
    trace is the classic CSV-import frustration.
    """
    errors: list[str] = []
    warnings: list[str] = []
    payload: dict = {"source": source, "on_missing": on_missing}

    if default_type:
        try:
            AssetType(default_type)
        except ValueError:
            errors.append(f"invalid default type '{default_type}'")
            default_type = None

    if assets_csv is not None:
        records: list[dict] = []
        unknown: set[tuple[str, str]] = set()
        for row in _read_csv(assets_csv, "assets", errors):
            record = dict(row)
            # First alias present wins; the others stay put, so a file carrying
            # both `ref` and `external_id` keeps external_id as a custom field.
            for alias in _ASSET_REF_ALIASES:
                if alias in record:
                    record["ref"] = record.pop(alias)
                    break
            if not record.get("type") and default_type:
                record["type"] = default_type
            if "tags" in record:
                record["tags"] = [
                    t.strip() for t in record["tags"].split(_TAG_DELIMITER) if t.strip()
                ]
            try:
                known = {f.name for f in CUSTOM_FIELDS.get(AssetType(record["type"]), [])}
            except (ValueError, KeyError):
                known = set()          # invalid/absent type — validation reports it
            else:
                for key in record:
                    if key not in _RESERVED_ASSET_KEYS and key not in known:
                        unknown.add((record["type"], key))
            records.append(record)
        payload["assets"] = records
        for asset_type, column in sorted(unknown):
            warnings.append(
                f"column '{column}' isn't a known field for type '{asset_type}'; ignored"
            )

    if relationships_csv is not None:
        edges: list[dict] = []
        for row in _read_csv(relationships_csv, "relationships", errors):
            edge = {_EDGE_ALIASES.get(k, k): v for k, v in row.items()}
            edges.append(edge)
        payload["relationships"] = edges

    return payload, errors, warnings


def _metadata(record: dict) -> dict:
    """Flat custom-field columns + the explicit ``metadata`` bag.

    Flat keys are coerced through the same ``CUSTOM_FIELDS`` definitions a CSV
    import uses, so a CSV row read into a dict posts through unchanged. An
    explicit bag wins over a flat key of the same name.
    """
    meta: dict = {}
    try:
        asset_type = AssetType(record["type"])
    except (ValueError, KeyError):
        asset_type = None
    if asset_type is not None:
        field_defs = {f.name: f for f in CUSTOM_FIELDS.get(asset_type, [])}
        for key, val in record.items():
            if key in _RESERVED_ASSET_KEYS or key not in field_defs:
                continue
            raw = str(val).strip() if val is not None else ""
            if raw:
                meta[key] = coerce_value(field_defs[key], raw)
    meta.update(record.get("metadata") or {})
    # Lets a uvt/CSPM finding citing this id resolve onto the asset (cmdb_sync
    # sets the same default for the same reason).
    meta.setdefault("external_id", record["ref"])
    return meta


async def _apply_local_fields(
    conn, organization_id: UUID, asset_id: UUID, record: dict,
    result: GenericSyncResult,
) -> None:
    """Write the columns ``external_sync`` doesn't carry: owner, tags, criticality.

    No connector needed these until now, so rather than widen the shared upsert
    primitive they land in a follow-up UPDATE — the same shape ``cmdb_sync``
    already uses for ownership. Column names come from a fixed allowlist; every
    value is bound.
    """
    sets: list[str] = []
    params: list = []

    if "tags" in record:
        params.append([str(t) for t in (record.get("tags") or [])])
        sets.append(f"tags = ${len(params)}::text[]")
    if "criticality" in record:
        params.append(record.get("criticality") or None)
        sets.append(f"criticality = ${len(params)}")

    owner = record.get("owner")
    if owner:
        row = await conn.fetchrow(
            """SELECT id FROM assets
               WHERE organization_id = $1 AND type IN ('person', 'organizational_unit')
                 AND name = $2
               ORDER BY created_at LIMIT 1""",
            organization_id, owner,
        )
        if row:
            params.append(row["id"])
            sets.append(f"owner_id = ${len(params)}")
            result.owners_linked += 1
        else:
            result.warnings.append(
                f"asset '{record['ref']}' owner '{owner}' not found; ownership left unset"
            )

    if not sets:
        return
    params.append(asset_id)
    await conn.execute(
        f"UPDATE assets SET {', '.join(sets)}, updated_at = now() WHERE id = ${len(params)}",
        *params,
    )


async def _reconcile_missing(
    conn, organization_id: UUID, source: str, asset_type: str,
    seen_refs: list[str], mode: str,
) -> tuple[int, int, list[str]]:
    """Handle rows under ``source``/``asset_type`` the payload no longer carries.

    Returns ``(archived, deleted, stale_refs)``. Always reports; only writes for
    ``archive``/``delete``.
    """
    rows = await conn.fetch(
        """SELECT id, source_ref FROM assets
           WHERE organization_id = $1 AND source = $2 AND type = $3
             AND source_ref <> ALL($4::text[])""",
        organization_id, source, asset_type, seen_refs,
    )
    refs = [r["source_ref"] for r in rows]
    if not rows or mode == "ignore":
        return 0, 0, refs

    ids = [r["id"] for r in rows]
    if mode == "archive":
        res = await conn.execute(
            """UPDATE assets SET status = 'archived', updated_at = now()
               WHERE id = ANY($1::uuid[]) AND status <> 'archived'""",
            ids,
        )
        return int(res.split()[-1]), 0, refs
    res = await conn.execute("DELETE FROM assets WHERE id = ANY($1::uuid[])", ids)
    return 0, int(res.split()[-1]), refs


async def _resolve_ref(
    conn, organization_id: UUID, source: str, ref: str, cache: dict[str, UUID | None]
) -> UUID | None:
    """Asset id for an external ``ref`` — this batch first, then earlier runs."""
    if ref in cache:
        return cache[ref]
    row = await conn.fetchrow(
        """SELECT id FROM assets
           WHERE organization_id = $1 AND source = $2 AND source_ref = $3""",
        organization_id, source, ref,
    )
    cache[ref] = row["id"] if row else None
    return cache[ref]


async def _resolve_by_name(
    conn, organization_id: UUID, name: str, asset_type: str,
    cache: dict[str, UUID | None], result: GenericSyncResult,
) -> UUID | None:
    """Asset id for a ``(name, type)`` pair, org-scoped and source-agnostic.

    Deliberately not restricted to human-authored rows: ``owner`` resolution
    already matches by name regardless of provenance, and a caller linking to an
    HRIS-synced Person would be baffled by a lookup that could only see
    hand-typed ones. Ambiguity resolves to the oldest match (the house rule in
    ``import_service`` and ``cmdb_sync``) but warns, so a duplicate name isn't
    silently picked for you. Never creates: an unmatched name skips the edge.
    """
    key = (name, asset_type)
    if key in cache:
        return cache[key]
    rows = await conn.fetch(
        """SELECT id FROM assets
           WHERE organization_id = $1 AND type = $2 AND name = $3
           ORDER BY created_at""",
        organization_id, asset_type, name,
    )
    if len(rows) > 1:
        result.warnings.append(
            f"'{name}' ({asset_type}) matches {len(rows)} assets; linked the oldest"
        )
    cache[key] = rows[0]["id"] if rows else None
    return cache[key]


async def _resolve_endpoint(
    conn, organization_id: UUID, source: str, edge: dict, side: str,
    ref_cache: dict[str, UUID | None], name_cache: dict[tuple[str, str], UUID | None],
    result: GenericSyncResult,
) -> UUID | None:
    """One end of an edge, addressed by external ``ref`` or by ``name`` + ``type``."""
    ref = edge.get(side)
    if ref:
        return await _resolve_ref(conn, organization_id, source, ref, ref_cache)
    return await _resolve_by_name(
        conn, organization_id, edge[f"{side}_name"], edge[f"{side}_type"],
        name_cache, result,
    )


async def sync(
    pool: asyncpg.Pool,
    payload: dict,
    *,
    organization_id: UUID | None = None,
    dry_run: bool = False,
) -> GenericSyncResult:
    """Project a caller-defined ``payload`` into the asset graph for one org."""
    result = GenericSyncResult(dry_run=dry_run)
    errors = validate_payload(payload)
    if errors:
        result.errors = errors
        return result

    source = payload["source"].strip()
    result.source = source
    org = organization_id or await organization_service.get_default_org_id(pool)

    assets: list[dict] = payload.get("assets") or []
    rels: list[dict] = payload.get("relationships") or []
    has_relationships = "relationships" in payload
    on_missing = payload.get("on_missing", "ignore")

    async with pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        try:
            ref_to_id: dict[str, UUID | None] = {}
            seen_by_type: dict[str, list[str]] = {}

            for record in assets:
                ref = record["ref"]
                r = await external_sync.upsert_external_asset(
                    conn, organization_id=org, source=source, source_ref=ref,
                    asset_type=record["type"], name=record["name"],
                    description=record.get("description"),
                    status=record.get("status") or "active",
                    metadata=_metadata(record),
                )
                ref_to_id[ref] = r.id
                seen_by_type.setdefault(record["type"], []).append(ref)
                if r.inserted:
                    result.assets_created += 1
                else:
                    result.assets_updated += 1
                await _apply_local_fields(conn, org, r.id, record, result)

            seen_edge_refs: list[str] = []
            name_to_id: dict[tuple[str, str], UUID | None] = {}
            for edge in rels:
                src_id = await _resolve_endpoint(
                    conn, org, source, edge, "from", ref_to_id, name_to_id, result)
                tgt_id = await _resolve_endpoint(
                    conn, org, source, edge, "to", ref_to_id, name_to_id, result)
                if src_id is None or tgt_id is None:
                    side = "from" if src_id is None else "to"
                    result.warnings.append(
                        f"relationship '{_edge_ref(edge)}' references unknown "
                        f"{_endpoint_label(edge, side)}; edge skipped"
                    )
                    continue
                if src_id == tgt_id:
                    result.warnings.append(
                        f"relationship '{_edge_ref(edge)}' is a self-link; edge skipped"
                    )
                    continue
                eref = edge.get("ref") or _edge_ref(edge)
                r = await external_sync.upsert_external_edge(
                    conn, organization_id=org, source=source, source_ref=eref,
                    source_asset_id=src_id, target_asset_id=tgt_id,
                    relationship_type=edge["type"],
                    description=edge.get("description") or "",
                )
                seen_edge_refs.append(eref)
                if r.inserted:
                    result.edges_created += 1
                else:
                    result.edges_updated += 1

            # Only prune edges when the payload spoke about edges at all — an
            # assets-only upload must not wipe the graph's edges.
            if has_relationships:
                result.edges_pruned = await external_sync.prune_stale_edges(
                    conn, organization_id=org, source=source, seen_refs=seen_edge_refs,
                )

            # Staleness is per asset *type* the payload carried, so a
            # multi-file sync under one source tag doesn't self-cannibalize.
            stale: list[str] = []
            for asset_type, refs in seen_by_type.items():
                archived, deleted, missing_refs = await _reconcile_missing(
                    conn, org, source, asset_type, refs, on_missing,
                )
                result.assets_archived += archived
                result.assets_deleted += deleted
                stale += missing_refs
            result.stale = stale

            if dry_run:
                await tx.rollback()
            else:
                await tx.commit()
        except Exception:
            await tx.rollback()
            raise

    return result
