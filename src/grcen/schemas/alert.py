from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from grcen.models.alert import ScheduleType


class AlertCreate(BaseModel):
    asset_id: UUID
    title: str
    message: str | None = None
    schedule_type: ScheduleType
    cron_expression: str | None = None
    next_fire_at: datetime | None = None
    enabled: bool = True


class AlertUpdate(BaseModel):
    title: str | None = None
    message: str | None = None
    schedule_type: ScheduleType | None = None
    cron_expression: str | None = None
    next_fire_at: datetime | None = None
    enabled: bool | None = None


class AlertResponse(BaseModel):
    id: UUID
    asset_id: UUID
    title: str
    message: str | None
    schedule_type: ScheduleType
    cron_expression: str | None
    next_fire_at: datetime | None
    enabled: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
