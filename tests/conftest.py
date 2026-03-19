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


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def pool():
    p = await init_pool()
    await init_schema()
    yield p
    await close_pool()


@pytest_asyncio.fixture(autouse=True)
async def clean_tables(pool):
    yield
    for table in ("notifications", "alerts", "attachments", "relationships", "assets", "users"):
        await pool.execute(f"DELETE FROM {table}")


@pytest_asyncio.fixture
async def client(pool):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def auth_client(pool, client):
    """Client with an authenticated admin session."""
    from grcen.services.auth import create_user

    user = await create_user(pool, f"admin_{uuid.uuid4().hex[:8]}", "testpass", is_admin=True)
    await client.post("/login", data={"username": user.username, "password": "testpass"})
    return client
