"""Access-log retention/purge + CSV export route."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from grcen.services import access_log_service, organization_service


@pytest.mark.asyncio
async def test_get_retention_returns_none_when_unset(pool):
    assert await access_log_service.get_retention_days(pool) is None


@pytest.mark.asyncio
async def test_set_and_clear_retention(pool):
    await access_log_service.set_retention_days(pool, 30)
    assert await access_log_service.get_retention_days(pool) == 30
    await access_log_service.set_retention_days(pool, None)
    assert await access_log_service.get_retention_days(pool) is None
    # Negative or zero clears it too.
    await access_log_service.set_retention_days(pool, 5)
    await access_log_service.set_retention_days(pool, 0)
    assert await access_log_service.get_retention_days(pool) is None


@pytest.mark.asyncio
async def test_purge_removes_only_old_rows(pool):
    org_id = await organization_service.get_default_org_id(pool)
    # Insert one ancient row + one recent row directly.
    old_id = uuid.uuid4()
    new_id = uuid.uuid4()
    old_ts = datetime.now(timezone.utc) - timedelta(days=60)
    await pool.execute(
        """INSERT INTO data_access_log
               (id, user_id, username, action, entity_type, entity_id,
                entity_name, path, ip_address, organization_id, created_at)
           VALUES ($1, NULL, 'tester', 'view', 'asset', NULL, 'old', '/x', '',
                   $2, $3)""",
        old_id, org_id, old_ts,
    )
    await pool.execute(
        """INSERT INTO data_access_log
               (id, user_id, username, action, entity_type, entity_id,
                entity_name, path, ip_address, organization_id)
           VALUES ($1, NULL, 'tester', 'view', 'asset', NULL, 'new', '/x', '',
                   $2)""",
        new_id, org_id,
    )
    # No retention configured → no purge.
    assert (await access_log_service.purge_expired(pool)) == 0
    # Set retention to 30 days; the 60-day-old row should drop.
    await access_log_service.set_retention_days(pool, 30)
    purged = await access_log_service.purge_expired(pool)
    assert purged == 1
    remaining = await pool.fetch(
        "SELECT id FROM data_access_log WHERE id IN ($1, $2)",
        old_id, new_id,
    )
    ids = {r["id"] for r in remaining}
    assert old_id not in ids
    assert new_id in ids


@pytest.mark.asyncio
async def test_csv_export_returns_attachment(auth_client, pool):
    """Export returns CSV with a download header and the column row."""
    # Seed a row through the service so it's tagged with the right org.
    from grcen.models.user import User
    user_row = await pool.fetchrow("SELECT * FROM users LIMIT 1")
    fake_user = User.from_row(user_row)
    await access_log_service.record(
        pool, user=fake_user, action="view",
        entity_type="asset", entity_id=None, entity_name="Sample",
        path="/assets/abc", ip_address="127.0.0.1",
    )
    resp = await auth_client.get("/admin/access-log/export.csv")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert 'attachment; filename="access_log.csv"' in resp.headers["content-disposition"]
    text = resp.text
    assert text.startswith("id,created_at,user_id,username")
    assert "Sample" in text


@pytest.mark.asyncio
async def test_csv_export_self_logs_export(auth_client, pool):
    """Exporting the access log writes its own access-log entry — auditable."""
    before = await pool.fetchval(
        "SELECT count(*) FROM data_access_log WHERE entity_type = 'access_log'"
    )
    await auth_client.get("/admin/access-log/export.csv")
    after = await pool.fetchval(
        "SELECT count(*) FROM data_access_log WHERE entity_type = 'access_log'"
    )
    assert after == before + 1


@pytest.mark.asyncio
async def test_retention_form_persists(auth_client, pool):
    resp = await auth_client.post(
        "/admin/access-log/retention", data={"retention_days": "90"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert await access_log_service.get_retention_days(pool) == 90
    # Empty value clears.
    resp = await auth_client.post(
        "/admin/access-log/retention", data={"retention_days": ""},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert await access_log_service.get_retention_days(pool) is None
