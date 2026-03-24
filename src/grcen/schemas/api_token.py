from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from grcen.permissions import Permission


class TokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    permissions: list[str]
    expires_at: datetime | None = None
    is_service_account: bool = False

    @field_validator("permissions")
    @classmethod
    def validate_permissions(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("at least one permission is required")
        valid = {p.value for p in Permission}
        for perm in v:
            if perm not in valid:
                raise ValueError(f"invalid permission: {perm}")
        return v


class TokenResponse(BaseModel):
    id: UUID
    user_id: UUID
    name: str
    permissions: list[str]
    expires_at: datetime | None
    last_used_at: datetime | None
    is_service_account: bool
    created_at: datetime
    revoked: bool

    model_config = ConfigDict(from_attributes=True)


class TokenCreatedResponse(TokenResponse):
    token: str


class TokenConfigUpdate(BaseModel):
    max_expiry_days: int | None = Field(default=None, ge=1, le=3650)


class TokenConfigResponse(BaseModel):
    max_expiry_days: int | None
