import csv
import io
import json

import asyncpg

from grcen.custom_fields import CUSTOM_FIELDS
from grcen.models.asset import AssetStatus, AssetType
from grcen.models.user import User
from grcen.services import asset as asset_svc
from grcen.services import redaction

# Generous upper bound on rows in one export. Orgs in this tool are far below it;
# beyond it the export is truncated (callers should surface that if it matters).
_EXPORT_CAP = 100_000


async def export_assets(
    pool: asyncpg.Pool,
    format: str = "csv",
    asset_types: list[AssetType] | None = None,
    status: AssetStatus | str | None = None,
    columns: list[str] | None = None,
    user: User | None = None,
    organization_id=None,
    *,
    asset_type: AssetType | None = None,
    q: str | None = None,
    owner: str | None = None,
    tag: str | None = None,
    created_after: str | None = None,
    created_before: str | None = None,
    meta_key: str | None = None,
    meta_value: str | None = None,
    unlinked: bool = False,
    sort: str = "name",
    order: str = "asc",
) -> str:
    """Serialize assets to CSV/JSON.

    Row selection delegates to ``asset_svc.list_assets`` so an export matches a
    filtered+sorted list view exactly (export-from-view parity), and masking goes
    through the override-aware path so per-org / per-asset sensitivity promotions
    are honored (the old code-only redaction leaked them).
    """
    status_val = status.value if isinstance(status, AssetStatus) else status
    metadata_filters = {meta_key: meta_value} if (meta_key and meta_value) else None
    assets, _ = await asset_svc.list_assets(
        pool,
        asset_type=asset_type,
        asset_types=asset_types,
        page=1,
        page_size=_EXPORT_CAP,
        q=q,
        status=status_val,
        owner=owner,
        created_after=created_after,
        created_before=created_before,
        metadata_filters=metadata_filters,
        tag=tag,
        unlinked=unlinked,
        sort=sort,
        order=order,
        organization_id=organization_id,
    )
    await redaction.redact_assets_by_type(pool, assets, user, organization_id)

    base_columns = ["id", "type", "name", "description", "status", "owner", "created_at"]
    custom_col_names: list[str] = []
    seen: set[str] = set()
    if asset_type is not None:
        types_to_include: list[AssetType] = [asset_type]
    elif asset_types:
        types_to_include = asset_types
    else:
        types_to_include = list(AssetType)
    for at in types_to_include:
        for f in CUSTOM_FIELDS.get(at, []):
            if f.name not in seen:
                custom_col_names.append(f.name)
                seen.add(f.name)

    all_columns = base_columns + custom_col_names
    selected = columns if columns else all_columns

    def _core(a, col):
        return {
            "id": str(a.id),
            "type": a.type.value,
            "name": a.name or "",
            "description": a.description or "",
            "status": a.status.value,
            "owner": a.owner or "",
            "created_at": str(a.created_at) if a.created_at else "",
            "updated_at": str(a.updated_at) if a.updated_at else "",
        }[col]

    data = []
    for a in assets:
        meta = a.metadata_ or {}
        item = {}
        for col in selected:
            if col in ("id", "type", "name", "description", "status", "owner", "created_at", "updated_at"):
                item[col] = _core(a, col)
            else:
                val = meta.get(col, "")
                item[col] = str(val) if val is not None else ""
        data.append(item)

    if format == "json":
        return json.dumps(data, indent=2, default=str)

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=selected)
    writer.writeheader()
    writer.writerows(data)
    return output.getvalue()
