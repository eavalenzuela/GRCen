"""Tests for the generic external-source upsert endpoint (roadmap I5)."""
import json
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from grcen.main import app
from grcen.models.asset import AssetType
from grcen.permissions import UserRole
from grcen.services import asset as asset_svc
from grcen.services import generic_sync, organization_service
from grcen.services.auth import create_user
from grcen.services.token_service import create_token

SOURCE = "etl:test"


async def _org(pool):
    return await organization_service.get_default_org_id(pool)


def _asset_rec(ref, name, atype="system", **over):
    rec = {"ref": ref, "type": atype, "name": name}
    rec.update(over)
    return rec


def _payload(*, assets=None, relationships=None, source=SOURCE, **over):
    body = {"source": source}
    if assets is not None:
        body["assets"] = assets
    if relationships is not None:
        body["relationships"] = relationships
    body.update(over)
    return body


async def _row(pool, org, ref, source=SOURCE):
    return await pool.fetchrow(
        """SELECT id, type, name, description, status, metadata, owner_id, tags, criticality
           FROM assets
           WHERE organization_id = $1 AND source = $2 AND source_ref = $3""",
        org, source, ref,
    )


# ── idempotency: the whole point ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resync_updates_in_place_rather_than_duplicating(pool):
    org = await _org(pool)
    body = _payload(assets=[_asset_rec("srv-1", "Prod Web")])

    first = await generic_sync.sync(pool, body, organization_id=org)
    assert first.assets_created == 1 and first.assets_updated == 0

    body["assets"][0]["name"] = "Prod Web Tier"
    second = await generic_sync.sync(pool, body, organization_id=org)
    assert second.assets_created == 0 and second.assets_updated == 1

    count = await pool.fetchval(
        "SELECT count(*) FROM assets WHERE organization_id = $1 AND source = $2",
        org, SOURCE,
    )
    assert count == 1
    row = await _row(pool, org, "srv-1")
    assert row["name"] == "Prod Web Tier"


@pytest.mark.asyncio
async def test_dry_run_writes_nothing(pool):
    org = await _org(pool)
    res = await generic_sync.sync(
        pool, _payload(assets=[_asset_rec("srv-1", "Prod Web")]),
        organization_id=org, dry_run=True,
    )
    assert res.assets_created == 1 and res.dry_run is True
    assert await pool.fetchval(
        "SELECT count(*) FROM assets WHERE source = $1", SOURCE) == 0


# ── field projection ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_flat_custom_fields_and_metadata_bag_both_land(pool):
    """A CSV row read into a dict posts through unchanged; an explicit bag wins."""
    org = await _org(pool)
    await generic_sync.sync(pool, _payload(assets=[
        _asset_rec("srv-1", "Prod Web",
                   environment="production", hosting="cloud",
                   metadata={"provider": "aws", "hosting": "hybrid"}),
    ]), organization_id=org)

    meta = json.loads((await _row(pool, org, "srv-1"))["metadata"])
    assert meta["environment"] == "production"   # flat key coerced through
    assert meta["provider"] == "aws"             # explicit bag
    assert meta["hosting"] == "hybrid"           # bag beats flat key
    assert meta["external_id"] == "srv-1"        # default, so uvt/CSPM resolve


@pytest.mark.asyncio
async def test_owner_tags_criticality_and_status(pool):
    org = await _org(pool)
    await asset_svc.create_asset(
        pool, organization_id=org, type=AssetType.PERSON, name="Ada Lovelace")

    await generic_sync.sync(pool, _payload(assets=[
        _asset_rec("srv-1", "Prod Web", owner="Ada Lovelace",
                   tags=["prod", "pci"], criticality="high", status="draft"),
    ]), organization_id=org)

    row = await _row(pool, org, "srv-1")
    assert row["owner_id"] is not None
    assert sorted(row["tags"]) == ["pci", "prod"]
    assert row["criticality"] == "high"
    assert row["status"] == "draft"


