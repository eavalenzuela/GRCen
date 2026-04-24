import enum
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


class AttachmentKind(enum.StrEnum):
    FILE = "file"
    URL = "url"
    DOCUMENT = "document"


@dataclass
class Attachment:
    id: UUID
    asset_id: UUID | None
    relationship_id: UUID | None
    kind: AttachmentKind
    name: str
    url_or_path: str | None
    created_at: datetime
    updated_at: datetime
    encrypted: bool = False

    @classmethod
    def from_row(cls, row) -> "Attachment":
        return cls(
            id=row["id"],
            asset_id=row.get("asset_id"),
            relationship_id=row.get("relationship_id"),
            kind=AttachmentKind(row["kind"]),
            name=row["name"],
            url_or_path=row["url_or_path"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            encrypted=row.get("encrypted", False),
        )
