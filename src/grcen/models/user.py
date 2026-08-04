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
    organization_id: UUID
    is_superadmin: bool = False
    oidc_sub: str | None = None
    saml_sub: str | None = None
    person_asset_id: UUID | None = None
    email: str | None = None
    last_login: datetime | None = None
    failed_login_count: int = 0
    locked_until: datetime | None = None
    email_notifications_enabled: bool = False
    email_notification_mode: str = "immediate"  # 'immediate' | 'digest'
    # Permission scope of the API token this request arrived on, if any. Not a
    # column — set per-request by deps.get_current_user. ``None`` means the
    # caller is a session user, whose scope is their role alone. An empty list
    # is a real, and maximally restrictive, token scope.
    token_permissions: list[str] | None = None

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
            email_notifications_enabled=row.get("email_notifications_enabled", False),
            email_notification_mode=row.get("email_notification_mode") or "immediate",
            organization_id=row["organization_id"],
            is_superadmin=row.get("is_superadmin", False),
        )
