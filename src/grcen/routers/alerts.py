from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse

from grcen.models.user import User
from grcen.permissions import Permission
from grcen.routers.deps import get_db, require_permission
from grcen.schemas.alert import AlertCreate, AlertResponse, AlertUpdate
from grcen.schemas.notification import NotificationResponse
from grcen.services import alert_service as alert_svc
from grcen.services import audit_service as audit_svc

router = APIRouter(prefix="/api/alerts", tags=["alerts"])

_ALERT_FIELDS = ["title", "message", "schedule_type", "cron_expression", "enabled"]


@router.get("/", response_model=list[AlertResponse])
async def list_alerts(
    asset_id: UUID | None = None,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    alerts = await alert_svc.list_alerts(pool, asset_id, organization_id=user.organization_id)
    return [AlertResponse.model_validate(a, from_attributes=True) for a in alerts]


@router.post("/", response_model=AlertResponse, status_code=201)
async def create_alert(
    data: AlertCreate,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.MANAGE_ALERTS)),
):
    # Ensure the alert is being attached to an asset in the user's org.
    asset_row = await pool.fetchrow(
        "SELECT organization_id FROM assets WHERE id = $1", data.asset_id
    )
    if asset_row is None or asset_row["organization_id"] != user.organization_id:
        raise HTTPException(status_code=404, detail="Asset not found")
    alert = await alert_svc.create_alert(
        pool,
        organization_id=user.organization_id,
        asset_id=data.asset_id,
        title=data.title,
        message=data.message,
        schedule_type=data.schedule_type.value,
        cron_expression=data.cron_expression,
        next_fire_at=data.next_fire_at,
        enabled=data.enabled,
    )
    await audit_svc.log_audit_event(
        pool,
        user_id=user.id,
        username=user.username,
        action="create",
        entity_type="alert",
        entity_id=alert.id,
        entity_name=alert.title,
        changes=audit_svc.create_snapshot(alert.__dict__, _ALERT_FIELDS),
    )
    return AlertResponse.model_validate(alert, from_attributes=True)


@router.get("/{alert_id}", response_model=AlertResponse)
async def get_alert(
    alert_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    alert = await alert_svc.get_alert(pool, alert_id, organization_id=user.organization_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    return AlertResponse.model_validate(alert, from_attributes=True)


@router.put("/{alert_id}", response_model=AlertResponse)
async def update_alert(
    alert_id: UUID,
    data: AlertUpdate,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.MANAGE_ALERTS)),
):
    old = await alert_svc.get_alert(pool, alert_id)
    if not old:
        raise HTTPException(status_code=404, detail="Alert not found")
    kwargs = data.model_dump(exclude_unset=True)
    if "schedule_type" in kwargs and kwargs["schedule_type"]:
        kwargs["schedule_type"] = kwargs["schedule_type"].value
    # Update is service-layer; the get_alert above already enforced org-scope on `old`.
    alert = await alert_svc.update_alert(pool, alert_id, **kwargs)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    diff = audit_svc.compute_diff(old.__dict__, alert.__dict__, _ALERT_FIELDS)
    if diff:
        await audit_svc.log_audit_event(
            pool,
            user_id=user.id,
            username=user.username,
            action="update",
            entity_type="alert",
            entity_id=alert.id,
            entity_name=alert.title,
            changes=diff,
        )
    return AlertResponse.model_validate(alert, from_attributes=True)


@router.delete("/{alert_id}", status_code=204)
async def delete_alert(
    alert_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.MANAGE_ALERTS)),
):
    old = await alert_svc.get_alert(pool, alert_id)
    if not old:
        raise HTTPException(status_code=404, detail="Alert not found")
    deleted = await pool.execute(
        "DELETE FROM alerts WHERE id = $1 AND organization_id = $2",
        alert_id, user.organization_id,
    ) == "DELETE 1"
    if not deleted:
        raise HTTPException(status_code=404, detail="Alert not found")
    await audit_svc.log_audit_event(
        pool,
        user_id=user.id,
        username=user.username,
        action="delete",
        entity_type="alert",
        entity_id=old.id,
        entity_name=old.title,
        changes=audit_svc.delete_snapshot(old.__dict__, _ALERT_FIELDS),
    )


@router.get("/notifications/all", response_model=list[NotificationResponse])
async def list_notifications(
    unread_only: bool = False,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    notifs = await alert_svc.list_notifications(pool, unread_only, organization_id=user.organization_id)
    return [NotificationResponse.model_validate(n, from_attributes=True) for n in notifs]


@router.get("/notifications/count", response_class=HTMLResponse)
async def notification_count(
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    count = await alert_svc.count_unread_notifications(pool, organization_id=user.organization_id)
    if count:
        return HTMLResponse(f"({count})")
    return HTMLResponse("")


@router.post("/notifications/{notif_id}/read", status_code=204)
async def mark_read(
    notif_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    ok = await alert_svc.mark_notification_read(pool, notif_id, organization_id=user.organization_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Notification not found")
