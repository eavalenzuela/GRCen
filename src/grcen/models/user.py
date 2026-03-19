from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass
class User:
    id: UUID
    username: str
    hashed_password: str
    is_active: bool
    is_admin: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row) -> "User":
        return cls(
            id=row["id"],
            username=row["username"],
            hashed_password=row["hashed_password"],
            is_active=row["is_active"],
            is_admin=row["is_admin"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
