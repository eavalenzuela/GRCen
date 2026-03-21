import asyncio
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from grcen.config import settings
from grcen.database import close_pool, get_pool, init_pool, init_schema
from grcen.routers import (
    alerts,
    assets,
    attachments,
    auth,
    exports,
    graph,
    imports,
    oidc,
    pages,
    relationships,
)

scheduler = AsyncIOScheduler()


async def _tick_alerts():
    """Check for alerts whose next_fire_at has passed and fire them."""
    from grcen.services.alert_service import fire_alert

    pool = await get_pool()
    now = datetime.now(UTC)
    rows = await pool.fetch(
        "SELECT id, schedule_type, cron_expression FROM alerts"
        " WHERE enabled = true AND next_fire_at <= $1",
        now,
    )
    for row in rows:
        await fire_alert(pool, row["id"])
        if row["schedule_type"] == "recurring" and row["cron_expression"]:
            trigger = CronTrigger.from_crontab(row["cron_expression"])
            next_time = trigger.get_next_fire_time(None, now)
            await pool.execute(
                "UPDATE alerts SET next_fire_at = $1, updated_at = now() WHERE id = $2",
                next_time,
                row["id"],
            )


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_pool()
    await init_schema()
    scheduler.add_job(_tick_alerts, "interval", minutes=1, id="alert_ticker")
    scheduler.start()
    yield
    scheduler.shutdown()
    await close_pool()


def create_app() -> FastAPI:
    app = FastAPI(title=settings.APP_NAME, lifespan=lifespan)

    app.add_middleware(SessionMiddleware, secret_key=settings.SECRET_KEY)

    app.mount("/static", StaticFiles(directory="src/grcen/static"), name="static")

    # API routers
    app.include_router(assets.router)
    app.include_router(relationships.router)
    app.include_router(attachments.router)
    app.include_router(graph.router)
    app.include_router(imports.router)
    app.include_router(exports.router)
    app.include_router(alerts.router)
    app.include_router(auth.router)
    app.include_router(oidc.router)

    # Page routers
    app.include_router(pages.router)

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        if exc.status_code == 403 and "text/html" in request.headers.get("accept", ""):
            from grcen.routers.pages import templates
            return templates.TemplateResponse(
                "errors/403.html", {"request": request, "user": None}, status_code=403
            )
        if exc.status_code == 401 and "text/html" in request.headers.get("accept", ""):
            from fastapi.responses import RedirectResponse
            return RedirectResponse("/login", status_code=302)
        return HTMLResponse(content=exc.detail, status_code=exc.status_code)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


app = create_app()


def cli():
    """CLI entrypoint for management commands."""
    if len(sys.argv) < 2:
        print("Usage: grcen <command>")
        print("Commands: createadmin, runserver")
        sys.exit(1)

    command = sys.argv[1]

    if command == "createadmin":
        asyncio.run(_create_admin())
    elif command == "runserver":
        import uvicorn

        uvicorn.run("grcen.main:app", host="0.0.0.0", port=8000, reload=settings.DEBUG)
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


async def _create_admin():
    from grcen.permissions import UserRole
    from grcen.services.auth import create_user

    pool = await init_pool()
    await init_schema()

    username = input("Username: ")
    password = input("Password: ")

    user = await create_user(pool, username, password, role=UserRole.ADMIN)
    print(f"Admin user '{user.username}' created (id={user.id}, role={user.role.value})")
    await close_pool()
