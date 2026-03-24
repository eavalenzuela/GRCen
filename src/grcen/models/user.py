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
    oidc_sub: str | None = None
    saml_sub: str | None = None
    person_asset_id: UUID | None = None
    email: str | None = None
    last_login: datetime | None = None
    failed_login_count: int = 0
    locked_until: datetime | None = None

    @property
    def is_admin(self) -> bool:
        return self.role == UserRole.ADMIN

    @property
    def is_sso(self) -> bool:
        return self.oidc_sub is not None or self.saml_sub is not None

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
            oidc_sub=row.get("oidc_sub"),
            saml_sub=row.get("saml_sub"),
            person_asset_id=row.get("person_asset_id"),
            email=row.get("email"),
            last_login=row.get("last_login"),
            failed_login_count=row.get("failed_login_count", 0),
            locked_until=row.get("locked_until"),
        )
