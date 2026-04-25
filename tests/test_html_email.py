"""HTML email rendering + multipart delivery shape."""
import uuid

import pytest

from grcen.config import settings
from grcen.models.alert import Alert
from grcen.services import email_service


def _alert(title="Quarterly review due", message="Check the policy."):
    return Alert(
        id=uuid.uuid4(),
        asset_id=uuid.uuid4(),
        title=title,
        message=message,
        schedule_type="once",
        cron_expression=None,
        next_fire_at=None,
        enabled=True,
        created_at=None,
        updated_at=None,
    )


def test_render_alert_email_returns_text_and_html():
    text, html = email_service.render_alert_email(_alert(), "Acme Policy", "https://app/asset/1")
    # Plain-text body has the basics
    assert "Quarterly review due" in text
    assert "Acme Policy" in text
    assert "https://app/asset/1" in text
    assert "Manage your notification preferences" in text or "preferences" in text
    # HTML body has the branded shell + button
    assert "<html" in html.lower()
    assert "Quarterly review due" in html
    assert "https://app/asset/1" in html
    assert "Manage notification preferences" in html


def test_render_uses_app_base_url_for_unsubscribe(monkeypatch):
    monkeypatch.setattr(settings, "APP_BASE_URL", "https://grc.example.com/")
    text, html = email_service.render_alert_email(_alert(), "X", "https://x")
    assert "https://grc.example.com/settings" in text
    assert "https://grc.example.com/settings" in html


def test_alert_html_escapes_user_supplied_content():
    text, html = email_service.render_alert_email(
        _alert(title="<script>x</script>", message="hi"), "asset", "https://x"
    )
    # Plaintext keeps the angle brackets verbatim.
    assert "<script>" in text
    # HTML body must not pass them through unescaped.
    assert "<script>x</script>" not in html
    assert "&lt;script&gt;" in html


@pytest.mark.asyncio
async def test_send_email_attaches_multipart(monkeypatch, pool):
    """When html_body is supplied, the resulting message is multipart/alternative."""
    captured = {}

    async def fake_send(msg, **kwargs):
        captured["msg"] = msg

    # Force SMTP to be configured.
    from grcen.services import smtp_settings as smtp_svc
    monkeypatch.setattr(
        smtp_svc, "get_settings",
        lambda _pool: _fake_smtp(),
    )
    monkeypatch.setattr("aiosmtplib.send", fake_send)

    ok, err = await email_service.send_email(
        pool,
        to="user@example.com",
        subject="Hi",
        body="plain",
        html_body="<p>plain</p>",
    )
    assert ok is True
    msg = captured["msg"]
    assert msg.is_multipart()
    parts = list(msg.walk())
    types = {p.get_content_type() for p in parts}
    assert "text/plain" in types
    assert "text/html" in types
    # List-Unsubscribe header should be set.
    assert msg["List-Unsubscribe"] is not None
    assert "/settings" in msg["List-Unsubscribe"]


async def _fake_smtp():
    """Stand-in for smtp_svc.get_settings — minimal enabled settings object."""
    from dataclasses import dataclass

    @dataclass
    class S:
        host: str = "localhost"
        port_int: int = 25
        username: str = ""
        password: str = ""
        from_address: str = "no-reply@example.com"
        from_name: str = "GRCen"
        starttls: bool = False
        ssl: bool = False
        is_enabled: bool = True

    return S()
