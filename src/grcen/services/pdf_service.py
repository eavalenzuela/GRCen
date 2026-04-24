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

from grcen.services import (
    alert_service as alert_svc,
    asset as asset_svc,
    attachment as att_svc,
    framework_service,
    relationship as rel_svc,
)

_templates = Jinja2Templates(directory="src/grcen/templates")


def _render(template_name: str, context: dict) -> bytes:
    """Render a Jinja template and convert it to PDF bytes."""
    env = _templates.env
    template = env.get_template(template_name)
    html_content = template.render(**context)
    return HTML(string=html_content).write_pdf()


async def render_framework_report(
    pool: asyncpg.Pool, framework_id: UUID
) -> bytes | None:
    detail = await framework_service.get_framework_detail(pool, framework_id)
    if not detail:
        return None
    return _render(
        "reports/framework.html",
        {
            "detail": detail,
            "generated_at": datetime.now(UTC),
        },
    )


async def render_asset_report(pool: asyncpg.Pool, asset_id: UUID) -> bytes | None:
    asset = await asset_svc.get_asset(pool, asset_id)
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

    return _render(
        "reports/asset.html",
        {
            "asset": asset,
            "meta": meta,
            "relationships": rel_rows,
            "attachments": attachments,
            "alerts": alerts,
            "generated_at": datetime.now(UTC),
        },
    )
