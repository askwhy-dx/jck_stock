# -*- coding: utf-8 -*-
"""
screen_danta_close.py — ⚡ 단타 2종 (2026-10-21 사용자 지시 「테스트 말고 적용」) · 토스 Open API 실시간

  --close (15:10) 🌙 오버나잇 = 종가 베팅 (대시보드 「오버나잇」 탭)
      조건 : 15:10 현재 전일 대비 +10% ~ +29.5% · 고가권(고가 − 현재가 ≤ 당일 범위의 10%) · 당일 거래량 ≥ 20일 평균 2.7배 · 시총 1,000억+
      국면 필터 없음 (10-21 사용자 지시로 제외 — 시장 20일·당일 등락은 표시만)
      매매 : 15:20~15:30 종가 단일가 매수 → 다음 날 시가 매도
      정렬 : 거래량 배수 순 최대 5종
  --open (09:15) ⚡ 단타 매수 추천 (대시보드 「단타」 탭) — 전 영업일 오버나잇 종목 중
      조건 : 09:15 가격 > 오늘 시가 · 09:00~09:15 거래량 ≥ 20일 평균 20% · 당일 프로그램(차익+비차익) 순매수 > 0
             · 판매대기(호가 매도잔량 합) > 구매대기(매수잔량 합)   — 10-23 사용자 지시: 외+기는 장중 미집계라 프로그램으로 교체, 호가 조건 추가
             (프로그램 오늘 행 없음·호가 빈 경우 = 판정 불가 = 미충족)
      매매 : 09:15 매수 → 당일 종가 매도

근거(일봉 백테스트 2024-10~2026-10, 종가 매수 → 다음 날 시가): +10~29.5%·고가 마감·거래량 3배 1,122건 평균 +0.75%(비용 0.25% 뒤 +0.50%),
  지수 20일 −5% 이하 국면 −0.14% · 당일 지수 −1% 이하 −0.18% → 국면 필터. 15:10 판정·09:15 규칙은 **백테스트 안 함**(사용자 지시로 바로 적용).
⚠️ 토스 토큰 1개 — stock-usa 스크립트와 동시 실행 금지. 토스 시세는 NXT 포함 통합시세.

손절가(2026-10-27): 🌙 오버나잇 = 15:10 가격 −2%(ON_STOP, 호가 단위 내림) → picks[].stop · Slack 「거래량 N배, 손절가 X원」 · 탭 손절가 열. Slack 「매매:」 줄은 10-27 삭제.
  근거 backtest_danta_stop.py = 기술정리 §5.3c (오버나잇은 손절보다 시가 매도가 낫고, 단타는 손절이 손실만 줄임).

출력: data/danta_close_{date}.json · data/danta_open_{date}.json → build_danta_tab.js · Slack 1장
사용: python screen_danta_close.py --close|--open [--dry-run] [--no-slack]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"
CACHE = DATA / "krx_ohlcv_cache"
sys.path.insert(0, str(ROOT.parent / "jck_stock-usa"))
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

CHG_LO, CHG_HI, TOP_TAIL, VOL_X, CAP = 0.10, 0.295, 0.10, 2.7, 1e11
OPEN_VOL, TOPN = 0.20, 5
ON_STOP = 0.02   # 🌙 손절가 = 15:10 가격 −2% (호가 단위 내림, 10-27 Slack·탭 표시)
KST = dt.timezone(dt.timedelta(hours=9))
log = lambda m: print(f"[danta_close] {m}", flush=True)


def stop_price(p: float) -> int:
    """p × (1 − ON_STOP) 를 KRX 호가 단위로 내림 (auto_intake.js tick 과 같은 표)."""
    s = p * (1 - ON_STOP)
    t = 1 if s < 2000 else 5 if s < 5000 else 10 if s < 20000 else 50 if s < 50000 else 100 if s < 200000 else 500 if s < 500000 else 1000
    return int(s // t * t)


def krx_recent(n_days: int = 21) -> tuple[list[str], dict]:
    """KRX 캐시 최근 n 영업일 {code: [(date, close, volume, cap, name), ...]} (오래된 → 최신)."""
    today = dt.datetime.now(KST).strftime("%Y%m%d")
    files = sorted({f.name[:8] for f in CACHE.glob("*_*.json") if f.name[:8] < today})[-(n_days + 15):]   # 연휴 날짜는 빈 파일 → 넉넉히 읽고 거른다
    rows = {}
    for d in files:
        for f in CACHE.glob(f"{d}_*.json"):
            for r in json.loads(f.read_text(encoding="utf-8")).get("rows") or []:
                if r.get("close"):
                    rows.setdefault(r["code"], []).append((d, float(r["close"]), float(r.get("volume") or 0), float(r.get("market_cap") or 0), r["name"]))
    days = sorted({x[0] for rs in rows.values() for x in rs})[-n_days:]
    rows = {c: v for c, rs in rows.items() if (v := [x for x in rs if x[0] >= days[0]])}
    return days, rows


def market_r20(days: list[str], rows: dict) -> float:
    """시총 가중 지수 대용: 최근 20 영업일 누적 수익률 (전일까지)."""
    idx = 1.0
    for a, b in zip(days[:-1], days[1:]):
        num = den = 0.0
        for rs in rows.values():
            m = {x[0]: x for x in rs}
            if a in m and b in m and m[a][3] >= CAP and m[a][1] > 0:
                num += m[a][3] * (m[b][1] / m[a][1] - 1)
                den += m[a][3]
        idx *= 1 + (num / den if den else 0)
    return idx - 1


def slack(text: str, dry: bool):
    if dry:
        log("Slack 생략(--no-slack/--dry-run)\n" + text)
        return
    env = {}
    for line in (ROOT.parent / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    url = env.get("SLACK_WEBHOOK_URL")
    if not url:
        log("SLACK_WEBHOOK_URL 없음 — 생략")
        return
    body = json.dumps({"username": env.get("SLACK_USERNAME") or "단테 자동분석", "icon_emoji": env.get("SLACK_ICON_EMOJI") or ":chart_with_upwards_trend:", "text": text}).encode()
    with urllib.request.urlopen(urllib.request.Request(url, body, {"Content-Type": "application/json"}), timeout=20) as r:
        log(f"Slack {r.status}")


def program_net(code: str, iso: str):
    """당일 프로그램(차익+비차익) 순매수 주. 오늘 행 없으면 None. 외+기는 장중 미집계(15:36·다음 날 06:50 반영)라 10-23 교체."""
    import toss
    rec = [r for r in toss.get(f"/api/v1/stocks/{code}/program-trades")["records"] if r["date"] == iso]
    return sum(float((rec[0].get(k) or {}).get("netBuyVolume") or 0) for k in ("arbitrage", "nonArbitrage")) if rec else None


def book_totals(code: str):
    """(판매대기, 구매대기) = 호가 매도·매수 잔량 합. 호가 비면 (None, None)."""
    import toss
    b = toss.get("/api/v1/orderbook", symbol=code)
    ask, bid = (sum(float(x["volume"]) for x in b.get(k) or []) for k in ("asks", "bids"))
    return (ask, bid) if ask and bid else (None, None)


def run_close(dry: bool, no_slack: bool):
    import toss
    today = dt.datetime.now(KST).strftime("%Y-%m-%d")
    days, rows = krx_recent()
    uni = {c: rs for c, rs in rows.items() if rs and rs[-1][0] == days[-1] and rs[-1][3] >= CAP and len(rs) >= 21}
    r20 = market_r20(days, rows)
    price = {}
    codes = list(uni)
    for i in range(0, len(codes), 200):
        for x in toss.get("/api/v1/prices", tps=15, symbols=",".join(codes[i:i + 200])):
            if x.get("lastPrice"):
                price[x["symbol"]] = float(x["lastPrice"])
    num = den = 0.0
    for c, p in price.items():
        prev, cap = uni[c][-1][1], uni[c][-1][3]
        num += cap * (p / prev - 1)
        den += cap
    r1 = num / den if den else 0.0
    regime_ok = True   # 국면 필터 제외 (2026-10-21 사용자 지시)
    log(f"KRX {days[-1]} 기준 {len(price)}종 시세 · 시장 20일 {r20:+.1%} · 당일 {r1:+.2%} → 표시만")
    picks = []
    for c, p in price.items():
        prev = uni[c][-1][1]
        chg = p / prev - 1
        if not (CHG_LO <= chg < CHG_HI):
            continue
        k = toss.get("/api/v1/candles", tps=20, symbol=c, interval="1d", count=1)["candles"]
        if not k or k[0]["timestamp"][:10] != today:
            continue
        o, h, l, cl, v = (float(k[0][f]) for f in ("openPrice", "highPrice", "lowPrice", "closePrice", "volume"))
        v20 = sum(x[2] for x in uni[c][-20:]) / 20
        rg = h - l
        if rg <= 0 or (h - p) / rg > TOP_TAIL or not v20 or v / v20 < VOL_X:
            continue
        picks.append(dict(code=c, name=uni[c][-1][4], prev=round(prev), price=round(p), chg_pct=round(chg * 100, 1),
                          high=round(h), vol_x=round(v / v20, 1), v20=round(v20), stop=stop_price(p)))
    picks.sort(key=lambda r: -r["vol_x"])
    out = {"date": today, "time": dt.datetime.now(KST).strftime("%H:%M"), "anchor": days[-1], "market_r20_pct": round(r20 * 100, 2),
           "market_today_pct": round(r1 * 100, 2), "regime_ok": regime_ok, "picks": picks[:TOPN], "passed": len(picks)}
    for r in out["picks"]:
        log(f"  ⚡ {r['name']}({r['code']}) {r['price']:,} ({r['chg_pct']:+}%) 거래량 {r['vol_x']}배")
    if dry:
        log("--dry-run: 저장·Slack 생략")
        return
    (DATA / f"danta_close_{today}.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    head = f"*🌙 오버나잇 {out['time']}* — 시장 20일 {r20:+.1%} · 당일 {r1:+.2%} (참고)"
    if not out["picks"]:
        text = f"{head}\n해당 종목 없음"
    else:
        text = head + "\n" + "\n".join(f"{i + 1}. *{r['name']}* {r['price']:,}원 ({r['chg_pct']:+}%) · 거래량 {r['vol_x']}배, 손절가 {r['stop']:,}원" for i, r in enumerate(out["picks"]))
    slack(text, no_slack)


def run_open(dry: bool, no_slack: bool):
    import toss
    today = dt.datetime.now(KST).strftime("%Y-%m-%d")
    prev = sorted(p for p in DATA.glob("danta_close_*.json") if p.stem[-10:] < today)
    if not prev:
        log("전 영업일 종가 베팅 파일 없음 — 건너뜀")
        return
    src = json.loads(prev[-1].read_text(encoding="utf-8"))
    cands = src["picks"]
    log(f"{prev[-1].name} · 대상 {len(cands)}종")
    recs = []
    for r in cands:
        m = toss.get("/api/v1/candles", tps=20, symbol=r["code"], interval="1m", count=200)["candles"]
        m = sorted((x["timestamp"][11:16], float(x["openPrice"]), float(x["closePrice"]), float(x["volume"])) for x in m
                   if x["timestamp"][:10] == today and "09:00" <= x["timestamp"][11:16] <= "09:15")
        if not m:
            recs.append({**r, "ok": False, "why": "09시 분봉 없음"}); continue
        o = next((x[1] for x in m if x[3] > 0), m[0][1])   # 09:00 봉이 거래량 0 인 자리표시 봉일 때가 있어 첫 체결 봉 시가
        p, v15 = m[-1][2], sum(x[3] for x in m)
        pg = program_net(r["code"], today)
        ask, bid = book_totals(r["code"])
        why = [w for w, bad in (("09:15 ≤ 시가", p <= o), (f"15분 거래량 {v15 / r['v20']:.0%} < 20%", v15 < OPEN_VOL * r["v20"]),
                                ("프로그램 판정 불가" if pg is None else f"프로그램 {pg:,.0f}주", pg is None or pg <= 0),
                                ("호가 판정 불가" if ask is None else f"판매대기 {ask:,.0f} ≤ 구매대기 {bid:,.0f}", ask is None or ask <= bid)) if bad]
        recs.append({**r, "open": round(o), "p915": round(p), "vol15_pct": round(v15 / r["v20"] * 100), "program": pg,
                     "ask_total": ask, "bid_total": bid, "ok": not why, "why": " · ".join(why)})
    out = {"date": today, "source": prev[-1].name, "regime_ok": src.get("regime_ok"), "recs": recs, "buy": [x for x in recs if x["ok"]]}
    for x in recs:
        log(f"  {'🟢' if x['ok'] else '❌'} {x['name']} {x.get('why') or ''}")
    if dry:
        log("--dry-run: 저장·Slack 생략")
        return
    (DATA / f"danta_open_{today}.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    if not cands:
        return
    text = f"*⚡ 단타 09:15 매수 추천* (어제 오버나잇 {len(cands)}종 중)\n" + ("\n".join(
        f"🟢 *{x['name']}* {x['p915']:,}원 (시가 {x['open']:,}) · 15분 거래량 {x['vol15_pct']}% · 프로그램 {x['program']:,.0f}주 · 판매대기 {x['ask_total']:,.0f} > 구매대기 {x['bid_total']:,.0f}" for x in out["buy"]) or "해당 없음") \
        + ("\n매매: 지금 매수 → 오늘 종가 매도" if out["buy"] else "")
    slack(text, no_slack)


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--close", action="store_true")
    g.add_argument("--open", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-slack", action="store_true")
    a = ap.parse_args()
    (run_close if a.close else run_open)(a.dry_run, a.no_slack or a.dry_run)


if __name__ == "__main__":
    main()
