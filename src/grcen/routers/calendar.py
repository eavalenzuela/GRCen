"""Login-less iCal review-calendar feed.

Follows the vendor-portal precedent: the URL embeds an unguessable per-user
token (``users.calendar_token``), which is the capability that authorizes the
read. Calendar apps can't do session auth, so the token IS the auth — rotate
or revoke it from /settings. The feed is read-only and scoped to the review
dates already visible to the token owner's active org.
"""

import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from grcen.config import settings
from grcen.routers.deps import get_db
from grcen.services import calendar_service

feed_router = APIRouter(tags=["calendar"])


@feed_router.get("/calendar/{token}/grcen.ics", include_in_schema=False)
async def calendar_feed(
    token: str,
    pool: asyncpg.Pool = Depends(get_db),
):
    ics = await calendar_service.feed_for_token(
        pool, token, base_url=settings.APP_BASE_URL
    )
    if ics is None:
        # Same 404 for unknown vs revoked tokens — no probing signal.
        raise HTTPException(status_code=404, detail="Not found")
    return Response(
        content=ics,
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": 'inline; filename="grcen.ics"'},
    )
