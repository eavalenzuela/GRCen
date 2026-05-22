"""Answer library workspace (feature_roadmap.md #21).

A dedicated home for posture answers — `AssetType.ANSWER` assets are excluded
from the general /assets surfaces, so this is where you browse, create, and
(later) fill them from incoming questionnaires.
"""
from datetime import date
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from grcen.models.user import User
from grcen.permissions import Permission
from grcen.routers._pages_shared import _csrf_check, templates
from grcen.routers.deps import get_db, require_permission
from grcen.services import alert_service as alert_svc, answer_service, questionnaire_service

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


# ── Inbound questionnaires (Phase 3) ────────────────────────────────────────


@router.get("/questionnaires", response_class=HTMLResponse)
async def questionnaires_list(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    items = await questionnaire_service.list_questionnaires(
        pool, organization_id=user.organization_id
    )
    notif_count = await alert_svc.count_unread_notifications(
        pool, organization_id=user.organization_id, user_id=user.id
    )
    return templates.TemplateResponse(
        request,
        "answers/questionnaires.html",
        context={"user": user, "questionnaires": items, "notif_count": notif_count},
    )


@router.post("/questionnaires")
async def questionnaire_create(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.EDIT)),
):
    form = await request.form()
    name = str(form.get("name", "")).strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")
    due_raw = str(form.get("due_date", "")).strip()
    due_date = date.fromisoformat(due_raw) if due_raw else None
    qid = await questionnaire_service.create_questionnaire(
        pool,
        organization_id=user.organization_id,
        name=name,
        source=str(form.get("source", "")).strip(),
        due_date=due_date,
        created_by=user.id,
    )
    return RedirectResponse(f"/questionnaires/{qid}", status_code=302)


@router.get("/questionnaires/{questionnaire_id}", response_class=HTMLResponse)
async def questionnaire_detail(
    request: Request,
    questionnaire_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    q = await questionnaire_service.get_questionnaire(
        pool, questionnaire_id, organization_id=user.organization_id
    )
    if q is None:
        raise HTTPException(status_code=404, detail="Questionnaire not found")
    responses = await questionnaire_service.list_responses(
        pool, questionnaire_id, organization_id=user.organization_id
    )
    # Library answers for the per-question mapping dropdown.
    answers = await answer_service.list_answers(pool, organization_id=user.organization_id)
    notif_count = await alert_svc.count_unread_notifications(
        pool, organization_id=user.organization_id, user_id=user.id
    )
    return templates.TemplateResponse(
        request,
        "answers/questionnaire_detail.html",
        context={
            "user": user,
            "questionnaire": q,
            "responses": responses,
            "library": answers,
            "statuses": list(questionnaire_service.VALID_STATUS),
            "notif_count": notif_count,
        },
    )


@router.post("/questionnaires/{questionnaire_id}/import")
async def questionnaire_import(
    request: Request,
    questionnaire_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.EDIT)),
):
    q = await questionnaire_service.get_questionnaire(
        pool, questionnaire_id, organization_id=user.organization_id
    )
    if q is None:
        raise HTTPException(status_code=404, detail="Questionnaire not found")
    form = await request.form()
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        raise HTTPException(status_code=400, detail="No file uploaded")
    content = await upload.read()
    try:
        column = int(str(form.get("column", "0")) or "0")
    except ValueError:
        column = 0
    has_header = str(form.get("has_header", "")) in ("on", "true", "1")
    questions = questionnaire_service.parse_questions(
        content, column=column, has_header=has_header
    )
    await questionnaire_service.import_questions(
        pool, questionnaire_id, questions, organization_id=user.organization_id
    )
    return RedirectResponse(f"/questionnaires/{questionnaire_id}", status_code=302)


@router.post("/questionnaires/{questionnaire_id}/responses/{response_id}")
async def questionnaire_set_response(
    request: Request,
    questionnaire_id: UUID,
    response_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.EDIT)),
):
    form = await request.form()
    answer_asset_id: UUID | None = None
    raw_answer_id = str(form.get("answer_asset_id", "")).strip()
    if raw_answer_id:
        try:
            answer_asset_id = UUID(raw_answer_id)
        except ValueError:
            answer_asset_id = None
    # An explicit manual answer wins; otherwise mapping auto-fills.
    manual = form.get("filled_answer")
    filled_answer = str(manual) if manual is not None and str(manual).strip() else None
    mark_reviewed = str(form.get("mark_reviewed", "")) in ("on", "true", "1")
    await questionnaire_service.set_response(
        pool,
        response_id,
        organization_id=user.organization_id,
        answer_asset_id=answer_asset_id,
        filled_answer=filled_answer,
        mark_reviewed=mark_reviewed,
    )
    return RedirectResponse(f"/questionnaires/{questionnaire_id}", status_code=302)


@router.post("/questionnaires/{questionnaire_id}/status")
async def questionnaire_set_status(
    request: Request,
    questionnaire_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.EDIT)),
):
    form = await request.form()
    try:
        await questionnaire_service.set_status(
            pool, questionnaire_id, str(form.get("status", "")),
            organization_id=user.organization_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return RedirectResponse(f"/questionnaires/{questionnaire_id}", status_code=302)


@router.post("/questionnaires/{questionnaire_id}/delete")
async def questionnaire_delete(
    questionnaire_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.DELETE)),
):
    await questionnaire_service.delete_questionnaire(
        pool, questionnaire_id, organization_id=user.organization_id
    )
    return RedirectResponse("/questionnaires", status_code=302)
