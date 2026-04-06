"""
Record E2E teacher scenario with human-like delays and visible cursor.
Flow: login → create lecture (PDF) → generate test → publish → open QR.
"""
import random
import re
import time
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = "http://localhost:8000"
PDF_PATH = r"C:\Users\sashka1337\Desktop\shlak\CCNA_ITN_Chp1.pdf"
VIDEO_DIR = Path(__file__).resolve().parent.parent / "presentation_assets" / "video"
VIDEO_DIR.mkdir(parents=True, exist_ok=True)

EMAIL = "teacher1@example.com"
PASSWORD = "Teacher123!"

# ── visible cursor overlay (40 px, drop-shadow) ──
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
    """Human delay — slightly randomised."""
    time.sleep(random.uniform(lo, hi))

def _jitter():
    """Small random offset so clicks aren't dead-centre."""
    return random.uniform(-4, 4)

def human_type(page, selector, text):
    """Click field, then type char-by-char with realistic tempo."""
    page.click(selector)
    hd(0.3, 0.6)
    for i, ch in enumerate(text):
        page.keyboard.type(ch, delay=random.randint(30, 90))
        # occasional micro-pause ("thinking")
        if random.random() < 0.08:
            time.sleep(random.uniform(0.15, 0.35))
        else:
            time.sleep(random.uniform(0.02, 0.06))
    hd(0.4, 0.8)

def move_click(page, selector):
    """Move cursor with natural arc, pause, click."""
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
    """Move cursor without clicking."""
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
        page.set_default_timeout(90000)

        # ═══════ 1. LOGIN ═══════
        page.goto(f"{BASE}/login")
        page.wait_for_load_state("networkidle"); inject_cursor(page)
        hd(1.0, 1.8)

        human_type(page, 'input[name="login"]', EMAIL)
        hd(0.5, 0.9)
        human_type(page, 'input[name="password"]', PASSWORD)
        hd(0.6, 1.0)
        move_click(page, 'button[type="submit"]')
        page.wait_for_load_state("networkidle"); inject_cursor(page)
        hd(1.5, 2.5)

        # ═══════ 2. LECTURES LIST ═══════
        page.goto(f"{BASE}/teacher/lectures")
        page.wait_for_load_state("networkidle"); inject_cursor(page)
        hd(1.5, 2.2)

        # ═══════ 3. NEW LECTURE ═══════
        move_click(page, 'a[href="/teacher/lectures/new"]')
        page.wait_for_load_state("domcontentloaded"); inject_cursor(page)
        hd(1.0, 1.5)

        human_type(page, 'input[name="title"]', "Введение в компьютерные сети (CCNA)")
        hd(0.6, 1.0)

        move_click(page, 'select[name="discipline_id"]')
        hd(0.3, 0.5)
        opts = page.locator('select[name="discipline_id"] option')
        for i in range(opts.count()):
            v = opts.nth(i).get_attribute("value")
            if v:
                page.select_option('select[name="discipline_id"]', v)
                break
        hd(0.7, 1.2)

        # Upload PDF
        move_to(page, 'input[name="lecture_file"]')
        page.set_input_files('input[name="lecture_file"]', PDF_PATH)
        hd(1.2, 1.8)

        move_click(page, '#save-lecture-btn')
        page.wait_for_url("**/teacher/lectures**", timeout=90000)
        page.wait_for_load_state("networkidle"); inject_cursor(page)
        hd(2.0, 3.0)

        # ═══════ 4. OPEN LECTURE ═══════
        toggles = page.locator("button[data-teacher-discipline-toggle]")
        for i in range(toggles.count()):
            if toggles.nth(i).get_attribute("aria-expanded") != "true":
                toggles.nth(i).click(); hd(0.3, 0.5)
        hd(0.6, 1.0)

        card = page.locator('.admin-card:has(.card-title:has-text("Введение в компьютерные сети"))')
        if card.count() > 0:
            btn = card.first.locator('a:has-text("Открыть лекцию")')
            btn.scroll_into_view_if_needed(); hd(0.25, 0.4)
            box = btn.bounding_box()
            if box:
                page.mouse.move(box["x"]+box["width"]/2, box["y"]+box["height"]/2, steps=16)
                hd(0.3, 0.6)
            btn.click()
        else:
            move_click(page, 'a:has-text("Открыть лекцию")')
        page.wait_for_load_state("networkidle"); inject_cursor(page)
        hd(1.5, 2.2)

        # ═══════ 5. GENERATE TEST ═══════
        qc = page.locator('input[name="question_count"]')
        if qc.count():
            qc.scroll_into_view_if_needed(); hd(0.3, 0.5)
            qc.click(click_count=3); hd(0.2, 0.3)
            page.keyboard.type("5", delay=80)
        hd(0.5, 0.8)

        df = page.locator('select[name="difficulty"]')
        if df.count():
            move_click(page, 'select[name="difficulty"]')
            hd(0.2, 0.4)
            df.select_option("medium")
        hd(0.6, 1.0)

        move_click(page, '#generate-test-submit')
        page.wait_for_url("**/teacher/tests/*/edit**", timeout=120000)
        page.wait_for_load_state("networkidle"); inject_cursor(page)
        hd(2.0, 3.0)

        # ═══════ 6. SCROLL QUESTIONS ═══════
        page.evaluate("window.scrollTo({top:document.body.scrollHeight*.35,behavior:'smooth'})")
        hd(1.8, 2.8)
        page.evaluate("window.scrollTo({top:document.body.scrollHeight*.7,behavior:'smooth'})")
        hd(1.8, 2.8)
        page.evaluate("window.scrollTo({top:0,behavior:'smooth'})")
        hd(1.2, 1.8)

        # ═══════ 7. PUBLISH ═══════
        pub = page.locator('button:has-text("Опубликовать")')
        if pub.count():
            pub.scroll_into_view_if_needed(); hd(0.3, 0.5)
            box = pub.bounding_box()
            if box:
                page.mouse.move(box["x"]+box["width"]/2, box["y"]+box["height"]/2, steps=14)
                hd(0.4, 0.7)
            pub.click()
            page.wait_for_load_state("networkidle"); inject_cursor(page)
            hd(1.5, 2.5)

        # ═══════ 8. OPEN QR ═══════
        m = re.search(r"/teacher/tests/(\d+)", page.url)
        if m:
            tid = m.group(1)
            qr_loc = page.locator(f'a[href="/teacher/tests/{tid}/qr"]')
            if qr_loc.count():
                move_click(page, f'a[href="/teacher/tests/{tid}/qr"]')
            else:
                page.goto(f"{BASE}/teacher/tests/{tid}/qr")
        else:
            qr_loc = page.locator('a:has-text("QR")')
            if qr_loc.count():
                move_click(page, 'a:has-text("QR")')

        page.wait_for_load_state("networkidle"); inject_cursor(page)
        hd(4.0, 5.5)

        # Final calm pause so the video doesn't cut abruptly
        hd(1.5, 2.5)

        # ═════ close ═════
        ctx.close(); browser.close()

    vids = sorted(VIDEO_DIR.glob("*.webm"), key=os.path.getmtime, reverse=True)
    if vids:
        out = VIDEO_DIR / "e2e_teacher_scenario.webm"
        if out.exists(): out.unlink()
        vids[0].rename(out)
        print(f"\n✅ Video 1 saved: {out}")


if __name__ == "__main__":
    run()
