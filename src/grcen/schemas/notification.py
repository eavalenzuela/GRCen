from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class NotificationResponse(BaseModel):
    id: UUID
    alert_id: UUID
    title: str
    message: str | None
    read: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