@pytest.mark.asyncio
async def test_unresolved_owner_warns_without_failing(pool):
    org = await _org(pool)
    res = await generic_sync.sync(pool, _payload(assets=[
        _asset_rec("srv-1", "Prod Web", owner="Nobody At All"),
    ]), organization_id=org)
    assert res.assets_created == 1
    assert not res.errors
    assert any("Nobody At All" in w for w in res.warnings)
    assert (await _row(pool, org, "srv-1"))["owner_id"] is None


# ── relationships ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_relationships_by_ref_within_batch(pool):
    org = await _org(pool)
    res = await generic_sync.sync(pool, _payload(
        assets=[_asset_rec("srv-1", "Web"), _asset_rec("db-1", "DB")],
        relationships=[{"from": "srv-1", "to": "db-1", "type": "depends_on"}],
    ), organization_id=org)
    assert res.edges_created == 1

    edge = await pool.fetchrow(
        """SELECT r.relationship_type, s.source_ref AS src, t.source_ref AS tgt
           FROM relationships r
           JOIN assets s ON s.id = r.source_asset_id
           JOIN assets t ON t.id = r.target_asset_id
           WHERE r.organization_id = $1 AND r.source = $2""",
        org, SOURCE,
    )
    assert (edge["src"], edge["relationship_type"], edge["tgt"]) == (
        "srv-1", "depends_on", "db-1")


@pytest.mark.asyncio
async def test_relationships_resolve_against_earlier_runs(pool):
    """A relationships-only payload wires up an inventory synced previously."""
    org = await _org(pool)
    await generic_sync.sync(pool, _payload(
        assets=[_asset_rec("srv-1", "Web"), _asset_rec("db-1", "DB")],
    ), organization_id=org)

    res = await generic_sync.sync(pool, _payload(
        relationships=[{"from": "srv-1", "to": "db-1", "type": "depends_on"}],
    ), organization_id=org)
    assert res.edges_created == 1 and not res.warnings


@pytest.mark.asyncio
async def test_dangling_and_self_refs_warn_but_dont_block(pool):
    org = await _org(pool)
    res = await generic_sync.sync(pool, _payload(
        assets=[_asset_rec("srv-1", "Web"), _asset_rec("db-1", "DB")],
        relationships=[
            {"from": "srv-1", "to": "ghost", "type": "depends_on"},
            {"from": "srv-1", "to": "srv-1", "type": "depends_on"},
            {"from": "srv-1", "to": "db-1", "type": "depends_on"},
        ],
    ), organization_id=org)
    assert res.edges_created == 1
    assert not res.errors
    assert len(res.warnings) == 2


@pytest.mark.asyncio
async def test_assets_only_payload_does_not_prune_edges(pool):
    org = await _org(pool)
    await generic_sync.sync(pool, _payload(
        assets=[_asset_rec("srv-1", "Web"), _asset_rec("db-1", "DB")],
        relationships=[{"from": "srv-1", "to": "db-1", "type": "depends_on"}],
    ), organization_id=org)

    res = await generic_sync.sync(pool, _payload(
        assets=[_asset_rec("srv-1", "Web"), _asset_rec("db-1", "DB")],
    ), organization_id=org)
    assert res.edges_pruned == 0
    assert await pool.fetchval(
        "SELECT count(*) FROM relationships WHERE source = $1", SOURCE) == 1


@pytest.mark.asyncio
async def test_dropped_edge_is_pruned_when_relationships_present(pool):
    org = await _org(pool)
    await generic_sync.sync(pool, _payload(
        assets=[_asset_rec("srv-1", "Web"), _asset_rec("db-1", "DB")],
        relationships=[{"from": "srv-1", "to": "db-1", "type": "depends_on"}],
    ), organization_id=org)

    res = await generic_sync.sync(pool, _payload(
        assets=[_asset_rec("srv-1", "Web"), _asset_rec("db-1", "DB")],
        relationships=[],
    ), organization_id=org)
    assert res.edges_pruned == 1


