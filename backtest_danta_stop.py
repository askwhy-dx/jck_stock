# -*- coding: utf-8 -*-
"""오버나잇·단타 손절 백테스트 (2026-10-26) — 신호는 backtest_danta_open.signals 와 같음, D 장중 = 토스 1분봉(09:00~15:30). ⚠️ 토스 토큰 1개(회차·stock-usa 와 동시 실행 금지)."""
import json, sys
from pathlib import Path
import numpy as np

STOCK = Path(__file__).parent
sys.path.insert(0, str(STOCK)); sys.path.insert(0, str(STOCK.parent / "jck_stock-usa"))
sys.stdout.reconfigure(encoding="utf-8")
import backtest_danta_open as B

SCR = STOCK / "data"
MC = SCR / "backtest_danta_stop_cache.json"
COST = 0.0025


def day_bars(code, day, cache):
    key = f"{code}|{day}"
    if key not in cache:
        import toss
        rows = {}
        for b in ("12:11", "15:31"):
            for x in toss.get("/api/v1/candles", tps=15, symbol=code, interval="1m", count=200, before=f"{B.iso(day)}T{b}:00+09:00")["candles"]:
                t = x["timestamp"]
                if t[:10] == B.iso(day) and "09:00" <= t[11:16] <= "15:30":
                    rows[t[11:16]] = [float(x[k]) for k in ("openPrice", "highPrice", "lowPrice", "closePrice", "volume")]
        cache[key] = sorted([t] + v for t, v in rows.items())
    return cache[key]


def sim(bars, entry, tgt, stop, start="09:00"):
    """bars 순서대로: 시가 갭 → 그 가격 체결, 한 봉에서 둘 다 닿으면 손절 먼저(보수). 끝까지 안 닿으면 마지막 종가."""
    amb = False
    for t, o, h, l, c, v in bars:
        if t < start or v <= 0:
            continue
        if stop is not None and o <= entry * (1 - stop):
            return o / entry - 1, "stop", amb
        if tgt is not None and o >= entry * (1 + tgt):
            return o / entry - 1, "tgt", amb
        hs = stop is not None and l <= entry * (1 - stop)
        ht = tgt is not None and h >= entry * (1 + tgt)
        if hs and ht:
            amb = True
            return -stop, "stop", amb
        if hs:
            return -stop, "stop", amb
        if ht:
            return tgt, "tgt", amb
    return bars[-1][4] / entry - 1, "close", amb


def stat(rs):
    a = np.array([r[0] for r in rs]) - COST
    k = [r[1] for r in rs]
    return f"n{len(a):4} 승{(a > 0).mean() * 100:5.1f}% 평균{a.mean() * 100:+6.2f}% 중앙{np.median(a) * 100:+6.2f}% 누적{a.sum() * 100:+7.1f}% | 익절{k.count('tgt'):3} 손절{k.count('stop'):3} 종가{k.count('close'):3} 모호{sum(r[2] for r in rs):3}"


def main():
    sig = B.signals(B.load())
    old = json.loads(B.CACHE_F.read_text(encoding="utf-8"))
    cache = json.loads(MC.read_text(encoding="utf-8")) if MC.exists() else {}
    on, dt_core, dt_core_p = [], [], []
    for i, s in enumerate(sig):
        try:
            bars = day_bars(s["code"], s["day"], cache)
        except (Exception, SystemExit) as ex:
            print("[WARN]", s["name"], s["day"], str(ex)[:80]); continue
        dd = old.get(f"d|{s['code']}|{s['day']}")
        if not bars or not dd:
            continue
        k = s["d_close"] / dd[1]                       # KRX 원주가 ÷ 토스 수정주가
        ent_on = s["sig_close"] / k
        on.append((s, bars, ent_on))
        m = old.get(f"m|{s['code']}|{s['day']}") or []
        if m:
            o = next((x[1] for x in m if x[3] > 0), m[0][1]); p = m[-1][2]
            v15 = sum(x[3] for x in m) / k
            if p > o and v15 >= B.OPEN_VOL * s["v20"]:
                ps = (old.get(f"p|{s['code']}|{s['day']}") or [None])[0]
                dt_core.append((s, bars, p))
                if ps is not None and ps > 0:
                    dt_core_p.append((s, bars, p))
        if i % 100 == 99:
            MC.write_text(json.dumps(cache), encoding="utf-8"); print(f"  … {i + 1}/{len(sig)}", flush=True)
    MC.write_text(json.dumps(cache), encoding="utf-8")

    stops = [None, 0.01, 0.015, 0.02, 0.03, 0.05]
    lab = lambda x: "없음" if x is None else f"-{x * 100:g}%"
    print("\n=== 🌙 오버나잇 (S 종가 매수) ===")
    print("현행 D 시가 매도      ", stat([(next(b for b in bars if b[5] > 0)[1] / e - 1, "close", False) for s, bars, e in on]))
    for tgt in (0.03, 0.04):
        for st in stops:
            rs = [sim(bars, e, tgt, st) for s, bars, e in on]
            print(f"익절+{tgt * 100:g}% 손절{lab(st):5} → D 종가", stat(rs))
    for name, grp in (("⚡ 단타 ①+② (09:15 매수)", dt_core), ("⚡ 단타 ①+②+신호일 프로그램>0", dt_core_p)):
        print(f"\n=== {name} ===")
        for tgt in (None, 0.03, 0.04):
            for st in stops:
                rs = [sim(bars, e, tgt, st, start="09:15") for s, bars, e in grp]
                print(f"익절{('+' + format(tgt * 100, 'g') + '%') if tgt else '없음':5} 손절{lab(st):5}", stat(rs))
    # 연도별 — 오버나잇 +3%/-2%, 단타 ①+② +3%/-2%
    for y in ("2025", "2026"):
        a = [sim(b, e, 0.03, 0.02) for s, b, e in on if s["day"][:4] == y]
        a0 = [sim(b, e, 0.03, None) for s, b, e in on if s["day"][:4] == y]
        print(f"\n{y} 오버나잇 +3%/손절없음", stat(a0)); print(f"{y} 오버나잇 +3%/-2%    ", stat(a))


if __name__ == "__main__":
    main()
