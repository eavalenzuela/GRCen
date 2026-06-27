"""Bundled compliance content packs.

A *content pack* is a ready-to-install catalog — frameworks and their
requirements, optionally a shared controls library and cross-framework
crosswalks — that ships with GRCen so a brand-new org can seed a real
compliance baseline in one action instead of staring at an empty register.

Packs are assembled from JSON *fragments* under ``src/grcen/content_packs/``
and installed through the same idempotent projection the external catalog sync
uses (``services/catalog_sync``). Each pack installs under its own ``source``
tag (``grcen-pack:<id>``) so packs coexist with — and stay distinct from — any
catalog synced from the autocomply system-of-record (``source = 'autocomply'``)
or anything a human authored (``source IS NULL``). Re-installing upserts in
place; uninstalling deletes exactly the rows tagged with the pack's source.

Fragment layout::

    content_packs/frameworks/<slug>.json    {"framework": {ref, name, requirements: [...]}}
    content_packs/controls/<name>.json      {"controls": [{ref, name, satisfies: [...]}]}
    content_packs/crosswalks/<name>.json    {"crosswalks": [{from, to, relationship}]}

A pack names the fragments it composes; ``load_catalog`` merges them into one
catalog dict (the autocomply export shape) that ``catalog_sync`` consumes.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import asyncpg

from grcen.services import catalog_sync

DATA_DIR = Path(__file__).resolve().parent.parent / "content_packs"
SOURCE_PREFIX = "grcen-pack:"


@dataclass(frozen=True)
class ContentPack:
    """A named, installable bundle of catalog fragments."""

    id: str
    title: str
    version: str
    summary: str
    attribution: str
    frameworks: tuple[str, ...] = ()  # framework fragment slugs
    controls: tuple[str, ...] = ()  # control fragment names
    crosswalks: tuple[str, ...] = ()  # crosswalk fragment names
    tags: tuple[str, ...] = ()

    @property
    def source(self) -> str:
        """The ``assets.source`` / ``relationships.source`` tag for this pack."""
        return f"{SOURCE_PREFIX}{self.id}"


# ── registry ─────────────────────────────────────────────────────────────────
# The flagship `common-baseline` bundles all four frameworks plus the shared
# control library and crosswalks, so one install yields a fully cross-mapped
# starting point. The single-framework packs are for orgs that want just one
# register; installing the baseline AND a single-framework pack would seed that
# framework twice (distinct sources), so the admin UI steers toward one or the
# other.

PACKS: tuple[ContentPack, ...] = (
    ContentPack(
        id="common-baseline",
        title="Common Compliance Baseline",
        version="1.0",
        summary=(
            "NIST CSF 2.0, CIS Controls v8.1, SOC 2, and ISO/IEC 27001:2022 in one "
            "install, tied together by a shared control library and cross-framework "
            "crosswalks — a complete, cross-mapped starting point."
        ),
        attribution=(
            "Framework structures are public references (NIST CSF 2.0 is U.S. "
            "public domain; SOC 2 / ISO 27001 / CIS control identifiers and titles "
            "are reproduced as references, not standard text)."
        ),
        frameworks=("nist-csf-2.0", "cis-v8.1", "soc2", "iso27001"),
        controls=("common-controls",),
        crosswalks=("common-crosswalks",),
        tags=("baseline", "multi-framework", "crosswalked"),
    ),
    ContentPack(
        id="nist-csf-2.0",
        title="NIST CSF 2.0",
        version="2.0",
        summary=(
            "The NIST Cybersecurity Framework 2.0: 6 Functions, 22 Categories, and "
            "their Subcategories as requirements."
        ),
        attribution="NIST Cybersecurity Framework 2.0 (U.S. public domain).",
        frameworks=("nist-csf-2.0",),
        tags=("nist", "csf"),
    ),
    ContentPack(
        id="cis-controls-v8.1",
        title="CIS Controls v8.1",
        version="8.1",
        summary=(
            "The CIS Critical Security Controls v8.1: 18 Controls and their "
            "Safeguards (with Implementation Group tags) as requirements."
        ),
        attribution=(
            "CIS Critical Security Controls v8.1 identifiers and titles "
            "(© Center for Internet Security)."
        ),
        frameworks=("cis-v8.1",),
        tags=("cis",),
    ),
    ContentPack(
        id="soc2-tsc",
        title="SOC 2 (Trust Services Criteria)",
        version="2017 (rev. 2022)",
        summary=(
            "AICPA SOC 2 Trust Services Criteria: the Common Criteria (CC1–CC9) "
            "plus the Availability, Confidentiality, Processing Integrity, and "
            "Privacy categories as requirements."
        ),
        attribution="AICPA Trust Services Criteria identifiers and titles (© AICPA).",
        frameworks=("soc2",),
        tags=("soc2", "aicpa"),
    ),
    ContentPack(
        id="iso27001-2022",
        title="ISO/IEC 27001:2022 (Annex A)",
        version="2022",
        summary=(
            "ISO/IEC 27001:2022 Annex A: all 93 controls across the four themes "
            "(Organizational, People, Physical, Technological) as requirements."
        ),
        attribution="ISO/IEC 27001:2022 Annex A control identifiers and titles (© ISO/IEC).",
        frameworks=("iso27001",),
        tags=("iso", "27001"),
    ),
)

_BY_ID: dict[str, ContentPack] = {p.id: p for p in PACKS}


def list_packs() -> list[ContentPack]:
    """All bundled packs, baseline first."""
    return list(PACKS)


def get_pack(pack_id: str) -> ContentPack | None:
    return _BY_ID.get(pack_id)


# ── fragment assembly ────────────────────────────────────────────────────────


def _read_fragment(base_dir: Path, kind: str, name: str) -> dict:
    path = base_dir / kind / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"content pack fragment not found: {path}")
    data: dict = json.loads(path.read_text())
    return data


def load_catalog(pack: ContentPack, *, base_dir: Path = DATA_DIR) -> dict:
    """Merge a pack's fragments into a single catalog (autocomply export shape)."""
    frameworks: list[dict] = []
    controls: list[dict] = []
    crosswalks: list[dict] = []
    for slug in pack.frameworks:
        frag = _read_fragment(base_dir, "frameworks", slug)
        frameworks.append(frag["framework"])
    for name in pack.controls:
        controls.extend(_read_fragment(base_dir, "controls", name).get("controls", []))
    for name in pack.crosswalks:
        crosswalks.extend(
            _read_fragment(base_dir, "crosswalks", name).get("crosswalks", [])
        )
    return {
        "catalog_version": "1",
        "source": pack.source,
        "frameworks": frameworks,
        "controls": controls,
        "crosswalks": crosswalks,
    }


