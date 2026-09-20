import io
from collections import Counter
from playwright.sync_api import sync_playwright
from PIL import Image

BASE = "http://127.0.0.1:5000"


def hover_tooltip(pg, sel, theme):
    loc = pg.locator(sel)
    if not loc.count():
        return None
    box = loc.bounding_box()
    pg.mouse.move(box["x"] + box["width"] * 0.62, box["y"] + box["height"] * 0.40)
    pg.wait_for_timeout(550)
    png = pg.screenshot(clip={"x": 0, "y": 0, "width": box["width"], "height": box["height"]})
    img = Image.open(io.BytesIO(png)).convert("RGB")
    x0, x1 = int(box["width"] * 0.45), int(box["width"] * 0.85)
    y0, y1 = int(box["height"] * 0.10), int(box["height"] * 0.80)
    crop = img.crop((x0, y0, x1, y1))
    white = dark = 0
    cnt = Counter()
    for i in range(0, len(crop.tobytes()), 3):
        r, g, b = crop.tobytes()[i:i+3]
        if r > 245 and g > 245 and b > 245:
            white += 1
        if abs(r - 20) <= 10 and abs(g - 24) <= 10 and abs(b - 31) <= 10:
            dark += 1
        cnt[(r // 32, g // 32, b // 32)] += 1
    top = [f"#{r*32:02x}{g*32:02x}{b*32:02x}" for (r, g, b), _ in cnt.most_common(5)]
    return {"white_px": white, "dark_px": dark, "top": top}


with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    ctx = b.new_context(viewport={"width": 1440, "height": 900})
    pg = ctx.new_page()
    pg.goto(f"{BASE}/login", wait_until="networkidle")
    pg.fill('input[name="email"]', "qa@example.com")
    pg.fill('input[name="password"]', "Qa-password-1")
    pg.click('button[type="submit"]')
    pg.wait_for_url("**/dashboard", timeout=15000)
    for url, sel, label in (
        ("/dashboard", "#iq-performance-chart", "dash"),
        ("/analytics", "#trend-chart", "trend"),
        ("/analytics", "#skills-chart", "skills"),
    ):
        pg.goto(f"{BASE}{url}", wait_until="networkidle")
        pg.evaluate(f"document.querySelector('{sel}').scrollIntoView({{block:'center'}})")
        pg.wait_for_timeout(700)
        for theme in ("light", "dark"):
            pg.evaluate(f"localStorage.setItem('interviewiq-theme','{theme}')")
            pg.reload(wait_until="networkidle")
            pg.evaluate(f"document.querySelector('{sel}').scrollIntoView({{block:'center'}})")
            pg.wait_for_timeout(700)
            res = hover_tooltip(pg, sel, theme)
            print(f"{label:6s} {theme:5s} white={res['white_px']:6} dark={res['dark_px']:6} top={res['top']}")
    b.close()