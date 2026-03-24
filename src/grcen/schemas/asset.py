from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from grcen.models.asset import AssetStatus, AssetType


def _validate_tags(v: list[str] | None) -> list[str] | None:
    if v is None:
        return v
    if len(v) > 50:
        raise ValueError("maximum 50 tags allowed")
    for tag in v:
        if len(tag) > 100:
            raise ValueError("each tag must be 100 characters or fewer")
    return v


class AssetCreate(BaseModel):
    type: AssetType
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=10000)
    status: AssetStatus = AssetStatus.ACTIVE
    owner_id: UUID | None = None
    metadata_: dict | None = None
    tags: list[str] | None = None
    criticality: str | None = Field(default=None, max_length=20)

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, v: list[str] | None) -> list[str] | None:
        return _validate_tags(v)


class AssetUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=10000)
    status: AssetStatus | None = None
    owner_id: UUID | None = None
    metadata_: dict | None = None
    tags: list[str] | None = None
    criticality: str | None = Field(default=None, max_length=20)

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, v: list[str] | None) -> list[str] | None:
        return _validate_tags(v)


class AssetResponse(BaseModel):
    id: UUID
    type: AssetType
    name: str
    description: str | None
    status: AssetStatus
    owner: str | None
    owner_id: UUID | None
    metadata_: dict | None
    tags: list[str] | None = None
    criticality: str | None = None
    created_at: datetime
    updated_at: datetime
    updated_by: UUID | None = None

    model_config = ConfigDict(from_attributes=True)


class AssetListResponse(BaseModel):
    items: list[AssetResponse]
    total: int
    page: int
    page_size: int
