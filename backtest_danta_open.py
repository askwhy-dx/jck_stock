# -*- coding: utf-8 -*-
"""
backtest_danta_open.py — 🌙 오버나잇 → ⚡ 단타 09:15 규칙 백테스트 (2026-10-23 사용자 「이렇게 해서 백테스트 해봐, 25년부터 2개년」)

신호일 S (KRX 일봉 캐시, 2025-01 ~): 등락 +10~29.5% · (고가−종가) ≤ 당일 범위 10% · 거래량 ≥ 직전 20일 평균 2.7배 · 전일 시총 1,000억+
    → 거래량 배수 순 하루 5종 (screen_danta_close --close 와 같음, 단 15:10 대신 종가로 판정)
다음 날 D (토스 1분봉 before= 로 과거 조회):
    시가 = 09:00 이후 첫 체결 봉 시가 · 09:15 가격 = 09:14 봉 종가 (09:15 봉은 아직 안 끝났으므로 미사용)
    15분 거래량 = 09:00~09:14 봉 합 ≥ v20 × 20%
    매매 = 09:15 가격 매수 → D 종가 매도 (토스 일봉 종가 = 1분봉과 같은 수정주가 · 거래량은 KRX÷토스 종가 비율로 원주 환산)
수급 (프로그램 = 토스 program-trades 일별 합계만 있음 — 09:15 장중값 이력 없음):
    P_sig  = 신호일 S 프로그램 순매수 > 0     (09:15에 이미 확정된 값 — 실전 대체 후보)
    P_day  = D 하루 전체 프로그램 순매수 > 0  (⚠️ 09:15 이후 정보 포함 = 미래 참조 · 상한 참고용)
호가(판매대기 > 구매대기)는 과거 이력이 API에 없어 **측정 불가**.
비용 0.25% (오버나잇 백테스트와 같음) — 승률은 비용 전 수익 > 0.

출력: data/backtest_danta_open.json · 캐시 data/backtest_danta_cache.json
⚠️ 토스 토큰 1개 — 08:30·09:15·15:10·16:30·19:00 회차, stock-usa 와 동시 실행 금지.
사용: python backtest_danta_open.py [--limit N]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).parent
DATA = ROOT / "data"
CACHE_DIR = DATA / "krx_ohlcv_cache"
CACHE_F = DATA / "backtest_danta_cache.json"
sys.path.insert(0, str(ROOT.parent / "jck_stock-usa"))
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

START, LOAD_FROM = "20250101", "20241101"
CHG_LO, CHG_HI, TOP_TAIL, VOL_X, CAP, TOPN, OPEN_VOL, COST = 0.10, 0.295, 0.10, 2.7, 1e11, 5, 0.20, 0.0025


def load():
    bars = defaultdict(list)
    for f in sorted(CACHE_DIR.glob("*_*.json")):
        if f.name[:8] < LOAD_FROM:
            continue
        for r in json.loads(f.read_text(encoding="utf-8")).get("rows") or []:
            if r.get("close") and r.get("open"):
                bars[r["code"]].append((r["date"], float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]),
                                        float(r.get("volume") or 0), float(r.get("market_cap") or 0), r["name"]))
    return {c: sorted(v) for c, v in bars.items()}


def signals(bars):
    by_day = defaultdict(list)
    for c, rs in bars.items():
        for i in range(21, len(rs) - 1):
            d, o, h, l, cl, v, cap, nm = rs[i]
            if d < START:
                continue
            prev, pcap = rs[i - 1][4], rs[i - 1][6]
            v20 = sum(x[5] for x in rs[i - 20:i]) / 20
            chg = cl / prev - 1
            if pcap < CAP or not (CHG_LO <= chg < CHG_HI) or h <= l or (h - cl) / (h - l) > TOP_TAIL or not v20 or v / v20 < VOL_X:
                continue
            nd = rs[i + 1]
            by_day[d].append(dict(code=c, name=nm, sig=d, day=nd[0], sig_close=cl, v20=v20, vol_x=v / v20,
                                  d_open=nd[1], d_close=nd[4]))
    out = []
    for d in sorted(by_day):
        out += sorted(by_day[d], key=lambda x: -x["vol_x"])[:TOPN]
    return out


def iso(d):
    return f"{d[:4]}-{d[4:6]}-{d[6:]}"


def minute(code, day, cache):
    key = f"m|{code}|{day}"
    if key not in cache:
        import toss
        c = toss.get("/api/v1/candles", tps=15, symbol=code, interval="1m", count=30, before=f"{iso(day)}T09:15:00+09:00")["candles"]
        cache[key] = sorted((x["timestamp"][11:16], float(x["openPrice"]), float(x["closePrice"]), float(x["volume"])) for x in c
                            if x["timestamp"][:10] == iso(day) and "09:00" <= x["timestamp"][11:16] <= "09:14")
    return cache[key]


def daily(code, day, cache):
    """토스 D 일봉 (시가, 종가) — 1분봉과 같은 수정주가. KRX 원주가와 섞으면 분할·병합 종목이 ±80~400%로 깨진다(10-23 실측 39건)."""
    key = f"d|{code}|{day}"
    if key not in cache:
        import toss
        c = toss.get("/api/v1/candles", tps=15, symbol=code, interval="1d", count=1, before=f"{iso(day)}T23:59:00+09:00")["candles"]
        cache[key] = [float(c[0]["openPrice"]), float(c[0]["closePrice"])] if c and c[0]["timestamp"][:10] == iso(day) else None
    return cache[key]


def program(code, sig, day, cache):
    key = f"p|{code}|{day}"
    if key not in cache:
        import toss
        recs = toss.get(f"/api/v1/stocks/{code}/program-trades", tps=15, until=iso(day))["records"]
        g = {r["date"]: sum(float((r.get(k) or {}).get("netBuyVolume") or 0) for k in ("arbitrage", "nonArbitrage")) for r in recs}
        cache[key] = [g.get(iso(sig)), g.get(iso(day))]
    return cache[key]


def stat(xs):
    if not xs:
        return dict(n=0)
    a = np.array(xs)
    net = a - COST
    return dict(n=len(a), win=round(float((a > 0).mean()) * 100, 1), avg=round(float(a.mean()) * 100, 2),
                net=round(float(net.mean()) * 100, 2), med=round(float(np.median(a)) * 100, 2),
                sum_net=round(float(net.sum()) * 100, 1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    sig = signals(load())
    if a.limit:
        sig = sig[:a.limit]
    print(f"신호 {len(sig)}건 ({sig[0]['sig']} ~ {sig[-1]['sig']})", flush=True)
    cache = json.loads(CACHE_F.read_text(encoding="utf-8")) if CACHE_F.exists() else {}
    rows, fail = [], 0
    for i, s in enumerate(sig):
        try:
            m = minute(s["code"], s["day"], cache)
            ps, pd = program(s["code"], s["sig"], s["day"], cache)
            dd = daily(s["code"], s["day"], cache) if m else None
        except (Exception, SystemExit) as ex:
            fail += 1
            print(f"[WARN] {s['name']} {s['day']}: {str(ex)[:100]}")
            continue
        base = dict(s, overnight=s["d_open"] / s["sig_close"] - 1, p_sig=ps, p_day=pd)
        if m and dd:
            k = s["d_close"] / dd[1]                    # 원주가 ÷ 수정주가 → 1분봉 거래량을 원주 기준으로 되돌림
            o = next((x[1] for x in m if x[3] > 0), m[0][1])
            p, v15 = m[-1][2], sum(x[3] for x in m) / k
            base.update(open_ok=p > o, vol_ok=v15 >= OPEN_VOL * s["v20"], ret=dd[1] / p - 1, p915=p, adj=round(k, 3))
        rows.append(base)
        if i % 200 == 199:
            CACHE_F.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
            print(f"  … {i + 1}/{len(sig)}", flush=True)
    CACHE_F.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")

    has = [r for r in rows if "ret" in r]
    pos = lambda v: v is not None and v > 0
    core = [r for r in has if r["open_ok"] and r["vol_ok"]]
    pol = {
        "오버나잇 (S 종가 → D 시가)": [r["overnight"] for r in rows],
        "아무 조건 없이 09:15 매수": [r["ret"] for r in has],
        "① 09:15 > 시가": [r["ret"] for r in has if r["open_ok"]],
        "② 15분 거래량 20%+": [r["ret"] for r in has if r["vol_ok"]],
        "①+② (가격·거래량)": [r["ret"] for r in core],
        "①+②+신호일 프로그램 > 0": [r["ret"] for r in core if pos(r["p_sig"])],
        "①+②+신호일 프로그램 ≤ 0": [r["ret"] for r in core if not pos(r["p_sig"])],
        "①+②+D 하루 프로그램 > 0 ⚠️미래참조": [r["ret"] for r in core if pos(r["p_day"])],
    }
    res = dict(signals=len(sig), fail=fail, minute_missing=len(rows) - len(has), cost_pct=COST * 100,
               policies={k: stat(v) for k, v in pol.items()},
               years={y: {k: stat([x for x, r in zip(v, src) if r["day"][:4] == y]) for k, v, src in (
                   ("①+②", [r["ret"] for r in core], core),
                   ("①+②+신호일 프로그램 > 0", [r["ret"] for r in core if pos(r["p_sig"])], [r for r in core if pos(r["p_sig"])]))}
                      for y in ("2025", "2026")})
    (DATA / "backtest_danta_open.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n조회 실패 {fail} · 1분봉 없음 {res['minute_missing']} · 비용 {COST:.2%}")
    print("정책 | n | 승률% | 평균% | 비용 뒤% | 중앙% | 비용 뒤 누적%")
    for k, st in res["policies"].items():
        print(f"  {k:34} | {st['n']:4} | {st.get('win')} | {st.get('avg')} | {st.get('net')} | {st.get('med')} | {st.get('sum_net')}")
    for y, v in res["years"].items():
        print(f"  {y}: " + " · ".join(f"{k} n{st['n']} 승{st.get('win')}% 비용뒤{st.get('net')}%" for k, st in v.items()))


if __name__ == "__main__":
    main()
