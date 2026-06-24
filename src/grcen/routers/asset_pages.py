"""Asset CRUD pages, owner search, and the node graph view."""
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from grcen import registers
from grcen.custom_fields import CUSTOM_FIELDS
from grcen.models.asset import ORGANIZATIONAL_TYPES, AssetType
from grcen.models.user import User
from grcen.permissions import Permission
from grcen.routers._pages_shared import (
    _ASSET_FIELDS,
    _csrf_check,
    _extract_metadata,
    suggested_relationship_types,
    templates,
)
from grcen.routers.deps import (
    get_db,
    require_permission,
)
from grcen.services import (
    access_log_service,
    alert_service as alert_svc,
    asset as asset_svc,
    attachment as att_svc,
    audit_service as audit_svc,
    redaction,
    register_service,
    relationship as rel_svc,
    risk_service as risk_svc,
    saved_search_service,
    tag_service,
    workflow_service,
)

router = APIRouter(tags=["pages"], dependencies=[Depends(_csrf_check)])

@router.get("/assets", response_class=HTMLResponse)
async def asset_list(
    request: Request,
    type: str | None = None,
    q: str | None = None,
    status: str | None = None,
    owner: str | None = None,
    created_after: str | None = None,
    created_before: str | None = None,
    meta_key: str | None = None,
    meta_value: str | None = None,
    tag: str | None = None,
    unlinked: str | None = None,
    sort: str = "name",
    order: str = "asc",
    columns: str | None = None,
    page: int = 1,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    asset_type = AssetType(type) if type else None
    metadata_filters = None
    if meta_key and meta_value:
        metadata_filters = {meta_key: meta_value}
    unlinked_flag = (unlinked or "").lower() in ("on", "1", "true", "yes")
    items, total = await asset_svc.list_assets(
        pool,
        asset_type=asset_type,
        page=page,
        page_size=25,
        q=q,
        status=status,
        owner=owner,
        created_after=created_after,
        created_before=created_before,
        metadata_filters=metadata_filters,
        tag=tag,
        unlinked=unlinked_flag,
        sort=sort,
        order=order,
        organization_id=user.organization_id,
    )
    notif_count = await alert_svc.count_unread_notifications(pool, organization_id=user.organization_id, user_id=user.id)
    all_tags = await tag_service.list_tags_with_counts(pool, organization_id=user.organization_id)
    saved_searches = await saved_search_service.list_visible(
        pool, user.id, path="/assets", organization_id=user.organization_id
    )
    # Register framework: a single asset type resolves to a RegisterDef, which
    # gives the list a name, curated columns (?columns=curated), and a metrics
    # header. Ad-hoc /assets?type=X (no ?columns) keeps today's "all columns".
    register = registers.by_type(asset_type)
    columns_mode = "curated" if (columns == "curated" and register is not None) else "all"
    # Override-aware sensitivity: exclude effective-sensitive columns AND mask
    # override-promoted values in the rows (the list path previously leaked them).
    effective_sensitive: set[str] = set()
    if asset_type is not None:
        effective_sensitive = await redaction.effective_sensitive_field_names(
            pool, asset_type, user.organization_id
        )
        await redaction.redact_assets_metadata(
            pool, items, asset_type, user, user.organization_id, effective=effective_sensitive
        )
    columns_resolved = registers.resolve_columns(
        register, columns_mode, asset_type, effective_sensitive
    )
    metrics = (
        await register_service.build_metrics(pool, register, organization_id=user.organization_id)
        if register is not None and register.metrics
        else []
    )
    # Build filter params string for pagination links
    filter_params = ""
    if asset_type:
        filter_params += f"&type={asset_type.value}"
    if q:
        filter_params += f"&q={q}"
    if status:
        filter_params += f"&status={status}"
    if owner:
        filter_params += f"&owner={owner}"
    if created_after:
        filter_params += f"&created_after={created_after}"
    if created_before:
        filter_params += f"&created_before={created_before}"
    if meta_key and meta_value:
        filter_params += f"&meta_key={meta_key}&meta_value={meta_value}"
    if tag:
        filter_params += f"&tag={tag}"
    if unlinked_flag:
        filter_params += "&unlinked=on"
    if sort != "name":
        filter_params += f"&sort={sort}"
    if order != "asc":
        filter_params += f"&order={order}"
    if columns_mode != "all":
        filter_params += f"&columns={columns_mode}"
    return templates.TemplateResponse(request, "assets/list.html", context={
            "user": user,
            "assets": items,
            "total": total,
            "page": page,
            "pages": (total + 24) // 25,
            "current_type": asset_type,
            "asset_types": ORGANIZATIONAL_TYPES,
            "notif_count": notif_count,
            "filter_q": q or "",
            "filter_status": status or "",
            "filter_owner": owner or "",
            "filter_created_after": created_after or "",
            "filter_created_before": created_before or "",
            "filter_meta_key": meta_key or "",
            "filter_meta_value": meta_value or "",
            "filter_tag": tag or "",
            "filter_unlinked": unlinked_flag,
            "register": register,
            "columns": columns_resolved,
            "columns_mode": columns_mode,
            "metrics": metrics,
            "all_tags": all_tags,
            "saved_searches": saved_searches,
            "current_path": "/assets",
            "current_query": filter_params.lstrip("&"),
            "filter_params": filter_params,
            "statuses": ["active", "inactive", "draft", "archived"],
            "sort": sort,
            "order": order,
        },
    )


@router.get("/assets/new", response_class=HTMLResponse)
async def asset_new(
    request: Request,
    type: str | None = None,
    user: User = Depends(require_permission(Permission.CREATE)),
    pool: asyncpg.Pool = Depends(get_db),
):
    notif_count = await alert_svc.count_unread_notifications(pool, organization_id=user.organization_id, user_id=user.id)
    known_tags = await tag_service.list_tags_with_counts(pool, organization_id=user.organization_id)
    # A ?type= query param pins the asset type (used by posture-type workspaces
    # like /answers, whose type is hidden from the general create picker).
    forced_type: AssetType | None = None
    if type:
        try:
            forced_type = AssetType(type)
        except ValueError:
            forced_type = None
    context = {
        "user": user,
        "asset": None,
        "asset_types": ORGANIZATIONAL_TYPES,
        "notif_count": notif_count,
        "custom_fields": CUSTOM_FIELDS,
        "known_tags": known_tags,
        "forced_type": forced_type,
    }
    if forced_type is not None:
        context["asset_custom_fields"] = CUSTOM_FIELDS.get(forced_type, [])
        if forced_type == AssetType.ANSWER:
            context["name_label"] = "Question"
            context["description_label"] = "Canonical answer"
    return templates.TemplateResponse(request, "assets/form.html", context=context)


@router.post("/assets/new")
async def asset_create_submit(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.CREATE)),
):
    form = await request.form()
    asset_type = AssetType(form["type"])
    metadata = _extract_metadata(form, asset_type)
    if asset_type == AssetType.RISK:
        score = risk_svc.compute_risk_score(metadata.get("likelihood"), metadata.get("impact"))
        if score is not None:
            metadata["inherent_risk_score"] = score
    owner_id = None
    owner_id_str = str(form.get("owner_id", "")).strip()
    if owner_id_str:
        from uuid import UUID as _UUID
        try:
            owner_id = _UUID(owner_id_str)
        except ValueError:
            pass
    tags_raw = str(form.get("tags", "")).strip()
    tags = [t.strip() for t in tags_raw.split(",") if t.strip()] if tags_raw else []
    criticality = str(form.get("criticality", "")).strip() or None
    name = str(form["name"])
    description = str(form.get("description", ""))
    status = str(form.get("status", "active"))
    if await workflow_service.requires_approval(
        pool, asset_type, "create", organization_id=user.organization_id
    ):
        change = await workflow_service.submit(
            pool,
            action="create",
            asset_type=asset_type,
            target_asset_id=None,
            title=name,
            payload=workflow_service.asset_create_payload(
                name=name, description=description, status=status,
                owner_id=owner_id, metadata=metadata, tags=tags,
                criticality=criticality,
            ),
            user=user,
        )
        return RedirectResponse(f"/approvals/{change.id}?submitted=1", status_code=302)
    try:
        asset = await asset_svc.create_asset(
            pool,
            organization_id=user.organization_id,
            type=asset_type,
            name=name,
            description=description,
            status=status,
            owner_id=owner_id,
            metadata_=metadata,
            updated_by=user.id,
            tags=tags,
            criticality=criticality,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await audit_svc.log_audit_event(
        pool,
        user_id=user.id,
        username=user.username,
        action="create",
        entity_type="asset",
        entity_id=asset.id,
        entity_name=asset.name,
        changes=audit_svc.create_snapshot(asset.__dict__, _ASSET_FIELDS),
    )
    return RedirectResponse(f"/assets/{asset.id}", status_code=302)


@router.get("/assets/{asset_id}", response_class=HTMLResponse)
async def asset_detail(
    request: Request,
    asset_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    asset = await asset_svc.get_asset(pool, asset_id, organization_id=user.organization_id)
    if not asset:
        return HTMLResponse("Not found", status_code=404)
    await access_log_service.record(
        pool,
        user=user,
        action="view",
        entity_type="asset",
        entity_id=asset.id,
        entity_name=asset.name,
        path=str(request.url.path),
        ip_address=request.client.host if request.client else None,
    )
    asset.metadata_ = redaction.redact_metadata(asset.metadata_, asset.type, user)
    rels = await rel_svc.list_relationships_for_asset(pool, asset_id, organization_id=user.organization_id)
    if rels:
        rel_ids = [r.id for r in rels]
        count_rows = await pool.fetch(
            """SELECT relationship_id, count(*)::int AS n
               FROM attachments
               WHERE relationship_id = ANY($1::uuid[])
               GROUP BY relationship_id""",
            rel_ids,
        )
        counts = {row["relationship_id"]: row["n"] for row in count_rows}
        for r in rels:
            r.attachment_count = counts.get(r.id, 0)
    atts = await att_svc.list_attachments(pool, asset_id, organization_id=user.organization_id)
    alerts = await alert_svc.list_alerts(pool, asset_id, organization_id=user.organization_id)
    notif_count = await alert_svc.count_unread_notifications(pool, organization_id=user.organization_id, user_id=user.id)
    # For Person assets, find linked user account
    linked_user = None
    if asset.type == AssetType.PERSON:
        row = await pool.fetchrow(
            "SELECT id, username, role, is_active FROM users WHERE person_asset_id = $1",
            asset_id,
        )
        if row:
            linked_user = dict(row)
    pending_changes = await workflow_service.list_changes(
        pool, status="pending", target_asset_id=asset_id,
        organization_id=user.organization_id,
    )
    sensitive_overrides = await redaction.list_asset_overrides(pool, asset_id)
    relationship_types = suggested_relationship_types(
        await rel_svc.list_relationship_types(pool, organization_id=user.organization_id)
    )
    return templates.TemplateResponse(request, "assets/detail.html", context={
            "user": user,
            "asset": asset,
            "relationships": rels,
            "relationship_types": relationship_types,
            "attachments": atts,
            "alerts": alerts,
            "asset_types": ORGANIZATIONAL_TYPES,
            "notif_count": notif_count,
            "asset_custom_fields": CUSTOM_FIELDS.get(asset.type, []),
            "linked_user": linked_user,
            "pending_changes": pending_changes,
            "sensitive_overrides": sensitive_overrides,
        },
    )


@router.get("/assets/{asset_id}/edit", response_class=HTMLResponse)
async def asset_edit(
    request: Request,
    asset_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.EDIT)),
):
    asset = await asset_svc.get_asset(pool, asset_id, organization_id=user.organization_id)
    if not asset:
        return HTMLResponse("Not found", status_code=404)
    notif_count = await alert_svc.count_unread_notifications(pool, organization_id=user.organization_id, user_id=user.id)
    known_tags = await tag_service.list_tags_with_counts(pool, organization_id=user.organization_id)
    return templates.TemplateResponse(request, "assets/form.html", context={
            "user": user,
            "asset": asset,
            "asset_types": ORGANIZATIONAL_TYPES,
            "notif_count": notif_count,
            "custom_fields": CUSTOM_FIELDS,
            "asset_custom_fields": CUSTOM_FIELDS.get(asset.type, []),
            "known_tags": known_tags,
        },
    )


