from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from grcen.permissions import UserRole


@dataclass
class User:
    id: UUID
    username: str
    hashed_password: str
    is_active: bool
    role: UserRole
    created_at: datetime
    updated_at: datetime

    @property
    def is_admin(self) -> bool:
        return self.role == UserRole.ADMIN

    @classmethod
    def from_row(cls, row) -> "User":
        return cls(
            id=row["id"],
            username=row["username"],
            hashed_password=row["hashed_password"],
            is_active=row["is_active"],
            role=UserRole(row["role"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
