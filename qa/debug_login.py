import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5000"
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    ctx = b.new_context(viewport={"width": 1440, "height": 900})
    ctx.add_init_script("localStorage.setItem('interviewiq-theme','light');")
    pg = ctx.new_page()
    pg.on("console", lambda m: print("CONSOLE:", m.type, m.text[:150]))
    pg.on("pageerror", lambda e: print("PAGEERR:", str(e)[:200]))
    pg.goto(f"{BASE}/login", wait_until="networkidle", timeout=15000)
    print("url after load:", pg.url)
    print("inputs:", pg.evaluate("[...document.querySelectorAll('input')].map(i=>({n:i.name,t:i.type}))"))
    pg.fill('input[name="email"]', "qa@example.com")
    pg.fill('input[name="password"]', "Qa-password-1")
    print("submit buttons:", pg.evaluate("[...document.querySelectorAll('button')].map(b=>b.type)"))
    pg.click('button[type="submit"]')
    pg.wait_for_timeout(2500)
    print("url after submit:", pg.url)
    print("data-theme:", pg.evaluate("document.documentElement.getAttribute('data-theme')"))
    body = pg.evaluate("document.body.innerText.slice(0,600)")
    print("body text:", body[:400])
    # try register path if login failed
    pg2 = ctx.new_page()
    for u in ("/register",):
        pg2.goto(f"{BASE}{u}", wait_until="networkidle", timeout=15000)
        print(f"register GET {u} status ok, inputs:", pg2.evaluate("[...document.querySelectorAll('input')].map(i=>i.name)"))
    b.close()