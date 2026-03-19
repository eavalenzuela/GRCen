import asyncio
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from grcen.config import settings
from grcen.database import close_pool, get_pool, init_pool
from grcen.routers import (
    alerts,
    assets,
    attachments,
    auth,
    exports,
    graph,
    imports,
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
    await _run_migrations()
    scheduler.add_job(_tick_alerts, "interval", minutes=1, id="alert_ticker")
    scheduler.start()
    yield
    scheduler.shutdown()
    await close_pool()


async def _run_migrations():
    """Apply SQL migration files on startup."""
    import os

    pool = await get_pool()

    # Ensure migrations tracking table exists
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS _migrations (
            name TEXT PRIMARY KEY,
            applied_at TIMESTAMPTZ DEFAULT now()
        )
    """)

    migrations_dir = os.path.join(os.path.dirname(__file__), "..", "..", "migrations")
    migrations_dir = os.path.normpath(migrations_dir)

    if not os.path.isdir(migrations_dir):
        return

    applied = {r["name"] for r in await pool.fetch("SELECT name FROM _migrations")}

    files = sorted(f for f in os.listdir(migrations_dir) if f.endswith(".sql"))
    for fname in files:
        if fname in applied:
            continue
        sql = open(os.path.join(migrations_dir, fname)).read()
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO _migrations (name) VALUES ($1)", fname
                )


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

    # Page routers
    app.include_router(pages.router)

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
    from grcen.services.auth import create_user

    pool = await init_pool()
    await _run_migrations()

    username = input("Username: ")
    password = input("Password: ")

    user = await create_user(pool, username, password, is_admin=True)
    print(f"Admin user '{user.username}' created (id={user.id})")
    await close_pool()
