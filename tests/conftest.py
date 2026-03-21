import asyncio
import os
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# Point at test database
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://grcen:grcen@localhost:5432/grcen_test"
)
os.environ["SECRET_KEY"] = "test-secret"

from grcen.database import close_pool, init_pool, init_schema  # noqa: E402
from grcen.main import app  # noqa: E402


@pytest_asyncio.fixture(scope="session")
async def pool():
    p = await init_pool()
    await init_schema()
    yield p
    await close_pool()


@pytest_asyncio.fixture(autouse=True)
async def clean_tables(pool):
    yield
    for table in ("audit_log", "notifications", "alerts", "attachments", "relationships", "assets", "users"):
        await pool.execute(f"DELETE FROM {table}")
    # Reset audit config to defaults so tests start fresh
    await pool.execute("UPDATE audit_config SET enabled = true, field_level = true")
    from grcen.services import audit_service
    audit_service._config_cache = None


@pytest_asyncio.fixture
async def client(pool):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def auth_client(pool, client):
    """Client with an authenticated admin session."""
    from grcen.permissions import UserRole
    from grcen.services.auth import create_user

    user = await create_user(pool, f"admin_{uuid.uuid4().hex[:8]}", "testpass", role=UserRole.ADMIN)
    await client.post("/login", data={"username": user.username, "password": "testpass"})
    return client


@pytest_asyncio.fixture
async def editor_client(pool):
    from grcen.permissions import UserRole
    from grcen.services.auth import create_user

    user = await create_user(pool, f"editor_{uuid.uuid4().hex[:8]}", "testpass", role=UserRole.EDITOR)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await c.post("/login", data={"username": user.username, "password": "testpass"})
        yield c


@pytest_asyncio.fixture
async def viewer_client(pool):
    from grcen.permissions import UserRole
    from grcen.services.auth import create_user

    user = await create_user(pool, f"viewer_{uuid.uuid4().hex[:8]}", "testpass", role=UserRole.VIEWER)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await c.post("/login", data={"username": user.username, "password": "testpass"})
        yield c


@pytest_asyncio.fixture
async def auditor_client(pool):
    from grcen.permissions import UserRole
    from grcen.services.auth import create_user

    user = await create_user(pool, f"auditor_{uuid.uuid4().hex[:8]}", "testpass", role=UserRole.AUDITOR)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await c.post("/login", data={"username": user.username, "password": "testpass"})
        yield c