# ── name-based linking to assets this source doesn't own ─────────────────────


async def _edges(pool, org, source=SOURCE):
    return await pool.fetch(
        """SELECT r.relationship_type, r.source_ref,
                  s.name AS src_name, t.name AS tgt_name
           FROM relationships r
           JOIN assets s ON s.id = r.source_asset_id
           JOIN assets t ON t.id = r.target_asset_id
           WHERE r.organization_id = $1 AND r.source = $2
           ORDER BY r.source_ref""",
        org, source,
    )


@pytest.mark.asyncio
async def test_links_to_a_human_authored_asset_by_name(pool):
    org = await _org(pool)
    await asset_svc.create_asset(
        pool, organization_id=org, type=AssetType.ORGANIZATIONAL_UNIT,
        name="Security Team")

    res = await generic_sync.sync(pool, _payload(
        assets=[_asset_rec("srv-1", "Prod Web")],
        relationships=[{
            "from": "srv-1", "to_name": "Security Team",
            "to_type": "organizational_unit", "type": "owned_by",
        }],
    ), organization_id=org)

    assert res.edges_created == 1 and not res.warnings
    edge = (await _edges(pool, org))[0]
    assert (edge["src_name"], edge["tgt_name"]) == ("Prod Web", "Security Team")
    assert edge["source_ref"] == "srv-1:owned_by:name:organizational_unit:Security Team"


@pytest.mark.asyncio
async def test_name_linking_reaches_other_synced_sources(pool):
    """Not restricted to source IS NULL — an HRIS-synced Person is linkable."""
    org = await _org(pool)
    await generic_sync.sync(pool, _payload(
        assets=[_asset_rec("p-1", "Ada Lovelace", atype="person")],
        source="etl:people"), organization_id=org)

    res = await generic_sync.sync(pool, _payload(
        assets=[_asset_rec("srv-1", "Prod Web")],
        relationships=[{"from": "srv-1", "to_name": "Ada Lovelace",
                        "to_type": "person", "type": "maintained_by"}],
    ), organization_id=org)
    assert res.edges_created == 1 and not res.warnings


@pytest.mark.asyncio
async def test_name_linking_works_on_both_ends(pool):
    org = await _org(pool)
    for name in ("Access Policy", "Security Team"):
        atype = AssetType.POLICY if name == "Access Policy" else AssetType.ORGANIZATIONAL_UNIT
        await asset_svc.create_asset(pool, organization_id=org, type=atype, name=name)

    res = await generic_sync.sync(pool, _payload(relationships=[{
        "from_name": "Access Policy", "from_type": "policy",
        "to_name": "Security Team", "to_type": "organizational_unit",
        "type": "owned_by",
    }]), organization_id=org)
    assert res.edges_created == 1
    assert (await _edges(pool, org))[0]["src_name"] == "Access Policy"


@pytest.mark.asyncio
async def test_unmatched_name_warns_and_creates_nothing(pool):
    org = await _org(pool)
    before = await pool.fetchval("SELECT count(*) FROM assets WHERE organization_id = $1", org)

    res = await generic_sync.sync(pool, _payload(
        assets=[_asset_rec("srv-1", "Prod Web")],
        relationships=[{"from": "srv-1", "to_name": "Ghost Team",
                        "to_type": "organizational_unit", "type": "owned_by"}],
    ), organization_id=org)

    assert res.edges_created == 0
    assert any("Ghost Team" in w and "organizational_unit" in w for w in res.warnings)
    # One new asset (srv-1) and nothing invented for the dangling name.
    after = await pool.fetchval("SELECT count(*) FROM assets WHERE organization_id = $1", org)
    assert after == before + 1


