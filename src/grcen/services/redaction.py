"""Field-level redaction for sensitive custom fields.

A custom field marked ``sensitive=True`` in ``custom_fields.py`` is visible only
to users whose role grants ``Permission.VIEW_PII``.  For everyone else we
replace the value with a placeholder at every egress point: HTML pages, JSON
APIs, exports, and PDF reports.

This module is the single place that knows how to mask.  Callers just ask
"redact this" and pass the user; nobody else needs to know which fields are
sensitive.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from grcen.custom_fields import CUSTOM_FIELDS
from grcen.models.asset import AssetType
from grcen.models.user import User
from grcen.permissions import Permission, has_permission

REDACTED_PLACEHOLDER = "[redacted]"


def can_view_pii(user: User | None) -> bool:
    if user is None:
        return False
    return has_permission(user.role, Permission.VIEW_PII)


def sensitive_field_names(asset_type: AssetType) -> set[str]:
    return {f.name for f in CUSTOM_FIELDS.get(asset_type, []) if f.sensitive}


def redact_metadata(
    metadata: dict[str, Any] | None,
    asset_type: AssetType | str | None,
    user: User | None,
) -> dict[str, Any]:
    """Return a copy of ``metadata`` with sensitive fields masked for the user.

    Non-mutating.  If the user has VIEW_PII (or the asset type has no sensitive
    fields) the original dict is returned unchanged.
    """
    if not metadata:
        return metadata or {}
    if can_view_pii(user):
        return metadata

    if isinstance(asset_type, str):
        try:
            asset_type = AssetType(asset_type)
        except ValueError:
            return metadata

    sensitive = sensitive_field_names(asset_type) if asset_type else set()
    if not sensitive:
        return metadata

    masked = deepcopy(metadata)
    for key in list(masked.keys()):
        if key in sensitive and masked[key] not in (None, "", [], {}):
            masked[key] = REDACTED_PLACEHOLDER
    return masked
