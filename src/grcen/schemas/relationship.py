from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from grcen.schemas.asset import AssetResponse


class RelationshipCreate(BaseModel):
    source_asset_id: UUID
    target_asset_id: UUID
    relationship_type: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=10000)


class RelationshipUpdate(BaseModel):
    relationship_type: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=10000)


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