@pytest.mark.asyncio
async def test_ambiguous_name_takes_oldest_and_warns(pool):
    org = await _org(pool)
    first = await asset_svc.create_asset(
        pool, organization_id=org, type=AssetType.ORGANIZATIONAL_UNIT, name="Ops")
    await asset_svc.create_asset(
        pool, organization_id=org, type=AssetType.ORGANIZATIONAL_UNIT, name="Ops")

    res = await generic_sync.sync(pool, _payload(
        assets=[_asset_rec("srv-1", "Prod Web")],
        relationships=[{"from": "srv-1", "to_name": "Ops",
                        "to_type": "organizational_unit", "type": "owned_by"}],
    ), organization_id=org)

    assert res.edges_created == 1
    assert any("matches 2 assets" in w for w in res.warnings)
    target = await pool.fetchval(
        "SELECT target_asset_id FROM relationships WHERE organization_id = $1 AND source = $2",
        org, SOURCE)
    assert target == first.id


@pytest.mark.asyncio
async def test_name_lookup_is_org_scoped(pool):
    org = await _org(pool)
    other = await organization_service.create_organization(
        pool, name="Other", slug=f"other-{uuid.uuid4().hex[:8]}")
    await asset_svc.create_asset(
        pool, organization_id=other.id, type=AssetType.ORGANIZATIONAL_UNIT,
        name="Security Team")

    res = await generic_sync.sync(pool, _payload(
        assets=[_asset_rec("srv-1", "Prod Web")],
        relationships=[{"from": "srv-1", "to_name": "Security Team",
                        "to_type": "organizational_unit", "type": "owned_by"}],
    ), organization_id=org)
    assert res.edges_created == 0
    assert any("Security Team" in w for w in res.warnings)


@pytest.mark.asyncio
async def test_addressing_modes_are_exclusive_and_validated(pool):
    org = await _org(pool)

    async def errs(rels):
        return (await generic_sync.sync(
            pool, _payload(relationships=rels), organization_id=org)).errors

    assert any("use one" in e for e in await errs([
        {"from": "a", "to": "b", "to_name": "X", "to_type": "policy", "type": "t"}]))
    assert any("'to_type'" in e for e in await errs([
        {"from": "a", "to_name": "X", "type": "t"}]))
    assert any("invalid 'to_type'" in e for e in await errs([
        {"from": "a", "to_name": "X", "to_type": "nonsense", "type": "t"}]))
    assert any("missing 'to' or 'to_name'" in e for e in await errs([
        {"from": "a", "type": "t"}]))


@pytest.mark.asyncio
async def test_ref_only_edge_refs_are_unchanged_by_the_feature(pool):
    """A derived ref must stay stable, or existing edges churn on re-sync."""
    org = await _org(pool)
    await generic_sync.sync(pool, _payload(
        assets=[_asset_rec("srv-1", "Web"), _asset_rec("db-1", "DB")],
        relationships=[{"from": "srv-1", "to": "db-1", "type": "depends_on"}],
    ), organization_id=org)
    assert (await _edges(pool, org))[0]["source_ref"] == "srv-1:depends_on:db-1"


@pytest.mark.asyncio
async def test_name_linked_edge_is_stable_across_resync(pool):
    org = await _org(pool)
    await asset_svc.create_asset(
        pool, organization_id=org, type=AssetType.ORGANIZATIONAL_UNIT, name="Security Team")
    body = _payload(
        assets=[_asset_rec("srv-1", "Prod Web")],
        relationships=[{"from": "srv-1", "to_name": "Security Team",
                        "to_type": "organizational_unit", "type": "owned_by"}],
    )
    await generic_sync.sync(pool, body, organization_id=org)
    res = await generic_sync.sync(pool, body, organization_id=org)

    assert res.edges_created == 0 and res.edges_updated == 1 and res.edges_pruned == 0
    assert len(await _edges(pool, org)) == 1


