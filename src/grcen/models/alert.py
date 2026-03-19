import enum
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


class ScheduleType(enum.StrEnum):
    ONCE = "once"
    RECURRING = "recurring"


@dataclass
class Alert:
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

    @classmethod
    def from_row(cls, row) -> "Alert":
        return cls(
            id=row["id"],
            asset_id=row["asset_id"],
            title=row["title"],
            message=row["message"],
            schedule_type=ScheduleType(row["schedule_type"]),
            cron_expression=row["cron_expression"],
            next_fire_at=row["next_fire_at"],
            enabled=row["enabled"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
