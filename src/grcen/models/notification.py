from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass
class Notification:
    id: UUID
    alert_id: UUID
    title: str
    message: str | None
    read: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row) -> "Notification":
        return cls(
            id=row["id"],
            alert_id=row["alert_id"],
            title=row["title"],
            message=row["message"],
            read=row["read"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