def test_csv_name_linking_columns():
    payload, errors, _ = generic_sync.payload_from_csv(
        source=SOURCE,
        relationships_csv=(
            "from,to_name,to_type,relationship_type\n"
            "srv-1,Security Team,organizational_unit,owned_by\n"
        ),
    )
    assert not errors
    assert payload["relationships"] == [{
        "from": "srv-1", "to_name": "Security Team",
        "to_type": "organizational_unit", "type": "owned_by",
    }]


# ── lifecycle (on_missing) ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_on_missing_defaults_to_ignore(pool):
    org = await _org(pool)
    await generic_sync.sync(pool, _payload(
        assets=[_asset_rec("srv-1", "Web"), _asset_rec("srv-2", "App")],
    ), organization_id=org)

    res = await generic_sync.sync(pool, _payload(
        assets=[_asset_rec("srv-1", "Web")]), organization_id=org)
    assert res.stale == ["srv-2"]
    assert res.assets_archived == 0 and res.assets_deleted == 0
    assert (await _row(pool, org, "srv-2"))["status"] == "active"


@pytest.mark.asyncio
async def test_on_missing_archive_and_delete(pool):
    org = await _org(pool)
    both = [_asset_rec("srv-1", "Web"), _asset_rec("srv-2", "App")]
    await generic_sync.sync(pool, _payload(assets=both), organization_id=org)

    res = await generic_sync.sync(pool, _payload(
        assets=[_asset_rec("srv-1", "Web")], on_missing="archive"), organization_id=org)
    assert res.assets_archived == 1
    assert (await _row(pool, org, "srv-2"))["status"] == "archived"

    await generic_sync.sync(pool, _payload(assets=both), organization_id=org)
    res = await generic_sync.sync(pool, _payload(
        assets=[_asset_rec("srv-1", "Web")], on_missing="delete"), organization_id=org)
    assert res.assets_deleted == 1
    assert await _row(pool, org, "srv-2") is None


@pytest.mark.asyncio
async def test_staleness_is_scoped_per_asset_type(pool):
    """systems.csv and devices.csv under one source tag don't cannibalize."""
    org = await _org(pool)
    await generic_sync.sync(pool, _payload(
        assets=[_asset_rec("dev-1", "Laptop", atype="device")],
        on_missing="archive"), organization_id=org)
    res = await generic_sync.sync(pool, _payload(
        assets=[_asset_rec("srv-1", "Web")], on_missing="archive"), organization_id=org)

    assert res.assets_archived == 0
    assert (await _row(pool, org, "dev-1"))["status"] == "active"


@pytest.mark.asyncio
async def test_other_sources_and_human_rows_are_untouched(pool):
    org = await _org(pool)
    human = await asset_svc.create_asset(
        pool, organization_id=org, type=AssetType.SYSTEM, name="Hand-typed")
    await generic_sync.sync(pool, _payload(
        assets=[_asset_rec("o-1", "Other")], source="etl:other"), organization_id=org)

    await generic_sync.sync(pool, _payload(
        assets=[_asset_rec("srv-1", "Web")], on_missing="delete"), organization_id=org)

    assert await pool.fetchval("SELECT count(*) FROM assets WHERE id = $1", human.id) == 1
    assert await pool.fetchval(
        "SELECT count(*) FROM assets WHERE source = 'etl:other'") == 1


# ── validation & guards ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reserved_source_tags_rejected(pool):
    org = await _org(pool)
    for src in ("scim", "cmdb", "CMDB", "hris:workday", "grcen-pack:soc2"):
        res = await generic_sync.sync(
            pool, _payload(assets=[_asset_rec("x", "X")], source=src),
            organization_id=org)
        assert res.errors, f"{src} should be rejected"
        assert "reserved" in res.errors[0]


