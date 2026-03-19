import csv
import io
import json
import uuid
from dataclasses import dataclass, field

import asyncpg

from grcen.models.asset import AssetStatus, AssetType


@dataclass
class ImportResult:
    created: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class ImportPreview:
    total_rows: int = 0
    valid_rows: int = 0
    errors: list[str] = field(default_factory=list)
    sample: list[dict] = field(default_factory=list)


def _parse_csv(content: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(content)))


def _parse_json(content: str) -> list[dict]:
    return json.loads(content)


def _validate_asset_row(row: dict, idx: int) -> list[str]:
    errors = []
    if not row.get("name"):
        errors.append(f"Row {idx}: missing 'name'")
    if not row.get("type"):
        errors.append(f"Row {idx}: missing 'type'")
    else:
        try:
            AssetType(row["type"])
        except ValueError:
            errors.append(f"Row {idx}: invalid type '{row['type']}'")
    if row.get("status"):
        try:
            AssetStatus(row["status"])
        except ValueError:
            errors.append(f"Row {idx}: invalid status '{row['status']}'")
    return errors


def preview_asset_import(content: str, format: str) -> ImportPreview:
    rows = _parse_csv(content) if format == "csv" else _parse_json(content)
    preview = ImportPreview(total_rows=len(rows))
    for idx, row in enumerate(rows, 1):
        errs = _validate_asset_row(row, idx)
        if errs:
            preview.errors.extend(errs)
        else:
            preview.valid_rows += 1
    preview.sample = rows[:5]
    return preview


async def execute_asset_import(
    pool: asyncpg.Pool, content: str, format: str
) -> ImportResult:
    rows = _parse_csv(content) if format == "csv" else _parse_json(content)
    result = ImportResult()

    async with pool.acquire() as conn:
        async with conn.transaction():
            for idx, row in enumerate(rows, 1):
                errs = _validate_asset_row(row, idx)
                if errs:
                    result.errors.extend(errs)
                    continue
                await conn.execute(
                    """
                    INSERT INTO assets (id, type, name, description, status, owner, metadata)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    """,
                    uuid.uuid4(),
                    row["type"],
                    row["name"],
                    row.get("description", ""),
                    row.get("status", "active"),
                    row.get("owner", ""),
                    json.dumps({}),
                )
                result.created += 1

    return result


def _validate_relationship_row(row: dict, idx: int) -> list[str]:
    errors = []
    required = ("source_name", "source_type", "target_name", "target_type", "relationship_type")
    for col_name in required:
        if not row.get(col_name):
            errors.append(f"Row {idx}: missing '{col_name}'")
    return errors


async def execute_relationship_import(
    pool: asyncpg.Pool, content: str, format: str
) -> ImportResult:
    rows = _parse_csv(content) if format == "csv" else _parse_json(content)
    result = ImportResult()

    async with pool.acquire() as conn:
        async with conn.transaction():
            for idx, row in enumerate(rows, 1):
                errs = _validate_relationship_row(row, idx)
                if errs:
                    result.errors.extend(errs)
                    continue

                source = await conn.fetchrow(
                    "SELECT id FROM assets WHERE name = $1 AND type = $2",
                    row["source_name"],
                    row["source_type"],
                )
                target = await conn.fetchrow(
                    "SELECT id FROM assets WHERE name = $1 AND type = $2",
                    row["target_name"],
                    row["target_type"],
                )

                if not source:
                    result.errors.append(
                        f"Row {idx}: source '{row['source_name']}' ({row['source_type']}) not found"
                    )
                    continue
                if not target:
                    result.errors.append(
                        f"Row {idx}: target '{row['target_name']}' ({row['target_type']}) not found"
                    )
                    continue

                await conn.execute(
                    """
                    INSERT INTO relationships
                        (id, source_asset_id, target_asset_id, relationship_type, description)
                    VALUES ($1, $2, $3, $4, $5)
                    """,
                    uuid.uuid4(),
                    source["id"],
                    target["id"],
                    row["relationship_type"],
                    row.get("description", ""),
                )
                result.created += 1

    return result
