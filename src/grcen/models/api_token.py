from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass
class ApiToken:
    id: UUID
    user_id: UUID
    name: str
    token_hash: str
    permissions: list[str]
    expires_at: datetime | None
    last_used_at: datetime | None
    is_service_account: bool
    created_at: datetime
    revoked: bool
    allowed_ips: list[str]
    # A token is bound to the org it was minted in and never switches (see
    # deps.get_current_user). Carrying it here lets callers scope writes to the
    # token's own tenant — `api_tokens` sits outside RLS, so nothing else will.
    organization_id: UUID | None = None
    # True for access tokens minted by the OAuth client-credentials grant.
    oauth_issued: bool = False

    @classmethod
    def from_row(cls, row) -> "ApiToken":
        return cls(
            id=row["id"],
            user_id=row["user_id"],
            name=row["name"],
            token_hash=row["token_hash"],
            permissions=list(row["permissions"]),
            expires_at=row["expires_at"],
            last_used_at=row["last_used_at"],
            is_service_account=row["is_service_account"],
            created_at=row["created_at"],
            revoked=row["revoked"],
            allowed_ips=list(row.get("allowed_ips") or []),
            organization_id=row.get("organization_id"),
            oauth_issued=bool(row.get("oauth_issued") or False),
        )
