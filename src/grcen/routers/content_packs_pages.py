"""Admin page to browse and one-click-install bundled compliance content packs.

Solves the empty-register cold start: a fresh org can seed a real, cross-mapped
compliance baseline (frameworks, requirements, a shared control library, and
cross-framework crosswalks) without standing up the external system-of-record.
Installs are idempotent and reversible — each pack owns an ``assets.source`` tag
(``grcen-pack:<id>``), so re-installing upserts and uninstalling removes exactly
that pack's rows.
"""
from urllib.parse import quote

import asyncpg
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from grcen.models.user import User
from grcen.permissions import Permission
from grcen.routers._pages_shared import _csrf_check, templates
from grcen.routers.deps import get_db, require_permission
from grcen.services import (
    alert_service as alert_svc,
    audit_service as audit_svc,
    content_packs,
)

router = APIRouter(tags=["pages"], dependencies=[Depends(_csrf_check)])


def _flash(flash: str | None) -> dict | None:
    if not flash:
        return None
    ok, _, message = flash.partition(":")
    return {"ok": ok == "ok", "message": message or flash}


def _redirect(message: str, ok: bool = True) -> RedirectResponse:
    tag = "ok" if ok else "fail"
    return RedirectResponse(
        f"/admin/content-packs?flash={quote(f'{tag}:{message}')}", status_code=302
    )


@router.get("/admin/content-packs", response_class=HTMLResponse)
async def content_packs_page(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.MANAGE_USERS)),
    flash: str | None = None,
):
    packs = []
    for pack in content_packs.list_packs():
        present = content_packs.fragments_present(pack)
        packs.append({
            "pack": pack,
            "present": present,
            "stats": content_packs.pack_stats(pack) if present else None,
            "installed": await content_packs.installed_asset_count(
                pool, pack, organization_id=user.organization_id
            ),
        })
    notif_count = await alert_svc.count_unread_notifications(
        pool, organization_id=user.organization_id, user_id=user.id
    )
    return templates.TemplateResponse(
        request,
        "admin/content_packs.html",
        context={
            "user": user,
            "packs": packs,
            "flash": _flash(flash),
            "notif_count": notif_count,
        },
    )


@router.post("/admin/content-packs/install")
async def content_packs_install(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.MANAGE_USERS)),
):
    form = await request.form()
    pack = content_packs.get_pack(str(form.get("pack_id", "")))
    action = str(form.get("action", "install"))
    if pack is None:
        return _redirect("Unknown content pack.", ok=False)

    if action == "uninstall":
        removed = await content_packs.uninstall_pack(
            pool, pack, organization_id=user.organization_id
        )
        await audit_svc.log_audit_event(
            pool, user_id=user.id, username=user.username,
            action="uninstall_pack", entity_type="asset",
            entity_name=pack.title,
            changes={"removed_assets": {"old": removed["assets"], "new": 0}},
            organization_id=user.organization_id,
        )
        return _redirect(
            f"Uninstalled {pack.title}: removed {removed['assets']} assets "
            f"and {removed['relationships']} relationships."
        )

    if not content_packs.fragments_present(pack):
        return _redirect(f"{pack.title} has no bundled content yet.", ok=False)
    errors = content_packs.validate_pack(pack)
    if errors:
        return _redirect(f"{pack.title} is invalid: {errors[0]}", ok=False)

    dry_run = action == "preview"
    result = await content_packs.install_pack(
        pool, pack, organization_id=user.organization_id, dry_run=dry_run
    )
    if result.errors:
        return _redirect(f"{pack.title} failed: {result.errors[0]}", ok=False)

    if not dry_run:
        await audit_svc.log_audit_event(
            pool, user_id=user.id, username=user.username,
            action="install_pack", entity_type="asset",
            entity_name=pack.title,
            changes={
                "frameworks": {"new": result.frameworks},
                "requirements": {"new": result.requirements},
                "controls": {"new": result.controls},
            },
            organization_id=user.organization_id,
        )

    verb = "Dry run — would install" if dry_run else "Installed"
    return _redirect(
        f"{verb} {pack.title}: {result.frameworks} framework(s), "
        f"{result.requirements} requirements, {result.controls} controls, "
        f"{result.crosswalks} crosswalks "
        f"({result.assets_created} created, {result.assets_updated} updated)."
    )