def pack_stats(pack: ContentPack, *, base_dir: Path = DATA_DIR) -> dict[str, int]:
    """Framework / requirement / control / crosswalk counts for display."""
    cat = load_catalog(pack, base_dir=base_dir)
    requirements = sum(len(f.get("requirements", [])) for f in cat["frameworks"])
    return {
        "frameworks": len(cat["frameworks"]),
        "requirements": requirements,
        "controls": len(cat["controls"]),
        "crosswalks": len(cat["crosswalks"]),
    }


def validate_pack(pack: ContentPack, *, base_dir: Path = DATA_DIR) -> list[str]:
    """Structural validation of a pack's assembled catalog. [] ⇒ valid."""
    try:
        cat = load_catalog(pack, base_dir=base_dir)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        return [str(exc)]
    return catalog_sync.validate_catalog(cat)


def fragments_present(pack: ContentPack, *, base_dir: Path = DATA_DIR) -> bool:
    """True when every fragment a pack references exists on disk."""
    for kind, names in (
        ("frameworks", pack.frameworks),
        ("controls", pack.controls),
        ("crosswalks", pack.crosswalks),
    ):
        for name in names:
            if not (base_dir / kind / f"{name}.json").exists():
                return False
    return True


# ── install / uninstall ──────────────────────────────────────────────────────


async def install_pack(
    pool: asyncpg.Pool,
    pack: ContentPack,
    *,
    organization_id: UUID | None = None,
    dry_run: bool = False,
    prune: bool = False,
    base_dir: Path = DATA_DIR,
) -> catalog_sync.CatalogSyncResult:
    """Project a pack into the asset graph under its own ``source`` tag."""
    catalog = load_catalog(pack, base_dir=base_dir)
    return await catalog_sync.sync_catalog(
        pool,
        catalog,
        organization_id=organization_id,
        source=pack.source,
        dry_run=dry_run,
        prune=prune,
    )


async def installed_asset_count(
    pool: asyncpg.Pool, pack: ContentPack, *, organization_id: UUID
) -> int:
    """How many assets this pack currently has installed for an org (0 ⇒ not installed)."""
    return (
        await pool.fetchval(
            "SELECT count(*) FROM assets WHERE source = $1 AND organization_id = $2",
            pack.source,
            organization_id,
        )
        or 0
    )


async def uninstall_pack(
    pool: asyncpg.Pool, pack: ContentPack, *, organization_id: UUID
) -> dict[str, int]:
    """Delete every asset and relationship tagged with this pack's source for an org.

    Human-authored edges (``source IS NULL``) hanging off a pack asset cascade
    away with the asset — the same trade-off documented for ``sync_catalog
    --prune`` — so uninstall is a deliberate, admin-gated action.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            edges = await conn.execute(
                "DELETE FROM relationships WHERE source = $1 AND organization_id = $2",
                pack.source,
                organization_id,
            )
            assets = await conn.execute(
                "DELETE FROM assets WHERE source = $1 AND organization_id = $2",
                pack.source,
                organization_id,
            )
    return {
        "assets": int(assets.split()[-1]),
        "relationships": int(edges.split()[-1]),
    }
