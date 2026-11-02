"""
collect_theme_flow.py — 08:15 회차 2구간 후반 눌림 스캐너 (sop_테마발굴_보조지표.md 정합)

산출:
  - stock/data/theme_flow_{YYYY-MM-DD}.json  : 224 돌파 후 첫 눌림 종목 상위 N개
  - stock/data/krx_ohlcv_cache/{YYYY-MM-DD}_{MARKET}.json : 일자별 전 종목 OHLCV 캐시

변경 이력:
  2026-05-20 KRX OpenAPI 전환 + 밥그릇 2→3 전환(신규 돌파) 스캐너
  2026-05-22 2구간 후반 눌림 스캐너로 전환
    - 시장 신호 부재 시 0종 빈번(2026-05-19~22 연속) → 돌파 직후 첫 눌림 포착으로 방향 전환
    - 단테 §4.4 "1차a 안착가 진입" 정합. 진입가·손절선 명확
    - 파일명·인터페이스 호환 유지 (theme_flow_{date}.json)

데이터 소스: KRX OpenAPI (data-dbg.krx.co.kr/svc/apis)
인증: .env 의 KRX_AUTH_KEY (krx_openapi.py 자동 로드)

사용:
  python collect_theme_flow.py                  # 직전 영업일 기준 스캔
  python collect_theme_flow.py --date=2026-05-22
  python collect_theme_flow.py --dry-run        # 저장 X
  python collect_theme_flow.py --backfill=240   # 초기 240일 캐시 백필
  python collect_theme_flow.py --healthcheck    # KRX API 연결 확인만
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

try:
    from krx_openapi import KRXClient, KRXAPIError
except ImportError as e:
    print(f"[ERROR] krx_openapi 모듈 로드 실패: {e}")
    sys.exit(1)

# DART 공시 필터 (선택 — API 키 없으면 자동 스킵)
try:
    from dart_openapi import DARTClient, DARTAPIError
except ImportError:
    DARTClient = None
    DARTAPIError = Exception

try:
    import pandas as pd
    import numpy as np
except ImportError:
    print("[ERROR] pandas/numpy 미설치. pip install pandas numpy requests 를 먼저 실행하세요.")
    sys.exit(1)


ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "krx_ohlcv_cache"
RECOMMEND_POOL_PATH = DATA_DIR / "recommend_pool.json"  # 영속 추천 풀 (통과/이탈 누적 추적)
DATA_DIR.mkdir(exist_ok=True)
CACHE_DIR.mkdir(exist_ok=True)

KST = dt.timezone(dt.timedelta(hours=9))

# 단테 §2.1 + §2.5.2 + §4.4 정합 임계값
THRESHOLDS = {
    "min_market_cap_billion": 1000,
    "min_history_days": 230,                # 224 이평 + 돌파 윈도우 여유
    "breakout_window_min_days": 5,          # 돌파 후 너무 가까운 자리는 1차 신호로 분류
    "breakout_window_max_days": 60,         # 돌파 후 너무 멀어진 자리는 추세 끝물
    "above_ma224_max_pct": 0.15,            # 224 위 +15% 초과 = 3구간 중반
    "pullback_min_pct": -0.15,              # 직전 20일 고점 대비 하한
    "pullback_max_pct": -0.02,              # 직전 20일 고점 대비 상한
    "support_window_pct": 0.05,             # 224 ±5% OR 112 ±5%
    # 🆕 승률 개선 필터 ①+②+④ (2026-06-11 백테스트 적용 · 단테 분할 기대값 -0.51%→+0.67%)
    "vol_shrink_max": 0.7,                  # ② 직전5/20 평균 ≤ 0.7 (강한 매도 약화만, 1.0→0.7)
    "require_yang": True,                   # ④ 통과일 양봉 필수 (음봉 눌림 제외)
    "min_pass_score": 80,                   # ① 통과 점수 ≥ 80 (돌파 탭과 통일, 90→80 · 2026-06-23)
    "max_candidates": 10,
    # 🆕 눌림 구간 쌍바닥(짝궁뎅이) 검출 — 단일 V자 눌림 대비 지지선 2회 테스트 W형 우대
    #   (가점+배지 방식: 단일 V도 기존처럼 통과, 쌍바닥이면 점수 가산+별점↑+🪑배지 · §2.2 공구리2)
    "db_window": 30,                        # 쌍바닥 탐색 윈도우 (최근 N영업일)
    "db_swing_k": 2,                        # swing low 국소최저 반경 (±k봉 중 최저)
    "db_min_gap": 4,                        # 두 바닥 간 최소 간격 (영업일, 노이즈 제거)
    "db_level_tol": 0.025,                  # 두 바닥 수평성 |T2/T1-1| ≤ 2.5%
    "db_bounce_min": 0.05,                  # 사이 반등(넥라인) ≥ 바닥 대비 +5% (뚜렷한 W 봉우리)
    "db_t2_recent": 6,                      # 두번째 바닥(T2)이 최근 N봉 내 (오늘이 T2 직후 반등)
    # 백테스트(2025-01-15~, backtest_pullback_improve.py) 결과: 모양만 쌍바닥은 우위 없음
    # (기대값 +0.15% < baseline +1.03%). "2차 바닥 거래량 감소(매도 소진)" 동반 쌍바닥만
    # 우위(+1.64%) → 가점은 거래량 다이버전스(db_vol_div)에만, 배지는 전 쌍바닥 표시.
    # 가점은 통과(게이트) 확정 '후' 적용 = 노출 정렬 우선순위만 ↑ (통과 구제 X → 희석 방지).
    "db_bonus": 5,                          # 거래량 다이버전스 쌍바닥 표시·정렬 가점 (모양만은 0)
    # 🆕 2단계 반등확인 진입 (2026-06-16 backtest_pullback_strategy.py 검증)
    #   1단계(눌림 셋업) 통과 후 즉시 추천 X → '반등확인 대기'. 이후 confirm_window일 내
    #   ②정배열(MA20>MA60>MA112) + ③전일(셋업일) 고가 돌파 충족 시 '확인'으로 승격.
    #   백테스트: 승률 21.6%→37.5%(②+③ 38.5%) · 기대값 +1.03%→+4.47%(+4.95%).
    "require_confirm": True,                 # 2단계 반등확인 ON (False=구 1단계 즉시추천)
    "confirm_window": 3,                     # 셋업 후 반등확인 허용 영업일 (이후 대기 만료)
    "require_align_on_confirm": True,        # 확인 시 정배열(MA20>MA60>MA112) 필수
    # 🆕 과열 회피 레짐 — 경고만(배지·배너), 추천 차단/감점 X (2026-06-16 약세장 재검증)
    #   재검증(analyze_regime.py): "강세장 진입"(원안 ≥50%)은 무효(20.4%)·≥60%는 유해(14.3%).
    #   반대로 시장폭 ≥70% '과열'에서 눌림 진입이 실패(5.9% 승률 · -4.64%) → 6월 광범위 조정에
    #   함께 무너짐. 스윗스팟 45~65%(승률 39%). 표본이 단일 조정(2026-06)에 의존 → 경고 표시만.
    "regime_overheat_breadth": 0.65,         # 시총1000억+ 종목 중 112선 위 비율 ≥ 이 값이면 ⚠️과열 경고
    # 노출 컷 + 생애주기 (2026-06-11 돌파 탭과 공통 규정 · 사용자 확정)
    "display_score_cut": 80,                # 눌림 탭·Slack 노출 점수 컷 (돌파 탭과 통일, 90→80 · 2026-06-23)
    "graduate_gain_pct": 20.0,             # 졸업: 포착일(first_seen) 종가 대비 +20%
    "watch_after_days": 5,                 # 5영업일 만료 → 지속관찰(탭 내림·추적 유지)
    # DART 공시 필터 (2026-05-22 v2 단계적 평가)
    "disclosure_lookback_days": 180,        # 사업보고서 검색 위해 6개월로 확장
    "bonus_annual_op_up": 10,               # 사업보고서 영업이익 YoY 증가
    "bonus_quarterly_op_up": 10,            # 분기/반기 영업이익 YoY 증가
    "bonus_positive_each": 5,               # 호재 공시 종류당 (최대 +15 캡)
    "bonus_positive_max": 15,
    "insider_lookback_days": 90,            # 임원 장내매수 조회 기간 (호재 1종류 +5)
}


def log(msg: str) -> None:
    ts = dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def prev_business_day(d: dt.date, n: int = 1) -> dt.date:
    while n > 0:
        d -= dt.timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def business_days_back(d: dt.date, n: int) -> list[dt.date]:
    """d 부터 과거 n 영업일 (d 포함). 주말만 스킵 — 공휴일은 KRX 빈 응답으로 자동 제외."""
    days = []
    cur = d
    while len(days) < n:
        if cur.weekday() < 5:
            days.append(cur)
        cur -= dt.timedelta(days=1)
    return days


# =============================================================================
# OHLCV 캐시 빌드 (KRX OpenAPI)
# =============================================================================

def ensure_ohlcv_cache(client: KRXClient, target_dates: list[dt.date]) -> dict[str, pd.DataFrame]:
    """필요 영업일 캐시를 보장하고 종목별 OHLCV DataFrame을 구성.

    반환: { ticker: DataFrame(date, open, high, low, close, volume, trading_value) }
    """
    rows_by_ticker: dict[str, list[dict]] = {}
    meta_by_ticker: dict[str, dict] = {}

    for d in sorted(target_dates):
        bas_dd = d.strftime("%Y%m%d")
        for market in ("KOSPI", "KOSDAQ"):
            try:
                rows = client.daily_ohlcv_raw(bas_dd, market)
            except KRXAPIError as e:
                log(f"  [WARN] {bas_dd} {market} 호출 실패: {e}")
                continue
            if not rows:
                continue
            for r in rows:
                code = r.get("code")
                if not code:
                    continue
                rows_by_ticker.setdefault(code, []).append({
                    "date": d.isoformat(),
                    "open": r.get("open"),
                    "high": r.get("high"),
                    "low": r.get("low"),
                    "close": r.get("close"),
                    "volume": r.get("volume"),
                    "trading_value": r.get("trading_value"),
                })
                if code not in meta_by_ticker:
                    meta_by_ticker[code] = {
                        "name": r.get("name"),
                        "market": market,
                        "sector": r.get("sector"),
                        "market_cap": r.get("market_cap"),
                        "shares": r.get("shares"),
                    }
                else:
                    meta_by_ticker[code]["market_cap"] = r.get("market_cap") or meta_by_ticker[code].get("market_cap")

    out: dict[str, pd.DataFrame] = {}
    for code, rows in rows_by_ticker.items():
        df = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
        for col in ("open", "high", "low", "close", "volume", "trading_value"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df.attrs["meta"] = meta_by_ticker.get(code, {})
        out[code] = df

    log(f"OHLCV 캐시 빌드 완료: {len(out)}종 × 최대 {len(target_dates)}영업일")
    return out


# =============================================================================
# 돌파 이력 탐색 + 눌림 신호 평가
# =============================================================================

def find_breakout_day(closes: pd.Series, max_lookback: int) -> int | None:
    """직전 max_lookback영업일 내 224일선 신규 돌파(어제 ≤ ma224, 오늘 > ma224) 발생 시점.

    반환: days_ago (1 = 어제, None = 없음).
    돌파일 기준 ma224는 그 시점에서 직전 224일 평균.
    """
    n = len(closes)
    if n < 225:
        return None
    for k in range(1, max_lookback + 1):
        if n < k + 225:
            break
        c_at = float(closes.iloc[n - k - 1])
        c_prev = float(closes.iloc[n - k - 2])
        ma224_at = float(closes.iloc[n - k - 1 - 224 + 1:n - k].mean())
        ma224_prev = float(closes.iloc[n - k - 2 - 224 + 1:n - k - 1].mean())
        if (c_prev <= ma224_prev) and (c_at > ma224_at):
            return k
    return None


def detect_double_bottom(df: pd.DataFrame) -> dict:
    """눌림 구간 내 쌍바닥(짝궁뎅이·W형) 검출 — 차트 없이 OHLCV만으로 결정적 판정.

    최근 db_window 영업일에서 swing low(국소최저) 2개를 찾아 ① 두 바닥 수평성
    ② 사이 반등(넥라인) ③ 두번째 바닥 최근성을 확인한다. 가장 최근의 유효한
    (T1,T2) 쌍을 채택. 단테 §2.2 공구리2(쌍바닥) 정합.

    반환: {"double_bottom": bool, (참일 때) db_gap/db_level_diff_pct/db_bounce_pct/
           db_vol_div/db_t1_low/db_t2_low/db_neckline}
    """
    window = THRESHOLDS["db_window"]
    k = THRESHOLDS["db_swing_k"]
    if len(df) < window + 1:
        return {"double_bottom": False}

    seg = df.iloc[-window:]
    lows = seg["low"].astype(float).to_numpy()
    highs = seg["high"].astype(float).to_numpy()
    vols = seg["volume"].astype(float).to_numpy()
    n = len(lows)

    # swing low = ±k봉 구간의 최저 (노이즈 제거)
    swings = [i for i in range(k, n - k) if lows[i] == lows[i - k:i + k + 1].min()]
    if len(swings) < 2:
        return {"double_bottom": False}

    # 가장 최근 T2부터 역방향으로, 조건을 만족하는 첫 (T1,T2) 쌍 채택
    for bi in range(len(swings) - 1, 0, -1):
        t2 = swings[bi]
        if (n - 1) - t2 > THRESHOLDS["db_t2_recent"]:
            continue  # 두번째 바닥이 너무 오래됨 (이미 반등 마감)
        for ai in range(bi - 1, -1, -1):
            t1 = swings[ai]
            if t2 - t1 < THRESHOLDS["db_min_gap"]:
                continue
            lo1, lo2 = float(lows[t1]), float(lows[t2])
            if lo1 <= 0:
                continue
            level_diff = abs(lo2 / lo1 - 1)
            if level_diff > THRESHOLDS["db_level_tol"]:
                continue
            peak = float(highs[t1:t2 + 1].max())
            base = max(lo1, lo2)
            bounce = peak / base - 1 if base else 0
            if bounce < THRESHOLDS["db_bounce_min"]:
                continue
            return {
                "double_bottom": True,
                "db_gap": int(t2 - t1),
                "db_level_diff_pct": round(level_diff * 100, 2),
                "db_bounce_pct": round(bounce * 100, 2),
                "db_vol_div": bool(vols[t2] < vols[t1]),  # 2번째 바닥 거래량 감소(매도 소진)
                "db_t1_low": int(lo1),
                "db_t2_low": int(lo2),
                "db_neckline": int(peak),
            }
    return {"double_bottom": False}


def compute_market_breadth(ticker_data: dict[str, pd.DataFrame]) -> dict:
    """스캔 시점 시장 브레드스 = (시총1000억+ 종목 중 종가>112선) / (유효). 추가 API 없음.

    반환: {"pct": float|None, "above": int, "total": int, "overheated": bool}
    """
    above = total = 0
    cap_min = THRESHOLDS["min_market_cap_billion"]
    for df in ticker_data.values():
        meta = df.attrs.get("meta", {})
        cap_b = (meta.get("market_cap") or 0) / 1e8
        if cap_b < cap_min:
            continue
        closes = df["close"].astype(float)
        if len(closes) < 112:
            continue
        ma112 = float(closes.iloc[-112:].mean())
        total += 1
        if float(closes.iloc[-1]) > ma112:
            above += 1
    pct = (above / total) if total else None
    overheated = pct is not None and pct >= THRESHOLDS["regime_overheat_breadth"]
    return {"pct": round(pct, 4) if pct is not None else None,
            "above": above, "total": total, "overheated": bool(overheated),
            "threshold": THRESHOLDS["regime_overheat_breadth"]}


def evaluate_pullback_signal(df: pd.DataFrame, meta: dict) -> dict:
    """단일 종목 OHLCV → 2구간 후반 눌림 신호 평가 (단테 §4.4 정합)."""
    if len(df) < THRESHOLDS["min_history_days"]:
        return {"pass": False, "reject": "이평+돌파창 부족 (230일 미충족)"}

    closes = df["close"].astype(float)
    opens = df["open"].astype(float)
    highs = df["high"].astype(float)
    volumes = df["volume"].astype(float)

    close_today = float(closes.iloc[-1])
    open_today = float(opens.iloc[-1])

    ma20 = float(closes.iloc[-20:].mean())
    ma60 = float(closes.iloc[-60:].mean())
    ma112 = float(closes.iloc[-112:].mean())
    ma224 = float(closes.iloc[-224:].mean())

    is_yang = close_today > open_today

    raw_mcap = meta.get("market_cap") or 0
    market_cap_billion = raw_mcap / 1e8 if raw_mcap else 0

    # 1) 시총
    if market_cap_billion < THRESHOLDS["min_market_cap_billion"]:
        return {"pass": False, "reject": f"시총 {market_cap_billion:.0f}억 < 1000억"}

    # 2) 224 돌파 이력
    days_since = find_breakout_day(closes, THRESHOLDS["breakout_window_max_days"])
    if days_since is None:
        return {"pass": False, "reject": "직전 60일 내 224 돌파 이력 없음"}
    if days_since < THRESHOLDS["breakout_window_min_days"]:
        return {"pass": False, "reject": f"돌파 {days_since}일전 < 5일 (1차 신호 영역)"}

    # 3) 현재 224 위 0~15%
    above_ma224_pct = (close_today / ma224 - 1) * 100
    if above_ma224_pct <= 0:
        return {"pass": False, "reject": f"224 아래 ({above_ma224_pct:.1f}%)"}
    if above_ma224_pct > THRESHOLDS["above_ma224_max_pct"] * 100:
        return {"pass": False, "reject": f"224 위 +{above_ma224_pct:.1f}% > 15% (3구간 중반)"}

    # 4) 112 위
    if close_today < ma112:
        return {"pass": False, "reject": "112 아래 (단기 추세 붕괴)"}

    # 5) 눌림 깊이
    high_20d = float(highs.iloc[-21:-1].max())
    pullback_pct = (close_today / high_20d - 1) * 100
    if pullback_pct > THRESHOLDS["pullback_max_pct"] * 100:
        return {"pass": False, "reject": f"눌림 {pullback_pct:.1f}% > -2% (조정 미발생)"}
    if pullback_pct < THRESHOLDS["pullback_min_pct"] * 100:
        return {"pass": False, "reject": f"눌림 {pullback_pct:.1f}% < -15% (추세 붕괴)"}

    # 6) 지지선 근접
    near_224_pct = above_ma224_pct
    near_112_pct = (close_today / ma112 - 1) * 100
    sup_win = THRESHOLDS["support_window_pct"] * 100
    near_224 = abs(near_224_pct) <= sup_win
    near_112 = abs(near_112_pct) <= sup_win
    if not (near_224 or near_112):
        return {"pass": False, "reject": f"지지선 거리 (224 {near_224_pct:+.1f}%, 112 {near_112_pct:+.1f}%)"}

    # 7) 거래량 감소 (② 0.7 이하 강한 매도 약화만 — 2026-06-11 개선)
    vol_5d = float(volumes.iloc[-5:].mean())
    vol_20d = float(volumes.iloc[-20:].mean())
    vol_shrink = vol_5d / vol_20d if vol_20d else 1.0
    if vol_shrink > THRESHOLDS["vol_shrink_max"]:
        return {"pass": False, "reject": f"거래량 5d/20d = {vol_shrink:.2f} > {THRESHOLDS['vol_shrink_max']} (매도 약화 부족)"}

    # 8) 통과일 양봉 (④ 음봉 눌림 제외 — 2026-06-11 개선)
    if THRESHOLDS.get("require_yang") and not is_yang:
        return {"pass": False, "reject": "음봉 통과일 (양봉 반등 미확인)"}

    # ============== 점수화 ==============
    score = 0
    if days_since <= 10:
        score += 30; fresh = "신선 (5~10일)"
    elif days_since <= 20:
        score += 25; fresh = "양호 (11~20일)"
    elif days_since <= 30:
        score += 20; fresh = "보통 (21~30일)"
    else:
        score += 15; fresh = "오래됨 (31~60일)"

    if abs(near_224_pct) <= 1:
        score += 20; support = "224 ±1%"
    elif abs(near_224_pct) <= 3:
        score += 15; support = "224 ±3%"
    elif near_224:
        score += 12; support = "224 ±5%"
    elif abs(near_112_pct) <= 3:
        score += 12; support = "112 ±3%"
    else:
        score += 8; support = "112 ±5%"

    if -8 <= pullback_pct <= -3:
        score += 20; dip = "건강 (-3~-8%)"
    elif -12 <= pullback_pct < -8:
        score += 15; dip = "깊음 (-8~-12%)"
    else:
        score += 10; dip = "기타"

    if 0.5 <= vol_shrink <= 0.7:
        score += 15
    elif 0.7 < vol_shrink <= 0.9:
        score += 10
    else:
        score += 5

    if is_yang:
        score += 10
    if ma20 > ma60:
        score += 5

    # 🆕 눌림 구간 쌍바닥(짝궁뎅이) — 배지는 전 쌍바닥, 가점은 거래량 다이버전스만 (§2.2 공구리2)
    #   백테스트상 "모양만 쌍바닥"은 우위 없음 → 2차 바닥 거래량 감소(매도 소진) 동반에만 가점.
    db = detect_double_bottom(df)
    db_reward = db["double_bottom"] and db.get("db_vol_div")

    score = min(100, max(0, score))

    # ① 통과 점수 게이트 (80 미만 컷 — 돌파 탭과 통일 2026-06-23 · 가점 전 원점수 판정)
    #   ⚠️ 게이트는 가점 전 '원점수'로 판정 — 가점을 게이트 앞에 두면 경계권(85~89) W형이
    #   무리하게 구제되어 약한 신호가 섞이고 W+다이버전스 우위가 희석됨(백테스트 확인 +1.64→+0.57).
    if score < THRESHOLDS.get("min_pass_score", 0):
        return {"pass": False, "reject": f"score {score} < {THRESHOLDS['min_pass_score']} (상위 신호 미달)"}

    # 통과 확정 후 표시·정렬용 가점 (노출 우선순위만 ↑, 통과 여부엔 영향 없음)
    if db_reward:
        score = min(100, score + THRESHOLDS.get("db_bonus", 0))

    # 별점 (SOP §4.4) — 거래량 다이버전스 쌍바닥이면 +1 (최대 5)
    if abs(near_224_pct) <= 1 and vol_shrink <= 0.7 and is_yang:
        stars = 5
    elif abs(near_224_pct) <= 3 and -10 <= pullback_pct <= -3:
        stars = 4
    elif abs(near_112_pct) <= 3:
        stars = 3
    else:
        stars = 2
    if db_reward:
        stars = min(5, stars + 1)

    return {
        "pass": True,
        "score": score,
        "stars": stars,
        "close_today": int(close_today),
        "candle": "양봉" if is_yang else "음봉",
        "days_since_breakout": days_since,
        "fresh": fresh,
        "support": support,
        "dip": dip,
        "pullback_pct": round(pullback_pct, 2),
        "vol_shrink": round(vol_shrink, 2),
        "ma20": round(ma20),
        "ma60": round(ma60),
        "ma112": round(ma112),
        "ma224": round(ma224),
        "above_ma224_pct": round(above_ma224_pct, 2),
        "near_112_pct": round(near_112_pct, 2),
        "market_cap_billion": round(market_cap_billion, 0),
        "align": bool(ma20 > ma60 > ma112),   # 정배열 (반등확인 게이트용)
        **db,  # double_bottom 플래그 + (참일 때) db_* 상세
    }


def evaluate_pullback_confirmed(df: pd.DataFrame, meta: dict) -> dict:
    """2단계 반등확인 눌림 평가 (단테 §4.4.1).

    1단계: 최근 confirm_window일 내 눌림 셋업(evaluate_pullback_signal 통과) 탐색.
    2단계: 셋업 이후 오늘 ②정배열 + ③셋업일 고가 돌파 → '확인'(pass=True) 승격.
           셋업 당일이거나 아직 미충족 → '대기'(pending=True).
    require_confirm=False면 구 1단계(즉시 추천)로 폴백.

    반환: stage='확인'(pass=True) / '대기'(pending=True) / 미해당(pass=False).
    """
    if not THRESHOLDS.get("require_confirm"):
        return evaluate_pullback_signal(df, meta)

    n = len(df)
    cw = THRESHOLDS["confirm_window"]
    closes = df["close"].astype(float)
    highs = df["high"].astype(float)
    today_close = float(closes.iloc[-1])

    # 최근(오늘 포함)부터 거슬러 첫 눌림 셋업 채택
    for k in range(0, cw + 1):
        s = n - 1 - k
        if s < THRESHOLDS["min_history_days"]:
            break
        setup = evaluate_pullback_signal(df.iloc[:s + 1], meta)
        if not setup.get("pass"):
            continue

        setup_high = float(highs.iloc[s])
        align_ok = setup.get("align", False) or not THRESHOLDS.get("require_align_on_confirm")
        setup["setup_date"] = str(df["date"].iloc[s]) if "date" in df.columns else None
        setup["setup_high"] = int(setup_high)
        setup["confirm_days"] = k

        if k == 0:
            # 셋업 당일 = 아직 반등확인 전
            setup["pass"] = False
            setup["pending"] = True
            setup["stage"] = "대기"
            setup["reject"] = "반등확인 대기 (눌림 셋업 당일 — 익일 이후 전일고가 돌파 대기)"
            return setup

        if align_ok and today_close > setup_high:
            # ②정배열 + ③셋업일 고가 돌파 → 확인 승격. 진입가 = 확인일(오늘) 종가
            setup["pass"] = True
            setup["pending"] = False
            setup["stage"] = "확인"
            setup["confirmed"] = True
            setup["close_today"] = int(today_close)
            return setup

        # 셋업은 있으나 미확인 = 대기
        why = []
        if not align_ok:
            why.append("정배열 미충족")
        if not (today_close > setup_high):
            why.append(f"셋업고가({int(setup_high)}) 미돌파")
        setup["pass"] = False
        setup["pending"] = True
        setup["stage"] = "대기"
        setup["reject"] = "반등확인 대기 (" + " · ".join(why) + ")"
        return setup

    return {"pass": False, "reject": f"최근 {cw}일 내 눌림 셋업 없음"}


# =============================================================================
# DART 공시 필터 (C안 하이브리드: 악재 컷 + 호재 점수+10 + 배지)
# =============================================================================

def apply_quality_fund_gate(candidates: list[dict], end_dd: str) -> list[dict]:
    """확인(pass) 종목에 펀더멘탈 ①~⑤ soft 게이트 부여 + ⭐우량 우선정렬.

    **soft = 탈락 없음.** pass/fail을 절대 뒤집지 않고 배지·정렬만 바꾼다.
    진입 판정은 사용자 4지 선다(§4.6.13) 유지.

    ⑥ 가격위치(바겐세일)는 제외한다 — 돌·눌·재 대상은 이미 224선을 돌파해
    52주 상단권이라 ⑥과 방향이 정반대다(포함 시 75% 부당 탈락 실증).
    근거 = 돌눌재_보강안_CwH_우량주게이트.md §0

    정렬 1순위 = 펀더멘탈 등급 (2026-07-15 사용자 확정) · 2순위 = 눌림 점수.
    DART 조회 실패/모듈 부재는 배지 없이 통과 (스캐너를 죽이지 않는다).
    """
    if not candidates:
        return candidates
    try:
        from screen_quality_value import grade_fundamental, FUND_BADGE_CUT
    except Exception as e:
        log(f"  [우량게이트] screen_quality_value 임포트 실패: {e} — 스킵")
        return candidates
    if DARTClient is None:
        log("  [우량게이트] dart_openapi 모듈 미존재 — 스킵")
        return candidates
    try:
        cli = DARTClient()
    except DARTAPIError as e:
        log(f"  [우량게이트] {e} → 스킵")
        return candidates

    badged = 0
    for c in candidates:
        try:
            q = grade_fundamental(c["code"], cli, end_dd=end_dd)
        except Exception as e:
            log(f"  [우량게이트] {c['name']}({c['code']}) 조회 실패: {e} — 배지 없이 통과")
            c["quality_fund"] = {"passed": 0, "grade": None, "badge": False,
                                 "details": {}, "error": str(e)}
            continue
        c["quality_fund"] = q
        if q.get("badge"):
            badged += 1
            mark = "🅰️ 우량" if q["grade"] == "A" else "🅱️"
            log(f"  [우량게이트] {c['name']}({c['code']}) {mark} 펀더 {q['passed']}/5")

    # 정렬 1순위 = 펀더멘탈 등급 · 2순위 = 눌림 점수 (사용자 확정 2026-07-15)
    candidates.sort(key=lambda x: (-(x.get("quality_fund") or {}).get("passed", 0),
                                   -x["score"]))
    log(f"  [우량게이트] 배지 {badged}종 (컷 ≥{FUND_BADGE_CUT}/5) / 대상 {len(candidates)}종 "
        f"· soft=탈락 없음 · 정렬 1순위=펀더멘탈 등급")
    return candidates


def apply_disclosure_filter(candidates: list[dict], ticker_data: dict[str, pd.DataFrame]) -> list[dict]:
    """후보 종목에 DART 공시 필터 적용 (3단계 평가).

    Step 0: 악재 (유증/CB/감자/횡령 등) 있으면 즉시 컷
    Step 1: 사업보고서(연간) 영업이익 YoY — 감소=컷 / 증가=+10
    Step 2: 가장 최근 분기·반기보고서 영업이익 YoY — 감소=컷 / 증가=+10
    Step 3: 호재 공시 (자사주취득·소각·단일판매계약·배당) — 종류당 +5 (최대 +15)

    데이터 누락(보고서 없거나 재무 추출 실패)은 컷 없이 통과 (점수 변경 X).
    DART_API_KEY 미설정 시 전체 스킵.
    """
    if DARTClient is None:
        log("  [공시필터] dart_openapi 모듈 미존재 — 스킵")
        return candidates
    try:
        cli = DARTClient()
    except DARTAPIError as e:
        log(f"  [공시필터] {e} → 스킵")
        return candidates

    out = []
    for c in candidates:
        stock_code = c["code"]
        name = c["name"]
        try:
            disc = cli.recent_disclosures(stock_code, days=THRESHOLDS["disclosure_lookback_days"])
        except DARTAPIError as e:
            log(f"  [공시필터] {name}({stock_code}) 조회 실패: {e} — 통과 (필터 X)")
            c["disclosure"] = {"checked": False, "reason": str(e)}
            out.append(c)
            continue

        corp_code = cli.to_corp_code(stock_code)
        negative = cli.find_negative(disc)

        # Step 0: 악재 컷
        if negative:
            neg = ", ".join(f"{n['matched']}({n['rcept_dt']})" for n in negative[:3])
            log(f"  [컷-악재] {name}({stock_code}): {neg}")
            continue

        bonus = 0
        badges = []
        cut_reason = None
        detail = {"annual": None, "quarterly": None, "positives": []}

        # Step 1: 사업보고서 (연간) 영업이익 YoY
        annual = cli.latest_annual(disc)
        if annual and annual.get("bsns_year") and corp_code:
            try:
                fin_a = cli.financial_summary(corp_code, annual["bsns_year"], "11011")
            except DARTAPIError:
                fin_a = {}
            op_yoy = fin_a.get("op_yoy_pct")
            detail["annual"] = {"bsns_year": annual["bsns_year"], "op_yoy_pct": op_yoy}
            if op_yoy is not None:
                if op_yoy < 0:
                    cut_reason = f"연간 영업이익 YoY {op_yoy:+.1f}% (사업보고서 {annual['bsns_year']})"
                    log(f"  [컷-실적] {name}({stock_code}): {cut_reason}")
                    continue
                else:
                    bonus += THRESHOLDS["bonus_annual_op_up"]
                    badges.append(f"✅ {annual['bsns_year']} 연간 영업이익 YoY {op_yoy:+.1f}%")

        # Step 2: 분기/반기 영업이익 YoY
        quarterly = cli.latest_quarterly(disc)
        if quarterly and quarterly.get("bsns_year") and corp_code:
            try:
                fin_q = cli.financial_summary(corp_code, quarterly["bsns_year"], quarterly["reprt_code"])
            except DARTAPIError:
                fin_q = {}
            op_yoy_q = fin_q.get("op_yoy_pct")
            detail["quarterly"] = {"bsns_year": quarterly["bsns_year"],
                                   "reprt_code": quarterly["reprt_code"],
                                   "op_yoy_pct": op_yoy_q}
            if op_yoy_q is not None:
                if op_yoy_q < 0:
                    cut_reason = f"분기 영업이익 YoY {op_yoy_q:+.1f}% ({quarterly.get('report_nm')})"
                    log(f"  [컷-실적] {name}({stock_code}): {cut_reason}")
                    continue
                else:
                    bonus += THRESHOLDS["bonus_quarterly_op_up"]
                    label = quarterly.get("report_nm", "분기보고서").split(" ")[0]
                    badges.append(f"✅ {label} 영업이익 YoY {op_yoy_q:+.1f}%")

        # Step 3: 호재 공시 (종류당 +5, 최대 +15)
        positives = cli.find_positive(disc)
        # 임원(간부진) 장내매수도 호재 1종류 — 회장·대주주·사외이사 제외 (2026-10-16)
        try:
            buys = cli.insider_buys(stock_code, days=THRESHOLDS["insider_lookback_days"])
        except Exception as e:
            log(f"  [공시필터] {name}({stock_code}) 임원매수 조회 실패: {e} — 가점 없음")
            buys = []
        if buys:
            amt = sum(b["amount"] for b in buys) / 1e8
            positives.insert(0, {"matched": f"임원 장내매수 {len({b['repror'] for b in buys})}명 {amt:.1f}억",
                                 "rcept_dt": buys[0]["rcept_dt"]})
            detail["insider_buys"] = buys
        if positives:
            seen_kinds = set()
            extra = 0
            for p in positives:
                if p["matched"] in seen_kinds:
                    continue
                seen_kinds.add(p["matched"])
                extra += THRESHOLDS["bonus_positive_each"]
                badges.append(f"➕ {p['matched']} ({p['rcept_dt']})")
                if extra >= THRESHOLDS["bonus_positive_max"]:
                    break
            bonus += min(extra, THRESHOLDS["bonus_positive_max"])
            detail["positives"] = [{"matched": p["matched"], "rcept_dt": p["rcept_dt"]}
                                   for p in positives[:5]]

        c["disclosure"] = {
            "checked": True,
            "bonus_applied": bonus,
            "badges": badges,
            "detail": detail,
        }
        if bonus:
            c["score"] = min(100, c["score"] + bonus)
        out.append(c)

    return out


# =============================================================================
# 직전 회차 대비 이탈 종목 산출 (2026-05-26 §5 역할 전환)
# =============================================================================

DROP_REASON_LABELS = {
    "ma112_break": "112일선 종가 이탈 (단기 추세 붕괴)",
    "pullback_too_deep": "눌림 -15% 초과 (추세 붕괴 영역)",
    "vol_surge_selling": "거래량 평균 1배 초과 (매도 추세 전환)",
    "above_224_overheated": "224일선 +15% 초과 (3구간 중반 과열)",
    "ma224_break": "224일선 종가 이탈",
    "support_gap": "224·112 지지선 ±5% 이탈",
    "no_pullback": "눌림 -2% 미만 (조정 소멸·재돌파)",
    "breakout_aged_out": "60영업일 내 224 돌파 이력 소멸 (3구간 안착)",
    "market_cap_below": "시총 1,000억 하회 (유동성 미달)",
    "data_missing": "오늘 OHLCV 부재 (거래정지·상장폐지 가능성)",
    "other": "기타 조건 미충족",
}

DROP_REASON_PRIORITY = {
    "ma112_break": 0,
    "pullback_too_deep": 1,
    "vol_surge_selling": 2,
    "above_224_overheated": 3,
    "ma224_break": 4,
    "data_missing": 5,
    "support_gap": 6,
    "breakout_aged_out": 7,
    "no_pullback": 8,
    "market_cap_below": 9,
    "other": 10,
}


def _categorize_reject(reject: str) -> str:
    if not reject:
        return "other"
    if "112 아래" in reject:
        return "ma112_break"
    if "< -15%" in reject or "추세 붕괴" in reject:
        return "pullback_too_deep"
    if "거래량" in reject:
        return "vol_surge_selling"
    if "224 위 +" in reject and "> 15%" in reject:
        return "above_224_overheated"
    if "224 아래" in reject:
        return "ma224_break"
    if "지지선 거리" in reject:
        return "support_gap"
    if "돌파 이력 없음" in reject:
        return "breakout_aged_out"
    if "조정 미발생" in reject:
        return "no_pullback"
    if "시총" in reject:
        return "market_cap_below"
    return "other"


def _find_previous_theme_flow(run_date: dt.date) -> Path | None:
    today_name = f"theme_flow_{run_date.isoformat()}.json"
    cands = sorted(
        (p for p in DATA_DIR.glob("theme_flow_*.json") if p.name != today_name),
        reverse=True,
    )
    return cands[0] if cands else None


def compute_dropped_candidates(today_candidates: list[dict],
                                ticker_data: dict[str, pd.DataFrame],
                                run_date: dt.date) -> list[dict]:
    """직전 theme_flow와 비교하여 오늘 빠진 종목의 이탈 사유 산출.

    제외: 점수가 낮아 top10 cap 밖으로 밀린 종목(여전히 pass) — '이탈' 아님.
    포함: pass=False가 된 종목 + 오늘 OHLCV 결손 종목.
    """
    today_codes = {c["code"] for c in today_candidates}
    prev_path = _find_previous_theme_flow(run_date)
    if not prev_path:
        log("직전 theme_flow 없음 → 이탈 종목 비교 스킵")
        return []

    try:
        prev = json.loads(prev_path.read_text(encoding="utf-8"))
    except Exception as e:
        log(f"직전 theme_flow 로드 실패: {e}")
        return []

    prev_entries = {c["code"]: c for c in (prev.get("candidates") or [])}
    dropped: list[dict] = []

    for code, prev_entry in prev_entries.items():
        if code in today_codes:
            continue  # 유지 — 이탈 아님

        df = ticker_data.get(code)
        if df is None:
            dropped.append({
                "code": code,
                "name": prev_entry.get("name", code),
                "market": prev_entry.get("market"),
                "reason_code": "data_missing",
                "reason_label": DROP_REASON_LABELS["data_missing"],
                "reason_brief": "오늘 OHLCV 부재",
                "prev_score": prev_entry.get("score"),
                "prev_stars": prev_entry.get("stars"),
                "prev_date": prev.get("date"),
            })
            continue

        meta = df.attrs.get("meta", {})
        try:
            sig = evaluate_pullback_signal(df, meta)
        except Exception as e:
            log(f"  [WARN] 이탈 재평가 실패 {code}: {e}")
            continue

        if sig.get("pass"):
            continue  # 통과하는데 점수가 낮아 cap 밖 — 이탈 아님

        reject = sig.get("reject", "")
        reason_code = _categorize_reject(reject)

        closes = df["close"].astype(float)
        volumes = df["volume"].astype(float)
        close_today = int(float(closes.iloc[-1]))
        ma112_v = round(float(closes.iloc[-112:].mean()))
        ma224_v = round(float(closes.iloc[-224:].mean()))
        vol5 = float(volumes.iloc[-5:].mean())
        vol20 = float(volumes.iloc[-20:].mean())
        vol_shrink = round(vol5 / vol20, 2) if vol20 else None

        delta_pct = None
        if prev_entry.get("close_today"):
            delta_pct = round((close_today / prev_entry["close_today"] - 1) * 100, 2)

        dropped.append({
            "code": code,
            "name": prev_entry.get("name", code),
            "market": prev_entry.get("market"),
            "reason_code": reason_code,
            "reason_label": DROP_REASON_LABELS.get(reason_code, "기타"),
            "reason_brief": reject,
            "prev_score": prev_entry.get("score"),
            "prev_stars": prev_entry.get("stars"),
            "prev_date": prev.get("date"),
            "prev_close": prev_entry.get("close_today"),
            "close_today": close_today,
            "delta_pct": delta_pct,
            "ma112": ma112_v,
            "ma224": ma224_v,
            "vol_shrink": vol_shrink,
        })

    dropped.sort(key=lambda x: (DROP_REASON_PRIORITY.get(x["reason_code"], 99),
                                 -(x.get("prev_score") or 0)))
    return dropped


# =============================================================================
# 영속 추천 풀 (recommend_pool.json)
#   - 한번 등장한 추천 종목을 누적 보관. 자동 삭제 없음 (사용자 지시 시에만 제거).
#   - 매 회차: ① 기존 풀 전 종목 재평가(통과↔이탈) → ② 신규 통과 종목 편입.
#   - feedback_recommend_persistent_tracking (2026-06-02 사용자 확정) 정합.
# =============================================================================

def load_recommend_pool() -> dict:
    """영속 추천 풀 로드. 없으면 빈 풀."""
    if RECOMMEND_POOL_PATH.exists():
        try:
            pool = json.loads(RECOMMEND_POOL_PATH.read_text(encoding="utf-8"))
            if isinstance(pool, dict) and isinstance(pool.get("stocks"), dict):
                return pool
        except Exception as e:
            log(f"[WARN] recommend_pool 로드 실패: {e} → 빈 풀로 시작")
    return {"version": "pool_v1", "updated": None, "stocks": {}}


def save_recommend_pool(pool: dict) -> None:
    RECOMMEND_POOL_PATH.write_text(
        json.dumps(pool, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"recommend_pool 저장: {RECOMMEND_POOL_PATH} "
        f"(통과 {sum(1 for e in pool['stocks'].values() if e['status']=='통과')} / "
        f"이탈 {sum(1 for e in pool['stocks'].values() if e['status']=='이탈')})")


def _apply_pool_eval(entry: dict, status: str, today_iso: str,
                     sig: dict | None = None, reject: str | None = None,
                     reason_code: str | None = None) -> None:
    """풀 엔트리에 오늘 재평가 결과 반영 (history append 포함)."""
    entry["status"] = status
    entry["last_eval"] = today_iso
    if status == "통과":
        entry["reject"] = None
        entry["reason_code"] = None
        entry["consecutive_drop_days"] = 0
        if sig:
            entry["score"] = sig.get("score")
            entry["stars"] = sig.get("stars")
            entry["double_bottom"] = bool(sig.get("double_bottom"))
            entry["db_vol_div"] = bool(sig.get("db_vol_div"))
            entry["db_bounce_pct"] = sig.get("db_bounce_pct")
            entry["confirmed"] = bool(sig.get("confirmed"))
            entry["setup_date"] = sig.get("setup_date")
            entry["confirm_days"] = sig.get("confirm_days")
            if sig.get("close_today"):
                entry["close_today"] = sig["close_today"]
    else:  # 이탈
        entry["double_bottom"] = False
        entry["db_vol_div"] = False
        entry["confirmed"] = False
        entry["reject"] = reject
        entry["reason_code"] = reason_code
        entry["consecutive_drop_days"] = (entry.get("consecutive_drop_days") or 0) + 1
        if sig and sig.get("close_today"):
            entry["close_today"] = sig["close_today"]
    # 고수위(peak) 갱신 — 졸업 2단(+10/+20) 영구 판정용 (2026-06-11)
    if entry.get("close_today"):
        if not entry.get("ref_close"):
            entry["ref_close"] = entry["close_today"]
        r = entry["close_today"] / entry["ref_close"] - 1
        entry["peak_return"] = round(max(entry.get("peak_return", 0.0), r), 4)
    hist = entry.setdefault("history", [])
    hist.append({"date": today_iso, "status": status, "score": entry.get("score")})
    entry["history"] = hist[-30:]  # 최근 30회차만 보관


def _refresh_peak_from_frame(entry: dict, df) -> None:
    """이탈 종목 close_today 동결로 인한 peak(졸업) 누락 방지 — first_seen 이후 실제
    종가 히스토리에서 진짜 고수위를 재계산해 peak_return을 갱신한다 (2026-06-29 버그 수정).
    조선내화(462520) 사례: 이탈 후 +10.7% 회복했으나 close_today 동결로 졸업 누락."""
    if df is None or not entry.get("ref_close"):
        return
    try:
        fs = (entry.get("first_seen") or "").replace("-", "")
        sub = df[df["date"] >= fs] if fs else df
        closes = sub["close"]
        if len(closes) == 0:
            return
        entry["close_today"] = int(closes.iloc[-1])           # 동결 해제: 최신 종가
        peak = float(closes.max()) / entry["ref_close"] - 1   # 진짜 고수위
        entry["peak_return"] = round(max(entry.get("peak_return", 0.0), peak), 4)
    except Exception:
        pass


def update_recommend_pool(today_passed: list[dict],
                          ticker_data: dict[str, pd.DataFrame],
                          run_date: dt.date,
                          lineup_codes: set[str]) -> tuple[dict, list[dict], list[dict]]:
    """영속 추천 풀 갱신 (사용자 워크플로우: 풀 재평가 먼저 → 신규 편입).

    Step A: 기존 풀 전 종목을 오늘 OHLCV로 재평가 → 통과↔이탈 분류 갱신 (삭제 없음).
            이탈 종목도 조건 회복 시 통과로 복귀.
    Step B: 오늘 신규 통과 종목(today_passed) 중 풀에 없는 것 편입.

    반환: (pool, pool_passed[점수순], pool_dropped[점수순])
    """
    pool = load_recommend_pool()
    stocks = pool.setdefault("stocks", {})
    today_iso = run_date.isoformat()
    today_by_code = {c["code"]: c for c in today_passed}

    # --- Step A: 기존 풀 재평가 (신규 서칭 전 우선 정리) ---
    for code, entry in stocks.items():
        entry["promoted"] = "candidate/active" if code in lineup_codes else None
        if code in today_by_code:
            # 오늘 전종목 스캔에서도 통과 → 통과 유지/복귀
            _apply_pool_eval(entry, "통과", today_iso, sig=today_by_code[code])
            continue
        df = ticker_data.get(code)
        if df is None:
            _apply_pool_eval(entry, "이탈", today_iso, reject="오늘 OHLCV 부재",
                             reason_code="data_missing")
            continue
        try:
            sig = evaluate_pullback_signal(df, df.attrs.get("meta", {}))
        except Exception as e:
            log(f"  [WARN] 풀 재평가 실패 {code}: {e}")
            sig = {"pass": False, "reject": "재평가 오류"}
        if sig.get("pass"):
            _apply_pool_eval(entry, "통과", today_iso, sig=sig)
        else:
            reject = sig.get("reject", "")
            _apply_pool_eval(entry, "이탈", today_iso, sig=sig, reject=reject,
                             reason_code=_categorize_reject(reject))
        _refresh_peak_from_frame(entry, df)  # 이탈이어도 실제 고수위 반영 (졸업 누락 방지)

    # --- Step B: 신규 통과 종목 편입 ---
    for c in today_passed:
        if c["code"] in stocks:
            continue
        stocks[c["code"]] = {
            "code": c["code"],
            "name": c.get("name", c["code"]),
            "market": c.get("market"),
            "sector": c.get("sector"),
            "status": "통과",
            "first_seen": today_iso,
            "last_eval": today_iso,
            "score": c.get("score"),
            "stars": c.get("stars"),
            "double_bottom": bool(c.get("double_bottom")),
            "db_vol_div": bool(c.get("db_vol_div")),
            "db_bounce_pct": c.get("db_bounce_pct"),
            # 펀더멘탈 ①~⑤ soft 게이트 (배지 + 노출 정렬 1순위). 편입 시점 1회 산출 후
            # 유지 — 재무는 분기 단위로만 바뀌므로 매 회차 DART를 다시 때릴 이유가 없다.
            "quality_fund": c.get("quality_fund"),
            "confirmed": bool(c.get("confirmed")),
            "setup_date": c.get("setup_date"),
            "confirm_days": c.get("confirm_days"),
            "close_today": c.get("close_today"),
            "ref_close": c.get("close_today"),   # +20% 졸업 기준가 = 포착일 종가
            "reject": None,
            "reason_code": None,
            "consecutive_drop_days": 0,
            "promoted": "candidate/active" if c["code"] in lineup_codes else None,
            "history": [{"date": today_iso, "status": "통과", "score": c.get("score")}],
        }

    pool["updated"] = today_iso
    pool_passed = sorted((e for e in stocks.values() if e["status"] == "통과"),
                         key=lambda x: -(x.get("score") or 0))
    pool_dropped = sorted((e for e in stocks.values() if e["status"] == "이탈"),
                          key=lambda x: (x.get("consecutive_drop_days") or 0,
                                         -(x.get("score") or 0)))
    return pool, pool_passed, pool_dropped


# =============================================================================
# 생애주기 도출 (2026-06-11 돌파 탭과 공통 · 졸업/이탈/지속관찰/노출컷)
#   기존 통과/이탈(status) 인터페이스는 보존하고 lifecycle 필드를 덧입힌다.
#   - 🎓 졸업    : 포착일(ref_close) 대비 +20% → terminal 성공
#   - ❌ 이탈    : 추세선(112/224) 종가 이탈(reason_code ma112/ma224_break) → 제거
#   - ⏳ 지속관찰: 그 외 미통과 + 5영업일 경과 → 탭 내림·추적 유지
#   - 통과       : score ≥ 80 만 눌림 탭/Slack 노출
# =============================================================================

def _bdays_elapsed(d0_iso: str, d1_iso: str, all_iso: list[str]) -> int:
    """캐시 영업일 인덱스 기준 d0~d1 경과 영업일수 (없으면 달력일 근사)."""
    try:
        return max(0, all_iso.index(d1_iso) - all_iso.index(d0_iso))
    except ValueError:
        try:
            return (dt.date.fromisoformat(d1_iso) - dt.date.fromisoformat(d0_iso)).days
        except Exception:
            return 0


def derive_lifecycle(pool: dict, run_date: dt.date,
                     ticker_data: dict[str, pd.DataFrame]) -> tuple[list, list, list, list]:
    """풀 엔트리에 lifecycle 부여 → 4버킷 (돌파 탭과 공통 용어).
      🟢 노출   : 통과 AND score ≥ 80
      🎓 졸업   : 포착일(ref_close) 종가 대비 +20%
      ⏳ 지속관찰: 통과지만 80 미만 / 추세 유효한데 조건 미달(이탈 사유가 추세선 아님)
      ❌ 이탈   : 추세선(112/224) 종가 이탈 (reason_code ma112/ma224_break)
    """
    cut = THRESHOLDS["display_score_cut"]
    display, grad10, grad20, watching, removed = [], [], [], [], []
    for code, e in pool["stocks"].items():
        ref = e.get("ref_close") or e.get("close_today")
        cur = e.get("close_today")
        if not e.get("ref_close") and ref:
            e["ref_close"] = ref  # 구버전 엔트리 보정
        peak = e.get("peak_return")
        if peak is None and ref and cur:
            peak = cur / ref - 1   # 구버전 보정
        peak = peak or 0.0
        # ① 졸업 2단 (고수위, 영구): +20% → 졸업②, +10% → 졸업①
        if peak >= 0.20:
            e["lifecycle"] = "졸업20"; e["gain_pct"] = round(peak * 100, 1); grad20.append(e); continue
        if peak >= 0.10:
            e["lifecycle"] = "졸업10"; e["gain_pct"] = round(peak * 100, 1); grad10.append(e); continue
        # ② 통과 → 80+ 노출 / 80 미만은 지속관찰
        if e.get("status") == "통과":
            if (e.get("score") or 0) >= cut:
                e["lifecycle"] = "노출"; display.append(e)
            else:
                e["lifecycle"] = "지속관찰"; watching.append(e)
            continue
        # ③ 이탈: 추세선 종가 이탈만 제거, 그 외(추세 유효)는 지속관찰
        if e.get("reason_code") in ("ma112_break", "ma224_break"):
            e["lifecycle"] = "이탈"; removed.append(e)
        else:
            e["lifecycle"] = "지속관찰"; watching.append(e)
    # 노출 정렬 1순위 = 펀더멘탈 등급(①~⑤ soft 게이트) · 2순위 = 눌림 점수
    #   (2026-07-15 사용자 확정). 게이트 도입 前 편입분은 quality_fund가 없어
    #   passed=0으로 취급되어 뒤로 밀린다 — 탈락이 아니라 정렬상 후순위일 뿐.
    display.sort(key=lambda x: (-((x.get("quality_fund") or {}).get("passed") or 0),
                                -(x.get("score") or 0)))
    grad10.sort(key=lambda x: x.get("last_eval") or "", reverse=True)
    grad20.sort(key=lambda x: x.get("last_eval") or "", reverse=True)
    watching.sort(key=lambda x: -(x.get("score") or 0))
    removed.sort(key=lambda x: (x.get("consecutive_drop_days") or 0, -(x.get("score") or 0)))
    return display, grad10, grad20, watching, removed


# =============================================================================
# 기존 보유·후보 종목 추세 점검 (08:15 회차, 장 시작 전 전일 확정 OHLCV)
# =============================================================================

def load_lineup() -> list[dict]:
    """stocks.json 활성+후보 라인업 로드 (name/code/tag)."""
    p = ROOT / "stocks.json"
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("stocks", [])
    except Exception as e:
        log(f"  [WARN] stocks.json 로드 실패: {e}")
        return []


def evaluate_holdings_trend(lineup: list[dict], ticker_data: dict[str, pd.DataFrame]) -> list[dict]:
    """보유·후보 종목의 추세 붕괴 여부 판정 (단테 §4.9 추세 판정을 수치 지표로 근사).

    차트 이미지가 없는 장 시작 전 회차이므로 5/20/60/112/224 이평 + 종가 위치 + 거래량으로
    ✅ 추세 유효 / ⚠️ 추세 약화 / 🛑 추세 이탈 을 결정적으로 산출한다.
    스캐너용으로 이미 받아둔 OHLCV(ticker_data)를 재사용하므로 추가 API 호출 없음.
    """
    out: list[dict] = []
    for meta in lineup:
        code = meta.get("code", "")
        name = meta.get("name", code)
        tag = meta.get("tag", "")
        df = ticker_data.get(code)
        if df is None or len(df) < 60:
            out.append({
                "code": code, "name": name, "tag": tag,
                "status": "❓ 데이터 부족", "trend_break": False,
                "reason": "OHLCV 60일 미만 (캐시 부족·ETF/신규 종목 등)",
            })
            continue

        closes = df["close"].astype(float)
        vols = df["volume"].astype(float)
        close_today = float(closes.iloc[-1])
        prev_close = float(closes.iloc[-2])
        ma20 = float(closes.iloc[-20:].mean())
        ma60 = float(closes.iloc[-60:].mean())
        ma112 = float(closes.iloc[-112:].mean()) if len(closes) >= 112 else None
        ma224 = float(closes.iloc[-224:].mean()) if len(closes) >= 224 else None
        vol20 = float(vols.iloc[-20:].mean())
        vol_ratio = round(float(vols.iloc[-1]) / vol20, 2) if vol20 else None
        chg_pct = round((close_today - prev_close) / prev_close * 100, 2) if prev_close else 0.0
        is_down = close_today < prev_close

        # 중장기 지지 이평 종가 이탈 + 1% 버퍼 = 단테 추세 이탈 라인 (§2.5.2 / §2.6)
        broke_ma224 = ma224 is not None and close_today < ma224 * 0.99
        broke_ma112 = ma112 is not None and close_today < ma112 * 0.99
        below_ma60 = close_today < ma60
        below_ma20 = close_today < ma20
        # 음봉 거래량 2배+ = 매도 폭증 (§4.6.12 진입 금지 영역)
        vol_surge_down = is_down and vol_ratio is not None and vol_ratio >= 2.0

        reasons = []
        if broke_ma224:
            reasons.append(f"224일선({int(ma224)}) -1% 종가 이탈")
        if broke_ma112:
            reasons.append(f"112일선({int(ma112)}) -1% 종가 이탈")
        if below_ma60:
            reasons.append(f"60일선({int(ma60)}) 하회")
        if below_ma20:
            reasons.append(f"20일선({int(ma20)}) 하회")
        if vol_surge_down:
            reasons.append(f"음봉 거래량 {vol_ratio}배 폭증")

        if broke_ma224 or broke_ma112 or vol_surge_down:
            status, trend_break = "🛑 추세 이탈", True
        elif below_ma60 or below_ma20:
            status, trend_break = "⚠️ 추세 약화", False
        else:
            status, trend_break = "✅ 추세 유효", False
            reasons.append("5/20/60/112/224 이평 위 유지")

        out.append({
            "code": code, "name": name, "tag": tag,
            "status": status, "trend_break": trend_break,
            "close_today": int(close_today), "change_pct": chg_pct,
            "ma20": int(ma20), "ma60": int(ma60),
            "ma112": int(ma112) if ma112 else None,
            "ma224": int(ma224) if ma224 else None,
            "vol_ratio": vol_ratio,
            "reason": " · ".join(reasons),
        })
    return out


# =============================================================================
# Main
# =============================================================================

def run_scan(target_date: dt.date, dry_run: bool = False, backfill_days: int = 0,
             run_date: dt.date | None = None) -> dict:
    run_date = run_date or target_date
    log(f"=== 2구간 후반 눌림 스캐너 시작 (scan={target_date}, run_date={run_date}) ===")
    client = KRXClient()

    # 캐시 보장 영업일 = 224 + 돌파 윈도우 60 + 여유 6 = 290 (백필 시 확장)
    history_days = max(THRESHOLDS["min_history_days"] + THRESHOLDS["breakout_window_max_days"] + 6,
                       backfill_days)
    target_dates = business_days_back(target_date, history_days)
    log(f"OHLCV 캐시 영업일 {len(target_dates)}일 (가장 오래된 {target_dates[-1]} ~ 최신 {target_dates[0]})")

    ticker_data = ensure_ohlcv_cache(client, target_dates)
    log(f"분석 대상 종목: {len(ticker_data)}종")

    # 과열 회피 레짐: 시장 브레드스 산출 (경고 표시 전용 · 추천 차단/감점 없음)
    market_breadth = compute_market_breadth(ticker_data)
    if market_breadth["pct"] is not None:
        log(f"시장 브레드스 {market_breadth['pct']*100:.1f}% "
            f"({market_breadth['above']}/{market_breadth['total']}) "
            f"{'⚠️ 과열(눌림 진입 주의)' if market_breadth['overheated'] else '✅ 정상'}")

    # 2단계 반등확인: '확인'(candidates) = 추천 승격 / '대기'(pending) = 셋업 후 반등확인 대기
    candidates = []
    pending = []
    for code, df in ticker_data.items():
        meta = df.attrs.get("meta", {})
        try:
            sig = evaluate_pullback_confirmed(df, meta)
        except Exception as e:
            log(f"  [WARN] {code} 평가 실패: {e}")
            continue
        row = {
            "code": code,
            "name": meta.get("name", code),
            "market": meta.get("market"),
            "sector": meta.get("sector"),
            **sig,
        }
        if sig.get("pass"):
            candidates.append(row)
        elif sig.get("pending"):
            pending.append(row)

    candidates.sort(key=lambda x: x["score"], reverse=True)
    candidates = candidates[:THRESHOLDS["max_candidates"]]
    pending.sort(key=lambda x: x.get("score", 0), reverse=True)
    pending = pending[:THRESHOLDS["max_candidates"]]

    log(f"2단계 눌림: ✅확인(추천 승격) {len(candidates)}종 / ⏳반등확인 대기 {len(pending)}종 "
        f"(점수순 상위 {THRESHOLDS['max_candidates']}컷)")

    # DART 공시 필터 (악재 컷 + 호재 점수+10 + 배지)
    pre_count = len(candidates)
    candidates = apply_disclosure_filter(candidates, ticker_data)
    candidates.sort(key=lambda x: x["score"], reverse=True)
    log(f"공시 필터 후 {len(candidates)}종 (컷 {pre_count - len(candidates)}종)")

    # 펀더멘탈 ①~⑤ soft 게이트 (⑥ 가격위치 제외) — 배지 + ⭐우량 우선정렬 · 탈락 없음
    candidates = apply_quality_fund_gate(candidates, target_date.strftime("%Y%m%d"))

    # === 영속 추천 풀 갱신 (사용자 워크플로우: ① 기존 풀 재평가 먼저 → ② 신규 편입 · 자동삭제 없음) ===
    # candidates = 오늘 전종목 스캔 통과(=신규 서칭 결과). 풀 재평가로 통과/이탈을 누적 추적.
    # theme_flow의 candidates/dropped 인터페이스는 풀 기반으로 산출 (claude_dante_analyze.js 호환).
    lineup = load_lineup()
    lineup_codes = {s["code"] for s in lineup}
    pool, pool_passed, pool_dropped = update_recommend_pool(
        candidates, ticker_data, run_date, lineup_codes)
    # 생애주기 5버킷 (돌파 탭과 공통): 노출(80+) / 졸업①(+10) / 졸업②(+20) / 지속관찰 / 이탈
    display_candidates, grad10, grad20, watching, removed = derive_lifecycle(pool, run_date, ticker_data)
    log(f"추천 풀 갱신: 🟢노출 {len(display_candidates)} / 🎓졸업①{len(grad10)} 졸업②{len(grad20)} / "
        f"⏳지속관찰 {len(watching)} / ❌이탈 {len(removed)} "
        f"(오늘 신규 스캔 통과 {len(candidates)} · 풀 총 {len(pool['stocks'])})")
    if not dry_run:
        save_recommend_pool(pool)

    # 기존 보유·후보 추세점검 비활성 (2026-06-11 사용자 지시 — 눌림 탭 카드·관련 산출 제거)
    holdings_trend = []
    log("기존 보유·후보 추세점검 비활성 (사용자 지시)")

    result = {
        "date": run_date.isoformat(),
        "scan_date": target_date.isoformat(),
        "data_source": "KRX OpenAPI (data-dbg.krx.co.kr/svc/apis)",
        "scanner_version": "pullback_v1.4 (2026-06-16 2단계 반등확인 + 짝궁뎅이 쌍바닥)",
        "thresholds": THRESHOLDS,
        "universe_size": len(ticker_data),
        "market_breadth": market_breadth,  # ⚠️ 과열 회피 레짐 (경고 표시 전용)
        "candidates": display_candidates,  # 🟢 노출 = 통과 AND score≥80
        "pending": pending,                # ⏳ 반등확인 대기 (눌림 셋업·전일고가 미돌파)
        "graduated10": grad10,             # 🎓 졸업① (+10%)
        "graduated20": grad20,             # 🎓 졸업② (+20%)
        "watching": watching,              # ⏳ 지속관찰 (추세유효·노출 미달)
        "removed": removed,                # ❌ 이탈 (추세선 112/224 종가 이탈)
        "pool_passed_all": pool_passed,    # 80컷 전 전체 통과 (참고)
        "dropped": pool_dropped,           # (legacy) 영속 풀 이탈 누적
        "scan_passed_today": candidates,   # 오늘 전종목 스캔 통과 (참고용)
        "holdings_trend": holdings_trend,
        "pool_meta": {
            "total": len(pool["stocks"]),
            "passed": len(pool_passed),
            "dropped": len(pool_dropped),
            "new_today": [c["code"] for c in candidates
                          if c["code"] in {p["code"] for p in pool_passed}
                          and pool["stocks"][c["code"]].get("first_seen") == run_date.isoformat()],
        },
        "stats": {
            "scanned": len(ticker_data),
            "passed": len(pool_passed),
            "dropped": len(pool_dropped),
            "scan_passed_today": len(candidates),
            "pool_total": len(pool["stocks"]),
            "min_score": min((e["score"] for e in pool_passed if e.get("score")), default=0),
            "max_score": max((e["score"] for e in pool_passed if e.get("score")), default=0),
        },
    }

    if not dry_run:
        out_path = DATA_DIR / f"theme_flow_{run_date.isoformat()}.json"
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"theme_flow 저장: {out_path}")
    else:
        log("--dry-run: 저장 생략. 상위 5종 미리보기:")
        preview = {k: v for k, v in result.items() if k != "candidates"}
        preview["candidates"] = result["candidates"][:5]
        print(json.dumps(preview, ensure_ascii=False, indent=2)[:3000])

    log(f"=== 완료 ===")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None, help="YYYY-MM-DD (KST). 미지정 시 직전 영업일.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--backfill", type=int, default=0, help="추가 백필 영업일 수")
    parser.add_argument("--healthcheck", action="store_true")
    args = parser.parse_args()

    if args.healthcheck:
        from krx_openapi import healthcheck
        ok = healthcheck()
        sys.exit(0 if ok else 1)

    now_kst = dt.datetime.now(KST).date()
    if args.date:
        target = dt.date.fromisoformat(args.date)
        run_date = now_kst
    else:
        target = prev_business_day(now_kst, 1)
        run_date = now_kst

    try:
        run_scan(target, dry_run=args.dry_run, backfill_days=args.backfill, run_date=run_date)
    except KRXAPIError as e:
        log(f"[FATAL] KRX API 오류: {e}")
        sys.exit(1)
    except Exception as e:
        log(f"[FATAL] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
