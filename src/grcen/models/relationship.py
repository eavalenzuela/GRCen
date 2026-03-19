from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from grcen.models.asset import Asset


@dataclass
class Relationship:
    id: UUID
    source_asset_id: UUID
    target_asset_id: UUID
    relationship_type: str
    description: str | None
    created_at: datetime
    updated_at: datetime
    source_asset: Asset | None = None
    target_asset: Asset | None = None

    @classmethod
    def from_row(cls, row, prefix: str = "") -> "Relationship":
        return cls(
            id=row[f"{prefix}id"],
            source_asset_id=row[f"{prefix}source_asset_id"],
            target_asset_id=row[f"{prefix}target_asset_id"],
            relationship_type=row[f"{prefix}relationship_type"],
            description=row[f"{prefix}description"],
            created_at=row[f"{prefix}created_at"],
            updated_at=row[f"{prefix}updated_at"],
        )