@router.post("/assets/{asset_id}/edit")
async def asset_update_submit(
    request: Request,
    asset_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.EDIT)),
):
    form = await request.form()
    old = await asset_svc.get_asset(pool, asset_id, organization_id=user.organization_id)
    metadata = _extract_metadata(form, old.type) if old else {}
    if old and old.type == AssetType.RISK:
        score = risk_svc.compute_risk_score(metadata.get("likelihood"), metadata.get("impact"))
        if score is not None:
            metadata["inherent_risk_score"] = score
    owner_id = None
    owner_id_str = str(form.get("owner_id", "")).strip()
    if owner_id_str:
        from uuid import UUID as _UUID
        try:
            owner_id = _UUID(owner_id_str)
        except ValueError:
            pass
    tags_raw = str(form.get("tags", "")).strip()
    tags = [t.strip() for t in tags_raw.split(",") if t.strip()] if tags_raw else []
    criticality = str(form.get("criticality", "")).strip() or None
    if old and await workflow_service.requires_approval(
        pool, old.type, "update", organization_id=user.organization_id
    ):
        try:
            change = await workflow_service.submit(
                pool,
                action="update",
                asset_type=old.type,
                target_asset_id=asset_id,
                title=str(form["name"]),
                payload=workflow_service.asset_update_payload({
                    "name": str(form["name"]),
                    "description": str(form.get("description", "")),
                    "status": str(form.get("status", "active")),
                    "owner_id": owner_id,
                    "metadata_": metadata,
                    "tags": tags,
                    "criticality": criticality,
                }),
                user=user,
            )
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e))
        return RedirectResponse(f"/approvals/{change.id}?submitted=1", status_code=302)
    try:
        updated = await asset_svc.update_asset(
            pool,
            asset_id,
            organization_id=user.organization_id,
            name=str(form["name"]),
            description=str(form.get("description", "")),
            status=str(form.get("status", "active")),
            owner_id=owner_id,
            metadata_=metadata,
            updated_by=user.id,
            tags=tags,
            criticality=criticality,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if old and updated:
        diff = audit_svc.compute_diff(old.__dict__, updated.__dict__, _ASSET_FIELDS)
        if diff:
            await audit_svc.log_audit_event(
                pool,
                user_id=user.id,
                username=user.username,
                action="update",
                entity_type="asset",
                entity_id=asset_id,
                entity_name=updated.name,
                changes=diff,
            )
    return RedirectResponse(f"/assets/{asset_id}", status_code=302)


@router.post("/assets/{asset_id}/delete")
async def asset_delete_submit(
    asset_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.DELETE)),
):
    old = await asset_svc.get_asset(pool, asset_id, organization_id=user.organization_id)
    if old and await workflow_service.requires_approval(
        pool, old.type, "delete", organization_id=user.organization_id
    ):
        try:
            change = await workflow_service.submit(
                pool,
                action="delete",
                asset_type=old.type,
                target_asset_id=asset_id,
                title=old.name,
                payload={},
                user=user,
            )
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e))
        return RedirectResponse(f"/approvals/{change.id}?submitted=1", status_code=302)
    await asset_svc.delete_asset(pool, asset_id, organization_id=user.organization_id)
    if old:
        await audit_svc.log_audit_event(
            pool,
            user_id=user.id,
            username=user.username,
            action="delete",
            entity_type="asset",
            entity_id=old.id,
            entity_name=old.name,
            changes=audit_svc.delete_snapshot(old.__dict__, _ASSET_FIELDS),
        )
    return RedirectResponse("/assets", status_code=302)


