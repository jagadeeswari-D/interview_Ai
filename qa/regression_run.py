import os, sys, re, threading, time
import requests
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5000"
EMAIL = "qa@example.com"
PASSWORD = "Qa-password-1"

PATH = r"C:\Users\sarath\Desktop\interview_Ai\interview_Ai"
sys.path.insert(0, PATH)
os.chdir(PATH)
from dotenv import load_dotenv
load_dotenv()
from app import create_app
app = create_app()

def run_server():
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False, threaded=True)

threading.Thread(target=run_server, daemon=True).start()
for _ in range(40):
    try:
        requests.get(f"{BASE}/login", timeout=2); break
    except Exception:
        time.sleep(0.5)

def login_cookies():
    s = requests.Session()
    html = s.get(f"{BASE}/login", timeout=15).text
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    token = m.group(1) if m else ""
    r = s.post(f"{BASE}/login", data={"csrf_token": token, "email": EMAIL, "password": PASSWORD}, allow_redirects=False, timeout=15)
    assert r.status_code == 302, "login failed"
    return s.cookies.get_dict()

PAGES = ["/dashboard", "/analytics", "/practice", "/interview", "/roadmap",
         "/history", "/replay", "/resume", "/reports", "/profile", "/settings"]
WIDTHS = [360, 560, 768, 1440]
THEMES = ["light", "dark"]

with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    cookies = login_cookies()
    favicon_status = {}
    for page in PAGES:
        for theme in THEMES:
            for w in WIDTHS:
                ctx = b.new_context(viewport={"width": w, "height": 900})
                ctx.add_cookies([{"name": k, "value": v, "domain": "127.0.0.1", "path": "/"} for k, v in cookies.items()])
                ctx.set_default_timeout(15000)
                pg = ctx.new_page()
                pg.add_init_script(f"localStorage.setItem('interviewiq-theme','{theme}');")
                csp = []; errs = []; bad = []
                pg.on("console", lambda m, csp=csp, errs=errs: (
                    csp.append(m.text) if ('style-src' in m.text or 'Content Security' in m.text or 'Refused to' in m.text) else None,
                    errs.append(m.text) if m.type in ("error", "warning") else None))
                pg.on("pageerror", lambda e, errs=errs: errs.append(str(e)))
                pg.on("response", lambda r, bad=bad, favicon_status=favicon_status: (
                    favicon_status.__setitem__(r.url, r.status) if r.url.endswith("favicon.svg") else None,
                    bad.append((r.url, r.status)) if (r.status >= 400 and "favicon" not in r.url) else None))
                try:
                    pg.goto(BASE + page, wait_until="load")
                    time.sleep(1.3)
                    ov = pg.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
                    flag = "OK " if ov <= 0 else "OVERFLOW+%d" % ov
                    print(f"{page:14s} {theme:5s} @ {w:4d} -> {flag}  csp={len(csp)} errs={len(errs)} badResp={len(bad)}")
                    for t in csp[:3]: print("      CSP:", t[:140])
                    for t in errs[:3]: print("      console:", t[:140])
                    for u, s in bad[:3]: print(f"      badResp: {s} {u}")
                except Exception as e:
                    print(f"{page:14s} {theme:5s} @ {w:4d} -> EXC {str(e)[:120]}")
                ctx.close()
    print("\nfavicon.svg status:", favicon_status)
b.close()