@pytest.mark.asyncio
async def test_validation_errors(pool):
    org = await _org(pool)

    async def errs(body):
        return (await generic_sync.sync(pool, body, organization_id=org)).errors

    assert "missing 'source'" in await errs({"assets": []})
    assert any("'ref'" in e for e in await errs(
        _payload(assets=[{"type": "system", "name": "X"}])))
    assert any("invalid type" in e for e in await errs(
        _payload(assets=[_asset_rec("a", "X", atype="nonsense")])))
    assert any("invalid status" in e for e in await errs(
        _payload(assets=[_asset_rec("a", "X", status="nonsense")])))
    assert any("duplicate asset ref" in e for e in await errs(
        _payload(assets=[_asset_rec("a", "X"), _asset_rec("a", "Y")])))
    assert any("on_missing" in e for e in await errs(
        _payload(assets=[_asset_rec("a", "X")], on_missing="nuke")))
    assert any("must contain" in e for e in await errs({"source": SOURCE}))


@pytest.mark.asyncio
async def test_validation_failure_writes_nothing(pool):
    org = await _org(pool)
    res = await generic_sync.sync(pool, _payload(assets=[
        _asset_rec("srv-1", "Web"),
        _asset_rec("srv-2", "App", atype="nonsense"),
    ]), organization_id=org)
    assert res.errors
    assert await pool.fetchval("SELECT count(*) FROM assets WHERE source = $1", SOURCE) == 0


# ── tenancy ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_same_ref_in_two_orgs_stays_separate(pool):
    org = await _org(pool)
    other = await organization_service.create_organization(
        pool, name="Other", slug=f"other-{uuid.uuid4().hex[:8]}")

    await generic_sync.sync(pool, _payload(assets=[_asset_rec("srv-1", "Ours")]),
                            organization_id=org)
    await generic_sync.sync(pool, _payload(assets=[_asset_rec("srv-1", "Theirs")]),
                            organization_id=other.id)

    assert (await _row(pool, org, "srv-1"))["name"] == "Ours"
    assert (await _row(pool, other.id, "srv-1"))["name"] == "Theirs"


# ── CSV adapter ──────────────────────────────────────────────────────────────


def _from_csv(**kw):
    kw.setdefault("source", SOURCE)
    return generic_sync.payload_from_csv(**kw)


def test_csv_maps_columns_onto_the_json_contract():
    payload, errors, warnings = _from_csv(assets_csv=(
        "ref,type,name,description,status,owner,tags,criticality,hostname\n"
        "srv-1,system,Prod Web,The web tier,draft,Ada Lovelace,\"prod,pci\",high,web01\n"
    ))
    assert not errors and not warnings
    assert payload["assets"] == [{
        "ref": "srv-1", "type": "system", "name": "Prod Web",
        "description": "The web tier", "status": "draft", "owner": "Ada Lovelace",
        "tags": ["prod", "pci"], "criticality": "high", "hostname": "web01",
    }]


def test_csv_header_normalization_bom_and_blank_rows():
    payload, errors, _ = _from_csv(assets_csv=(
        "﻿ Ref , Type , Name \n"
        "srv-1,system,Prod Web\n"
        "\n"
        ",,\n"
        "srv-2,system,App\n"
    ))
    assert not errors
    assert [r["ref"] for r in payload["assets"]] == ["srv-1", "srv-2"]
    assert payload["assets"][0]["type"] == "system"


def test_csv_blank_cells_are_omitted_not_blanked():
    payload, _, _ = _from_csv(assets_csv=(
        "ref,type,name,tags,criticality\n"
        "srv-1,system,Prod Web,,\n"
    ))
    record = payload["assets"][0]
    assert "tags" not in record and "criticality" not in record


def test_csv_ref_aliases_and_default_type():
    payload, errors, _ = _from_csv(
        assets_csv="ci_id,name\nsys_abc,Prod Web\n", default_type="system")
    assert not errors
    assert payload["assets"] == [
        {"ref": "sys_abc", "name": "Prod Web", "type": "system"}]

    # An explicit `ref` wins and leaves external_id alone as a custom field.
    payload, _, _ = _from_csv(
        assets_csv="ref,external_id,type,name\nr1,ext-9,system,Web\n")
    assert payload["assets"][0]["ref"] == "r1"
    assert payload["assets"][0]["external_id"] == "ext-9"


