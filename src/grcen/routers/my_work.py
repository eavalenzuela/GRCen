"""The "My Work" page — one place where daily GRC work starts.

Aggregates, for the signed-in user:

- approvals waiting on someone (actionable if the user holds APPROVE),
- their own submissions still pending,
- reviews due/overdue on assets owned by their linked Person asset,
- open findings assigned to their Person asset,
- answer-library entries flagged needs-review (editors+),
- the assets they watch.

Everything shown is already reachable elsewhere; the value is the single
per-user aggregation.
"""

import json

import asyncpg
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from grcen.models.user import User
from grcen.permissions import Permission, has_permission
from grcen.routers._pages_shared import _csrf_check, templates
from grcen.routers.deps import get_current_user, get_db
from grcen.services import (
    alert_service as alert_svc,
    answer_service,
    review_service,
    watcher_service,
    workflow_service,
)

router = APIRouter(tags=["pages"], dependencies=[Depends(_csrf_check)])


@router.get("/my-work", response_class=HTMLResponse)
async def my_work(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(get_current_user),
):
    org_id = user.organization_id
    can_approve = user.is_superadmin or has_permission(user.role, Permission.APPROVE)
    can_edit = user.is_superadmin or has_permission(user.role, Permission.EDIT)

    # Approvals: pending changes I can act on vs my own submissions in flight.
    pending = await workflow_service.list_changes(
        pool, status="pending", organization_id=org_id
    )
    my_submissions = [c for c in pending if c.submitted_by == user.id]
    actionable = (
        [c for c in pending if c.submitted_by != user.id] if can_approve else []
    )

    # Reviews on assets owned by my linked Person asset.
    my_reviews: list[dict] = []
    if user.person_asset_id:
        reviews = await review_service.get_reviews(pool, organization_id=org_id)
        my_reviews = [
            r for r in reviews
            if r.get("owner_id") == user.person_asset_id
            and r["status"] in ("overdue", "due_soon")
        ]

    # Open findings assigned to my Person asset.
    my_findings: list[dict] = []
    if user.person_asset_id:
        rows = await pool.fetch(
            """SELECT id, name, metadata FROM assets
               WHERE type = 'finding' AND organization_id = $1
                 AND owner_id = $2 AND status = 'active'
               ORDER BY name""",
            org_id, user.person_asset_id,
        )
        for r in rows:
            meta = r["metadata"]
            if isinstance(meta, str):
                meta = json.loads(meta or "{}")
            meta = meta or {}
            if str(meta.get("finding_status", "")).lower() in ("closed", "verified"):
                continue
            my_findings.append({
                "id": r["id"],
                "name": r["name"],
                "finding_status": meta.get("finding_status", ""),
                "due_date": meta.get("due_date", ""),
            })

    # Answer-library freshness (org-wide; shown to people who can fix it).
    answers_needing_review = (
        await answer_service.count_needs_review(pool, organization_id=org_id)
        if can_edit else 0
    )

    watched = await watcher_service.watched_assets(
        pool, user.id, organization_id=org_id
    )
    notif_count = await alert_svc.count_unread_notifications(
        pool, organization_id=org_id, user_id=user.id
    )
    return templates.TemplateResponse(
        request, "my_work.html",
        context={
            "user": user,
            "actionable": actionable,
            "my_submissions": my_submissions,
            "my_reviews": my_reviews,
            "my_findings": my_findings,
            "answers_needing_review": answers_needing_review,
            "watched": watched,
            "has_person_link": bool(user.person_asset_id),
            "can_approve": can_approve,
            "notif_count": notif_count,
        },
    )
