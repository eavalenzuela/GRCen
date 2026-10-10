"""Shared Playwright helpers for driving the local GRCen container.

Usage from any script:
    from grc_pw import browser, login, BASE
    with browser() as (pw, b):
        page = login(b, "admin")        # returns a logged-in Page (1440x900)
"""
import contextlib, glob, os
from playwright.sync_api import sync_playwright

BASE = os.environ.get("GRCEN_BASE", "https://localhost:8443")
CREDS = {
    "admin": ("admin", "AdminPass123!"),
    "editor": ("editor", "Passw0rd!123"),
    "viewer": ("viewer", "Passw0rd!123"),
    "auditor": ("auditor", "Passw0rd!123"),
}
for _i in range(1, 17): CREDS[f"admin{_i:02d}"] = (f"admin{_i:02d}", "Passw0rd!123")
for _u in ("editor2", "viewer2", "auditor2", "editor3"): CREDS[_u] = (_u, "Passw0rd!123")
EXE = sorted(glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium-1208/chrome-linux64/chrome")))[0]

@contextlib.contextmanager
def browser(headless=True):
    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path=EXE, headless=headless)
        try:
            yield pw, b
        finally:
            b.close()

def new_context(b, width=1440, height=900):
    return b.new_context(ignore_https_errors=True, viewport={"width": width, "height": height},
                         base_url=BASE)

def login(b, role=None, width=1440, height=900):
    """role: a key of CREDS (e.g. "admin03", "editor2"). Defaults to $GRCEN_USER or "admin"."""
    role = role or os.environ.get("GRCEN_USER", "admin")
    ctx = new_context(b, width, height)
    page = ctx.new_page()
    user, pw = CREDS[role]
    page.goto("/login")
    page.fill("#username", user)
    page.fill("#password", pw)
    page.click("button[type=submit]")
    page.wait_for_load_state("networkidle")
    assert "/login" not in page.url, f"login failed for {role}: {page.url}"
    return page
