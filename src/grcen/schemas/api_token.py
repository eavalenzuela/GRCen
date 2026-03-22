from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class TokenCreate(BaseModel):
    name: str
    permissions: list[str]
    expires_at: datetime | None = None
    is_service_account: bool = False


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
    max_expiry_days: int | None = None


class TokenConfigResponse(BaseModel):
    max_expiry_days: int | None
