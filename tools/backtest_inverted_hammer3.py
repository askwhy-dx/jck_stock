# -*- coding: utf-8 -*-
"""
backtest_inverted_hammer3.py
KB금융(105560): 3거래일 연속 '역망치 양봉' 캔들 출현 후 다음날 수익률 백테스트.
데이터: data/krx_ohlcv_cache (2025-01~ 연속 구간만 사용)
"""
import json, glob, sys, os
from datetime import datetime
sys.stdout.reconfigure(encoding="utf-8")

CODE = "105560"

# --- 시계열 로드 (2025-01-01 이후 연속 구간) ---
rows = []
for f in sorted(glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "krx_ohlcv_cache", "*_KOSPI.json"))):
    d = json.load(open(f, encoding="utf-8"))
    for r in d["rows"]:
        if r.get("code") == CODE:
            if r["date"] >= "20250101":
                rows.append(r)
            break
rows.sort(key=lambda x: x["date"])
print(f"표본: {rows[0]['date']} ~ {rows[-1]['date']}  총 {len(rows)}거래일\n")


def is_inverted_hammer_bull(r, mode="loose"):
    o, h, l, c = r["open"], r["high"], r["low"], r["close"]
    body = abs(c - o)
    upper = h - max(o, c)
    lower = min(o, c) - l
    rng = h - l
    if rng == 0:
        return False
    if c <= o:                       # 양봉 조건
        return False
    b = body if body else 1
    if mode == "strict":             # 정통 역망치 (윗꼬리>=2몸통, 아랫꼬리 짧음)
        return upper >= 2 * b and lower <= 0.5 * b
    if mode == "loose":              # 양봉 + 윗꼬리>=몸통 + 윗꼬리 우세 + 윗꼬리 전체30%+
        return upper >= b and upper >= lower and upper >= 0.30 * rng
    if mode == "visual":             # 사용자 시각 기준: 윗꼬리 긴 양봉 (현재 6/12·15 매칭)
        return upper >= lower and upper >= 0.25 * rng and upper >= 0.6 * b


def run(mode, streak=3):
    flags = [is_inverted_hammer_bull(r, mode) for r in rows]
    print(f"{'='*60}\n[{mode.upper()}] 정의  (단일 캔들 출현 {sum(flags)}건/{len(rows)})  →  {streak}연속 패턴")
    signals = []  # i = 패턴의 마지막 캔들 index
    for i in range(streak-1, len(rows) - 1):
        if all(flags[i-k] for k in range(streak)):
            signals.append(i)
    print(f"{streak}연속 출현 횟수: {len(signals)}건")
    if not signals:
        print(" -> 표본 없음\n"); return
    def fwd(i, k):  # i 다음 k거래일 종가 수익률 (오늘 종가 대비)
        if i + k >= len(rows): return None
        return (rows[i+k]["close"] / rows[i]["close"] - 1) * 100
    nx_open_gap, nx_ret1, ret2, ret3 = [], [], [], []
    nx_high, nx_low = [], []
    details = []
    for i in signals:
        c0 = rows[i]["close"]; n = rows[i+1]
        gap = (n["open"]/c0 - 1)*100
        r1 = (n["close"]/c0 - 1)*100
        hi = (n["high"]/c0 - 1)*100
        lo = (n["low"]/c0 - 1)*100
        nx_open_gap.append(gap); nx_ret1.append(r1); nx_high.append(hi); nx_low.append(lo)
        r2 = fwd(i,2); r3 = fwd(i,3)
        if r2 is not None: ret2.append(r2)
        if r3 is not None: ret3.append(r3)
        details.append((rows[i-2]["date"], rows[i]["date"], gap, r1, hi, lo, r2, r3))
    def stat(a): return (sum(a)/len(a), min(a), max(a)) if a else (0,0,0)
    win1 = sum(1 for x in nx_ret1 if x>0)/len(nx_ret1)*100
    print(f"\n[다음날 결과 / 오늘 종가 대비]")
    print(f"  시가 갭      평균 {stat(nx_open_gap)[0]:+.2f}%")
    print(f"  종가 수익률  평균 {stat(nx_ret1)[0]:+.2f}%  (min {stat(nx_ret1)[1]:+.2f} / max {stat(nx_ret1)[2]:+.2f})")
    print(f"  종가 상승확률 {win1:.0f}%")
    print(f"  장중 고점    평균 {stat(nx_high)[0]:+.2f}%")
    print(f"  장중 저점    평균 {stat(nx_low)[0]:+.2f}%")
    print(f"  2일후 종가   평균 {stat(ret2)[0]:+.2f}%")
    print(f"  3일후 종가   평균 {stat(ret3)[0]:+.2f}%")
    print(f"\n[개별 신호] (패턴시작~종료 / 다음날 갭·종가·고점·저점 / 2일·3일후)")
    for s,e,g,r1,hi,lo,r2,r3 in details:
        s=f"{s[:4]}-{s[4:6]}-{s[6:]}"; e=f"{e[:4]}-{e[4:6]}-{e[6:]}"
        r2s=f"{r2:+.2f}" if r2 is not None else "  -"
        r3s=f"{r3:+.2f}" if r3 is not None else "  -"
        print(f"  {s}~{e}: 갭{g:+.2f} 종가{r1:+.2f} 고{hi:+.2f} 저{lo:+.2f} | 2d{r2s} 3d{r3s}")
    print()


run("visual", streak=1)
run("visual", streak=2)
run("visual", streak=3)
run("loose", streak=2)
run("loose", streak=3)
run("strict", streak=1)
