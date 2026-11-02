# -*- coding: utf-8 -*-
"""
backtest_buy_gate_4of5.py — 🎯 손익비 진입조건 「5/5 매수」 vs 「4/5 매수가능」 (2026-10-23 사용자 지시)

질문: 2026-10-23 Slack 표시 규칙(🟢 5/5 매수 · 🟡 4/5 매수가능 = 가격·양봉 pass + 거래량·외국인·프로그램 중 1개만
미충족)이 5/5 만 사는 것보다 나쁜가. 08-25 backtest_gate_tolerance.py 는 수급 이력이 없어 못 쟀다 —
이번엔 토스 Open API investor-trading·program-trades 의 until= 로 과거 당일 수급을 받는다.

이벤트 (screen_rr_breakout 과거 사례와 같은 정의):
    신호 = max(MA112,MA224) 상향 돌파 · 양봉 · 거래량 ≥ 20일 평균 2배 · 시총 1,000억
    편입일 k = 신호 뒤 15일 안 처음으로 손절 폭 2~5%·양봉·선 위인 날
    시나리오 (auto_intake.scenario 근사): 손절 = L×0.99 · 1차 = min(종가, L×1.04) · 거래량 범위 = 20일 평균 × 0.7~1.5
    관찰 = 편입 다음 날부터 20거래일 (auto_intake 미체결 퇴출) · 종가 < 손절 또는 MA20·60 동시 하회면 종료
    같은 종목은 관찰 기간이 끝난 뒤의 신호만 다시 본다 (겹침 제거)
진입조건 (관찰일마다, 라이브 §4.2 5종):
    가격 = 종가 ≤ 1차 · 양봉 · 거래량 = 범위 안 · 외국인 당일 순매수 > 0 · 프로그램(차익+비차익) 당일 순매수 > 0
    수급 행이 없으면 unknown = 미충족 (라이브 fail-closed)
성과 (screen_rr_breakout 과 같은 규칙): 판정일 종가 진입 → 60일 안 고가 +10% 먼저 = 승(+10%) ·
    종가 ≤ 손절 먼저 = 패(그날 종가) · 둘 다 아니면 60일째 종가.

출력: data/backtest_buy_gate_4of5.json + 콘솔 표 (그룹별 · 연도별 · 정책별)
수급 캐시: data/backtest_flow_cache.json (재실행 시 재조회 안 함)

⚠️ 토스 토큰은 1개 — 15:10·15:30·19:00 회차, stock-usa 와 동시 실행 금지.
사용: python backtest_buy_gate_4of5.py [--limit N]   (N = 이벤트 수 제한, 시험용)
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

from screen_rr_breakout import load, ma, TP, H, LB

ROOT = Path(__file__).parent
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT.parent / "jck_stock-usa"))
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

WATCH = 20
CACHE_F = DATA / "backtest_flow_cache.json"
GATES = ["price", "bullish", "volume", "foreign", "program"]
LABEL = {"volume": "거래량", "foreign": "외국인", "program": "프로그램", "bullish": "양봉", "price": "가격"}


def events(limit=None):
    bars, names, _ = load()
    out = []
    for code, rows in bars.items():
        if len(rows) < 300:
            continue
        rows.sort()
        a = np.array([r[1:] for r in rows], dtype=float)
        o, h, l, c, v, cap = a.T
        L = np.fmax(ma(c, 112), ma(c, 224))
        v20p = ma(v, 20)
        m20, m60 = ma(c, 20), ma(c, 60)
        n, i = len(c), 225
        while i < n - H - 1:
            if cap[i] < 1e11 or not (c[i - 1] <= L[i - 1] and c[i] > L[i]) or c[i] <= o[i] or not v[i] >= 2 * v20p[i - 1]:
                i += 1
                continue
            k = next((k for k in range(i, min(i + LB, n - H)) if 0.02 <= 1 - L[k] * 0.99 / c[k] <= 0.05 and c[k] > o[k] and c[k] > L[k]), None)
            if k is None:
                i += 1
                continue
            stop, p1, vlo, vhi = L[k] * 0.99, min(c[k], L[k] * 1.04), v20p[k] * 0.7, v20p[k] * 1.5
            days = []
            for d in range(k + 1, min(k + 1 + WATCH, n - H)):
                if c[d] < stop or (c[d] < m20[d] and c[d] < m60[d]):
                    break
                if c[d] <= p1:
                    days.append(dict(d=d, date=rows[d][0], bullish=c[d] > o[d], volume=vlo <= v[d] <= vhi))
            if days:
                out.append(dict(code=code, name=names[code], signal=rows[i][0], intake=rows[k][0], stop=stop, days=days,
                                arr=(h, c)))
            i = k + 1 + WATCH
    out.sort(key=lambda e: e["intake"])
    return out[:limit] if limit else out


def outcome(h, c, d, stop):
    T = c[d] * (1 + TP)
    for j in range(d + 1, d + 1 + H):
        if h[j] >= T:
            return 1, TP
        if c[j] <= stop:
            return 0, c[j] / c[d] - 1
    return 0, c[d + H] / c[d] - 1


def iso(d):
    return f"{d[:4]}-{d[4:6]}-{d[6:]}"


def flows(code, dates, cache):
    """{YYYYMMDD: (외국인, 프로그램)} — 없으면 None. until= 로 10행씩 거슬러 받는다."""
    import toss
    out = {}
    for kind in ("investor-trading", "program-trades"):
        key = f"{code}|{kind}"
        got = cache.setdefault(key, {})
        need = sorted(d for d in dates if iso(d) not in got)
        while need:
            until = iso(need[-1])
            recs = toss.get(f"/api/v1/stocks/{code}/{kind}", until=until)["records"]
            recs = [r for r in recs if r["date"] <= until]
            for r in recs:
                g = lambda k: float((r.get(k) or {}).get("netBuyVolume") or 0)
                got[r["date"]] = g("foreigner") if kind == "investor-trading" else g("arbitrage") + g("nonArbitrage")
            lo = min((r["date"] for r in recs), default=until)
            for d in need:
                if iso(d) >= lo:
                    got.setdefault(iso(d), None)   # 범위 안인데 행 없음 = unknown
            need = [d for d in need if iso(d) < lo]
            if not recs:
                for d in need:
                    got[iso(d)] = None
                break
    for d in dates:
        f, p = cache[f"{code}|investor-trading"].get(iso(d)), cache[f"{code}|program-trades"].get(iso(d))
        out[d] = (f, p)
    return out


def group(g):
    miss = [k for k in GATES if not g[k]]
    if not miss:
        return "5/5"
    if len(miss) == 1:
        return f"4/5 {LABEL[miss[0]]}❌"
    return "≤3/5"


ALLOWED = {"5/5", "4/5 거래량❌", "4/5 외국인❌", "4/5 프로그램❌"}


def stat(xs):
    n = len(xs)
    if not n:
        return dict(n=0)
    w = sum(x[0] for x in xs) / n
    z, den = 1.96, 1 + 1.96 ** 2 / n
    c = (w + z * z / (2 * n)) / den
    e = z * math.sqrt(w * (1 - w) / n + z * z / (4 * n * n)) / den
    return dict(n=n, win=round(w * 100, 1), ci=f"{(c - e) * 100:.0f}~{(c + e) * 100:.0f}", ev=round(float(np.mean([x[1] for x in xs])) * 100, 2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    ev = events(a.limit)
    years = defaultdict(int)
    for e in ev:
        years[e["intake"][:4]] += 1
    print(f"이벤트 {len(ev)}건 (가격 도달일 1일+) · 연도별 {dict(sorted(years.items()))}")
    cache = json.loads(CACHE_F.read_text(encoding="utf-8")) if CACHE_F.exists() else {}
    by_group, by_year, pol = defaultdict(list), defaultdict(lambda: defaultdict(list)), {"A 5/5만": [], "B 5/5+4/5매수가능": [], "C 가격만(조건 무시)": []}
    unknown, fail = 0, 0
    for idx, e in enumerate(ev):
        try:
            fl = flows(e["code"], [x["date"] for x in e["days"]], cache)
        except (Exception, SystemExit) as ex:
            fail += 1
            print(f"[WARN] {e['name']}({e['code']}) {e['intake']}: {str(ex)[:120]}")
            continue
        h, c = e["arr"]
        seen, a_done, b_done = set(), False, False
        for x in e["days"]:
            f, p = fl[x["date"]]
            unknown += f is None or p is None
            g = dict(price=True, bullish=x["bullish"], volume=x["volume"], foreign=bool(f and f > 0), program=bool(p and p > 0))
            grp = group(g)
            res = outcome(h, c, x["d"], e["stop"])
            if grp not in seen:                       # 이벤트당 그룹별 첫날 1건 (같은 이벤트 연속일 중복 방지)
                seen.add(grp)
                by_group[grp].append(res)
                by_year[grp][x["date"][:4]].append(res)
            if not a_done and grp == "5/5":
                pol["A 5/5만"].append(res); a_done = True
            if not b_done and grp in ALLOWED:
                pol["B 5/5+4/5매수가능"].append(res); b_done = True
        pol["C 가격만(조건 무시)"].append(outcome(h, c, e["days"][0]["d"], e["stop"]))
        if idx % 100 == 99:
            CACHE_F.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
            print(f"  … {idx + 1}/{len(ev)}", flush=True)
    CACHE_F.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    order = ["5/5", "4/5 거래량❌", "4/5 외국인❌", "4/5 프로그램❌", "4/5 양봉❌", "≤3/5"]
    res = dict(events=len(ev), fail=fail, unknown_days=unknown,
               groups={g: stat(by_group[g]) for g in order},
               years={g: {y: stat(v) for y, v in sorted(by_year[g].items())} for g in order},
               policies={k: stat(v) for k, v in pol.items()})
    (DATA / "backtest_buy_gate_4of5.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n조회 실패 {fail} · 수급 unknown 일수 {unknown}")
    print("\n[그룹별 — 이벤트당 그룹 첫날]  그룹 | n | 승률% (95% CI) | 기대값%")
    for g in order:
        s = res["groups"][g]
        print(f"  {g:12} | {s['n']:4} | {s.get('win', '-')} ({s.get('ci', '-')}) | {s.get('ev', '-')}")
        print("     연도: " + " · ".join(f"{y} n{v['n']} 승{v['win']}% 기{v['ev']}%" for y, v in res["years"][g].items()))
    print("\n[정책별 — 이벤트당 첫 진입]")
    for k, s in res["policies"].items():
        print(f"  {k:18} | n {s['n']} | 승률 {s.get('win')}% ({s.get('ci')}) | 기대값 {s.get('ev')}%")


if __name__ == "__main__":
    main()
