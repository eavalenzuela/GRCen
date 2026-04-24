from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from grcen.models.attachment import AttachmentKind


class AttachmentCreate(BaseModel):
    kind: AttachmentKind
    name: str = Field(min_length=1, max_length=255)
    url_or_path: str | None = Field(default=None, max_length=2048)


class AttachmentResponse(BaseModel):
    id: UUID
    asset_id: UUID | None = None
    relationship_id: UUID | None = None
    kind: AttachmentKind
    name: str
    url_or_path: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
