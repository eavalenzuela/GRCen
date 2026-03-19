import csv
import io
import json

import asyncpg

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

    all_columns = ["id", "type", "name", "description", "status", "owner", "created_at"]
    selected = columns if columns else all_columns

    data = []
    for row in rows:
        item = {}
        for col in selected:
            val = row.get(col, "")
            item[col] = str(val) if val is not None else ""
        data.append(item)

    if format == "json":
        return json.dumps(data, indent=2, default=str)

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=selected)
    writer.writeheader()
    writer.writerows(data)
    return output.getvalue()
