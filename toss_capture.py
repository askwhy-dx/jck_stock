"""
Toss Invest 일봉 차트 자동 캡처
- 매 실행마다 AI단테.md §6 (현재 종목 현황)의 활성 + 후보 종목을 자동 파싱
- 매칭 실패한 종목만 stocks.json 시드로 보강 (fallback)
- 한국 6자리 코드는 'A015750' → '015750' 순으로 토스 URL 자동 시도
- 차트 영역만 PNG로 캡처해 images/{종목명}_{YYYY-MM-DD}.png 저장
- 첫 실행 시 로그인 1회 → ./toss_session/ 에 쿠키 저장 → 이후 자동 로그인

사용:
    python toss_capture.py            # AI단테.md §6 기준 자동 캡처
    python toss_capture.py --login    # 로그인 전용 (브라우저 열고 사용자 직접 로그인 → 닫기)
    python toss_capture.py --headed   # 브라우저 창 보이는 모드
    python toss_capture.py --dry-run  # 캡처 안 하고 파싱된 종목 리스트만 출력
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import Page, TimeoutError as PWTimeout, sync_playwright

# Windows cmd 한글 출력 보장
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).parent              # .../test/stock
SESSION_DIR = ROOT / "toss_session"       # stock/toss_session
STOCKS_FILE = ROOT / "stocks.json"        # stock/stocks.json (fallback seed)
DANTE_MD = ROOT / "AI단테.md"             # 자동 파싱 소스
IMAGES_DIR = ROOT / "images"              # stock/images (대시보드 공유 폴더)
PRICES_DIR = ROOT / "data"                # stock/data/prices_YYYY-MM-DD.json

TOSS_URL = "https://www.tossinvest.com/stocks/{code}"
NAV_TIMEOUT_MS = 30_000
CHART_WAIT_MS = 10_000         # 차트 영역 DOM 진입 후 초기 대기 (lazy-load 여유)
CHART_RENDER_TIMEOUT_MS = 25_000  # 캔버스 픽셀이 실제로 그려질 때까지 폴링 최대 대기
NETWORKIDLE_TIMEOUT_MS = 12_000   # networkidle 대기 (모든 XHR 종료까지)

# 차트 영역 후보 selector (토스 DOM 변경 시 위에서부터 매칭되는 첫 요소 사용)
CHART_SELECTORS = [
    'canvas[class*="chart" i]',
    'div[class*="chart-container" i] canvas',
    'div[data-testid*="chart" i]',
    'div[class*="ChartContainer" i]',
    'section[class*="chart" i]',
    'div[class*="Chart" i]:not([class*="container" i])',  # 일반 Chart 컴포넌트
    'iframe[src*="chart" i]',                              # iframe 차트
    'iframe[src*="tradingview" i]',                        # TradingView iframe
    '.tv-lightweight-charts',                              # TradingView lightweight charts
    '[id*="chart" i]',                                     # id 기반 fallback
]

# 일봉 탭 후보 (필요 시 클릭)
DAILY_TAB_SELECTORS = [
    'button:has-text("일")',
    'button:has-text("1일")',
    '[role="tab"]:has-text("일")',
]

# 차트 크게보기 버튼 후보 (우상단 ↗ 아이콘)
EXPAND_CHART_SELECTORS = [
    'button:has-text("차트 크게보기")',
    'a:has-text("차트 크게보기")',
    'button:has-text("크게보기")',
    'span:has-text("차트 크게보기")',                     # span 텍스트
    'div:has-text("차트 크게보기")',                       # div 텍스트
    'text=차트 크게보기',                                  # Playwright text= 셀렉터
    '[aria-label*="크게보기"]',
    '[aria-label*="fullscreen" i]',
    '[aria-label*="expand" i]',
    'button[class*="expand" i]',
    'button[class*="fullscreen" i]',
]

# 풀스크린 모달 내 차트 영역 selector (위에서부터 매칭 → 좁은 영역 우선)
EXPANDED_CHART_SELECTORS = [
    '[role="dialog"] .tv-lightweight-charts',
    '[role="dialog"] canvas',
    '[role="dialog"] div[class*="chart" i]',
    '[role="dialog"] section',
    'div[class*="modal" i] canvas',
    'div[class*="Modal" i] div[class*="chart" i]',
    'div[class*="Modal" i] section',
    '.tv-lightweight-charts',
    # fallback: 모달 박스 자체 (큰 영역 포함)
    '[role="dialog"]',
    'div[class*="Modal" i]',
    'div[class*="modal" i]',
]

# 차트 기간 (당해년도 ≈ 1년 또는 YTD)
PERIOD_YEAR_SELECTORS = [
    '[role="dialog"] button:has-text("1년")',
    '[role="dialog"] button:has-text("YTD")',
    '[role="dialog"] button:has-text("년")',
    'button:has-text("1년")',
    'button:has-text("YTD")',
]


# ------------------------------------------------------------
# AI단테.md §6 파서
# ------------------------------------------------------------

def _clean_name(raw: str) -> str:
    """리스트 항목에서 종목명만 추출 (이모지·괄호·#번호·마크다운 제거)."""
    s = re.sub(r'\([^)]*\)', '', raw)        # (...) 제거
    s = re.sub(r'\*+', '', s)                # 마크다운 ** 제거
    s = re.sub(r'#\d+', '', s)               # #1, #2 후보 순위 제거
    s = re.sub(r'🆕|🔥|🚀|⭐|✅|🚫|⚠️', '', s)  # 자주 등장하는 이모지
    # 잔여 비-한글/영문/숫자/공백 제거 (이모지·기호 일괄)
    s = re.sub(r'[^\w가-힣\s]', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def parse_holdings_from_md() -> list[dict] | None:
    """AI단테.md §6 활성 종목 자동 추출 (자유 형식 §6 SSOT 지원).

    §6의 명시적인 **활성** 줄만 신뢰한다. 과거 이력의 코드는 수집하지 않는다.
    반환값 []는 정상적인 활성 0종, None은 섹션/활성 줄 파싱 실패다.
        """
    if not DANTE_MD.exists():
        return None
    text = DANTE_MD.read_text(encoding="utf-8")

    # 1. §6 현재 섹션만 분리 (`## 6. <space>` 으로 시작, §6.OLD/§6.9는 점 뒤 바로 글자라 제외)
    sec_match = re.search(r'(?ms)^## 6\.\s.*?(?=^## )', text)
    if not sec_match:
        return None
    section = sec_match.group(0)
    # HTML 주석(<!-- ... -->) 제거 — 종결 종목을 주석으로 기록하면 코드가 active로 오수확되는 버그 방지
    section = re.sub(r'<!--.*?-->', '', section, flags=re.S)

    # 2. §6 본문 안의 모든 `종목명(NNNNNN)` 추출 — §6 우선, 본문 매핑은 §6 외부 코드 보강용
    code_pattern = re.compile(r'([가-힣A-Z][가-힣A-Z0-9&·\s]{1,25}?)\((\d{6})\b')
    section_codes: dict[str, str] = {}  # §6 안에서만 발견된 코드 → 라인업
    name_to_code: dict[str, str] = {}   # 분류 매칭용 (§6 + 전체 문서)

    for m in code_pattern.finditer(section):
        name = m.group(1).strip()
        if not name or not re.search(r'[가-힣A-Z]', name):
            continue
        # 노이즈 토큰 제외
        if name in {"활성", "후보", "스윙", "중장기", "단타"}:
            continue
        if name not in section_codes:
            section_codes[name] = m.group(2)
        if name not in name_to_code:
            name_to_code[name] = m.group(2)

    # §6 밖 문서에서 추가 매핑 (분류 시 후보명과 매칭하기 위함)
    for m in code_pattern.finditer(text):
        name = m.group(1).strip()
        if not name or not re.search(r'[가-힣A-Z]', name):
            continue
        if name not in name_to_code:
            name_to_code[name] = m.group(2)

    def _clean_for_match(token: str) -> str:
        s = _clean_name(token)
        # 트레일링 이모지/공백/단어 제거
        s = re.sub(r'[🆕🔥⭐🎯⚡🚀🟢🟡🟠🔴🚫⏸💳🦷🔋🔐🏆🛑🔬]', '', s).strip()
        s = re.sub(r'\s+', ' ', s)
        return s

    # 3c. **활성** SSOT 라인 (§6 헤더 바로 아래 표준 라인) — 가장 견고한 패턴
    #     형식: `> - **활성**: 종목명 (NNNNNN) · 종목명 (NNNNNN) · ...`
    ssot_active_names: set[str] = set()
    ssot_codes: dict[str, str] = {}  # 코드 → 'active'
    ssot_names: dict[str, str] = {}  # 코드 → SSOT 라인에 적힌 종목명 (이름 결정 1순위)
    ssot_found = False

    # 후보 분류 폐지(2026-06-11) — 라인업은 매집(체결) 여부와 무관하게 전부 active (2026-10-13 사용자 지시)
    for label, tag in [('활성', 'active')]:
        for m in re.finditer(
            rf'>?\s*-\s*\*\*{label}\*\*[^\n:：]*[:：][ \t]*([^\n]*)',
            section, re.MULTILINE
        ):
            ssot_found = True
            for raw_part in re.split(r'[·•、,，/+]', m.group(1)):
                part = raw_part.strip()
                if not part:
                    continue
                # 코드 추출 (있으면)
                code_m = re.search(r'\((\d{6})\)', part)
                code = code_m.group(1) if code_m else None
                # 종목명 = 코드 부분 제거 후 정리
                nm_text = re.sub(r'\s*\(\d{6}\)\s*', '', part)
                nm = _clean_for_match(nm_text)
                if not nm or not re.search(r'[가-힣A-Z]{2,}', nm):
                    continue
                # 코드가 없으면 name_to_code 매핑에서 찾기
                if not code and nm in name_to_code:
                    code = name_to_code[nm]
                if code:
                    ssot_codes[code] = tag
                    # 최후 폴백용. SSOT 라인의 nm은 `·` 분할 때문에 "셀트리온미체결 1차 182" 처럼
                    # 뒤 설명이 붙을 수 있어 이름 1순위로 쓰지 않는다 (1순위는 code_to_name).
                    ssot_names.setdefault(code, nm)
                    if tag == 'active':
                        ssot_active_names.add(nm)

    # 4. 라인업 결정 — SSOT 라인 우선, 없으면 §6 안 코드 + 후보 라인 분류 fallback
    holdings: list[dict] = []
    seen_codes: set[str] = set()

    if ssot_found:
        # SSOT 라인이 있으면 그것만 라인업으로 (가장 견고한 모드)
        # ⚠️ 코드→이름 역매핑은 '먼저 등장한 이름'이 이긴다(setdefault).
        #    dict comprehension 역전(`{v: k for ...}`)은 같은 코드가 여러 이름으로 등장할 때
        #    '마지막 이름'이 덮어써서, 문서 뒤쪽 서술의 잡음 토큰이 종목명이 되는 버그가 있었다.
        #    (2026-08-03 실측: 068270이 '셀트리온' 대신 '제거'로 stocks.json에 기록 → 캡처 실패 예고)
        code_to_name: dict[str, str] = {}
        for _nm, _cd in name_to_code.items():
            code_to_name.setdefault(_cd, _nm)
        for code, tag in ssot_codes.items():
            if code in seen_codes:
                continue
            name = code_to_name.get(code) or ssot_names.get(code) or code
            holdings.append({"name": name, "code": code, "tag": tag})
            seen_codes.add(code)
    else:
        # 파싱 실패와 명시적인 활성 0종([])을 구분한다. 이력은 라인업이 아니다.
        return None

    return holdings


def sync_stocks_json(holdings: list[dict], *, allow_empty: bool = False) -> bool:
    """§6 파싱 결과로 stocks.json 자동 갱신. 기존 파일은 .bak 백업.

    안전장치:
    - 기본적으로 활성 0종 갱신 거부. SSOT 파싱이 성공한 빈 목록만 allow_empty=True로 허용
    - 활성 종목이 기존 목록의 절반 미만이면 갱신 보류 (확인된 빈 SSOT는 예외)
    """
    if not holdings and not allow_empty:
        return False
    active_count = sum(1 for h in holdings if h["tag"] == "active")
    if active_count == 0 and not allow_empty:
        print(f"[WARN] §6 활성 0종 — stocks.json 자동 갱신 보류 (의도치 않은 와이프 방지)")
        return False

    existing_active = 0
    if STOCKS_FILE.exists():
        try:
            existing = json.loads(STOCKS_FILE.read_text(encoding="utf-8"))
            existing_active = sum(1 for s in existing.get("stocks", []) if s.get("tag") == "active")
        except Exception:
            pass

    if existing_active > 0 and active_count < existing_active * 0.5 and not (allow_empty and not holdings):
        print(f"[WARN] §6 활성 {active_count}종이 기존 {existing_active}종의 절반 미만 — 자동 갱신 보류 (수동 검증 필요)")
        return False

    # 백업
    if STOCKS_FILE.exists():
        bak = STOCKS_FILE.with_suffix(".json.bak")
        try:
            bak.write_text(STOCKS_FILE.read_text(encoding="utf-8"), encoding="utf-8")
        except Exception:
            pass

    payload = {
        "_comment": f"AI단테.md §6 자동 동기화 (toss_capture.py 갱신 · 활성 {active_count}종). 사용자가 §6 갱신 시 다음 회차에 자동 반영.",
        "stocks": holdings,
    }
    STOCKS_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[OK] stocks.json 자동 동기화 (활성 {active_count}종)")
    return True


def load_stocks_seed() -> list[dict]:
    """stocks.json (fallback seed) 로드. 코드/이름이 있는 항목만."""
    if not STOCKS_FILE.exists():
        return []
    try:
        data = json.loads(STOCKS_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[WARN] stocks.json 파싱 실패: {e}")
        return []
    return [s for s in data.get("stocks", []) if s.get("name") and s.get("code")]


def load_bowl3_watch(existing: list[dict]) -> list[dict]:
    """🍚 밥그릇3 관찰 종목(data/bowl3_watch.json, build_bowl3_tab.js 산출)을 캡처 대상에 더한다 (2026-10-19).
    stocks.json·§6 에는 넣지 않는다(sync_stocks_json 이후에 붙임). 라인업과 겹치면 한 번만."""
    p = ROOT / "data" / "bowl3_watch.json"
    if not p.exists():
        return []
    try:
        w = json.loads(p.read_text(encoding="utf-8")).get("stocks", [])
    except Exception as e:
        print(f"[WARN] bowl3_watch.json 파싱 실패: {e}")
        return []
    have = {s["code"] for s in existing}
    out = [{"name": s["name"], "code": s["code"], "tag": "bowl3"} for s in w if s.get("code") and s["code"] not in have]
    for s in out:
        print(f"       · bowl3     {s['name']} ({s['code']})")
    return out


def resolve_stocks() -> list[dict]:
    """AI단테.md §6 SSOT 자동 파싱 + stocks.json 자동 동기화.

    §6 파싱 성공 시: §6 결과를 라인업으로 사용 + stocks.json 자동 갱신 (안전장치 통과 시).
    §6 파싱 실패 시: 기존 stocks.json fallback.
    """
    md_holdings = parse_holdings_from_md()

    if md_holdings is not None:
        active = [h for h in md_holdings if h["tag"] == "active"]
        print(f"[INFO] AI단테.md §6에서 {len(md_holdings)}종 자동 추출 (활성 {len(active)})")
        for h in md_holdings:
            print(f"       · {h['tag']:9} {h['name']} ({h['code']})")
        # SSOT 파싱 성공으로 확인한 0종만 빈 목록 저장을 허용한다.
        sync_stocks_json(md_holdings, allow_empty=not md_holdings)
        return md_holdings + load_bowl3_watch(md_holdings)

    seed = load_stocks_seed()
    if seed:
        print(f"[WARN] AI단테.md §6 파싱 실패 → stocks.json 시드 {len(seed)}종 사용 (수동 갱신 권장)")
        return seed

    sys.exit("[ERROR] AI단테.md §6 파싱 실패 & stocks.json 시드도 비어 있음")


# ------------------------------------------------------------
# Toss 캡처
# ------------------------------------------------------------

def _candidate_codes(code: str) -> list[str]:
    """토스 focusedProductCode 후보 형식 (한국 6자리는 A-prefix 우선, bare 폴백)."""
    if re.fullmatch(r'\d{6}', code):
        return [f"A{code}", code]
    return [code]


def click_daily_tab(page: Page) -> None:
    for sel in DAILY_TAB_SELECTORS:
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible():
                loc.click(timeout=2000)
                page.wait_for_timeout(800)
                return
        except Exception:
            continue


def find_chart_locator(page: Page):
    for sel in CHART_SELECTORS:
        loc = page.locator(sel).first
        try:
            if loc.count() and loc.is_visible():
                return loc
        except Exception:
            continue
    return None


def click_expand_chart(page: Page) -> bool:
    """'차트 크게보기' 버튼 클릭. 성공 시 True."""
    for sel in EXPAND_CHART_SELECTORS:
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible():
                loc.click(timeout=2000)
                page.wait_for_timeout(2200)  # 모달 애니메이션 + 차트 재렌더
                print(f"    [INFO] '차트 크게보기' 클릭 성공: {sel}")
                return True
        except Exception:
            continue
    return False


def click_period_1y(page: Page) -> bool:
    """차트 모달(또는 일반) 안 '1년' / 'YTD' 기간 옵션 클릭 → 당해년도 줌."""
    for sel in PERIOD_YEAR_SELECTORS:
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible():
                loc.click(timeout=1500)
                page.wait_for_timeout(1200)
                print(f"    [INFO] 기간 1년/YTD 클릭: {sel}")
                return True
        except Exception:
            continue
    return False


def trigger_chart_lazy_load(page: Page) -> None:
    """차트 lazy-load 트리거: 차트 영역 스크롤 + 호버 + 클릭.

    토스 종목 페이지는 차트 영역이 viewport에 들어오거나 사용자 인터랙션이 있을 때
    렌더링되는 lazy-load 패턴 사용 가능성. 명시적 트리거로 강제 활성화.
    """
    try:
        # 1) 차트가 들어가는 위치 (page.scrollTo 트릭) — 페이지 상단·중단·다시 상단
        page.evaluate("window.scrollTo({top: 200, behavior: 'instant'})")
        page.wait_for_timeout(300)
        page.evaluate("window.scrollTo({top: 0, behavior: 'instant'})")
        page.wait_for_timeout(300)

        # 2) 차트 영역 후보 위 호버 (lazy 활성화 신호)
        for sel in CHART_SELECTORS:
            try:
                loc = page.locator(sel).first
                if loc.count():
                    loc.hover(timeout=1500, force=True)
                    page.wait_for_timeout(400)
                    break
            except Exception:
                continue

        # 3) "차트" 텍스트 가진 버튼/영역 클릭 시도 (탭 활성화 등)
        for sel in ['button:has-text("차트")', '[role="tab"]:has-text("차트")', 'div:has-text("차트·호가")']:
            try:
                loc = page.locator(sel).first
                if loc.count() and loc.is_visible():
                    loc.click(timeout=1500)
                    page.wait_for_timeout(500)
                    break
            except Exception:
                continue
    except Exception as e:
        print(f"    [WARN] lazy-load 트리거 실패: {e}")


def extract_price_info(page: Page) -> dict:
    """종목 페이지 좌측 상단 가격/변동 텍스트 DOM 추출.

    토스 종목 페이지 헤더는 다음 형식:
        [종목명] [코드] [전일/현재] 1번 페이지 진입
        [가격]원
        [기준일자]보다 [±변동원] (±변동률)
    가격은 페이지 첫 번째 '\\d+,?\\d*원' 패턴, 변동률은 '(\\d+.\\d+%)' 패턴으로 매칭.
    """
    info = {
        "price": None,
        "change_won": None,
        "change_pct": None,
        "foreign_flow_direction": None,
        "foreign_flow_label": None,
        "execution_strength_pct": None,
    }
    try:
        # 가격: 페이지 첫 N개 요소에서 'NNN,NNN원' 형식 매칭
        result = page.evaluate(
            """() => {
                const text = document.body.innerText || '';
                // 첫 번째 가격 (헤더 영역)
                const priceMatch = text.match(/(\\d{1,3}(?:,\\d{3})*)\\s*원/);
                // 변동 (예: '-2,000원 (1.69%)' 또는 '+8,400원 (6.59%)')
                const changeMatch = text.match(/([+-]?\\d{1,3}(?:,\\d{3})*)\\s*원\\s*\\(([+-]?\\d+(?:\\.\\d+)?)%\\)/);
                const foreignMatch = text.match(/외국인\\s*순(매수|매도)(?:\\s*(\\d+\\s*위|100위\\s*밖))?/);
                const strengthMatch = text.match(/체결강도\\s*([\\d.]+)%/);
                return {
                    price: priceMatch ? priceMatch[1] : null,
                    change_won: changeMatch ? changeMatch[1] : null,
                    change_pct: changeMatch ? changeMatch[2] : null,
                    foreign_flow_direction: foreignMatch ? (foreignMatch[1] === '매수' ? 'buy' : 'sell') : null,
                    foreign_flow_label: foreignMatch ? foreignMatch[0].replace(/\\s+/g, ' ').trim() : null,
                    execution_strength_pct: strengthMatch ? Number(strengthMatch[1]) : null,
                };
            }"""
        )
        info.update(result or {})
        # 토스는 등락률(%)을 부호 없이 색상으로만 표시 → change_pct는 크기만 잡힘.
        # 부호가 살아있는 change_won 기준으로 change_pct 부호를 보정한다.
        # (미보정 시 다운스트림 parseFloat(change_pct)>=0 → 음봉도 '양봉'으로 오판)
        cw, cp = info.get("change_won"), info.get("change_pct")
        if cw and cp and not str(cp).startswith(("+", "-")):
            if str(cw).startswith("-"):
                info["change_pct"] = "-" + str(cp)
    except Exception as e:
        print(f"    [WARN] 가격 DOM 추출 실패: {e}")
    return info


def wait_for_canvas_painted(page: Page, timeout_ms: int = CHART_RENDER_TIMEOUT_MS) -> bool:
    """차트 캔버스의 비투명 픽셀이 그려질 때까지 폴링 대기.

    토스 차트는 lazy-render되므로 단순 sleep만으로는 부족.
    canvas getImageData로 (10,10) 픽셀의 alpha 채널이 0이 아닌지 검사.
    """
    try:
        page.wait_for_function(
            """() => {
                const canvases = Array.from(document.querySelectorAll('canvas'));
                for (const c of canvases) {
                    if (c.width < 100 || c.height < 100) continue;
                    try {
                        const ctx = c.getContext('2d');
                        if (!ctx) continue;
                        // 중앙 픽셀이 그려졌는지 확인
                        const cx = Math.floor(c.width / 2);
                        const cy = Math.floor(c.height / 2);
                        const data = ctx.getImageData(cx, cy, 1, 1).data;
                        if (data[3] > 0) return true;  // alpha > 0
                    } catch (e) { /* CORS/tainted canvas는 스킵 */ }
                }
                return false;
            }""",
            timeout=timeout_ms,
        )
        return True
    except PWTimeout:
        return False
    except Exception:
        return False


def find_expanded_chart_locator(page: Page):
    for sel in EXPANDED_CHART_SELECTORS:
        loc = page.locator(sel).first
        try:
            if loc.count() and loc.is_visible():
                return loc
        except Exception:
            continue
    return None


def capture_one(page: Page, name: str, code: str, today: str) -> tuple[Path | None, dict]:
    out = IMAGES_DIR / f"{name}_{today}.png"
    last_url = None
    price_info: dict = {"price": None, "change_won": None, "change_pct": None}
    for candidate in _candidate_codes(code):
        url = TOSS_URL.format(code=candidate)
        last_url = url
        print(f"  → {url}")
        try:
            page.goto(url, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
        except PWTimeout:
            print("    [WARN] 네비게이션 타임아웃, 그대로 진행")

        # networkidle 대기 (모든 XHR 종료) — 차트 데이터 fetch 완료 신호
        try:
            page.wait_for_load_state("networkidle", timeout=NETWORKIDLE_TIMEOUT_MS)
            print(f"    [INFO] networkidle 도달")
        except PWTimeout:
            print(f"    [WARN] networkidle 타임아웃 (12s) — 진행")

        page.wait_for_timeout(CHART_WAIT_MS)
        click_daily_tab(page)

        # 가격/변동 DOM 추출 (차트와 무관하게 안정적)
        price_info = extract_price_info(page)
        if price_info.get("price"):
            chg = price_info.get("change_pct")
            print(f"    [PRICE] {price_info['price']}원 ({chg or 'n/a'}%)")

        # 차트 lazy-load 명시적 트리거 (스크롤·호버·탭 클릭)
        trigger_chart_lazy_load(page)
        page.wait_for_timeout(2000)  # 트리거 후 렌더 안정화

        # 캔버스 픽셀이 그려질 때까지 폴링 대기 (최대 25초)
        if wait_for_canvas_painted(page):
            print(f"    [INFO] 차트 캔버스 픽셀 렌더 확인")
        else:
            print(f"    [WARN] 차트 캔버스 렌더 미감지 (25s 타임아웃) — 진행")

        # 1순위: 차트 크게보기 버튼 → 풀스크린 모달 차트
        if click_expand_chart(page):
            # 당해년도 줌 (1년 / YTD 클릭)
            click_period_1y(page)
            page.wait_for_timeout(1500)  # 줌 적용 후 차트 재렌더 대기
            expanded = find_expanded_chart_locator(page)
            if expanded:
                try:
                    expanded.screenshot(path=str(out))
                    print(f"    [OK] 풀스크린 차트 저장 → {out.name} (code={candidate})")
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(500)
                    return out, price_info
                except Exception as e:
                    print(f"    [WARN] 풀스크린 캡처 실패: {e} → 모달 덮인 페이지 캡처")
            # 모달 selector 매칭 실패해도 모달이 활성화된 상태에서 페이지 전체 캡처
            try:
                page.screenshot(path=str(out), full_page=False)
                print(f"    [OK] 모달 덮인 페이지 캡처 → {out.name} (code={candidate})")
                page.keyboard.press("Escape")
                page.wait_for_timeout(500)
                return out, price_info
            except Exception as e:
                print(f"    [WARN] 모달 페이지 캡처 실패: {e} → 일반 차트 폴백")
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)
        else:
            print(f"    [INFO] 차트 크게보기 버튼 미발견 → 일반 차트 시도")

        # 2순위 (폴백): 일반 차트 영역
        chart = find_chart_locator(page)
        if chart:
            try:
                chart.screenshot(path=str(out))
                print(f"    [OK] 일반 차트 영역 저장 → {out.name} (code={candidate})")
                return out, price_info
            except Exception as e:
                print(f"    [ERROR] 차트 캡처 실패: {e} (code={candidate})")
                continue
        else:
            print(f"    [MISS] 차트 selector 매칭 실패 (code={candidate})")

    # 모든 형식 실패 → 뷰포트 전체로 저장 (디버깅용)
    try:
        page.screenshot(path=str(out), full_page=False)
        print(f"    [WARN] 차트 미감지 → 뷰포트 전체 저장 → {out.name} (last={last_url})")
        return out, price_info
    except Exception as e:
        print(f"    [ERROR] 뷰포트 캡처도 실패: {e}")
        return None, price_info


# ------------------------------------------------------------
# 투자자공매 — 투자자별 매매동향 / 프로그램매매 / 공매도 (거래현황 탭)
# ------------------------------------------------------------

TRADE_TAB_SELECTORS = [
    'a:has-text("거래현황")', 'button:has-text("거래현황")',
    '[role="tab"]:has-text("거래현황")', 'text=거래현황',
]


def _click_first(page: Page, selectors: list[str], wait: int = 1500) -> bool:
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible():
                loc.click(timeout=2000)
                page.wait_for_timeout(wait)
                return True
        except Exception:
            continue
    return False


def _shot_growing(page: Page, start_text: str, out_path: Path) -> bool:
    """버튼/헤딩 텍스트에서 위로 올라가며 테이블 포함 조상(height>350)을 찾아 캡처."""
    tagged = page.evaluate(
        """(kw) => {
            const els = Array.from(document.querySelectorAll('button,[role="tab"],a,h1,h2,h3'))
                .filter(e => (e.innerText||'').trim().startsWith(kw));
            if (!els.length) return false;
            let el = els[0];
            for (let i=0;i<8 && el;i++){
                const r = el.getBoundingClientRect();
                if (r.height>350 && r.width>500){ el.setAttribute('data-cap','1'); return true; }
                el = el.parentElement;
            }
            return false;
        }""", start_text)
    if tagged:
        try:
            loc = page.locator('[data-cap="1"]').first
            loc.scroll_into_view_if_needed(timeout=2000)
            loc.screenshot(path=str(out_path))
            page.evaluate("""() => { const e=document.querySelector('[data-cap=\\"1\\"]'); if(e) e.removeAttribute('data-cap'); }""")
            return True
        except Exception:
            pass
    return False


def capture_tujaja_gongmae(page: Page, name: str, code: str, today: str) -> dict:
    """거래현황 탭 → 투자자별 매매동향 / 프로그램매매 / 공매도 3종 캡처.
    실패해도 차트 파이프라인에 영향 없도록 호출부에서 try/except (non-fatal)."""
    saved: dict[str, Path] = {}
    if not _click_first(page, TRADE_TAB_SELECTORS, wait=2500):
        print(f"    [투자자공매][MISS] 거래현황 탭 미발견 ({name})")
        return saved
    page.wait_for_timeout(1500)

    # A. 투자자별 매매동향 (모달)
    if _click_first(page, ['button:has-text("투자자별 순매수 보기")',
                           'a:has-text("투자자별 순매수")', 'text=투자자별 순매수 보기'], wait=2000):
        out = IMAGES_DIR / f"{name}_투자자공매_투자자_{today}.png"
        if not _shot_growing(page, "투자자별 순매수", out):
            try:
                dlg = page.locator('[role="dialog"]').first
                if dlg.count():
                    dlg.screenshot(path=str(out))
            except Exception:
                pass
        if out.exists():
            saved["투자자"] = out
            print(f"    [투자자공매][OK] {out.name}")
        page.keyboard.press("Escape")
        page.wait_for_timeout(1000)

    # B/C. 프로그램매매 / 공매도 (투자 유형별 현황 서브탭)
    for kw, key in [("프로그램 매매", "프로그램"), ("공매도", "공매도")]:
        _click_first(page, [f'button:has-text("{kw}")', f'[role="tab"]:has-text("{kw}")', f'text={kw}'], wait=1800)
        page.wait_for_timeout(1000)
        out = IMAGES_DIR / f"{name}_투자자공매_{key}_{today}.png"
        if _shot_growing(page, kw, out) and out.exists():
            saved[key] = out
            print(f"    [투자자공매][OK] {out.name}")
        else:
            print(f"    [투자자공매][MISS] {kw} ({name})")
    return saved


def run(headed: bool, login_only: bool, dry_run: bool, only: str | None = None,
        no_flow: bool = False, flow_only: bool = False) -> None:
    IMAGES_DIR.mkdir(exist_ok=True)
    SESSION_DIR.mkdir(exist_ok=True)

    today = dt.date.today().isoformat()
    stocks = resolve_stocks()
    if only:
        stocks = [s for s in stocks if only in s["name"]]
        if not stocks:
            sys.exit(f"[ERROR] --only '{only}' 매칭 종목 없음")
        print(f"[INFO] --only 필터: {len(stocks)}종 ({', '.join(s['name'] for s in stocks)})")

    if dry_run:
        print(f"\n[DRY-RUN] {len(stocks)}종 — 실제 캡처는 생략")
        return

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(SESSION_DIR),
            headless=not (headed or login_only),
            viewport={"width": 1920, "height": 1080},
            locale="ko-KR",
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        if login_only:
            page.goto("https://www.tossinvest.com/", timeout=NAV_TIMEOUT_MS)
            print("\n[로그인 모드] 브라우저에서 직접 로그인 후 이 콘솔에서 Enter ...")
            try:
                input()
            except EOFError:
                time.sleep(60)
            ctx.close()
            print("세션 저장 완료. 다음부터 자동 로그인됩니다.")
            return

        print(f"\n[{today}] {len(stocks)}종목 캡처 시작\n")

        ok, fail, flow_ok = 0, 0, 0
        prices: dict[str, dict] = {}
        for s in stocks:
            tag = f"[{s.get('tag', '?')}]"
            print(f"- {tag} {s['name']} ({s['code']})")

            if not flow_only:
                res, info = capture_one(page, s["name"], s["code"], today)
                prices[s["name"]] = {
                    "code": s["code"],
                    "tag": s.get("tag"),
                    "price": info.get("price"),
                    "change_won": info.get("change_won"),
                    "change_pct": info.get("change_pct"),
                    "foreign_flow_direction": info.get("foreign_flow_direction"),
                    "foreign_flow_label": info.get("foreign_flow_label"),
                    "execution_strength_pct": info.get("execution_strength_pct"),
                }
                if res:
                    ok += 1
                else:
                    fail += 1
            else:
                # flow-only: 차트 캡처 생략, 종목 페이지만 진입
                try:
                    page.goto(TOSS_URL.format(code=_candidate_codes(s["code"])[0]),
                              timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
                    page.wait_for_timeout(3000)
                except Exception as e:
                    print(f"    [WARN] flow-only 네비 실패: {e}")

            # 투자자공매 캡처 (수급 차원) — non-fatal
            if not no_flow:
                try:
                    fl = capture_tujaja_gongmae(page, s["name"], s["code"], today)
                    if fl:
                        flow_ok += 1
                except Exception as e:
                    print(f"    [투자자공매][ERR] {e} ({s['name']})")

        ctx.close()

        # 가격 정보 JSON 저장 (flow-only 모드에서는 기존 차트/가격 데이터 보존을 위해 생략)
        if not flow_only:
            PRICES_DIR.mkdir(exist_ok=True)
            prices_file = PRICES_DIR / f"prices_{today}.json"
            prices_file.write_text(
                json.dumps({"date": today, "stocks": prices}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"\n완료: 차트 성공 {ok}, 실패 {fail} / 투자자공매 {flow_ok}종")
            print(f"가격 JSON: {prices_file}")
        else:
            print(f"\n완료: 투자자공매 {flow_ok}종 (flow-only)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--login", action="store_true", help="로그인 전용 모드")
    ap.add_argument("--headed", action="store_true", help="브라우저 창 보이게")
    ap.add_argument("--dry-run", action="store_true", help="파싱 결과만 출력")
    ap.add_argument("--only", default=None, help="특정 종목명 1종만 캡처 (디버그용)")
    ap.add_argument("--no-flow", action="store_true", help="투자자공매(수급) 캡처 생략")
    ap.add_argument("--flow-only", action="store_true", help="차트 생략, 투자자공매만 캡처 (가격 JSON 미갱신)")
    args = ap.parse_args()
    run(headed=args.headed, login_only=args.login, dry_run=args.dry_run, only=args.only,
        no_flow=args.no_flow, flow_only=args.flow_only)


if __name__ == "__main__":
    main()
