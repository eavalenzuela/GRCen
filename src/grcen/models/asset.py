import enum
import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


class AssetType(enum.StrEnum):
    PERSON = "person"
    POLICY = "policy"
    PRODUCT = "product"
    SYSTEM = "system"
    DEVICE = "device"
    DATA_CATEGORY = "data_category"
    AUDIT = "audit"
    REQUIREMENT = "requirement"
    PROCESS = "process"
    INTELLECTUAL_PROPERTY = "intellectual_property"
    RISK = "risk"
    ORGANIZATIONAL_UNIT = "organizational_unit"
    VENDOR = "vendor"
    CONTROL = "control"
    INCIDENT = "incident"
    FRAMEWORK = "framework"


class AssetStatus(enum.StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    DRAFT = "draft"
    ARCHIVED = "archived"


@dataclass
class Asset:
    id: UUID
    type: AssetType
    name: str
    description: str | None
    status: AssetStatus
    owner: str | None  # display name (resolved from owner_id JOIN)
    owner_id: UUID | None  # FK to assets table
    metadata_: dict | None
    created_at: datetime
    updated_at: datetime
    updated_by: UUID | None = None
    tags: list[str] | None = None
    criticality: str | None = None

    @classmethod
    def from_row(cls, row) -> "Asset":
        # Prefer JOINed owner_name over legacy text column
        owner_display = row.get("owner_name") if "owner_name" in row.keys() else row.get("owner")
        return cls(
            id=row["id"],
            type=AssetType(row["type"]),
            name=row["name"],
            description=row["description"],
            status=AssetStatus(row["status"]),
            owner=owner_display,
            owner_id=row.get("owner_id"),
            metadata_=json.loads(row["metadata"]) if isinstance(row["metadata"], str) else row["metadata"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            updated_by=row.get("updated_by"),
            tags=list(row["tags"]) if row.get("tags") else [],
            criticality=row.get("criticality"),
        )
