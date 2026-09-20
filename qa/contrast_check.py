import json
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5000"

VARS = ["--text", "--text-secondary", "--text-muted", "--placeholder", "--bg",
        "--bg-elevated", "--bg-inset", "--sidebar-bg", "--primary", "--primary-text",
        "--primary-hover", "--primary-contrast", "--input-bg", "--input-border",
        "--border", "--chart-tooltip-bg", "--chart-tooltip-text",
        "--chart-tooltip-border", "--success", "--warning", "--danger",
        "--focus-ring"]


def parse(s):
    s = s.strip()
    if not s:
        return None
    if s.startswith("rgb") or s.startswith("#"):
        return s
    # rgba(r,g,b,a) -> css color string usable by a tiny comp func
    return s


def to_rgb(s):
    if s.startswith("#"):
        s = s[1:]
        if len(s) == 3:
            s = "".join(c * 2 for c in s)
        return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
    inner = s[s.index("(") + 1:s.rindex(")")]
    parts = [p.strip().rstrip("%") for p in inner.split(",")]
    vals = []
    for p in parts:
        vals.append(int(round(float(p) * 255 / 100)) if "%" in p or (False) else int(round(float(p))))
    while len(vals) < 3:
        vals.append(255)
    return tuple(vals[:3])


def lum(rgb):
    def f(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(rgb[0]) + 0.7152 * f(rgb[1]) + 0.0722 * f(rgb[2])


def cr(t, b):
    l1, l2 = sorted([lum(t), lum(b)], reverse=True)
    return round((l1 + 0.05) / (l2 + 0.05), 2)


def blend(fg, bg):
    # fg = rgba(r,g,b,a)
    inner = fg[fg.index("(") + 1:fg.rindex(")")]
    p = [x.strip() for x in inner.split(",")]
    a = float(p[3]) if len(p) == 4 else 1.0
    return tuple(round(f * a + b * (1 - a)) for f, b in zip(to_rgb(fg), bg))


COMBOS = [
    ("text/bg page", "--text", "--bg"),
    ("text-secondary/bg", "--text-secondary", "--bg"),
    ("text-muted/bg", "--text-muted", "--bg"),
    ("placeholder/input", "--placeholder", "--input-bg"),
    ("primary-text/bg (links)", "--primary-text", "--bg"),
    ("btn primary text/bg", "--primary-contrast", "--primary"),
    ("tooltip text/bg", "--chart-tooltip-text", "--chart-tooltip-bg"),
]


def col(s):
    return s.strip()


with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    ctx = b.new_context(viewport={"width": 1440, "height": 900})
    pg = ctx.new_page()
    pg.goto(f"{BASE}/login", wait_until="networkidle")
    pg.fill('input[name="email"]', "qa@example.com")
    pg.fill('input[name="password"]', "Qa-password-1")
    pg.click('button[type="submit"]')
    pg.wait_for_url("**/dashboard", timeout=15000)

    report = {}
    for theme in ("light", "dark"):
        pg.evaluate(f"localStorage.setItem('interviewiq-theme','{theme}')")
        pg.reload(wait_until="networkidle")
        vals = pg.evaluate(
            "(()=>{const cs=getComputedStyle(document.body);const o={};"
            + ",".join(f"o['{v}']=cs.getPropertyValue('{v}')" for v in VARS)
            + ";return o})()"
        )
        row = {}
        for label, t, b in COMBOS:
            tc, bc = col(vals[t]), col(vals[b])
            if tc.startswith("rgb") or tc.startswith("#"):
                row[label] = cr(to_rgb(tc), to_rgb(bc)) if tc and bc else None
            else:
                row[label] = None
        report[theme] = {"vars": vals, "contrast": row}
        print(f"\n=== {theme} theme ===")
        for rk, rv in row.items():
            flag = "OK " if (rv is None or rv is str or rv >= 4.5) else (
                "!! " if rv < 4.5 else "")
            print(f"  {flag} {rk:24s} {str(rv)}")
        for k in ("--bg", "--bg-elevated", "--bg-inset", "--sidebar-bg",
                  "--primary", "--primary-contrast", "--input-bg",
                  "--chart-tooltip-bg", "--chart-tooltip-text",
                  "--chart-tooltip-border"):
            print(f"    {k} = {vals[k][:60]}")

    with open("qa/contrast_report.json", "w") as fh:
        json.dump(report, fh, indent=2)
    b.close()