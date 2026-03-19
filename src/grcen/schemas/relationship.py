from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from grcen.schemas.asset import AssetResponse


class RelationshipCreate(BaseModel):
    source_asset_id: UUID
    target_asset_id: UUID
    relationship_type: str
    description: str | None = None


class RelationshipUpdate(BaseModel):
    relationship_type: str | None = None
    description: str | None = None


class RelationshipResponse(BaseModel):
    id: UUID
    source_asset_id: UUID
    target_asset_id: UUID
    relationship_type: str
    description: str | None
    created_at: datetime
    updated_at: datetime
    source_asset: AssetResponse | None = None
    target_asset: AssetResponse | None = None

    model_config = ConfigDict(from_attributes=True)
