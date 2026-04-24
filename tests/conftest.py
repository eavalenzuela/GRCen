import os
import uuid

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# Point at test database
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://grcen:grcen@localhost:5432/grcen_test"
)
os.environ["SECRET_KEY"] = "test-secret"
os.environ["DEBUG"] = "true"
os.environ["SSL_CERTFILE"] = ""
os.environ["SSL_KEYFILE"] = ""
os.environ["ENCRYPTION_KEY"] = ""
os.environ["ENCRYPTION_KEY_RETIRED"] = ""

from grcen.database import close_pool, init_pool, init_schema  # noqa: E402
from grcen.main import app  # noqa: E402


def _extract_csrf_from_html(html: str) -> str:
    """Extract CSRF token from a hidden input in HTML."""
    import re
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
    return match.group(1) if match else ""


def _extract_csrf_from_session_cookie(client: AsyncClient) -> str:
    """Decode the Starlette session cookie to read the CSRF token."""
    import base64
    import json
    cookie = client.cookies.get("session", "")
    if not cookie:
        return ""
    # Starlette session cookie format: base64_payload.timestamp.signature
    payload = cookie.split(".")[0]
    # Add padding
    payload += "=" * (-len(payload) % 4)
    try:
        data = json.loads(base64.b64decode(payload))
        return data.get("csrf_token", "")
    except Exception:
        return ""


async def get_csrf_token(client: AsyncClient, path: str = "/login") -> str:
    """Fetch a page to extract the CSRF token (from HTML or session cookie)."""
    resp = await client.get(path)
    token = _extract_csrf_from_html(resp.text)
    if not token:
        token = _extract_csrf_from_session_cookie(client)
    return token


async def login_with_csrf(client: AsyncClient, username: str, password: str):
    """GET the login page to obtain a CSRF token, then POST the form.

    Sets the X-CSRF-Token header on the client so subsequent POST requests
    to page routes pass CSRF validation automatically.
    """
    # Clear rate limiter so test fixture logins aren't throttled
    from grcen.rate_limit import _reset as _reset_rate_limit
    _reset_rate_limit()
    csrf_token = await get_csrf_token(client, "/login")
    await client.post("/login", data={
        "username": username,
        "password": password,
        "csrf_token": csrf_token,
    })
    # Login clears the session (fixation prevention). The middleware will seed
    # a new CSRF token on the next request. Trigger it with a lightweight GET.
    await client.get("/health")
    new_csrf = _extract_csrf_from_session_cookie(client)
    client.headers["X-CSRF-Token"] = new_csrf


@pytest_asyncio.fixture(scope="session")
async def pool():
    p = await init_pool()
    await init_schema()
    yield p
    await close_pool()


@pytest_asyncio.fixture(autouse=True)
async def clean_tables(pool):
    # Reset login rate limiter before each test so logins aren't throttled
    from grcen.rate_limit import _reset as _reset_rate_limit
    _reset_rate_limit()
    yield
    for table in ("sessions", "api_tokens", "app_settings", "audit_log", "webhook_deliveries", "webhooks", "notification_deliveries", "notifications", "alerts", "attachments", "relationships", "risk_snapshots", "assets", "users", "encryption_config"):
        await pool.execute(f"DELETE FROM {table}")
    # Reset audit config to defaults so tests start fresh
    await pool.execute("UPDATE audit_config SET enabled = true, field_level = true")
    from grcen.services import audit_service
    audit_service._config_cache = None
    # Reset OIDC config to defaults
    await pool.execute("DELETE FROM oidc_config")
    await pool.execute("""
        INSERT INTO oidc_config (key, value) VALUES
            ('issuer_url', ''), ('client_id', ''), ('client_secret', ''),
            ('scopes', 'openid email profile'), ('role_claim', 'groups'),
            ('role_mapping', '{}'), ('default_role', 'viewer'), ('display_name', 'SSO')
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
    """)
    from grcen.services import oidc_settings
    oidc_settings._cache = None
    # Reset SAML config to defaults
    await pool.execute("DELETE FROM saml_config")
    await pool.execute("""
        INSERT INTO saml_config (key, value) VALUES
            ('idp_entity_id', ''), ('idp_sso_url', ''), ('idp_slo_url', ''),
            ('idp_x509_cert', ''), ('sp_entity_id', ''), ('sp_private_key', ''),
            ('sp_x509_cert', ''),
            ('name_id_format', 'urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress'),
            ('role_attribute', 'Role'), ('role_mapping', '{}'),
            ('default_role', 'viewer'), ('display_name', 'SAML SSO'),
            ('want_assertions_signed', 'true'), ('want_name_id_encrypted', 'false')
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
    """)
    from grcen.services import saml_settings as _saml_settings
    _saml_settings._cache = None
    # Reset SMTP config to defaults
    await pool.execute("DELETE FROM smtp_config")
    await pool.execute("""
        INSERT INTO smtp_config (key, value) VALUES
            ('host', ''), ('port', '587'), ('username', ''), ('password', ''),
            ('from_address', ''), ('from_name', 'GRCen'),
            ('use_starttls', 'true'), ('use_ssl', 'false'), ('enabled', 'false')
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
    """)
    from grcen.services import smtp_settings as _smtp_settings
    _smtp_settings._cache = None
    # Reset encryption config cache
    from grcen.services import encryption_config as _enc_cfg
    _enc_cfg._cache = None
    _enc_cfg._profile_cache = None
    # Reset login rate limiter between tests
    from grcen.rate_limit import _reset as _reset_rate_limit
    _reset_rate_limit()


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
    await login_with_csrf(client, user.username, "testpass")
    return client


@pytest_asyncio.fixture
async def editor_client(pool):
    from grcen.permissions import UserRole
    from grcen.services.auth import create_user

    user = await create_user(pool, f"editor_{uuid.uuid4().hex[:8]}", "testpass", role=UserRole.EDITOR)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await login_with_csrf(c, user.username, "testpass")
        yield c


@pytest_asyncio.fixture
async def viewer_client(pool):
    from grcen.permissions import UserRole
    from grcen.services.auth import create_user

    user = await create_user(pool, f"viewer_{uuid.uuid4().hex[:8]}", "testpass", role=UserRole.VIEWER)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await login_with_csrf(c, user.username, "testpass")
        yield c


@pytest_asyncio.fixture
async def auditor_client(pool):
    from grcen.permissions import UserRole
    from grcen.services.auth import create_user

    user = await create_user(pool, f"auditor_{uuid.uuid4().hex[:8]}", "testpass", role=UserRole.AUDITOR)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await login_with_csrf(c, user.username, "testpass")
        yield c
