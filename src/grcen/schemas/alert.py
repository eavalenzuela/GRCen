from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from grcen.models.alert import ScheduleType


def _validate_cron(v: str | None) -> str | None:
    if v is None:
        return v
    from apscheduler.triggers.cron import CronTrigger
    try:
        CronTrigger.from_crontab(v)
    except ValueError as e:
        raise ValueError(f"invalid cron expression: {e}") from e
    return v


class AlertCreate(BaseModel):
    asset_id: UUID
    title: str = Field(min_length=1, max_length=255)
    message: str | None = Field(default=None, max_length=10000)
    schedule_type: ScheduleType
    cron_expression: str | None = Field(default=None, max_length=100)
    next_fire_at: datetime | None = None
    enabled: bool = True

    @field_validator("cron_expression")
    @classmethod
    def validate_cron(cls, v: str | None) -> str | None:
        return _validate_cron(v)


class AlertUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    message: str | None = Field(default=None, max_length=10000)
    schedule_type: ScheduleType | None = None
    cron_expression: str | None = Field(default=None, max_length=100)
    next_fire_at: datetime | None = None
    enabled: bool | None = None

    @field_validator("cron_expression")
    @classmethod
    def validate_cron(cls, v: str | None) -> str | None:
        return _validate_cron(v)


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
