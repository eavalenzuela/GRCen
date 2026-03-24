from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=150, pattern=r"^[a-zA-Z0-9_.\-]+$")
    password: str = Field(min_length=8, max_length=128)
    role: str = "viewer"

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str) -> str:
        if v not in {"admin", "editor", "viewer", "auditor"}:
            raise ValueError("role must be one of: admin, editor, viewer, auditor")
        return v


class UserResponse(BaseModel):
    id: UUID
    username: str
    is_active: bool
    role: str
    email: str | None = None
    person_asset_id: UUID | None = None
    is_sso: bool = False
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
