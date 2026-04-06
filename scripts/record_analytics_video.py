"""
Record video 2: teacher opens group analytics after students have taken tests.
Human-like cursor + delays.
"""
import random
import time
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = "http://localhost:8000"
VIDEO_DIR = Path(__file__).resolve().parent.parent / "presentation_assets" / "video"
VIDEO_DIR.mkdir(parents=True, exist_ok=True)

EMAIL = "teacher1@example.com"
PASSWORD = "Teacher123!"

CURSOR_JS = """
(function(){
  if(document.getElementById('_pw_cur')) return;
  const d=document.createElement('div'); d.id='_pw_cur';
  d.style.cssText='position:fixed;z-index:999999;pointer-events:none;width:40px;height:40px;'
    +'transition:left .10s cubic-bezier(.4,0,.2,1),top .10s cubic-bezier(.4,0,.2,1);left:-60px;top:-60px;'
    +'filter:drop-shadow(1px 2px 3px rgba(0,0,0,.45));';
  d.innerHTML='<svg width="40" height="40" viewBox="0 0 24 24">'
    +'<path d="M5 3l14 9-7 2-4 7z" fill="#fff" stroke="#18304f" stroke-width="1.2"/></svg>';
  document.body.appendChild(d);
  document.addEventListener('mousemove',e=>{d.style.left=e.clientX+'px';d.style.top=e.clientY+'px';});
})()
"""

def inject_cursor(page):
    page.evaluate(CURSOR_JS)

def hd(lo=0.6, hi=1.6):
    time.sleep(random.uniform(lo, hi))

def _jitter():
    return random.uniform(-4, 4)

def human_type(page, selector, text):
    page.click(selector)
    hd(0.3, 0.6)
    for ch in text:
        page.keyboard.type(ch, delay=random.randint(30, 90))
        if random.random() < 0.08:
            time.sleep(random.uniform(0.15, 0.35))
        else:
            time.sleep(random.uniform(0.02, 0.06))
    hd(0.4, 0.8)

def move_click(page, selector):
    el = page.locator(selector).first
    el.scroll_into_view_if_needed()
    hd(0.2, 0.45)
    box = el.bounding_box()
    if box:
        tx = box["x"] + box["width"]/2 + _jitter()
        ty = box["y"] + box["height"]/2 + _jitter()
        page.mouse.move(tx, ty, steps=random.randint(22, 38))
        hd(0.35, 0.75)
    el.click()
    hd(0.45, 0.9)

def move_to(page, selector):
    el = page.locator(selector).first
    el.scroll_into_view_if_needed()
    box = el.bounding_box()
    if box:
        page.mouse.move(box["x"]+box["width"]/2 + _jitter(),
                         box["y"]+box["height"]/2 + _jitter(),
                         steps=random.randint(18, 30))
    hd(0.4, 0.8)


def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            record_video_dir=str(VIDEO_DIR),
            record_video_size={"width": 1920, "height": 1080},
        )
        page = ctx.new_page()
        page.set_default_timeout(60000)

        # ═══════ 0. SILENT LOGIN (no visible login page) ═══════
        # Use API to set session cookie before recording starts
        page.goto(f"{BASE}/login")
        page.wait_for_load_state("networkidle")
        page.fill('input[name="login"]', EMAIL)
        page.fill('input[name="password"]', PASSWORD)
        page.click('button[type="submit"]')
        page.wait_for_load_state("networkidle")

        # Close context, re-open with cookies so recording starts fresh on dashboard
        cookies = ctx.cookies()
        ctx.close(); browser.close()

        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            record_video_dir=str(VIDEO_DIR),
            record_video_size={"width": 1920, "height": 1080},
        )
        ctx.add_cookies(cookies)
        page = ctx.new_page()
        page.set_default_timeout(60000)

        # ═══════ 1. DASHBOARD ═══════
        page.goto(f"{BASE}/dashboard")
        page.wait_for_load_state("networkidle"); inject_cursor(page)
        hd(1.5, 2.5)

        # Click "Панель преподавателя"
        move_click(page, 'a[href="/v2/teacher/tests"]')
        page.wait_for_load_state("networkidle"); inject_cursor(page)
        hd(1.5, 2.0)

        # ═══════ 2. CLICK "Успеваемость" TAB ═══════
        move_click(page, 'a[href="/v2/teacher/analytics"]')
        page.wait_for_load_state("networkidle"); inject_cursor(page)
        hd(2.0, 3.0)

        # ═══════ 3. EXPLORE ANALYTICS ═══════
        # Hover over stat cards
        stats = page.locator(".stat")
        for i in range(min(stats.count(), 3)):
            box = stats.nth(i).bounding_box()
            if box:
                page.mouse.move(box["x"]+box["width"]/2 + _jitter(),
                                 box["y"]+box["height"]/2 + _jitter(),
                                 steps=random.randint(18, 30))
                hd(0.9, 1.5)

        # Scroll to trend chart
        page.evaluate("window.scrollTo({top:350,behavior:'smooth'})")
        hd(2.5, 3.5)

        # Scroll to tables
        page.evaluate("window.scrollTo({top:700,behavior:'smooth'})")
        hd(2.0, 3.0)

        # Hover over student rows
        rows = page.locator("table tbody tr")
        for i in range(min(rows.count(), 4)):
            box = rows.nth(i).bounding_box()
            if box:
                page.mouse.move(box["x"]+box["width"]/2 + _jitter(),
                                 box["y"]+box["height"]/2 + _jitter(),
                                 steps=random.randint(14, 24))
                hd(0.6, 1.2)

        # Scroll to bar chart
        page.evaluate("window.scrollTo({top:document.body.scrollHeight*0.75,behavior:'smooth'})")
        hd(2.5, 3.5)

        # Scroll to bottom
        page.evaluate("window.scrollTo({top:document.body.scrollHeight,behavior:'smooth'})")
        hd(2.5, 3.5)

        # Final pause
        hd(1.5, 2.5)

        ctx.close(); browser.close()

    vids = sorted(VIDEO_DIR.glob("*.webm"), key=os.path.getmtime, reverse=True)
    if vids:
        out = VIDEO_DIR / "e2e_teacher_analytics.webm"
        if out.exists(): out.unlink()
        vids[0].rename(out)
        print(f"\n✅ Video 2 saved: {out}")


if __name__ == "__main__":
    run()
