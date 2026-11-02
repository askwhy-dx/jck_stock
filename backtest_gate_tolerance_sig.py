# -*- coding: utf-8 -*-
"""
backtest_gate_tolerance_sig.py — 게이트 결론의 유의성 + 연도 일관성 최종 판정

detail 스크립트가 CI 를 보여줬지만 "CI 가 겹친다/안 겹친다"는 보수적 눈대중이라,
여기서 두 비율 차이의 z 검정(p값)과 게이트별 연도 방향 일치를 같이 본다.

판정 규칙(이 파일의 결론 등급):
  ✅ 채택   p < 0.05  AND  3개 연도 방향 일치
  ⚠️ 보류   p < 0.05  이지만 연도 방향이 갈림  (국면 산물 의심)
  ❌ 기각   p >= 0.05                          (표본으로는 차이 못 봄)

사용: python backtest_gate_tolerance_sig.py [--since 20240101] [--cache-start 20221021]
"""
from __future__ import annotations
import argparse, json, math, os, sys

sys.stdout.reconfigure(encoding="utf-8")
from backtest_pullback_improve import gen_signals
from backtest_gate_tolerance import GATES, NAMES, agg

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def log(m): print(m, flush=True)


def _phi(x):
    """표준정규 CDF (erf 기반) — scipy 없이."""
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def ztest(w1, n1, w2, n2):
    """두 승률 차이 z 검정 → (p1, p2, z, p_value). n 은 청산 건수(승+패)."""
    if n1 == 0 or n2 == 0:
        return (0.0, 0.0, 0.0, 1.0)
    p1, p2 = w1 / n1, w2 / n2
    pool = (w1 + w2) / (n1 + n2)
    se = math.sqrt(pool * (1 - pool) * (1 / n1 + 1 / n2))
    if se == 0:
        return (p1, p2, 0.0, 1.0)
    z = (p1 - p2) / se
    return (p1, p2, z, 2 * (1 - _phi(abs(z))))


