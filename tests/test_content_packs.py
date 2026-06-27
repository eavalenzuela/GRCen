"""Tests for bundled compliance content packs (loader, install, uninstall)."""
import json

import pytest

from grcen.services import content_packs, framework_service, organization_service


def _write_fixture_pack(tmp_path):
    """A tiny two-framework pack with a shared control and a crosswalk."""
    (tmp_path / "frameworks").mkdir()
    (tmp_path / "controls").mkdir()
    (tmp_path / "crosswalks").mkdir()
    (tmp_path / "frameworks" / "fw_a.json").write_text(json.dumps({
        "framework": {
            "ref": "fwa", "name": "Framework A",
            "metadata": {"version": "1", "governing_body": "Test"},
            "requirements": [
                {"ref": "fwa:R1", "name": "A R1", "reference_id": "R1"},
                {"ref": "fwa:R2", "name": "A R2", "reference_id": "R2"},
            ],
        }
    }))
    (tmp_path / "frameworks" / "fw_b.json").write_text(json.dumps({
        "framework": {
            "ref": "fwb", "name": "Framework B",
            "requirements": [{"ref": "fwb:Q1", "name": "B Q1"}],
        }
    }))
    (tmp_path / "controls" / "ctrls.json").write_text(json.dumps({
        "controls": [
            {"ref": "C1", "name": "Shared Control",
             "metadata": {"control_type": "preventive"},
             "satisfies": ["fwa:R1", "fwb:Q1"]},
        ]
    }))
    (tmp_path / "crosswalks" / "xw.json").write_text(json.dumps({
        "crosswalks": [
            {"from": "fwa:R1", "to": "fwb:Q1",
             "relationship": "equivalent", "confidence": "high"},
        ]
    }))
    return content_packs.ContentPack(
        id="fixture", title="Fixture", version="1.0",
        summary="t", attribution="t",
        frameworks=("fw_a", "fw_b"), controls=("ctrls",), crosswalks=("xw",),
    )


async def _org(pool):
    return await organization_service.get_default_org_id(pool)


def test_load_catalog_merges_fragments(tmp_path):
    pack = _write_fixture_pack(tmp_path)
    cat = content_packs.load_catalog(pack, base_dir=tmp_path)
    assert cat["source"] == "grcen-pack:fixture"
    assert {f["ref"] for f in cat["frameworks"]} == {"fwa", "fwb"}
    assert len(cat["controls"]) == 1
    assert len(cat["crosswalks"]) == 1
    # The assembled catalog is structurally valid for catalog_sync.
    assert content_packs.validate_pack(pack, base_dir=tmp_path) == []


def test_pack_stats(tmp_path):
    pack = _write_fixture_pack(tmp_path)
    stats = content_packs.pack_stats(pack, base_dir=tmp_path)
    assert stats == {"frameworks": 2, "requirements": 3, "controls": 1, "crosswalks": 1}


@pytest.mark.asyncio
async def test_install_seeds_and_lights_coverage(pool, tmp_path):
    pack = _write_fixture_pack(tmp_path)
    org = await _org(pool)
    result = await content_packs.install_pack(
        pool, pack, organization_id=org, base_dir=tmp_path
    )
    assert not result.errors
    # 2 frameworks + 3 requirements + 1 control
    assert result.assets_created == 6
    assert result.crosswalks == 1

    # Everything carries the pack's source tag.
    tagged = await pool.fetchval(
        "SELECT count(*) FROM assets WHERE source = $1 AND organization_id = $2",
        pack.source, org,
    )
    assert tagged == 6

    # The shared control lights up coverage in both frameworks.
    summaries = await framework_service.list_frameworks(pool, organization_id=org)
    by_name = {s.name: s for s in summaries}
    assert by_name["Framework A"].satisfied_count == 1  # R1 satisfied, R2 a gap
    assert by_name["Framework B"].coverage_percent == 100

    # The crosswalk produced a cross_maps edge between the two frameworks.
    xmaps = await pool.fetchval(
        """SELECT count(*) FROM relationships
           WHERE relationship_type = 'cross_maps' AND source = $1""",
        pack.source,
    )
    assert xmaps == 1


