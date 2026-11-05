# -*- coding: utf-8 -*-
"""
backtest_bowl3_linebreak.py — 밥그릇3 「장기선 이탈 삭제」 조건 백테스트 (2026-11-05)

비교: 대시보드 밥그릇3 탭 운용을 일봉으로 재현
  OLD = 26-11-05 이전 규칙 — 노출 60거래일 안에서 편입(종가 > 목표면 건너뜀) → 종가 < 손절 또는 > 목표면 삭제 + 20거래일 재진입 금지
  NEW = OLD + 종가 < 장기선(max MA112·224) × 0.99 이면 삭제·편입 제외 (재진입 금지 없음)
판정 = 손익비(평균 이익 ÷ 평균 손실) 1순위 → 기대값 → 승률 (26-11-05 사용자 지시)

가정
  · 신호 = screen_bowl3_quality.find_pullbacks (1·2·3구간 + 눌림 양봉 + 시총 + 180일 중복 제외) · 수급(토스) 필터 = 있는 표본/없는 표본 둘 다
  · 공시 필터는 빼고 잼(과거 연도 DART 조회 생략)
  · 판정은 j일 종가, 체결은 j+1일 시가 (대시보드는 08:30 에 그려지고 다음 날 매매)
  · 손절·목표 = auto_intake.scenario 근사: 지지선 = 장기선(종가 이하일 때) 아니면 종가×0.995 아래 MA20·60·112·224·직전 저점(±5봉, 120봉) 중 가장 높은 것 → 손절 = 지지선×0.99
    목표 = 진입가 위 저항(±5봉 고점 240봉 + 최근 20봉 최고) 중 1.08~1.15배 첫 값, 없으면 1.05배 넘는 첫 값, 없으면 목표 없음
  · 1·2·3차 분할 매수는 무시하고 진입가 1개 · 왕복 비용 0.25%
  · 노출 끝(눌림일 + 60거래일)이면 다음 날 시가 청산

사용: python -X utf8 backtest_bowl3_linebreak.py [--no-flow]
"""
from __future__ import annotations
import json, sys, statistics as st
from pathlib import Path
import numpy as np
from screen_rr_breakout import load, ma
from screen_bowl3_quality import find_pullbacks, flow_5d

ROOT = Path(__file__).parent
FLOW_CACHE = ROOT / "data" / "bt_bowl3_flow_cache.json"
SHOW, BAN, TOL, COST = 60, 20, 0.99, 0.0025


def swing_lows(l, j):
    s = max(5, j - 119)
    return [l[i] for i in range(s, j) if l[i] == l[i - 5:i + 6].min()]


def plan(o, h, l, c, mas, top, j):
    C = c[j]
    if top[j] <= C:
        S = top[j]
    else:
        cands = [m[j] for m in mas if not np.isnan(m[j])] + swing_lows(l, j)
        cands = [x for x in cands if x <= C * 0.995]
        if not cands:
            return None
        S = max(cands)
    w0 = max(5, j - 239)
    res = sorted({h[i] for i in range(w0, j - 4) if h[i] == h[i - 5:i + 6].max()} | {h[max(0, j - 19):j + 1].max()})
    res = [x for x in res if x > C * 1.05]
    tgt = next((x for x in res if C * 1.08 <= x <= C * 1.15), res[0] if res else None)
    return S * 0.99, tgt


def simulate(a, top, mas, k, new):
    o, h, l, c = a[:4]
    n, end = len(c), min(k + SHOW, len(c) - 2)
    trades, pos, ban_until, j = [], None, -1, k
    while j <= end:
        if pos is None:
            if j > ban_until and not (new and c[j] < top[j] * TOL):
                p = plan(o, h, l, c, mas, top, j)
                if p and not (p[1] and c[j] > p[1]):
                    pos = dict(i=j + 1, px=o[j + 1], stop=p[0], tgt=p[1])
        else:
            why = ("stop" if c[j] < pos["stop"] else "target" if pos["tgt"] and c[j] > pos["tgt"]
                   else "line" if new and c[j] < top[j] * TOL else "end" if j == end else None)
            if why:
                trades.append(dict(ret=o[j + 1] / pos["px"] - 1 - COST, why=why, days=j + 1 - pos["i"], y=None))
                pos = None
                if why in ("stop", "target"):
                    ban_until = j + BAN
        j += 1
    if pos:  # 데이터 끝
        trades.append(dict(ret=c[end] / pos["px"] - 1 - COST, why="end", days=end - pos["i"], y=None))
    return trades


