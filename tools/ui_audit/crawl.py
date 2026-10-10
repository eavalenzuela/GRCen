"""Crawl every reachable GET page as a role; record status, console errors, failed
sub-requests, layout overflow, a11y heuristics; screenshot desktop + mobile."""
import json, re, sys, time
from urllib.parse import urljoin, urlparse, urldefrag
from grc_pw import browser, login, new_context, BASE, CREDS

role = sys.argv[1] if len(sys.argv) > 1 else "admin"
MAX_PAGES = int(sys.argv[2]) if len(sys.argv) > 2 else 220
OUT = f"out/{role}"
import os; os.makedirs(f"{OUT}/desktop", exist_ok=True); os.makedirs(f"{OUT}/mobile", exist_ok=True)

SEEDS = ["/", "/assets", "/assets/new", "/registers", "/graph", "/org-views", "/reviews", "/overdue",
 "/risk-management", "/frameworks", "/controls", "/evidence", "/reports/executive", "/answers",
 "/questionnaires", "/vendor-campaigns", "/tags", "/alerts", "/imports", "/exports", "/approvals",
 "/my-work", "/notifications", "/settings", "/tokens", "/products", "/org-chart", "/business-structure",
 "/admin/users", "/admin/users/new", "/admin/invitations", "/admin/tokens", "/admin/oauth-clients",
 "/admin/oidc-settings", "/admin/saml-settings", "/admin/scim-settings", "/admin/smtp-settings",
 "/admin/smtp-settings/preview", "/admin/webhooks", "/admin/itsm-settings", "/admin/encryption",
 "/admin/audit/settings", "/admin/workflow", "/admin/sensitive-fields", "/admin/sessions",
 "/admin/rate-limits", "/admin/organization", "/admin/content-packs", "/admin/risk-appetite",
 "/admin/orgs", "/admin/audit", "/admin/access-log", "/docs"]
SKIP_RE = re.compile(r"(/logout|/static/|\.pdf$|\.csv$|\.zip$|\.ics$|/api/|/docs|/openapi|/calendar/)")
DOWNLOAD_RE = re.compile(r"\.(pdf|csv|zip|ics)$")

def norm(href, cur):
    u = urldefrag(urljoin(cur, href))[0]
    p = urlparse(u)
    if p.netloc and p.netloc not in urlparse(BASE).netloc: return None
    path = p.path + (("?" + p.query) if p.query else "")
    return path

report = {"role": role, "pages": [], "downloads": {}}
seen, queue = set(), []
# limit how many detail pages of the same route family we crawl
family_counts = {}
def family(path):
    return re.sub(r"/[0-9a-f-]{36}", "/{id}", re.sub(r"\?.*", "", path))
def family_ok(path):
    f = family(path)
    cap = 3 if "{id}" in f else 6
    return family_counts.get(f, 0) < cap

