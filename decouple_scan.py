"""
decouple_scan.py — 선반영(디커플링) 역발상 스캐너 + 슬랙 카드 (테스트 v0.1)

로직 (07:30 KST · 한국장 개장 전):
  US 섹터가 상승추세(20MA 위)인데 어젯밤 장대음봉 급락(≤ -3%)  +  KR 동조 테마가
  어제(D-1) 먼저 하락(≤ -2%, 선반영)  +  4종 체크 통과  →  🟢 오늘 아침 공포매물 역발상 후보.
  반대로 KR이 먼저 신고가(상승 선반영) + US는 소폭 상승만 →  🔴 추격매수 주의.

  4종 체크 (단테 기법 정합):
    ① 주도주/재료 생존   = US 섹터 추세 유효(20MA 위)
    ② 5MA 첫 이탈 + 10MA 생존 (떨어지는 칼날 vs 단기 눌림 구별)
    ③ 하락 거래량 < 직전 급등 거래량의 절반 (매도 미소진 = 안 끝난 차트)
    ④ 다음 지지선 존재 (10/20/60MA ±3% 근접)

데이터: US=fetch_us_sectors.py 산출(JSON) · KR=KRX OpenAPI 일별 캐시(krx_openapi)
산출: data/decouple_signal_{YYYY-MM-DD}.json  +  콘솔 카드  +  (--slack) 슬랙 발송

사용:
  python decouple_scan.py                # US fetch + KR scan + 카드 출력 (저장)
  python decouple_scan.py --dry-run      # 저장 X
  python decouple_scan.py --slack        # 슬랙 웹훅 발송 (SLACK_WEBHOOK_URL)
  python decouple_scan.py --no-fetch     # 기존 us_overnight_{today}.json 재사용
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

try:
    from krx_openapi import KRXClient, KRXAPIError
except ImportError as e:
    print(f"[ERROR] krx_openapi 로드 실패: {e}")
    sys.exit(1)

import fetch_us_sectors

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
CONFIG = ROOT / "config" / "decouple_map.json"
KST = dt.timezone(dt.timedelta(hours=9))
KR_HISTORY_DAYS = 30  # 5/10/20/60 이평 + 거래량 lookback 여유


def log(msg: str) -> None:
    ts = dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def load_webhook() -> str | None:
    for env_path in (ROOT / ".env", ROOT.parent / ".env"):
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("SLACK_WEBHOOK_URL="):
                    return line.partition("=")[2].strip().strip('"').strip("'")
    return os.environ.get("SLACK_WEBHOOK_URL")


def business_days_back(d: dt.date, n: int) -> list[dt.date]:
    days, cur = [], d
    while len(days) < n:
        if cur.weekday() < 5:
            days.append(cur)
        cur -= dt.timedelta(days=1)
    return days


def prev_business_day(d: dt.date) -> dt.date:
    """d 직전(엄격히 이전) 영업일. 07:30엔 한국장 미개장이므로 어제=D-1이 최신 확정봉."""
    cur = d - dt.timedelta(days=1)
    while cur.weekday() >= 5:
        cur -= dt.timedelta(days=1)
    return cur


# =============================================================================
# KR OHLCV 로딩 (KRX 일별 캐시 재사용 — 매핑 종목만 필터)
# =============================================================================

def load_kr_series(codes: set[str], latest: dt.date) -> dict[str, dict]:
    """매핑 종목 코드의 최근 KR_HISTORY_DAYS 영업일 OHLCV 시계열.

    반환: { code: {"name","date":[...],"close":[...],"open","high","low","volume"} }
    """
    client = KRXClient()
    target_dates = sorted(business_days_back(latest, KR_HISTORY_DAYS))  # 오래된→최신
    series: dict[str, dict] = {}
    for d in target_dates:
        bas = d.strftime("%Y%m%d")
        for market in ("KOSPI", "KOSDAQ"):
            try:
                rows = client.daily_ohlcv_raw(bas, market)
            except KRXAPIError as e:
                log(f"  [WARN] {bas} {market} 호출 실패: {e}")
                continue
            for r in rows:
                code = r.get("code")
                if code not in codes:
                    continue
                s = series.setdefault(code, {"name": r.get("name"), "date": [],
                                             "close": [], "open": [], "high": [],
                                             "low": [], "volume": []})
                s["name"] = r.get("name") or s["name"]
                s["date"].append(d.isoformat())
                for k in ("open", "high", "low", "close", "volume"):
                    v = r.get(k)
                    s[k].append(float(v) if v is not None else None)
    return series


# =============================================================================
# KR 종목 4종 체크 평가
# =============================================================================

def _ma(vals: list[float], n: int, end: int | None = None) -> float | None:
    end = len(vals) if end is None else end
    seg = [v for v in vals[end - n:end] if v is not None]
    return sum(seg) / len(seg) if len(seg) == n else None


def eval_kr_stock(s: dict, params: dict) -> dict:
    """단일 KR 종목 → 어제(D-1) 기준 선반영/4종체크 평가."""
    closes = s["close"]
    highs = s["high"]
    vols = s["volume"]
    if len([c for c in closes if c is not None]) < 21:
        return {"ok": False, "reason": "데이터 21일 미만"}

    close = closes[-1]
    prev = closes[-2]
    if close is None or prev is None or prev == 0:
        return {"ok": False, "reason": "종가 결손"}

    ma5 = _ma(closes, 5)
    ma5_prev = _ma(closes, 5, end=len(closes) - 1)
    ma10 = _ma(closes, 10)
    ma20 = _ma(closes, 20)
    ma60 = _ma(closes, 60) if len([c for c in closes if c is not None]) >= 60 else None

    chg = (close / prev - 1) * 100

    # ② 5MA 첫 이탈 + 10MA 생존
    first_break_ma5 = bool(ma5 and ma5_prev and close < ma5 and prev >= ma5_prev)
    ma10_alive = bool(ma10 and close >= ma10 * 0.99)

    # ③ 하락 거래량 < 직전 급등 거래량 절반
    lb = params["vol_surge_lookback"]
    prior = [v for v in vols[-(lb + 1):-1] if v is not None]
    vol_last = vols[-1]
    surge = max(prior) if prior else None
    down_vol_ok = bool(vol_last and surge and vol_last < surge * params["vol_down_half_ratio"])
    vol_vs_surge = round(vol_last / surge, 2) if (vol_last and surge) else None

    # ④ 다음 지지선: 받을 다음 지지가 가격 바로 아래(쿠션) 또는 살짝 위(눌림 중)에 존재
    #   dist>0 = 가격이 MA 위(=MA가 아래 지지) / dist<0 = 가격이 MA 하회(상단 저항·근접 반등)
    win = params["support_window_pct"]
    supports = []
    for label, mv in (("10MA", ma10), ("20MA", ma20), ("60MA", ma60)):
        if mv:
            dist = (close / mv - 1) * 100
            # MA가 가격 아래 win*2(≈6%) 이내 쿠션 OR 가격이 MA를 win% 이내로 하회(근접)
            if -win <= dist <= win * 2:
                supports.append((label, round(mv), round(dist, 2)))
    supports.sort(key=lambda x: abs(x[2]))  # 가장 가까운 지지 우선 표시
    support_ok = len(supports) > 0

    # 선반영 = 어제 KR 먼저 하락
    prereflected = chg <= params["kr_prereflect_drop_pct"]
    # 신고가 = 상승 선반영 (탐욕 경고용)
    recent_high = max([h for h in highs[-20:-1] if h is not None], default=None)
    made_new_high = bool(recent_high and close >= recent_high)

    checks_passed = first_break_ma5 and ma10_alive and down_vol_ok and support_ok

    return {
        "ok": True,
        "name": s["name"],
        "close": round(close),
        "change_pct": round(chg, 2),
        "ma5": round(ma5) if ma5 else None,
        "ma10": round(ma10) if ma10 else None,
        "ma20": round(ma20) if ma20 else None,
        "prereflected": prereflected,
        "made_new_high": made_new_high,
        "check_first_break_ma5": first_break_ma5,
        "check_ma10_alive": ma10_alive,
        "check_down_vol": down_vol_ok,
        "vol_vs_surge": vol_vs_surge,
        "check_support": support_ok,
        "supports": supports,
        "checks_passed": checks_passed,
        "buy_ready": bool(prereflected and checks_passed),
    }


# =============================================================================
# 카드 빌더
# =============================================================================

def build_card(result: dict) -> str:
    d = result["run_date_kst"]
    lines = [f"*🌀 선반영 역발상 브리핑 — {d} 08:15 (장 시작 전)*",
             "_미국장 강세 섹터 ↔ 한국 선반영 비교 · watchlist(자동매매 아님)_", ""]

    buys = result["buy_signals"]
    warns = result["warn_signals"]

    if buys:
        lines.append("*🟢 공포매물 역발상 후보*")
        for b in buys:
            us = b["us"]
            lines.append(
                f"• *{b['theme']}* — US {us['proxy_symbol']} {us['us_avg_change_pct']:+.1f}% "
                f"🔻밤사이급락 (추세 {'▲유효' if us['us_uptrend'] else '▽주의'})")
            for st in b["stocks"]:
                sup = ", ".join(f"{x[0]}({x[2]:+.1f}%)" for x in st["supports"]) or "—"
                lines.append(
                    f"    └ {st['name']} {st['close']:,}원 (어제 {st['change_pct']:+.1f}%) · "
                    f"5MA첫이탈✓ 10MA생존✓ 하락량 {st['vol_vs_surge']}×급등 · 지지 {sup}")
        lines.append("")
    if warns:
        lines.append("*🔴 추격매수 주의 (상승 선반영)*")
        for w in warns:
            us = w["us"]
            names = ", ".join(st["name"] for st in w["stocks"])
            lines.append(f"• *{w['theme']}* — KR 신고가 선반영({names}) vs US {us['proxy_symbol']} "
                         f"{us['us_avg_change_pct']:+.1f}% 소폭 → 추격 자제")
        lines.append("")
    if not buys and not warns:
        lines.append("오늘 선반영 후보 없음 — 한·미 디커플링 신호 미포착.")

    # 섹터 현황 요약
    lines.append("─" * 3)
    lines.append("*📊 섹터 현황 (US 밤사이)*")
    for t in result["themes"]:
        if not t.get("us_available"):
            lines.append(f"• {t['label']}: US 데이터 없음")
            continue
        flag = "🔻급락" if t["us_overnight_drop"] else ("🔺급등" if t["us_overnight_rise"] else "→보합")
        lines.append(f"• {t['label']}: {t['proxy_symbol']} {t['us_avg_change_pct']:+.1f}% {flag} "
                     f"/ KR선반영 {t['kr_prereflect_count']}종")

    # 자가진단: 이상 시 원인 동봉, 정상 시 "작동 이상없음"
    h = result.get("health", {})
    lines.append("─" * 3)
    if h.get("status") == "정상":
        lines.append(f"✅ *작동 이상없음* (US {h.get('us_collected')}/{h.get('us_total')} · "
                     f"KR {h.get('kr_collected')}/{h.get('kr_total')} 수집)")
    else:
        lines.append("⚠️ *작동 이상 — 원인*")
        for iss in h.get("issues", []):
            lines.append(f"  • {iss}")
        lines.append("  _(데이터 누락분은 해당 테마/종목 신호가 누락될 수 있음)_")
    return "\n".join(lines)


def send_slack(text: str, webhook: str) -> bool:
    try:
        r = requests.post(webhook, json={"text": text}, timeout=15)
        return r.status_code == 200
    except requests.RequestException as e:
        log(f"  [WARN] 슬랙 발송 실패: {e}")
        return False


# =============================================================================
# Main
# =============================================================================

def run(dry_run: bool = False, do_slack: bool = False, no_fetch: bool = False) -> dict:
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    params = cfg["params"]
    today = dt.datetime.now(KST).date()

    # --- US 수급 ---
    us_path = DATA_DIR / f"us_overnight_{today.isoformat()}.json"
    if no_fetch and us_path.exists():
        us = json.loads(us_path.read_text(encoding="utf-8"))
        log(f"US 재사용: {us_path}")
    else:
        us = fetch_us_sectors.run(dry_run=dry_run)
    us_themes = {t["key"]: t for t in us["themes"]}
    quotes = us.get("quotes", {})

    # --- US 자가진단: 요청 대비 수집 ---
    diag_issues: list[str] = []
    req_us = []
    for t in cfg["themes"]:
        req_us += t["us"].get("etf", []) + t["us"].get("leaders", [])
    req_us = list(dict.fromkeys(req_us))
    failed_us = [s for s in req_us if s not in quotes]
    if not quotes:
        diag_issues.append("US 데이터 전체 수신 실패 (Yahoo API/네트워크 — 차단·rate-limit 의심)")
    elif failed_us:
        diag_issues.append(f"US 일부 수집 실패 {len(failed_us)}/{len(req_us)}종: {', '.join(failed_us)}")

    # --- KR OHLCV (매핑 종목만 · 예외 시 원인 수집 후 계속) ---
    all_codes = {kr["code"] for t in cfg["themes"] for kr in t["kr"]}
    kr_latest = prev_business_day(today)  # 직전 영업일 = 어제(D-1, 최신 확정봉)
    log(f"KR OHLCV 로딩 (어제={kr_latest} 기준, {len(all_codes)}종)")
    try:
        kr_series = load_kr_series(all_codes, kr_latest)
    except KRXAPIError as e:
        kr_series = {}
        diag_issues.append(f"KRX API 실패 (인증/네트워크): {e}")
        log(f"  [ERROR] KRX 로딩 실패: {e}")
    except Exception as e:
        kr_series = {}
        diag_issues.append(f"KR 로딩 예외: {e}")
        log(f"  [ERROR] KR 로딩 예외: {e}")
    log(f"KR 시계열 확보: {len(kr_series)}/{len(all_codes)}종")
    missing = [c for c in all_codes if c not in kr_series]
    if not kr_series:
        diag_issues.append("KR 데이터 전체 수신 실패 (KRX API/네트워크)")
    elif missing:
        names = []
        for t in cfg["themes"]:
            for kr in t["kr"]:
                if kr["code"] in missing:
                    names.append(f"{kr['name']}({kr['code']})")
        diag_issues.append(f"KR 캐시 미발견 {len(missing)}종: {', '.join(names)}")
        log(f"  [주의] 캐시 미발견 코드(검증 필요): {', '.join(sorted(missing))}")

    # --- 테마별 신호 산출 ---
    themes_out, buy_signals, warn_signals = [], [], []
    for t in cfg["themes"]:
        us_t = us_themes.get(t["key"], {})
        proxy_sym = us_t.get("proxy_symbol")
        proxy_q = quotes.get(proxy_sym, {})
        us_uptrend_20 = bool(proxy_q.get("above_ma20"))  # 추세는 20MA(밤사이 급락에 덜 흔들림)
        us_drop = bool(us_t.get("us_overnight_drop"))
        us_rise = bool(us_t.get("us_overnight_rise"))

        kr_evals = []
        for kr in t["kr"]:
            s = kr_series.get(kr["code"])
            if not s:
                kr_evals.append({"code": kr["code"], "name": kr["name"],
                                 "ok": False, "reason": "캐시 없음"})
                continue
            ev = eval_kr_stock(s, params)
            ev["code"] = kr["code"]
            kr_evals.append(ev)

        prereflect_stocks = [e for e in kr_evals if e.get("buy_ready")]
        high_stocks = [e for e in kr_evals if e.get("ok") and e.get("made_new_high")]

        us_info = {"proxy_symbol": proxy_sym,
                   "us_uptrend": us_uptrend_20,
                   "us_avg_change_pct": us_t.get("us_avg_change_pct"),
                   "us_overnight_drop": us_drop, "us_overnight_rise": us_rise}

        # 🟢 BUY: US 추세유효 + 밤사이급락 + KR 선반영+4종통과 종목 존재
        if us_uptrend_20 and us_drop and prereflect_stocks:
            buy_signals.append({"theme": t["label"], "us": us_info, "stocks": prereflect_stocks})
        # 🔴 WARN: US 소폭상승 + KR 신고가 선반영
        if us_rise and high_stocks:
            warn_signals.append({"theme": t["label"], "us": us_info, "stocks": high_stocks})

        themes_out.append({
            "key": t["key"], "label": t["label"],
            "us_available": us_t.get("us_available", False),
            "proxy_symbol": proxy_sym,
            "us_uptrend": us_uptrend_20,
            "us_avg_change_pct": us_t.get("us_avg_change_pct"),
            "us_overnight_drop": us_drop, "us_overnight_rise": us_rise,
            "kr_prereflect_count": len(prereflect_stocks),
            "kr_evals": kr_evals,
        })

    # KR 데이터 부족/결손 종목(캐시는 있으나 21일 미만 등)
    short_data = [
        (e.get("name") or e["code"])
        for t in themes_out for e in t["kr_evals"]
        if not e.get("ok") and e.get("reason") and ("21일" in e["reason"] or "결손" in e["reason"])
    ]
    if short_data:
        diag_issues.append(f"KR 데이터 부족/결손: {', '.join(short_data)}")

    health = {
        "status": "정상" if not diag_issues else "이상",
        "issues": diag_issues,
        "us_collected": len(quotes), "us_total": len(req_us),
        "kr_collected": len(kr_series), "kr_total": len(all_codes),
    }

    result = {
        "run_date_kst": today.isoformat(),
        "kr_latest": kr_latest.isoformat(),
        "version": cfg["version"],
        "params": params,
        "health": health,
        "themes": themes_out,
        "buy_signals": buy_signals,
        "warn_signals": warn_signals,
    }

    card = build_card(result)
    print("\n" + "=" * 60)
    print(card)
    print("=" * 60 + "\n")
    log(f"신호 요약: 🟢 BUY {len(buy_signals)}테마 / 🔴 WARN {len(warn_signals)}테마")

    if not dry_run:
        out = DATA_DIR / f"decouple_signal_{today.isoformat()}.json"
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"저장: {out}")

    if do_slack:
        wh = load_webhook()
        if not wh:
            log("  [WARN] SLACK_WEBHOOK_URL 없음 — 발송 스킵")
        elif send_slack(card, wh):
            log("✅ 슬랙 발송 완료")
        else:
            log("❌ 슬랙 발송 실패")

    log("=== 완료 ===")
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--slack", action="store_true")
    ap.add_argument("--no-fetch", action="store_true")
    args = ap.parse_args()
    try:
        run(dry_run=args.dry_run, do_slack=args.slack, no_fetch=args.no_fetch)
    except KRXAPIError as e:
        log(f"[FATAL] KRX API: {e}")
        sys.exit(1)
    except Exception as e:
        log(f"[FATAL] {e}")
        sys.exit(1)
