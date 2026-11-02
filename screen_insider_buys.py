"""screen_insider_buys.py — 👔 임원매수 (08:30 Slack 「📋 노출 종목」 한 줄, 2026-10-16 사용자 지시)

직전 30일 임원(간부진) 장내매수 + 차트(5일선 장기이평 돌파 후 눌림) + 3개년 흑자 → 코스피·코스닥 각 5종(매수금액순).
  차트(2026-10-16 사용자 선택 B안): 최근 7거래일 안에(CROSS_WITHIN) 5일선이 112 또는 224일선을 상향 돌파했고,
  돌파 후 고점 대비 종가 -5% 이상 눌렸으며, 종가가 그 장기선의 -3%~+5% 안(지지선 근처).
  재무: 2023~2025 당기순이익 3년 모두 흑자(증가 여부는 안 봄).
매수 판정 = dart_openapi.insider_buys (회장·대표·대주주·지분 5%+·사외이사 제외, 원문 「장내매수(+)」 행만).
표시 전용 — exposure.js 에 넣지 않는다(넣으면 auto_intake.js 가 라인업에 자동 편입).

출력: data/insider_buys_{YYYY-MM-DD}.json  {date, kospi:[{code,name,theme,chart,amount,people}], kosdaq:[...]}
"""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

from dart_openapi import DARTClient

DATA_DIR = Path(__file__).parent / "data"
LOOKBACK_DAYS = 30
TOP_N = 5
CROSS_WITHIN = 7           # 돌파 탐색 거래일 (60→7, 2026-10-16 사용자 지시 「돌파 후 눌림은 7일 이내」)
PULLBACK_MIN_PCT = -5.0    # 돌파 후 고점 대비 종가
BAND = (-3.0, 5.0)         # 종가 / 장기선 - 1 (%)
LONG_MAS = (112, 224)


def log(msg: str) -> None:
    print(f"[{dt.datetime.now():%H:%M:%S}] {msg}", flush=True)


def theme_of() -> dict[str, str]:
    """종목 → 테마 1개 (구성종목 가장 적은 것 = slack_send.js stockTag 와 같은 규칙)."""
    try:
        themes = json.loads((DATA_DIR / "naver_theme_map.json").read_text(encoding="utf-8"))["themes"]
    except Exception:
        return {}
    m: dict[str, str] = {}
    for name, codes in sorted(themes.items(), key=lambda a: len(a[1])):
        for c in codes:
            m.setdefault(c, re.sub(r"\s*\([^)]*\)$", "", name))
    return m


def load_closes(codes: set[str]) -> dict[str, list[int]]:
    """KRX 캐시 → 종목별 종가(오래된→최신). 휴장일 파일은 rows 0개라 자연히 빠짐."""
    ser: dict[str, dict[str, int]] = {c: {} for c in codes}
    need = (CROSS_WITHIN + max(LONG_MAS)) * 3  # 시장 2파일/일 + 휴장일 파일(rows 0) 여유
    for f in sorted((DATA_DIR / "krx_ohlcv_cache").glob("*_KOS*.json"))[-need:]:
        for r in json.loads(f.read_text(encoding="utf-8")).get("rows", []):
            if r.get("code") in ser and r.get("close"):
                ser[r["code"]][r["date"]] = r["close"]
    return {c: [v for _, v in sorted(d.items())] for c, d in ser.items()}


def pullback_after_cross(cl: list[int]) -> dict | None:
    """5일선이 장기선을 상향 돌파 후 눌림이면 {ma, days_ago, dd, gap}, 아니면 None."""
    L = len(cl)
    sma = lambda n, i: sum(cl[i - n + 1:i + 1]) / n
    for n in LONG_MAS:
        if L < n + CROSS_WITHIN + 1:
            continue
        cross = None
        for i in range(L - CROSS_WITHIN, L):
            if sma(5, i) > sma(n, i) and sma(5, i - 1) <= sma(n, i - 1):
                cross = i
        if cross is None:
            continue
        dd = (cl[-1] / max(cl[cross:]) - 1) * 100
        gap = (cl[-1] / sma(n, L - 1) - 1) * 100
        if dd <= PULLBACK_MIN_PCT and BAND[0] <= gap <= BAND[1]:
            return {"ma": n, "days_ago": L - 1 - cross, "dd": round(dd, 1), "gap": round(gap, 1)}
    return None


def main() -> None:
    cli = DARTClient()
    today = dt.date.today()
    bgn = (today - dt.timedelta(days=LOOKBACK_DAYS)).strftime("%Y%m%d")
    reports, page = [], 1
    while True:  # D002 = 임원ㆍ주요주주특정증권등소유상황보고서
        r = cli._get("list.json", {"bgn_de": bgn, "end_de": today.strftime("%Y%m%d"),
                                   "pblntf_detail_ty": "D002", "page_no": page, "page_count": 100})
        reports += r.get("list", [])
        if page >= int(r.get("total_page", 1) or 1):
            break
        page += 1
    corps = {x["stock_code"]: (x["corp_name"], x["corp_cls"]) for x in reports
             if x.get("corp_cls") in ("Y", "K") and x.get("stock_code")}
    log(f"소유상황보고서 {len(reports)}건 · 대상 {len(corps)}사")

    themes = theme_of()
    bsns_year = str(today.year - 1)  # ponytail: 4월 전엔 전년 사업보고서 미제출 → 해당 종목은 재무 None 으로 빠짐
    out = {"date": today.isoformat(), "kospi": [], "kosdaq": []}
    bought = {}
    for code, (name, cls) in corps.items():
        try:
            buys = cli.insider_buys(code, days=LOOKBACK_DAYS)
        except Exception as e:
            log(f"  [skip] {name}({code}): {e}")
            continue
        if buys:
            bought[code] = (name, cls, buys)
    closes = load_closes(set(bought))
    log(f"임원매수 {len(bought)}종 → 차트·재무 판정")
    for code, (name, cls, buys) in bought.items():
        chart = pullback_after_cross(closes.get(code) or [])
        if not chart:
            continue
        try:
            ni = cli.financial_full(cli.to_corp_code(code), bsns_year).get("net_inc") or [None] * 3
        except Exception as e:
            log(f"  [skip] {name}({code}): {e}")
            continue
        if None in ni or min(ni) <= 0:
            continue
        out["kospi" if cls == "Y" else "kosdaq"].append({
            "code": code, "name": name, "theme": themes.get(code), "chart": chart,
            "amount": sum(b["amount"] for b in buys), "people": len({b["repror"] for b in buys})})
    for k in ("kospi", "kosdaq"):
        out[k] = sorted(out[k], key=lambda x: -x["amount"])[:TOP_N]
        log(f"{k}: " + " · ".join(x["name"] for x in out[k]))
    (DATA_DIR / f"insider_buys_{today.isoformat()}.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