with browser() as (pw, b):
    page = login(b, role)
    ctx = page.context
    mobile_ctx = new_context(b, 390, 844)
    # copy cookies into mobile context
    mobile_ctx.add_cookies(ctx.cookies())
    mpage = mobile_ctx.new_page()
    for s in SEEDS: queue.append(s)
    while queue and len(report["pages"]) < MAX_PAGES:
        path = queue.pop(0)
        if path in seen or SKIP_RE.search(path) or not family_ok(path): continue
        seen.add(path); family_counts[family(path)] = family_counts.get(family(path), 0) + 1
        console, failed, dialogs = [], [], []
        h1 = lambda m: console.append({"type": m.type, "text": m.text[:300]}) if m.type in ("error", "warning") else None
        h2 = lambda r: failed.append({"url": r.url.replace(BASE, ""), "status": r.status}) if r.status >= 400 else None
        h3 = lambda r: failed.append({"url": r.url.replace(BASE, ""), "status": "FAILED", "err": r.failure})
        page.on("console", h1); page.on("response", h2); page.on("requestfailed", h3)
        page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
        t0 = time.time()
        try:
            resp = page.goto(path, wait_until="networkidle", timeout=30000)
            status = resp.status if resp else None
        except Exception as e:
            status = f"ERR {str(e)[:120]}"
        load_ms = int((time.time() - t0) * 1000)
        final_url = page.url.replace(BASE, "")
        info = page.evaluate("""() => {
          const q = s => Array.from(document.querySelectorAll(s));
          const txt = document.body ? document.body.innerText : '';
          return {
            title: document.title,
            h1: q('h1').map(e => e.innerText.trim()).slice(0,3),
            h1_count: q('h1').length,
            forms: q('form').map(f => ({action: f.getAttribute('action'), method: f.method, fields: f.querySelectorAll('input,select,textarea').length})).slice(0,20),
            links: q('a[href]').map(a => a.getAttribute('href')),
            buttons_without_text: q('button').filter(b => !b.innerText.trim() && !b.getAttribute('aria-label') && !b.title).length,
            imgs_without_alt: q('img:not([alt])').length,
            inputs_without_label: q('input:not([type=hidden]):not([type=submit]):not([type=checkbox]):not([type=radio]),select,textarea').filter(i => {
               if (i.getAttribute('aria-label') || i.getAttribute('placeholder')) return false;
               if (i.id && document.querySelector(`label[for="${i.id}"]`)) return false;
               return !i.closest('label');
            }).length,
            inline_style_count: q('[style]').length,
            tables: q('table').length,
            rows: q('table tbody tr').length,
            scroll_w: document.documentElement.scrollWidth, inner_w: window.innerWidth,
            body_len: txt.length,
            has_error_text: /Internal Server Error|Traceback|Not Found|Method Not Allowed|detail":/.test(txt),
            empty_state_hint: /No .* yet|No .* found|nothing to show|No results/i.test(txt),
            text_sample: txt.slice(0, 600),
            onclick_handlers: q('[onclick]').length,
            confirm_dialogs: q('[onsubmit*=confirm],[onclick*=confirm]').length,
          };
        }""")
        shot = f"{OUT}/desktop/{re.sub(r'[^a-zA-Z0-9]+','_',path).strip('_') or 'root'}.png"
        try: page.screenshot(path=shot, full_page=True)
        except Exception as e: shot = f"ERR {e}"
        # mobile shot for top-level pages only
        mshot = None
        if path.count("/") <= 2 and "?" not in path:
            try:
                mpage.goto(path, wait_until="networkidle", timeout=30000)
                mshot = f"{OUT}/mobile/{re.sub(r'[^a-zA-Z0-9]+','_',path).strip('_') or 'root'}.png"
                mpage.screenshot(path=mshot, full_page=True)
                info["mobile_scroll_w"] = mpage.evaluate("document.documentElement.scrollWidth")
            except Exception as e: mshot = f"ERR {e}"
        page.remove_listener("console", h1); page.remove_listener("response", h2); page.remove_listener("requestfailed", h3)
        links = info.pop("links")
        for l in links:
            n = norm(l, BASE + path)
            if not n: continue
            if DOWNLOAD_RE.search(n.split("?")[0]):
                report["downloads"].setdefault(n, None); continue
            if n not in seen and n not in queue and not SKIP_RE.search(n): queue.append(n)
        report["pages"].append({"path": path, "final_url": final_url, "status": status, "load_ms": load_ms,
                                "console": console, "failed_requests": failed, "dialogs": dialogs,
                                "screenshot": shot, "mobile_screenshot": mshot, **info})
        print(f"{status} {load_ms:>5}ms {path}  console={len(console)} failed={len(failed)} overflow={info['scroll_w']>info['inner_w']}", flush=True)
    # HEAD-check downloads via request context
    for d in list(report["downloads"])[:40]:
        try:
            r = ctx.request.get(d, timeout=60000)
            report["downloads"][d] = {"status": r.status, "type": r.headers.get("content-type"), "len": len(r.body())}
        except Exception as e:
            report["downloads"][d] = {"status": f"ERR {str(e)[:100]}"}
        print("DL", d, report["downloads"][d], flush=True)
    print(f"unvisited queue: {len(queue)}")
    report["unvisited"] = queue[:200]
json.dump(report, open(f"{OUT}/report.json", "w"), indent=1)
print("wrote", f"{OUT}/report.json", "pages:", len(report["pages"]))
