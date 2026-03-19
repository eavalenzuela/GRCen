import asyncpg

from grcen.config import settings

pool: asyncpg.Pool | None = None


def _dsn() -> str:
    """Convert the config DATABASE_URL to a plain postgres:// DSN for asyncpg."""
    url = settings.DATABASE_URL
    if url.startswith("postgresql+asyncpg://"):
        url = url.replace("postgresql+asyncpg://", "postgresql://", 1)
    return url


async def init_pool() -> asyncpg.Pool:
    global pool
    pool = await asyncpg.create_pool(_dsn(), min_size=2, max_size=10)
    return pool


async def close_pool() -> None:
    global pool
    if pool:
        await pool.close()
        pool = None


async def get_pool() -> asyncpg.Pool:
    if pool is None:
        raise RuntimeError("Database pool not initialised")
    return pool
