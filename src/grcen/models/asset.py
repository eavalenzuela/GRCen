import enum
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
    owner: str | None
    metadata_: dict | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row) -> "Asset":
        return cls(
            id=row["id"],
            type=AssetType(row["type"]),
            name=row["name"],
            description=row["description"],
            status=AssetStatus(row["status"]),
            owner=row["owner"],
            metadata_=row["metadata"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
