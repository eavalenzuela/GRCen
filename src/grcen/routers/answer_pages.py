"""Answer library workspace (feature_roadmap.md #21).

A dedicated home for posture answers — `AssetType.ANSWER` assets are excluded
from the general /assets surfaces, so this is where you browse, create, and
(later) fill them from incoming questionnaires.
"""
import asyncpg
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from grcen.models.user import User
from grcen.permissions import Permission
from grcen.routers._pages_shared import _csrf_check, templates
from grcen.routers.deps import get_db, require_permission
from grcen.services import alert_service as alert_svc, answer_service

router = APIRouter(tags=["pages"], dependencies=[Depends(_csrf_check)])


@router.get("/answers", response_class=HTMLResponse)
async def answers_library(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    answers = await answer_service.list_answers(pool, organization_id=user.organization_id)
    notif_count = await alert_svc.count_unread_notifications(
        pool, organization_id=user.organization_id, user_id=user.id
    )
    return templates.TemplateResponse(
        request,
        "answers/list.html",
        context={"user": user, "answers": answers, "notif_count": notif_count},
    )
