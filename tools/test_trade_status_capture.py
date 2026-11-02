"""
[테스트] 토스 거래현황 탭 — 투자자별 매매동향 / 프로그램매매 / 공매도 캡처 가능성 검증
- 기존 toss_session 재사용 (자동 로그인)
- SK텔레콤(A017670) 1종으로 거래현황 탭 구조 탐색 + 서브섹션 스샷
- 결과: stock/test_trade_capture/ 에 PNG + 구조 덤프(txt) 저장

사용: python test_trade_status_capture.py [--headed] [--code A017670]
"""
from __future__ import annotations
import argparse, sys, datetime as dt
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass

ROOT = Path(__file__).resolve().parent.parent  # tools/ → stock/
SESSION_DIR = ROOT / "toss_session"
OUT_DIR = ROOT / "test_trade_capture"
TOSS_URL = "https://www.tossinvest.com/stocks/{code}"

# 거래현황 탭 (종목 페이지 상단 네비)
TRADE_TAB_SELECTORS = [
    'a:has-text("거래현황")', 'button:has-text("거래현황")',
    '[role="tab"]:has-text("거래현황")', 'text=거래현황',
]
# 거래현황 내부 서브탭/섹션 후보 키워드
SUBSECTIONS = ["투자자", "프로그램", "공매도"]


def dump(page, label):
    """현재 페이지의 탭/버튼 텍스트를 덤프해서 구조 파악."""
    try:
        items = page.evaluate(
            """() => {
                const out = [];
                const sels = ['a','button','[role="tab"]','li'];
                for (const s of sels) {
                    document.querySelectorAll(s).forEach(el => {
                        const t = (el.innerText||'').trim();
                        if (t && t.length<=12 && /[가-힣]/.test(t)) out.push(t);
                    });
                }
                return [...new Set(out)];
            }"""
        )
        (OUT_DIR / f"dump_{label}.txt").write_text("\n".join(items), encoding="utf-8")
        print(f"    [DUMP {label}] 탭/버튼 후보: {items}")
    except Exception as e:
        print(f"    [WARN] dump 실패: {e}")


def click_text(page, selectors, wait=1500):
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible():
                loc.click(timeout=2000)
                page.wait_for_timeout(wait)
                print(f"    [CLICK] {sel}")
                return True
        except Exception:
            continue
    return False


def run(headed: bool, code: str):
    OUT_DIR.mkdir(exist_ok=True)
    today = dt.date.today().isoformat()
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(SESSION_DIR),
            headless=not headed,
            viewport={"width": 1920, "height": 1080},
            locale="ko-KR",
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        url = TOSS_URL.format(code=code)
        print(f"[GOTO] {url}")
        try:
            page.goto(url, timeout=30000, wait_until="domcontentloaded")
        except PWTimeout:
            print("  [WARN] nav timeout")
        try:
            page.wait_for_load_state("networkidle", timeout=12000)
        except PWTimeout:
            pass
        page.wait_for_timeout(3000)

        # 0) 종목 페이지 진입 직후 상단 탭 구조 덤프
        dump(page, "0_stockpage")
        page.screenshot(path=str(OUT_DIR / f"00_landing_{code}.png"))

        # 1) 거래현황 탭 클릭
        if not click_text(page, TRADE_TAB_SELECTORS, wait=2500):
            print("  [MISS] 거래현황 탭 미발견 — URL 직접 시도")
            for suffix in ["/order", "/trades", "/flow"]:
                try:
                    page.goto(url + suffix, timeout=15000, wait_until="domcontentloaded")
                    page.wait_for_timeout(2500)
                    print(f"    [TRY URL] {url+suffix}")
                except Exception:
                    continue
        page.wait_for_timeout(2000)
        dump(page, "1_trade_tab")
        page.screenshot(path=str(OUT_DIR / f"01_trade_tab_{code}.png"), full_page=True)

        # 2) 투자자별 매매동향 — '투자자별 순매수 보기' → 모달 캡처
        print("  --- [A] 투자자별 매매동향 (모달) ---")
        if click_text(page, ['button:has-text("투자자별 순매수 보기")',
                             'a:has-text("투자자별 순매수")', 'text=투자자별 순매수 보기'], wait=2000):
            modal = page.locator('[role="dialog"]').first
            try:
                if modal.count():
                    modal.screenshot(path=str(OUT_DIR / f"A_투자자_{code}.png"))
                    print(f"    [OK] A_투자자_{code}.png (모달)")
            except Exception as e:
                print(f"    [WARN] 모달 캡처 실패: {e}")
            page.keyboard.press("Escape")
            page.wait_for_timeout(1000)
        else:
            print("    [MISS] 투자자별 순매수 보기 버튼 미발견")

        # 3) 투자 유형별 현황 — 프로그램매매 / 공매도 서브탭 클릭 후 섹션 캡처
        def shot_section(kw, fname):
            # 탭 버튼에서 위로 올라가며 '테이블을 포함할 만큼 큰' 조상 요소를 찾아 태깅
            tagged = page.evaluate(
                """(kw) => {
                    const btns = Array.from(document.querySelectorAll('button,[role="tab"],a'))
                        .filter(e => (e.innerText||'').trim().startsWith(kw));
                    if (!btns.length) return false;
                    let el = btns[0];
                    for (let i=0; i<8 && el; i++) {
                        const r = el.getBoundingClientRect();
                        // 테이블까지 포함하는 높이(>350px) + 적당한 폭이면 채택
                        if (r.height > 350 && r.width > 500) {
                            el.setAttribute('data-capture-target','1');
                            return true;
                        }
                        el = el.parentElement;
                    }
                    return false;
                }""", kw)
            if tagged:
                try:
                    loc = page.locator('[data-capture-target="1"]').first
                    loc.scroll_into_view_if_needed(timeout=2000)
                    loc.screenshot(path=str(OUT_DIR / fname))
                    page.evaluate("""() => { const e=document.querySelector('[data-capture-target="1"]'); if(e) e.removeAttribute('data-capture-target'); }""")
                    print(f"    [OK] {fname} (조상 섹션 자동탐색)")
                    return True
                except Exception as e:
                    print(f"    [WARN] 섹션 캡처 실패: {e}")
            page.screenshot(path=str(OUT_DIR / fname), full_page=True)
            print(f"    [FALLBACK] {fname} (full_page)")
            return False

        for kw, fname in [("프로그램 매매", f"B_프로그램_{code}.png"),
                          ("공매도", f"C_공매도_{code}.png")]:
            print(f"  --- [{kw}] ---")
            click_text(page, [f'button:has-text("{kw}")', f'[role="tab"]:has-text("{kw}")',
                             f'text={kw}'], wait=1800)
            page.wait_for_timeout(1200)
            shot_section(kw, fname)

        ctx.close()
        print(f"\n[완료] 결과 → {OUT_DIR}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--code", default="A017670")  # SK텔레콤
    a = ap.parse_args()
    run(a.headed, a.code)
