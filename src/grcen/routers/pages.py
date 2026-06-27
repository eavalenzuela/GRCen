"""Aggregates the page (HTML/form) sub-routers.

Historically this was one ~3,200-line module; it is now split into
feature-focused sub-routers in this package. This module wires them together
under a single ``router`` and re-exports the shared ``templates`` and
``_csrf_check`` for backward compatibility (main.py and workflow.py import
them from here).
"""
from fastapi import APIRouter

from grcen.routers import (
    admin_pages,
    answer_pages,
    asset_pages,
    auth_pages,
    content_packs_pages,
    dashboard_pages,
    framework_pages,
    register_pages,
    relationship_pages,
    reports_pages,
    risk_appetite,
    settings_pages,
    tag_pages,
    vendor_campaigns,
)
from grcen.routers._pages_shared import _csrf_check, templates  # noqa: F401  re-export

router = APIRouter()
for _sub in (
    auth_pages,
    dashboard_pages,
    asset_pages,
    register_pages,
    relationship_pages,
    tag_pages,
    framework_pages,
    settings_pages,
    answer_pages,
    admin_pages,
    content_packs_pages,
):
    router.include_router(_sub.router)
router.include_router(risk_appetite.page_router)
router.include_router(reports_pages.router)
router.include_router(vendor_campaigns.page_router)