def stats(tr):
    r = [t["ret"] for t in tr]
    if not r:
        return dict(n=0)
    w, lo = [x for x in r if x > 0], [x for x in r if x <= 0]
    aw, al = (st.mean(w) if w else 0), (st.mean(lo) if lo else 0)
    return dict(n=len(r), payoff=(aw / -al) if al else float("inf"), pf=(sum(w) / -sum(lo)) if lo else float("inf"),
                exp=st.mean(r), win=len(w) / len(r), avg_win=aw, avg_loss=al, sum=sum(r), med=st.median(r))


def fmt(s):
    if not s.get("n"):
        return "n=0"
    return (f"n={s['n']:3d} | 손익비 {s['payoff']:.2f} | PF {s['pf']:.2f} | 기대값 {s['exp']*100:+.2f}% | 승률 {s['win']*100:.0f}% "
            f"| 평균이익 {s['avg_win']*100:+.1f}% · 평균손실 {s['avg_loss']*100:+.1f}% | 합계 {s['sum']*100:+.0f}%p")


def main():
    use_flow = "--no-flow" not in sys.argv
    bars, names, _ = load()
    cache = json.loads(FLOW_CACHE.read_text(encoding="utf-8")) if FLOW_CACHE.exists() else {}
    res = {"OLD": [], "NEW": []}
    ev_n = 0
    for code, rows in bars.items():
        rows = sorted(rows)
        if len(rows) < 260:
            continue
        evs = find_pullbacks(rows)
        if not evs:
            continue
        a = np.array([r[1:] for r in rows], dtype=float).T
        c = a[3]
        m20, m60, m112, m224 = ma(c, 20), ma(c, 60), ma(c, 112), ma(c, 224)
        top = np.fmax(m112, m224)
        for e in evs:
            key = f"{code}_{e['pull_date']}"
            if key not in cache:
                cache[key] = flow_5d(code, e["pull_date"]) if use_flow else None
            fl = cache[key]
            ev_n += 1
            for name, new in (("OLD", False), ("NEW", True)):
                for t in simulate(a, top, (m20, m60, m112, m224), e["k"], new):
                    t.update(code=code, name=names[code], y=e["pull_date"][:4], flow=fl)
                    res[name].append(t)
    FLOW_CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    print(f"신호(눌림 양봉) {ev_n}건\n")
    for label, f in (("전체 신호", lambda t: True), ("수급>0 (실제 탭 조건)", lambda t: (t["flow"] or 0) > 0)):
        print(f"== {label}")
        for name in ("OLD", "NEW"):
            print(f"  {name}: {fmt(stats([t for t in res[name] if f(t)]))}")
        for name in ("OLD", "NEW"):
            tr = [t for t in res[name] if f(t)]
            why = {w: sum(1 for t in tr if t["why"] == w) for w in ("stop", "target", "line", "end")}
            print(f"  {name} 청산 사유: {why}")
        for y in sorted({t["y"] for t in res["OLD"]}):
            o = stats([t for t in res["OLD"] if f(t) and t["y"] == y]); nw = stats([t for t in res["NEW"] if f(t) and t["y"] == y])
            print(f"   {y} OLD {fmt(o)}\n   {y} NEW {fmt(nw)}")
        print()
    json.dump(res, open(ROOT / "data" / "bt_bowl3_linebreak_result.json", "w", encoding="utf-8"), ensure_ascii=False, default=float)


if __name__ == "__main__":
    main()
