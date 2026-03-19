from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException

from grcen.models.user import User
from grcen.routers.deps import get_current_user, get_db
from grcen.schemas.alert import AlertCreate, AlertResponse, AlertUpdate
from grcen.schemas.notification import NotificationResponse
from grcen.services import alert_service as alert_svc

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


@router.get("/", response_model=list[AlertResponse])
async def list_alerts(
    asset_id: UUID | None = None,
    pool: asyncpg.Pool = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    alerts = await alert_svc.list_alerts(pool, asset_id)
    return [AlertResponse.model_validate(a, from_attributes=True) for a in alerts]


@router.post("/", response_model=AlertResponse, status_code=201)
async def create_alert(
    data: AlertCreate,
    pool: asyncpg.Pool = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    alert = await alert_svc.create_alert(
        pool,
        asset_id=data.asset_id,
        title=data.title,
        message=data.message,
        schedule_type=data.schedule_type.value,
        cron_expression=data.cron_expression,
        next_fire_at=data.next_fire_at,
        enabled=data.enabled,
    )
    return AlertResponse.model_validate(alert, from_attributes=True)


@router.get("/{alert_id}", response_model=AlertResponse)
async def get_alert(
    alert_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    alert = await alert_svc.get_alert(pool, alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    return AlertResponse.model_validate(alert, from_attributes=True)


@router.put("/{alert_id}", response_model=AlertResponse)
async def update_alert(
    alert_id: UUID,
    data: AlertUpdate,
    pool: asyncpg.Pool = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    kwargs = data.model_dump(exclude_unset=True)
    if "schedule_type" in kwargs and kwargs["schedule_type"]:
        kwargs["schedule_type"] = kwargs["schedule_type"].value
    alert = await alert_svc.update_alert(pool, alert_id, **kwargs)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    return AlertResponse.model_validate(alert, from_attributes=True)


@router.delete("/{alert_id}", status_code=204)
async def delete_alert(
    alert_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    deleted = await alert_svc.delete_alert(pool, alert_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Alert not found")


@router.get("/notifications/all", response_model=list[NotificationResponse])
async def list_notifications(
    unread_only: bool = False,
    pool: asyncpg.Pool = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    notifs = await alert_svc.list_notifications(pool, unread_only)
    return [NotificationResponse.model_validate(n, from_attributes=True) for n in notifs]


@router.get("/notifications/count")
async def notification_count(
    pool: asyncpg.Pool = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    count = await alert_svc.count_unread_notifications(pool)
    return {"count": count}


@router.post("/notifications/{notif_id}/read", status_code=204)
async def mark_read(
    notif_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    ok = await alert_svc.mark_notification_read(pool, notif_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Notification not found")
