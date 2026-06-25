"""Shared helpers, templates object, and CSRF dependency for page routers."""

import asyncpg
from fastapi import Request
from fastapi.templating import Jinja2Templates

from grcen.custom_fields import CUSTOM_FIELDS, coerce_value
from grcen.models.asset import AssetType
from grcen.permissions import Permission, has_permission
from grcen.services import (
    oidc_settings,
    saml_settings,
)
from grcen.services.review_service import review_status

# Static mapping: relationship_type -> (outgoing_label, incoming_label)
RELATIONSHIP_LABELS: dict[str, tuple[str, str]] = {
    "manages": ("manages", "managed by"),
    "owns": ("owns", "owned by"),
    "leads": ("leads", "led by"),
    "member_of": ("member of", "has member"),
    "governs": ("governs", "governed by"),
    "depends_on": ("depends on", "depended on by"),
    "deployed_on": ("deployed on", "hosts"),
    "authenticates_via": ("authenticates via", "authenticates"),
    "authenticates": ("authenticates", "authenticated by"),
    "runs_on": ("runs on", "hosts"),
    "deploys_to": ("deploys to", "deployed from"),
    "monitors": ("monitors", "monitored by"),
    "protects": ("protects", "protected by"),
    "processes": ("processes", "processed by"),
    "stores": ("stores", "stored in"),
    "references": ("references", "referenced by"),
    "assesses": ("assesses", "assessed by"),
    "reviews": ("reviews", "reviewed by"),
    "satisfied_by": ("satisfied by", "satisfies"),
    "implemented_by": ("implemented by", "implements"),
    "operates_on": ("operates on", "operated on by"),
    "scans": ("scans", "scanned by"),
    "approves_changes_to": ("approves changes to", "changes approved by"),
    "threatens": ("threatens", "threatened by"),
    "mitigated_by": ("mitigated by", "mitigates"),
    "trained_on": ("trained on", "trains"),
    "used_by": ("used by", "uses"),
    "describes": ("describes", "described by"),
    "defines": ("defines", "defined by"),
    "sends_data_to": ("sends data to", "receives data from"),
    "connects_to": ("connects to", "connected from"),
    "links_to": ("links to", "linked from"),
    "replaced_by": ("replaced by", "replaces"),
    "mirrors": ("mirrors", "mirrored by"),
    "enforces": ("enforces", "enforced by"),
    "classifies": ("classifies", "classified by"),
    "provides_service_to": ("provides service to", "serviced by"),
    "affected_by": ("affected by", "affects"),
    "triggered_by": ("triggered by", "triggered"),
    "resulted_in": ("resulted in", "resulted from"),
    "subprocessor_of": ("subprocessor of", "has subprocessor"),
    "certifies": ("certifies", "certified by"),
    "tested_by": ("tested by", "tests"),
    "parent_of": ("parent of", "child of"),
    # Answer-library entry → the Control/Policy/System/Framework/Audit that backs it
    "substantiated_by": ("substantiated by", "substantiates"),
}


def _rel_direction_label(rel_type: str, is_outgoing: bool) -> str:
    """Return a human-readable direction label for a relationship."""
    labels = RELATIONSHIP_LABELS.get(rel_type)
    if labels:
        return labels[0] if is_outgoing else labels[1]
    return rel_type if is_outgoing else f"incoming: {rel_type}"


def suggested_relationship_types(db_types: list[str]) -> list[str]:
    """Canonical vocabulary ∪ types already in use, sorted — for input datalists.

    Suggestions only: any new free-text type is still accepted. Offering the
    canonical set at the moment of creation is what keeps a fresh org from
    fragmenting its vocabulary ("owns" vs "owned by" vs "manages").
    """
    return sorted(set(RELATIONSHIP_LABELS) | set(db_types))

_ASSET_FIELDS = ["name", "description", "status", "owner", "metadata"]
_USER_FIELDS = ["username", "role", "is_active"]

templates = Jinja2Templates(directory="src/grcen/templates")
templates.env.globals["has_perm"] = has_permission
templates.env.globals["Permission"] = Permission
templates.env.globals["rel_label"] = _rel_direction_label
templates.env.globals["review_status"] = review_status

async def _csrf_check(request: Request):
    """Verify CSRF token on POST form submissions.

    Accepts the token from either:
    - A ``csrf_token`` form field (standard HTML forms)
    - The ``X-CSRF-Token`` header (useful for programmatic clients)
    """
    if request.method != "POST":
        return

    expected = request.session.get("csrf_token", "")
    if not expected:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="CSRF token mismatch")

    # Check header first (e.g. from test clients or JS fetch)
    header_token = request.headers.get("x-csrf-token", "")
    if header_token:
        import hmac
        if hmac.compare_digest(str(header_token), str(expected)):
            return

    # Fall back to form field
    content_type = request.headers.get("content-type", "")
    is_form = (
        "application/x-www-form-urlencoded" in content_type
        or "multipart/form-data" in content_type
    )
    if is_form:
        form = await request.form()
        submitted = form.get("csrf_token", "")
        import hmac
        if submitted and hmac.compare_digest(str(submitted), str(expected)):
            return

    from fastapi import HTTPException
    raise HTTPException(status_code=403, detail="CSRF token mismatch")


def _extract_metadata(form, asset_type: AssetType) -> dict:
    """Extract custom field values from form data into a metadata dict."""
    metadata = {}
    for field_def in CUSTOM_FIELDS.get(asset_type, []):
        key = f"metadata.{field_def.name}"
        raw = str(form.get(key, ""))
        # Checkboxes are absent from form when unchecked
        if field_def.field_type == "boolean":
            metadata[field_def.name] = key in form
        elif raw:
            metadata[field_def.name] = coerce_value(field_def, raw)
    return metadata


# --- Auth pages ---


async def _sso_context(pool: asyncpg.Pool) -> dict:
    """Gather SSO provider state for the login template."""
    oidc_cfg = await oidc_settings.get_settings(pool)
    saml_cfg = await saml_settings.get_settings(pool)
    return {
        "oidc_enabled": oidc_cfg.enabled,
        "oidc_display_name": oidc_cfg.display_name,
        "saml_enabled": saml_cfg.enabled,
        "saml_display_name": saml_cfg.display_name,
    }