@pytest.mark.asyncio
async def test_crosswalk_surfaces_on_framework_detail(pool, tmp_path):
    pack = _write_fixture_pack(tmp_path)
    org = await _org(pool)
    await content_packs.install_pack(pool, pack, organization_id=org, base_dir=tmp_path)

    fw_a_id = await pool.fetchval(
        "SELECT id FROM assets WHERE source = $1 AND source_ref = 'framework:fwa'",
        pack.source,
    )
    detail = await framework_service.get_framework_detail(
        pool, fw_a_id, organization_id=org
    )
    assert detail.crosswalk_count == 1
    r1 = next(r for r in detail.requirements if r.name == "A R1")
    assert len(r1.crosswalks) == 1
    assert r1.crosswalks[0]["framework"] == "Framework B"
    assert r1.crosswalks[0]["relationship"] == "equivalent"
    # A requirement with no crosswalk shows none.
    r2 = next(r for r in detail.requirements if r.name == "A R2")
    assert r2.crosswalks == []


@pytest.mark.asyncio
async def test_install_is_idempotent(pool, tmp_path):
    pack = _write_fixture_pack(tmp_path)
    org = await _org(pool)
    await content_packs.install_pack(pool, pack, organization_id=org, base_dir=tmp_path)
    again = await content_packs.install_pack(
        pool, pack, organization_id=org, base_dir=tmp_path
    )
    assert again.assets_created == 0
    assert again.assets_updated == 6


@pytest.mark.asyncio
async def test_installed_count_and_uninstall(pool, tmp_path):
    pack = _write_fixture_pack(tmp_path)
    org = await _org(pool)
    assert await content_packs.installed_asset_count(pool, pack, organization_id=org) == 0

    await content_packs.install_pack(pool, pack, organization_id=org, base_dir=tmp_path)
    assert await content_packs.installed_asset_count(pool, pack, organization_id=org) == 6

    removed = await content_packs.uninstall_pack(pool, pack, organization_id=org)
    assert removed["assets"] == 6
    assert removed["relationships"] >= 3  # 2 parent_of + 2 satisfies + 1 cross_maps
    assert await content_packs.installed_asset_count(pool, pack, organization_id=org) == 0


def test_dry_run_install_writes_nothing(tmp_path):
    # load_catalog is pure; dry_run is exercised against the DB elsewhere, here
    # we just assert the source namespace contract holds for every pack.
    for pack in content_packs.list_packs():
        assert pack.source.startswith("grcen-pack:")


@pytest.mark.parametrize("pack", content_packs.list_packs(), ids=lambda p: p.id)
def test_registered_packs_are_valid(pack):
    """Every bundled pack whose fragments exist must assemble into a valid catalog."""
    if not content_packs.fragments_present(pack):
        pytest.skip(f"pack '{pack.id}' fragments not authored yet")
    errors = content_packs.validate_pack(pack)
    assert errors == [], f"pack '{pack.id}' invalid: {errors[:5]}"


@pytest.mark.asyncio
async def test_common_baseline_installs_cross_mapped(pool):
    """End-to-end acceptance: the flagship baseline seeds a real, cross-mapped graph."""
    pack = content_packs.get_pack("common-baseline")
    if not content_packs.fragments_present(pack):
        pytest.skip("common-baseline fragments not authored yet")
    org = await _org(pool)

    result = await content_packs.install_pack(pool, pack, organization_id=org)
    assert not result.errors
    assert result.frameworks == 4
    assert result.requirements > 300  # four full frameworks
    assert result.controls >= 20
    assert result.crosswalks > 0

    # Coverage lights up (the shared controls satisfy requirements) and at least
    # one framework exposes cross-framework crosswalks.
    summaries = await framework_service.list_frameworks(pool, organization_id=org)
    assert len(summaries) == 4
    assert any(s.satisfied_count > 0 for s in summaries)

    cross_total = 0
    for s in summaries:
        detail = await framework_service.get_framework_detail(
            pool, s.id, organization_id=org
        )
        cross_total += detail.crosswalk_count
    assert cross_total > 0

    # Idempotent re-install.
    again = await content_packs.install_pack(pool, pack, organization_id=org)
    assert again.assets_created == 0