def wl(grp):
    w = sum(1 for s in grp if s["outcome"] == "win")
    l = sum(1 for s in grp if s["outcome"] == "loss")
    return w, w + l


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="20240101")
    ap.add_argument("--cache-start", default="20221021")
    a = ap.parse_args()

    log(f"신호 재생성 (since={a.since}, cache={a.cache_start}) ...")
    sigs = gen_signals(a.since, a.cache_start)
    for s in sigs:
        s["_gates"] = {k: bool(fn(s)) for k, fn in GATES.items()}
        s["_pass"] = sum(s["_gates"].values())
        s["_failed"] = [k for k, v in s["_gates"].items() if not v]
        s["_year"] = s["entry"][:4]
    years = sorted({s["_year"] for s in sigs})
    log(f"신호 {len(sigs)}개 · 연도 {years}\n")

    out = {"since": a.since, "cache_start": a.cache_start, "years": years}

    # ── 1. 핵심 의사결정: 전부충족 vs 정확히 1개 미충족 (겹치지 않는 두 집단) ──
    print("=== 1. 전부 충족 vs 1개 미충족 — 겹치지 않게 비교 ===")
    full = [s for s in sigs if s["_pass"] == 5]
    one = [s for s in sigs if s["_pass"] == 4]
    w1, n1 = wl(full); w2, n2 = wl(one)
    p1, p2, z, p = ztest(w1, n1, w2, n2)
    print(f"  전부 충족    승 {w1:>4}/{n1:<5} 승률 {p1*100:5.1f}%   기대값 {agg(full)['expectancy_pct']:+.2f}%")
    print(f"  1개 미충족   승 {w2:>4}/{n2:<5} 승률 {p2*100:5.1f}%   기대값 {agg(one)['expectancy_pct']:+.2f}%")
    print(f"  → 차이 {(p1-p2)*100:+.1f}%p · z={z:.2f} · p={p:.4f} "
          f"{'✅ 유의' if p < 0.05 else '❌ 유의하지 않음'}")
    out["full_vs_one"] = {"full": [w1, n1], "one_miss": [w2, n2], "z": round(z, 3), "p": round(p, 4)}

    # 연도별 방향
    print("  연도별 방향:")
    dirs = []
    for y in years:
        fy = [s for s in full if s["_year"] == y]; oy = [s for s in one if s["_year"] == y]
        a1, b1 = wl(fy); a2, b2 = wl(oy)
        r1 = a1 / b1 * 100 if b1 else 0; r2 = a2 / b2 * 100 if b2 else 0
        d = "전부충족 우세" if r1 > r2 else ("1개허용 우세" if r2 > r1 else "동률")
        dirs.append(r1 > r2)
        print(f"    {y}  전부 {r1:5.1f}% (n={b1:<4}) vs 1개미충족 {r2:5.1f}% (n={b2:<4})  → {d}")
    print(f"  → 방향 일치: {sum(dirs)}/{len(dirs)} 연도\n")
    out["full_vs_one_year_dirs"] = sum(dirs)

    # ── 2. 게이트 단독 효과 유의성 + 연도 일관성 ──────────────────
    print("=== 2. 게이트별 — 충족 vs 미충족 (단독 효과) ===")
    print(f"{'게이트':<16}{'충족 승률':>10}{'미충족 승률':>12}{'차이%p':>9}{'p값':>9}  {'연도일치':>8}  판정")
    gate_out = {}
    for name in NAMES:
        on = [s for s in sigs if s["_gates"][name]]
        off = [s for s in sigs if not s["_gates"][name]]
        if not off:
            print(f"{name:<16}{'—':>10}{'—':>12}{'—':>9}{'—':>9}  {'—':>8}  ⛔ 상수(baseline 강제)")
            gate_out[name] = {"constant": True}
            continue
        w1, n1 = wl(on); w2, n2 = wl(off)
        p1, p2, z, p = ztest(w1, n1, w2, n2)
        agree = 0
        for y in years:
            a1, b1 = wl([s for s in on if s["_year"] == y])
            a2, b2 = wl([s for s in off if s["_year"] == y])
            r1 = a1 / b1 if b1 else 0; r2 = a2 / b2 if b2 else 0
            if (r1 > r2) == (p1 > p2):
                agree += 1
        if p >= 0.05:
            verdict = "❌ 기각 (차이 못 봄)"
        elif agree == len(years):
            verdict = "✅ 채택 (도움)" if p1 > p2 else "✅ 채택 (역효과)"
        else:
            verdict = "⚠️ 보류 (연도 갈림)"
        gate_out[name] = {"on": [w1, n1], "off": [w2, n2], "p": round(p, 4),
                          "years_agree": agree, "verdict": verdict}
        print(f"{name:<16}{p1*100:9.1f}%{p2*100:11.1f}%{(p1-p2)*100:+8.1f}{p:>9.4f}  {agree}/{len(years):<6}  {verdict}")
    out["gates"] = gate_out

    # ── 3. 연도(국면) 자체의 크기 — 게이트보다 큰가 ────────────────
    print("\n=== 3. 국면(연도) 효과 vs 게이트 효과 — 무엇이 더 큰가 ===")
    print(f"{'연도':<8}{'신호':>7}{'승률%':>8}{'기대값%':>10}")
    yr = {}
    for y in years:
        g = [s for s in sigs if s["_year"] == y]
        r = agg(g); yr[y] = r
        print(f"{y:<8}{r['signals']:>7}{r['win_rate_pct']:>8}{r['expectancy_pct']:>10}")
    rates = [yr[y]["win_rate_pct"] for y in years]
    print(f"\n  연도 간 승률 폭: {min(rates):.1f}% ~ {max(rates):.1f}%  = {max(rates)-min(rates):.1f}%p")
    print(f"  게이트 최대 효과(전부충족 - 1개미충족): {(out['full_vs_one']['full'][0]/out['full_vs_one']['full'][1] - out['full_vs_one']['one_miss'][0]/out['full_vs_one']['one_miss'][1])*100:.1f}%p")
    out["by_year"] = yr

    path = os.path.join(DATA, "gate_tolerance_significance.json")
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    log(f"\n저장: {path}")


if __name__ == "__main__":
    main()
