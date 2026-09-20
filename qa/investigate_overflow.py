import os, sys, json
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5000"


def find_overflowers(page):
    return page.evaluate("""
      (()=>{
        const vw = document.documentElement.clientWidth;
        const out = [];
        document.querySelectorAll('body *').forEach(el=>{
          const r = el.getBoundingClientRect();
          if (r.right > vw + 1 || r.left < -1) {
            const cs = getComputedStyle(el);
            out.push({tag: el.tagName, cls: (el.className||'').toString().slice(0,80),
                      left: Math.round(r.left), right: Math.round(r.right),
                      w: Math.round(r.width)});
          }
        });
        out.sort((a,b)=> (b.right - vw) - (a.right - vw));
        return {vw, count: out.length, top: out.slice(0, 14), sw: document.documentElement.scrollWidth};
      })()
    """)


with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    ctx = b.new_context(viewport={"width": 1440, "height": 900})
    ctx.add_init_script("localStorage.setItem('interviewiq-theme','light');")
    pg = ctx.new_page()
    pg.goto(f"{BASE}/login", wait_until="networkidle")
    pg.fill('input[name="email"]', "qa@example.com")
    pg.fill('input[name="password"]', "Qa-password-1")
    pg.click('button[type="submit"]')
    pg.wait_for_url("**/dashboard", timeout=15000)

    for url in ("/dashboard", "/analytics"):
        for theme in ("light", "dark"):
            for w in (360, 560):
                ctx.add_init_script(f"localStorage.setItem('interviewiq-theme','{theme}');")
                # easiest reliable theme switch: set storage + reload
                pg.evaluate(f"localStorage.setItem('interviewiq-theme','{theme}')")
                pg.set_viewport_size({"width": w, "height": 900})
                pg.reload(wait_until="networkidle")
                res = find_overflowers(pg)
                print(f"\n=== {url} {theme} @{w} overflowers={res['count']} scrollWidth={res['sw']} vw={res['vw']}")
                for e in res["top"]:
                    print(f"   {e['tag']:8s} {e['cls']:42s} L={e['left']:5} R={e['right']:5} W={e['w']}")
    b.close()