# -*- coding: utf-8 -*-
"""
toss_api_snapshot.py — 16:30·19:00 full 회차 시세·수급 = 토스 Open API (2026-10-21, toss_capture.py 웹 캡처 대체)

대상 종목 = toss_capture.resolve_stocks() 그대로 (AI단테.md §6 활성 + stocks.json 동기화 + bowl3_watch).
산출 (claude_dante_analyze.js 가 읽는 자리 그대로):
  data/prices_{date}.json            — toss_capture 와 같은 스키마 (price·change_won·change_pct 문자열, execution_strength_pct = null: API 미제공)
  data/api_chart/{종목}_{date}.txt     — 일봉 60개 + 5·20·60·112·224일선 (차트 PNG 대신 Claude 가 Read)
  data/api_chart/{종목}_수급_{date}.txt — 투자자(외국인·기관·개인)·프로그램·공매도 최근 10일 (투자자공매 PNG 대신)
  data/api_snapshot_{date}.json      — 당일 봉·외국인·프로그램 순매수 수치 (분석기가 entry_conditions 에 기계 주입)

⚠️ 토스 토큰은 클라이언트당 1개 — stock-usa 스크립트와 동시 실행 금지. 웹 화면(tossinvest.com)은 쓰지 않는다.

사용:
  python toss_api_snapshot.py            # 오늘 날짜로 저장
  python toss_api_snapshot.py --dry-run  # 대상 종목만 출력
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT.parent / "jck_stock-usa"))
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

DATA = ROOT / "data"
CHART_DIR = DATA / "api_chart"
NB = lambda r, k: float((r.get(k) or {}).get("netBuyVolume") or 0) if r else None


def ma(c: list[float], n: int, i: int):
    return round(sum(c[i - n + 1:i + 1]) / n) if i + 1 >= n else None


def daily(code: str, need: int = 290) -> list[list]:
    """[[YYYY-MM-DD, o, h, l, c, v], ...] 오름차순 (오늘 장중 봉 포함)."""
    import toss
    bars, before = {}, None
    while len(bars) < need:
        r = toss.get("/api/v1/candles", tps=20, symbol=code, interval="1d", count=200, before=before)
        for x in r["candles"]:
            bars[x["timestamp"][:10]] = [x["timestamp"][:10], *(float(x[k]) for k in ("openPrice", "highPrice", "lowPrice", "closePrice", "volume"))]
        before = r.get("nextBefore")
        if not before or not r["candles"]:
            break
    return sorted(bars.values())


def records(code: str, kind: str) -> list[dict]:
    import toss
    return toss.get(f"/api/v1/stocks/{code}/{kind}")["records"]   # 최신순 10건


def krx_prev(today: str) -> dict:
    """KRX 캐시 마지막 확정일(오늘 전) 종가 {code: close} — 토스 일봉은 NXT 포함 통합시세라 전일 종가가 KRX 와 다를 수 있어 등락은 KRX 기준."""
    cache = DATA / "krx_ohlcv_cache"
    days = sorted({f.name[:8] for f in cache.glob("*_*.json") if f.name[:8] < today.replace("-", "")})
    out = {}
    for f in cache.glob(f"{days[-1]}_*.json") if days else []:
        for r in json.loads(f.read_text(encoding="utf-8")).get("rows") or []:
            if r.get("close"):
                out[r["code"]] = float(r["close"])
    return out


def one(s: dict, today: str, prev_krx: dict) -> tuple[dict, dict]:
    b = daily(s["code"])
    c = [x[4] for x in b]
    i = len(b) - 1
    d, o, h, l, cl, v = b[i]
    prev = prev_krx.get(s["code"]) or (b[i - 1][4] if i else cl)
    v20 = sum(x[5] for x in b[i - 20:i]) / 20 if i >= 20 else None
    inv, prg, sht = records(s["code"], "investor-trading"), records(s["code"], "program-trades"), records(s["code"], "short-selling")
    inv0 = inv[0] if inv and inv[0]["date"] == today else None     # 당일 행 없으면 None → 분석기에서 unknown
    prg0 = prg[0] if prg and prg[0]["date"] == today else None
    lines = [f"# {s['name']}({s['code']}) 일봉 — 토스 Open API {dt.datetime.now():%Y-%m-%d %H:%M} 조회 · 마지막 봉 {d}{' (장중)' if d == today and dt.datetime.now().hour < 16 else ''} · 토스 통합시세(NXT 포함)라 KRX 종가와 다를 수 있음",
             f"20일 평균 거래량(오늘 제외) {v20:,.0f}주" if v20 else "",
             "날짜,시가,고가,저가,종가,거래량,MA5,MA20,MA60,MA112,MA224"]
    for k in range(max(0, i - 59), i + 1):
        lines.append(",".join([b[k][0], *(f"{x:.0f}" for x in b[k][1:])] + [str(ma(c, n, k) or "") for n in (5, 20, 60, 112, 224)]))
    (CHART_DIR / f"{s['name']}_{today}.txt").write_text("\n".join(x for x in lines if x is not None), encoding="utf-8")
    fl = [f"# {s['name']}({s['code']}) 수급 — 토스 Open API · 최신순 · 단위 주",
          f"당일({today}) 투자자 행: {'있음' if inv0 else '없음(아직 미집계)'} · 당일 프로그램 행: {'있음' if prg0 else '없음(아직 미집계)'}",
          "## 투자자별 순매수", "날짜,외국인,기관,개인"]
    fl += [f"{r['date']},{NB(r, 'foreigner'):.0f},{NB(r, 'institution'):.0f},{NB(r, 'individual'):.0f}" for r in inv]
    fl += ["## 프로그램 순매수", "날짜,차익,비차익"]
    fl += [f"{r['date']},{NB(r, 'arbitrage'):.0f},{NB(r, 'nonArbitrage'):.0f}" for r in prg]
    fl += ["## 공매도", "날짜,공매도 수량,공매도 비중%"]
    fl += [f"{r['date']},{r.get('shortSellingVolume') or ''},{r.get('shortSellingVolumeRate') or ''}" for r in sht]
    (CHART_DIR / f"{s['name']}_수급_{today}.txt").write_text("\n".join(fl), encoding="utf-8")
    chg = cl - prev
    price = {"code": s["code"], "tag": s.get("tag"), "price": f"{cl:,.0f}", "change_won": f"{chg:+,.0f}",
             "change_pct": f"{chg / prev * 100:+.2f}" if prev else None,
             "foreign_flow_direction": None, "foreign_flow_label": None, "execution_strength_pct": None}
    snap = {"code": s["code"], "bar_date": d, "open": o, "high": h, "low": l, "close": cl, "volume": v, "v20": v20,
            "foreign_net_today": NB(inv0, "foreigner") if inv0 else None,
            "program_net_today": (NB(prg0, "arbitrage") + NB(prg0, "nonArbitrage")) if prg0 else None}
    return price, snap


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    from toss_capture import resolve_stocks   # §6 파싱 + stocks.json 동기화 (캡처 회차와 같은 대상)
    stocks = resolve_stocks()
    today = dt.date.today().isoformat()
    if a.dry_run:
        print(f"[DRY-RUN] {len(stocks)}종 — 조회 생략")
        return
    CHART_DIR.mkdir(parents=True, exist_ok=True)
    prices, snaps, fail = {}, {}, 0
    prev_krx = krx_prev(today)
    for s in stocks:
        try:
            prices[s["name"]], snaps[s["name"]] = one(s, today, prev_krx)
            sn = snaps[s["name"]]
            print(f"- [{s.get('tag')}] {s['name']}({s['code']}) {prices[s['name']]['price']} ({prices[s['name']]['change_pct']}%) "
                  f"외국인 {sn['foreign_net_today']} · 프로그램 {sn['program_net_today']}")
        except (Exception, SystemExit) as e:
            fail += 1
            print(f"- [ERR] {s['name']}({s['code']}): {str(e)[:160]}")
    (DATA / f"prices_{today}.json").write_text(json.dumps({"date": today, "source": "toss_open_api", "stocks": prices}, ensure_ascii=False, indent=2), encoding="utf-8")
    (DATA / f"api_snapshot_{today}.json").write_text(json.dumps({"date": today, "stocks": snaps}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"완료: 성공 {len(prices)} · 실패 {fail}")
    if not prices and stocks:
        sys.exit(1)


if __name__ == "__main__":
    main()
