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
threading.Thread(target=lambda: app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False, threaded=True), daemon=True).start()
for _ in range(40):
    try:
        requests.get(f"{BASE}/login", timeout=2); break
    except Exception:
        time.sleep(0.5)

def login_cookies():
    s = requests.Session()
    html = s.get(f"{BASE}/login", timeout=15).text
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    r = s.post(f"{BASE}/login", data={"csrf_token": m.group(1), "email": EMAIL, "password": PASSWORD}, allow_redirects=False, timeout=15)
    assert r.status_code == 302
    return s.cookies.get_dict()

with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    cookies = login_cookies()
    for page, checks in {
        "/dashboard": [(".iq-kpis .iq-kpi", ">=2"), ("canvas, .iq-chart", ">=1"), (".iq-hero, .iq-panel", ">=1")],
        "/analytics": [(".readiness-table-wrap", ">=1"), ("canvas, .iq-chart", ">=1"), (".iq-panel, .iq-page", ">=1")],
        "/practice": [(".sp-setup, .sp-setup-form", ">=1"), (".sp-panel", ">=1"), ("[data-reveal], .iq-reveal", ">=1")],
        "/interview": [((".iq-config, .interview-config, form"), ">=1")],
    }.items():
        for w in (360, 1440):
            ctx = b.new_context(viewport={"width": w, "height": 900})
            ctx.add_cookies([{"name": k, "value": v, "domain": "127.0.0.1", "path": "/"} for k, v in cookies.items()])
            pg = ctx.new_page()
            pg.add_init_script("localStorage.setItem('interviewiq-theme','light');")
            pg.goto(BASE + page, wait_until="load")
            time.sleep(1.2)
            for sel, cond in checks:
                n = pg.evaluate("s => document.querySelectorAll(s).length", sel)
                pred = {">=1": n >= 1, ">=2": n >= 2}
                status = "OK " if pred.get(cond, n >= 1) else "MISSING"
                print(f"{page:12s} @{w:4d}  {sel:32s} count={n:<3d} {status}")
            ctx.close()
b.close()
print("DONE")