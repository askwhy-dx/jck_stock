# -*- coding: utf-8 -*-
"""
analyze_regime.py — 레짐(시장 브레드스) 필터 약세장 재검증

연속 캐시(2025-01-15~) 내 시장 브레드스 시계열을 산출하고,
① 저브레드스(약세) 구간이 실제로 존재하는지
② 그 구간에 눌림 신호가 몇 건 났고 성과가 어떤지
를 브레드스 버킷별 승률로 검증한다.

사용: python analyze_regime.py
"""
from __future__ import annotations
import sys
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
from backtest_scanners import (load_ticker_frames, _excluded, SCORE_CUT, CONTIGUOUS_START)
from backtest_pullback_strategy import build_breadth, _simulate
from collect_theme_flow import evaluate_pullback_signal


def log(m): print(m, flush=True)


def main():
    frames, meta = load_ticker_frames()
    log(f"종목 {len(frames)}개. 브레드스 산출 ...")
    breadth = build_breadth(frames, meta)
    bs = pd.Series(breadth).sort_index()
    bs = bs[bs.index >= CONTIGUOUS_START]

    # --- ① 브레드스 분포 ---
    log("\n=== 시장 브레드스(시총1000억+ 종목 중 112선 위 비율) 분포 ===")
    for q in (0, 5, 10, 25, 50, 75, 100):
        log(f"  {q:>3}분위: {bs.quantile(q/100)*100:5.1f}%")
    log(f"  평균: {bs.mean()*100:.1f}% · 표준편차: {bs.std()*100:.1f}%p · 일수: {len(bs)}")

    # 저브레드스(<50%) 구간 식별
    low = bs[bs < 0.50]
    log(f"\n약세(브레드스<50%) 영업일: {len(low)} / {len(bs)} ({len(low)/len(bs)*100:.1f}%)")
    log(f"가장 약했던 10일:")
    for d, v in bs.nsmallest(10).items():
        log(f"  {d}: {v*100:.1f}%")

    # --- ② 눌림 신호 + 진입일 브레드스 ---
    log("\n눌림 신호 생성(진입일 브레드스 부착) ...")
    sigs = []
    for code, df in frames.items():
        m = meta.get(code, {})
        nm = m.get("name", "")
        if _excluded(nm) or len(df) < 230:
            continue
        close = df["close"].to_numpy(); low_a = df["low"].to_numpy(); vol = df["volume"].to_numpy()
        ma112 = df["close"].rolling(112, min_periods=112).mean().to_numpy()
        ma224 = df["close"].rolling(224, min_periods=224).mean().to_numpy()
        n = len(df); prev_pass = False
        for i in range(229, n):
            d = df["date"].iat[i]
            if d < CONTIGUOUS_START:
                continue
            A112, B224 = ma112[i], ma224[i]
            if pd.isna(A112) or pd.isna(B224) or B224 == 0:
                prev_pass = False; continue
            above = close[i] / B224 - 1
            if not (close[i] > A112 and 0 < above <= 0.15):
                prev_pass = False; continue
            v5 = vol[i-4:i+1].mean(); v20 = vol[i-19:i+1].mean()
            if v20 <= 0 or v5 / v20 > 1.0:
                prev_pass = False; continue
            sig = evaluate_pullback_signal(df.iloc[:i+1], m)
            passed = sig.get("pass") and (sig.get("score", 0) >= SCORE_CUT)
            if passed and not prev_pass:
                outcome, ret = _simulate(close, low_a, ma112, i, close[i], B224, 0.20)
                sigs.append({"date": d, "breadth": breadth.get(d, 0.0),
                             "outcome": outcome, "ret": ret,
                             "align": sig["ma20"] > sig["ma60"] > sig["ma112"]})
            prev_pass = bool(passed)
    log(f"눌림 신호 {len(sigs)}건")

    def agg(rows):
        wins = [r for r in rows if r["outcome"] == "win"]
        losses = [r for r in rows if r["outcome"] == "loss"]
        closed = len(wins) + len(losses)
        wr = len(wins)/closed*100 if closed else 0
        exp = sum(r["ret"] for r in (wins+losses))/closed if closed else 0
        return len(rows), len(wins), len(losses), round(wr, 1), round(exp, 2)

    # --- ③ 브레드스 버킷별 승률 ---
    buckets = [("<40%", 0, .40), ("40~50%", .40, .50), ("50~60%", .50, .60),
               ("60~70%", .60, .70), (">=70%", .70, 1.01)]
    log("\n=== 진입일 브레드스 버킷별 눌림 성과 (+20%/112선) ===")
    log(f"{'브레드스':<10}{'신호':>5}{'승':>4}{'패':>4}{'승률%':>7}{'기대값%':>8}")
    for name, lo, hi in buckets:
        rows = [s for s in sigs if lo <= s["breadth"] < hi]
        n, w, l, wr, exp = agg(rows)
        log(f"{name:<10}{n:>5}{w:>4}{l:>4}{wr:>7}{exp:>8}")

    # 게이트 비교
    log("\n=== 레짐 게이트 비교 ===")
    for name, rows in [("전체(게이트 없음)", sigs),
                       ("레짐>=50%(원안)", [s for s in sigs if s["breadth"] >= .50]),
                       ("레짐>=60%", [s for s in sigs if s["breadth"] >= .60]),
                       ("레짐<50%(약세만)", [s for s in sigs if s["breadth"] < .50]),
                       ("과열회피<70%", [s for s in sigs if s["breadth"] < .70]),
                       ("과열회피<65%", [s for s in sigs if s["breadth"] < .65]),
                       ("스윗스팟 45~68%", [s for s in sigs if .45 <= s["breadth"] < .68]),
                       ("과열회피<70%+정배열", [s for s in sigs if s["breadth"] < .70 and s["align"]])]:
        n, w, l, wr, exp = agg(rows)
        log(f"  {name:<18} 신호 {n:>3} · 승률 {wr:>5}% · 기대값 {exp:>6}%")


if __name__ == "__main__":
    main()