def test_csv_unknown_columns_warn_rather_than_vanish():
    _, errors, warnings = _from_csv(
        assets_csv="ref,type,name,hostnmae\nsrv-1,system,Web,web01\n")
    assert not errors
    assert any("hostnmae" in w and "system" in w for w in warnings)


def test_csv_relationship_columns_and_alias():
    payload, errors, _ = _from_csv(
        relationships_csv="from,to,relationship_type,description\nsrv-1,db-1,depends_on,reads\n")
    assert not errors
    assert payload["relationships"] == [
        {"from": "srv-1", "to": "db-1", "type": "depends_on", "description": "reads"}]


def test_csv_only_supplied_sections_appear_in_payload():
    """Drives the assets-only-doesn't-prune-edges rule through the CSV door."""
    payload, _, _ = _from_csv(assets_csv="ref,type,name\nsrv-1,system,Web\n")
    assert "assets" in payload and "relationships" not in payload

    payload, _, _ = _from_csv(relationships_csv="from,to,type\na,b,depends_on\n")
    assert "relationships" in payload and "assets" not in payload


def test_csv_parse_errors():
    _, errors, _ = _from_csv(assets_csv="")
    assert any("no header row" in e for e in errors)

    _, errors, _ = _from_csv(
        assets_csv="ref,name\nsrv-1,Web\n", default_type="nonsense")
    assert any("invalid default type" in e for e in errors)


@pytest.mark.asyncio
async def test_csv_payload_flows_through_sync(pool):
    org = await _org(pool)
    payload, errors, _ = _from_csv(
        assets_csv="ref,type,name,environment\nsrv-1,system,Web,production\n")
    assert not errors
    res = await generic_sync.sync(pool, payload, organization_id=org)
    assert res.assets_created == 1
    meta = json.loads((await _row(pool, org, "srv-1"))["metadata"])
    assert meta["environment"] == "production"


