from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from grcen.models.attachment import AttachmentKind


class AttachmentCreate(BaseModel):
    kind: AttachmentKind
    name: str
    url_or_path: str | None = None


class AttachmentResponse(BaseModel):
    id: UUID
    asset_id: UUID
    kind: AttachmentKind
    name: str
    url_or_path: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