@router.post("/assets/{asset_id}/clone")
async def asset_clone_submit(
    request: Request,
    asset_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.CREATE)),
):
    form = await request.form()
    clone_rels = "clone_relationships" in form
    clone = await asset_svc.clone_asset(
        pool, asset_id, organization_id=user.organization_id,
        clone_relationships=clone_rels, updated_by=user.id,
    )
    if not clone:
        return HTMLResponse("Not found", status_code=404)
    await audit_svc.log_audit_event(
        pool,
        user_id=user.id,
        username=user.username,
        action="clone",
        entity_type="asset",
        entity_id=clone.id,
        entity_name=clone.name,
        changes={"cloned_from": {"new": str(asset_id)}},
    )
    return RedirectResponse(f"/assets/{clone.id}", status_code=302)


# --- Owner autocomplete (returns HTML fragment for htmx) ---


@router.get("/api/owner-search", response_class=HTMLResponse)
async def owner_search(
    request: Request,
    q: str = "",
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    if len(q) < 2:
        return HTMLResponse("")
    results = await asset_svc.search_assets(
        pool, q, organization_id=user.organization_id,
        types=[AssetType.PERSON, AssetType.ORGANIZATIONAL_UNIT],
    )
    from html import escape
    html = ""
    for a in results[:10]:
        label = a.type.value.replace("_", " ").title()
        safe_name = escape(a.name, quote=True)
        # Use data attributes instead of inline JS to prevent XSS
        html += (
            f'<div class="autocomplete-item" '
            f'data-id="{a.id}" data-name="{safe_name}" '
            f'onclick="selectOwner(this.dataset.id, this.dataset.name)">'
            f"{safe_name} <small>({label})</small></div>"
        )
    if not html:
        html = '<div class="autocomplete-item" style="color:var(--text-muted)">No results</div>'
    return HTMLResponse(html)


# --- Graph page ---


@router.get("/graph", response_class=HTMLResponse)
async def graph_overview_page(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW_GRAPH)),
):
    node_limit = 500
    total_assets = await pool.fetchval(
        "SELECT count(*) FROM assets WHERE organization_id = $1",
        user.organization_id,
    )
    notif_count = await alert_svc.count_unread_notifications(pool, organization_id=user.organization_id, user_id=user.id)
    return templates.TemplateResponse(request, "graph/overview.html", context={
            "user": user,
            "total_assets": total_assets,
            "node_limit": node_limit,
            "notif_count": notif_count,
        },
    )


@router.get("/graph/{asset_id}", response_class=HTMLResponse)
async def graph_page(
    request: Request,
    asset_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW_GRAPH)),
):
    asset = await asset_svc.get_asset(pool, asset_id, organization_id=user.organization_id)
    if not asset:
        return HTMLResponse("Not found", status_code=404)
    notif_count = await alert_svc.count_unread_notifications(pool, organization_id=user.organization_id, user_id=user.id)
    return templates.TemplateResponse(request, "graph/view.html", context={
            "user": user,
            "asset": asset,
            "notif_count": notif_count,
        },
    )


# --- Import page ---


