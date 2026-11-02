# -*- coding: utf-8 -*-
"""
backtest_gate_tolerance_detail.py — gate_tolerance 결과 신뢰도 점검

backtest_gate_tolerance.py 가 낸 수치를 그대로 믿기 전에 세 가지를 확인한다:
  ① 게이트 5개가 정말 5개인가 — baseline 신호 생성기가 이미 걸러버려
     항상 pass 인 게이트가 있으면 "5/5 충족"이라는 라벨 자체가 거짓이다.
  ② 연도 쏠림 — 2024/2025/2026 중 한 해가 결론을 혼자 만들고 있지 않은가.
     (선례: 눌림 「기대값 2배」가 41건 2026 편중 산물이었던 건)
  ③ 표본 크기 — 각 칸의 n 과 승률 95% 신뢰구간(Wilson).

사용: python backtest_gate_tolerance_detail.py [--since 20240101]
"""
from __future__ import annotations
import argparse, json, math, os, sys
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8")
from backtest_scanners import WIN_RET
from backtest_pullback_improve import gen_signals
from backtest_gate_tolerance import GATES, NAMES, agg

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def log(m): print(m, flush=True)


def wilson(w, n, z=1.96):
    """승률 95% 신뢰구간 (Wilson score) — n 이 작을 때 정규근사보다 정직하다."""
    if n == 0:
        return (0.0, 0.0)
    p = w / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(max(0.0, c - m) * 100, 1), round(min(1.0, c + m) * 100, 1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="20240101")
    ap.add_argument("--cache-start", default="20221021",
                    help="캐시 로드 시작일. 기본 20221021(캐시 전체). MA224 워밍업 229봉이 필요해 since보다 최소 1년은 앞서야 한다.")
    a = ap.parse_args()

    log(f"신호 재생성 (since={a.since}) ...")
    sigs = gen_signals(a.since, a.cache_start)
    for s in sigs:
        s["_gates"] = {k: bool(fn(s)) for k, fn in GATES.items()}
        s["_pass"] = sum(s["_gates"].values())
        s["_failed"] = [k for k, v in s["_gates"].items() if not v]
        s["_year"] = s["entry"][:4]
    log(f"신호 {len(sigs)}개\n")

    # ── ① 게이트가 실제로 변하는가 ──────────────────────────────
    print("=== ① 게이트 변별력 점검 (미충족 건이 0이면 baseline 이 이미 걸러낸 것) ===")
    print(f"{'게이트':<20}{'충족':>7}{'미충족':>8}  판정")
    dead = []
    for name in NAMES:
        ok = sum(1 for s in sigs if s["_gates"][name])
        ng = len(sigs) - ok
        verdict = "⛔ 상수 — 게이트로 작동 안 함" if ng == 0 else "✅ 변별력 있음"
        if ng == 0:
            dead.append(name)
        print(f"{name:<20}{ok:>7}{ng:>8}  {verdict}")
    live = [n for n in NAMES if n not in dead]
    print(f"\n→ 실제로 변하는 게이트 {len(live)}개: {', '.join(live)}")
    print(f"→ 그래서 '5/5 충족'은 사실상 '{len(live)}/{len(live)} 충족'이다.\n")

    # ── ② 연도별 쏠림 ───────────────────────────────────────────
    print("=== ② 연도 쏠림 — 매수 기준별 x 연도 ===")
    policies = {
        "전부 충족만": [s for s in sigs if s["_pass"] == 5],
        "1개 허용": [s for s in sigs if s["_pass"] >= 4],
        "게이트 무시": sigs,
    }
    years = sorted({s["_year"] for s in sigs})
    print(f"{'구분':<14}{'연도':>6}{'신호':>6}{'승':>4}{'패':>4}{'승률%':>7}{'기대값%':>9}  {'승률 95%CI':>14}")
    yr_out = {}
    for pname, grp in policies.items():
        for y in years:
            g = [s for s in grp if s["_year"] == y]
            if not g:
                continue
            r = agg(g)
            ci = wilson(r["wins"], r["closed"])
            yr_out[f"{pname}|{y}"] = {**r, "ci95": ci}
            print(f"{pname:<14}{y:>6}{r['signals']:>6}{r['wins']:>4}{r['losses']:>4}"
                  f"{r['win_rate_pct']:>7}{r['expectancy_pct']:>9}  {str(ci):>14}")
        print()

    # ── ③ 핵심 칸의 표본·신뢰구간 ───────────────────────────────
    print("=== ③ 핵심 결론 칸의 표본과 신뢰구간 ===")
    print(f"{'구분':<24}{'신호':>6}{'승':>4}{'패':>4}{'승률%':>7}{'기대값%':>9}  {'승률 95%CI':>14}")
    key = {
        "전부 충족만": [s for s in sigs if s["_pass"] == 5],
        "1개 허용(4/5 이상)": [s for s in sigs if s["_pass"] >= 4],
    }
    for name in NAMES:
        grp = [s for s in sigs if s["_pass"] == 4 and s["_failed"] == [name]]
        if grp:
            key[f"{name} 만 빼먹음"] = grp
    ci_out = {}
    for name, grp in key.items():
        r = agg(grp)
        ci = wilson(r["wins"], r["closed"])
        ci_out[name] = {**r, "ci95": ci}
        print(f"{name:<24}{r['signals']:>6}{r['wins']:>4}{r['losses']:>4}"
              f"{r['win_rate_pct']:>7}{r['expectancy_pct']:>9}  {str(ci):>14}")

    print("\n→ 신뢰구간이 서로 겹치면 '차이가 있다'고 말할 수 없다.")

    path = os.path.join(DATA, "gate_tolerance_detail.json")
    json.dump({"since": a.since, "dead_gates": dead, "live_gates": live,
               "by_year": yr_out, "key_cells": ci_out},
              open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    log(f"\n저장: {path}")


if __name__ == "__main__":
    main()
