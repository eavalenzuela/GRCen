from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from grcen.models.asset import AssetStatus, AssetType


class AssetCreate(BaseModel):
    type: AssetType
    name: str
    description: str | None = None
    status: AssetStatus = AssetStatus.ACTIVE
    owner_id: UUID | None = None
    metadata_: dict | None = None

    model_config = ConfigDict(populate_by_name=True)


class AssetUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    status: AssetStatus | None = None
    owner_id: UUID | None = None
    metadata_: dict | None = None


class AssetResponse(BaseModel):
    id: UUID
    type: AssetType
    name: str
    description: str | None
    status: AssetStatus
    owner: str | None
    owner_id: UUID | None
    metadata_: dict | None
    created_at: datetime
    updated_at: datetime
    updated_by: UUID | None = None

    model_config = ConfigDict(from_attributes=True)


class AssetListResponse(BaseModel):
    items: list[AssetResponse]
    total: int
    page: int
    page_size: int