# ── HTTP endpoint ────────────────────────────────────────────────────────────


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _sync_token(pool) -> str:
    admin = await create_user(
        pool, f"genadmin_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.ADMIN)
    _, raw = await create_token(pool, admin.id, "gen", ["sync"], is_service_account=True)
    return raw


@pytest.mark.asyncio
async def test_http_requires_sync_permission(pool):
    body = _payload(assets=[_asset_rec("srv-1", "Web")])
    async with _client() as c:
        assert (await c.post("/api/sync/upsert", json=body)).status_code == 401
        admin = await create_user(
            pool, f"a_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.ADMIN)
        _, raw = await create_token(pool, admin.id, "t", ["view"], is_service_account=True)
        r = await c.post("/api/sync/upsert", json=body,
                         headers={"Authorization": f"Bearer {raw}"})
        assert r.status_code == 403


@pytest.mark.asyncio
async def test_http_upsert_dry_run_then_commit(pool):
    org = await _org(pool)
    raw = await _sync_token(pool)
    body = _payload(
        assets=[_asset_rec("srv-1", "Web"), _asset_rec("db-1", "DB")],
        relationships=[{"from": "srv-1", "to": "db-1", "type": "depends_on"}],
    )
    async with _client() as c:
        h = {"Authorization": f"Bearer {raw}"}
        dr = await c.post("/api/sync/upsert", params={"dry_run": "true"}, json=body, headers=h)
        assert dr.status_code == 200
        assert dr.json()["assets_created"] == 2
        assert await pool.fetchval(
            "SELECT count(*) FROM assets WHERE source = $1", SOURCE) == 0

        r = await c.post("/api/sync/upsert", json=body, headers=h)
        assert r.status_code == 200
        assert r.json()["assets_created"] == 2 and r.json()["edges_created"] == 1

        again = await c.post("/api/sync/upsert", json=body, headers=h)
        assert again.json()["assets_created"] == 0
        assert again.json()["assets_updated"] == 2
        assert await pool.fetchval(
            "SELECT count(*) FROM assets WHERE organization_id = $1 AND source = $2",
            org, SOURCE) == 2


@pytest.mark.asyncio
async def test_http_csv_upsert_both_files_in_one_transaction(pool):
    org = await _org(pool)
    raw = await _sync_token(pool)
    files = {
        "assets": ("assets.csv", "ref,type,name\nsrv-1,system,Web\ndb-1,system,DB\n", "text/csv"),
        "relationships": ("rels.csv", "from,to,type\nsrv-1,db-1,depends_on\n", "text/csv"),
    }
    async with _client() as c:
        h = {"Authorization": f"Bearer {raw}"}
        dr = await c.post("/api/sync/upsert/csv", params={"dry_run": "true"},
                          data={"source": SOURCE}, files=files, headers=h)
        assert dr.status_code == 200 and dr.json()["assets_created"] == 2
        assert await pool.fetchval(
            "SELECT count(*) FROM assets WHERE source = $1", SOURCE) == 0

        r = await c.post("/api/sync/upsert/csv", data={"source": SOURCE},
                         files=files, headers=h)
        body = r.json()
        assert r.status_code == 200
        # The edge resolves against an asset created in the same request.
        assert body["assets_created"] == 2 and body["edges_created"] == 1

        again = await c.post("/api/sync/upsert/csv", data={"source": SOURCE},
                             files=files, headers=h)
        assert again.json()["assets_updated"] == 2
        assert await pool.fetchval(
            "SELECT count(*) FROM assets WHERE organization_id = $1 AND source = $2",
            org, SOURCE) == 2


@pytest.mark.asyncio
async def test_http_csv_default_type_and_column_warning(pool):
    raw = await _sync_token(pool)
    async with _client() as c:
        r = await c.post(
            "/api/sync/upsert/csv",
            data={"source": SOURCE, "default_type": "system"},
            files={"assets": ("a.csv", "ci_id,name,notacolumn\nsys_a,Web,x\n", "text/csv")},
            headers={"Authorization": f"Bearer {raw}"})
        assert r.status_code == 200
        assert r.json()["assets_created"] == 1
        assert any("notacolumn" in w for w in r.json()["warnings"])


@pytest.mark.asyncio
async def test_http_csv_requires_a_file_and_rejects_reserved_source(pool):
    raw = await _sync_token(pool)
    async with _client() as c:
        h = {"Authorization": f"Bearer {raw}"}
        r = await c.post("/api/sync/upsert/csv", data={"source": SOURCE}, headers=h)
        assert r.status_code == 400 and "assets" in r.json()["errors"][0]

        r = await c.post(
            "/api/sync/upsert/csv", data={"source": "cmdb"},
            files={"assets": ("a.csv", "ref,type,name\na,system,X\n", "text/csv")},
            headers=h)
        assert r.status_code == 400 and "reserved" in r.json()["errors"][0]


@pytest.mark.asyncio
async def test_http_csv_requires_sync_permission(pool):
    files = {"assets": ("a.csv", "ref,type,name\na,system,X\n", "text/csv")}
    async with _client() as c:
        r = await c.post("/api/sync/upsert/csv", data={"source": SOURCE}, files=files)
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_http_invalid_payload_400(pool):
    raw = await _sync_token(pool)
    async with _client() as c:
        h = {"Authorization": f"Bearer {raw}"}
        r = await c.post("/api/sync/upsert", json={"source": SOURCE, "assets": "nope"},
                         headers=h)
        assert r.status_code == 400 and r.json()["errors"]

        r = await c.post("/api/sync/upsert",
                         json=_payload(assets=[_asset_rec("a", "X")], source="scim"),
                         headers=h)
        assert r.status_code == 400 and "reserved" in r.json()["errors"][0]
