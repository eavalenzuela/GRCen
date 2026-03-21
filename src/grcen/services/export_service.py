import csv
import io
import json

import asyncpg

from grcen.custom_fields import CUSTOM_FIELDS
from grcen.models.asset import AssetStatus, AssetType


async def export_assets(
    pool: asyncpg.Pool,
    format: str = "csv",
    asset_types: list[AssetType] | None = None,
    status: AssetStatus | None = None,
    columns: list[str] | None = None,
) -> str:
    conditions: list[str] = []
    params: list = []
    idx = 1

    if asset_types:
        placeholders = ", ".join(f"${i}" for i in range(idx, idx + len(asset_types)))
        conditions.append(f"type IN ({placeholders})")
        params.extend(t.value for t in asset_types)
        idx += len(asset_types)
    if status:
        conditions.append(f"status = ${idx}")
        params.append(status.value)
        idx += 1

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = await pool.fetch(f"SELECT * FROM assets {where} ORDER BY name", *params)

    base_columns = ["id", "type", "name", "description", "status", "owner", "created_at"]

    # Collect custom field column names for the exported asset types
    custom_col_names: list[str] = []
    seen: set[str] = set()
    types_to_include = asset_types if asset_types else list(AssetType)
    for at in types_to_include:
        for f in CUSTOM_FIELDS.get(at, []):
            if f.name not in seen:
                custom_col_names.append(f.name)
                seen.add(f.name)

    all_columns = base_columns + custom_col_names
    selected = columns if columns else all_columns

    data = []
    for row in rows:
        item = {}
        metadata = row.get("metadata") or {}
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        for col in selected:
            if col in ("id", "type", "name", "description", "status", "owner", "created_at", "updated_at"):
                val = row.get(col, "")
                item[col] = str(val) if val is not None else ""
            else:
                val = metadata.get(col, "")
                item[col] = str(val) if val is not None else ""
        data.append(item)

    if format == "json":
        return json.dumps(data, indent=2, default=str)

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=selected)
    writer.writeheader()
    writer.writerows(data)
    return output.getvalue()
