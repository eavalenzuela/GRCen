"""PDF report generation via WeasyPrint.

Renders print-friendly HTML via Jinja2 and converts to PDF bytes. All reports
share a common header/footer and stylesheet so they feel like the same family
of documents.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

import asyncpg
from fastapi.templating import Jinja2Templates
from weasyprint import HTML

from grcen.models.user import User
from grcen.services import (
    alert_service as alert_svc,
    asset as asset_svc,
    attachment as att_svc,
    framework_service,
    redaction,
    relationship as rel_svc,
)

_templates = Jinja2Templates(directory="src/grcen/templates")


async def _branding_context(pool: asyncpg.Pool, organization_id: UUID | None) -> dict:
    """Pull per-org PDF branding (color, logo, name) for the report shell."""
    if organization_id is None:
        return {"brand_color": "#1e293b", "logo_url": "", "org_name": "GRCen"}
    from grcen.services import organization_service
    org = await organization_service.get_by_id(pool, organization_id)
    if org is None:
        return {"brand_color": "#1e293b", "logo_url": "", "org_name": "GRCen"}
    return {
        "brand_color": org.email_brand_color or "#1e293b",
        "logo_url": org.email_logo_url or "",
        "org_name": org.email_from_name or org.name,
    }


def _render(template_name: str, context: dict) -> bytes:
    """Render a Jinja template and convert it to PDF bytes."""
    env = _templates.env
    template = env.get_template(template_name)
    # Defaults so a caller that forgets to merge branding still renders.
    context.setdefault("brand_color", "#1e293b")
    context.setdefault("logo_url", "")
    context.setdefault("org_name", "GRCen")
    context.setdefault("cover_title", "")
    context.setdefault("cover_subtitle", "")
    html_content = template.render(**context)
    return HTML(string=html_content).write_pdf()


async def render_framework_report(
    pool: asyncpg.Pool,
    framework_id: UUID,
    *,
    organization_id: UUID | None = None,
) -> bytes | None:
    detail = await framework_service.get_framework_detail(
        pool, framework_id, organization_id=organization_id
    )
    if not detail:
        return None
    branding = await _branding_context(pool, organization_id)
    return _render(
        "reports/framework.html",
        {
            "detail": detail,
            "generated_at": datetime.now(UTC),
            "cover_title": detail.framework["name"],
            "cover_subtitle": "Compliance Summary",
            **branding,
        },
    )


async def render_framework_gap_report(
    pool: asyncpg.Pool,
    framework_id: UUID,
    *,
    organization_id: UUID | None = None,
) -> bytes | None:
    """Gap-only PDF: same data as the gap CSV but rendered as a printable doc."""
    rows = await framework_service.gap_report_rows(
        pool, framework_id, organization_id=organization_id
    )
    if not rows:
        return None
    fw_name = await pool.fetchval(
        "SELECT name FROM assets WHERE id = $1", framework_id,
    )
    branding = await _branding_context(pool, organization_id)
    return _render(
        "reports/framework_gap.html",
        {
            "framework_name": fw_name or "Framework",
            "rows": rows,
            "satisfied_count": sum(1 for r in rows if r["satisfied"] == "yes"),
            "gap_count": sum(1 for r in rows if r["satisfied"] == "no"),
            "generated_at": datetime.now(UTC),
            "cover_title": fw_name or "Framework",
            "cover_subtitle": "Gap Report",
            **branding,
        },
    )


async def render_audit_report(
    pool: asyncpg.Pool, audit_id: UUID, *, organization_id: UUID | None = None
) -> bytes | None:
    """Per-audit dossier: scope (certifies frameworks), findings (incidents), and any in-scope assets."""
    audit = await asset_svc.get_asset(pool, audit_id, organization_id=organization_id)
    if audit is None or audit.type.value != "audit":
        return None
    # Frameworks this audit certifies.
    fw_rows = await pool.fetch(
        """SELECT a.id, a.name FROM relationships r
           JOIN assets a ON a.id = r.target_asset_id
           WHERE r.source_asset_id = $1 AND r.relationship_type = 'certifies'
             AND a.type = 'framework'
           ORDER BY a.name""",
        audit_id,
    )
    # Findings — incidents linked via 'resulted_in' / 'affected_by'.
    finding_rows = await pool.fetch(
        """SELECT a.id, a.name, a.status FROM relationships r
           JOIN assets a ON a.id = r.target_asset_id
           WHERE r.source_asset_id = $1
             AND r.relationship_type IN ('resulted_in', 'affected_by')
             AND a.type = 'incident'
           ORDER BY a.name""",
        audit_id,
    )
    # In-scope assets — anything else linked to this audit.
    in_scope = await pool.fetch(
        """SELECT DISTINCT a.id, a.name, a.type::text AS type, a.status
           FROM assets a
           JOIN relationships r ON
                (r.source_asset_id = $1 AND r.target_asset_id = a.id)
                OR (r.target_asset_id = $1 AND r.source_asset_id = a.id)
           WHERE a.type NOT IN ('framework', 'incident')
           ORDER BY a.type::text, a.name""",
        audit_id,
    )
    branding = await _branding_context(pool, organization_id)
    return _render(
        "reports/audit.html",
        {
            "audit": audit,
            "frameworks": [dict(r) for r in fw_rows],
            "findings": [dict(r) for r in finding_rows],
            "in_scope": [dict(r) for r in in_scope],
            "generated_at": datetime.now(UTC),
            "cover_title": audit.name,
            "cover_subtitle": "Audit Report",
            **branding,
        },
    )


async def render_asset_list_report(
    pool: asyncpg.Pool,
    *,
    organization_id: UUID | None = None,
    user: User | None = None,
    asset_type=None,
    q: str | None = None,
    status: str | None = None,
    tag: str | None = None,
) -> bytes:
    """Filtered asset list as a paginated PDF. Honors redaction."""
    items, total = await asset_svc.list_assets(
        pool,
        asset_type=asset_type, q=q, status=status, tag=tag,
        page=1, page_size=500,  # cap so the doc stays sane
        organization_id=organization_id,
    )
    redacted = []
    for a in items:
        meta = redaction.redact_metadata(a.metadata_, a.type, user)
        redacted.append({
            "id": a.id, "name": a.name, "type": a.type.value,
            "status": a.status.value, "owner": a.owner or "",
            "criticality": a.criticality or "",
            "metadata": meta,
        })
    branding = await _branding_context(pool, organization_id)
    return _render(
        "reports/asset_list.html",
        {
            "items": redacted,
            "total": total,
            "filter_summary": _summarize_filters(asset_type, q, status, tag),
            "generated_at": datetime.now(UTC),
            "cover_title": "Asset Inventory",
            "cover_subtitle": _summarize_filters(asset_type, q, status, tag) or "All assets",
            **branding,
        },
    )


def _summarize_filters(asset_type, q, status, tag) -> str:
    parts = []
    if asset_type:
        parts.append(f"type={asset_type.value if hasattr(asset_type, 'value') else asset_type}")
    if status:
        parts.append(f"status={status}")
    if tag:
        parts.append(f"tag={tag}")
    if q:
        parts.append(f"q={q!r}")
    return ", ".join(parts)


async def render_asset_report(
    pool: asyncpg.Pool, asset_id: UUID, user: User | None = None,
    *,
    organization_id: UUID | None = None,
) -> bytes | None:
    asset = await asset_svc.get_asset(pool, asset_id, organization_id=organization_id)
    if not asset:
        return None

    rels = await rel_svc.list_relationships_for_asset(pool, asset_id)
    # Name-resolve each side of every edge in one batch.
    ids: set[UUID] = set()
    for r in rels:
        ids.add(r.source_asset_id)
        ids.add(r.target_asset_id)
    name_rows = await pool.fetch(
        "SELECT id, name, type::text AS type FROM assets WHERE id = ANY($1::uuid[])",
        list(ids),
    )
    names = {row["id"]: {"name": row["name"], "type": row["type"]} for row in name_rows}

    rel_rows = []
    for r in rels:
        is_outgoing = r.source_asset_id == asset_id
        other_id = r.target_asset_id if is_outgoing else r.source_asset_id
        rel_rows.append({
            "direction": "outgoing" if is_outgoing else "incoming",
            "type": r.relationship_type,
            "other_name": names.get(other_id, {}).get("name", "?"),
            "other_type": names.get(other_id, {}).get("type", ""),
            "description": r.description or "",
        })

    attachments = await att_svc.list_attachments(pool, asset_id)
    alerts = await alert_svc.list_alerts(pool, asset_id)

    meta = asset.metadata_
    if isinstance(meta, str):
        meta = json.loads(meta or "{}")
    meta = meta or {}
    meta = redaction.redact_metadata(meta, asset.type, user)

    branding = await _branding_context(pool, organization_id)
    return _render(
        "reports/asset.html",
        {
            "asset": asset,
            "meta": meta,
            "relationships": rel_rows,
            "attachments": attachments,
            "alerts": alerts,
            "generated_at": datetime.now(UTC),
            "cover_title": asset.name,
            "cover_subtitle": f"{asset.type.value.replace('_', ' ').title()} Dossier",
            **branding,
        },
    )
