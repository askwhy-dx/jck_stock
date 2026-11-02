# -*- coding: utf-8 -*-
"""
backtest_entry_timing_regime.py — A안(집행 시점) · C안(국면 게이트) 실측

사용자 결정(2026-08-25): B안(AND 완화)은 폐지. A·C만 숫자로 확인한다.

A안 — 판정을 장마감 후로 옮기고 집행을 익일로 미루면, 통과율 이득(조건별 1.3~2.3배)을
      익일 시가 갭이 까먹는가? 같은 신호를 두 방식으로 집행해 직접 비교한다.
        · 당일 종가 집행 : 진입가 = close[i]          (현행 백테스트 기준)
        · 익일 시가 집행 : 진입가 = open[i+1]         (장마감 후 판정 → 다음날 집행)
      청산 판정 시작 시점은 둘 다 i+1 로 동일하게 두어 **진입가만** 다르게 한다.

C안 — 종목 게이트 대신 국면(시장 breadth)으로 거르면 얼마나 개선되는가?
      breadth = (시총 1000억+ 종목 중 종가 > MA112) / 유효  ← compute_market_breadth 와 동일 정의를
      과거 전 구간에 대해 날짜별로 재계산해 신호에 붙인다.
      · breadth 수준(사분위) 별 성적
      · breadth 방향(자기 MA20 대비 상승/하락) 별 성적
      · 전부충족 게이트 × 국면 결합

매매 모델: simulate_dante (단테 1:1:2 · 평단 +20% 종가 도달 익절 / 112선 종가 이탈 손절)

사용: python backtest_entry_timing_regime.py [--since 20240101] [--cache-start 20221021]
"""
from __future__ import annotations
import argparse, json, math, os, sys
from collections import defaultdict

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
from backtest_scanners import (load_ticker_frames, simulate_dante, _excluded,
                               WIN_RET, SCORE_CUT)
from collect_theme_flow import evaluate_pullback_signal, THRESHOLDS as PB_TH
from backtest_gate_tolerance import GATES

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
CAP_MIN_BILLION = PB_TH["min_market_cap_billion"]


def log(m): print(m, flush=True)


def _phi(x): return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def ztest(w1, n1, w2, n2):
    if n1 == 0 or n2 == 0:
        return (0.0, 0.0, 0.0, 1.0)
    p1, p2 = w1 / n1, w2 / n2
    pool = (w1 + w2) / (n1 + n2)
    se = math.sqrt(pool * (1 - pool) * (1 / n1 + 1 / n2))
    if se == 0:
        return (p1, p2, 0.0, 1.0)
    z = (p1 - p2) / se
    return (p1, p2, z, 2 * (1 - _phi(abs(z))))


def agg(sigs, key="outcome"):
    """key: outcome(당일 종가 집행) 또는 outcome_open(익일 시가 집행)"""
    rk = "ret_pct" if key == "outcome" else "ret_pct_open"
    wins = [s for s in sigs if s.get(key) == "win"]
    losses = [s for s in sigs if s.get(key) == "loss"]
    closed = len(wins) + len(losses)
    avg_loss = sum(s[rk] for s in losses) / len(losses) if losses else 0.0
    wr = len(wins) / closed if closed else 0.0
    lr = len(losses) / closed if closed else 0.0
    return {"signals": len(sigs), "wins": len(wins), "losses": len(losses),
            "closed": closed, "win_rate_pct": round(wr * 100, 1),
            "avg_loss_pct": round(avg_loss, 2),
            "expectancy_pct": round(wr * (WIN_RET * 100) + lr * avg_loss, 2)}


HEAD = f"{'구분':<26}{'신호':>6}{'승':>5}{'패':>5}{'승률%':>8}{'평균손실%':>10}{'기대값%':>9}"


def row(label, r, w=26):
    return (f"{label:<{w}}{r['signals']:>6}{r['wins']:>5}{r['losses']:>5}"
            f"{r['win_rate_pct']:>8}{r['avg_loss_pct']:>10}{r['expectancy_pct']:>9}")


def build_breadth(frames, meta):
    """날짜별 시장 breadth = (시총1000억+ · 종가>MA112) / 유효.
    compute_market_breadth 는 '스캔 시점 1개'만 내므로, 백테스트용으로 전 구간 재계산한다."""
    above = defaultdict(int)
    total = defaultdict(int)
    for code, df in frames.items():
        m = meta.get(code, {})
        cap_b = (m.get("market_cap") or 0) / 1e8
        if cap_b < CAP_MIN_BILLION or len(df) < 112:
            continue
        c = df["close"].astype(float)
        ma112 = c.rolling(112, min_periods=112).mean()
        ok = ma112.notna()
        d = df["date"].to_numpy()[ok.to_numpy()]
        hit = (c[ok] > ma112[ok]).to_numpy()
        for dt, h in zip(d, hit):
            total[dt] += 1
            if h:
                above[dt] += 1
    br = {d: above[d] / total[d] for d in total if total[d] > 0}
    s = pd.Series(br).sort_index()
    ma20 = s.rolling(20, min_periods=20).mean()
    return s, ma20


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="20240101")
    ap.add_argument("--cache-start", default="20221021")
    a = ap.parse_args()

    log(f"캐시 로드 (cache_start={a.cache_start}) ...")
    frames, meta = load_ticker_frames(a.cache_start)
    log(f"종목 {len(frames)}개")

    log("날짜별 시장 breadth 재계산 ...")
    breadth, breadth_ma20 = build_breadth(frames, meta)
    log(f"breadth 산출 {len(breadth)}일 "
        f"({breadth.index[0]}~{breadth.index[-1]} · 중앙값 {breadth.median()*100:.1f}%)")

    log("신호 생성 + 두 집행 방식 동시 시뮬 ...")
    sigs = []
    skipped_last_bar = 0
    for code, df in frames.items():
        m = meta.get(code, {})
        nm = m.get("name", "")
        if _excluded(nm) or len(df) < 230:
            continue
        close = df["close"].to_numpy(); low = df["low"].to_numpy()
        openp = df["open"].to_numpy(); vol = df["volume"].to_numpy()
        dates = df["date"].to_numpy()
        ma112 = df["close"].rolling(112, min_periods=112).mean().to_numpy()
        ma224 = df["close"].rolling(224, min_periods=224).mean().to_numpy()
        n = len(df)
        prev_pass = False
        for i in range(229, n):
            if dates[i] < a.since:
                continue
            A112, B224 = ma112[i], ma224[i]
            if pd.isna(A112) or pd.isna(B224) or B224 == 0:
                prev_pass = False; continue
            abovep = close[i] / B224 - 1
            if not (close[i] > A112 and 0 < abovep <= 0.15):
                prev_pass = False; continue
            v5 = vol[i-4:i+1].mean(); v20 = vol[i-19:i+1].mean()
            if v20 <= 0 or v5 / v20 > 1.0:
                prev_pass = False; continue
            sig = evaluate_pullback_signal(df.iloc[:i+1], m)
            passed = sig.get("pass") and (sig.get("score", 0) >= SCORE_CUT)
            if passed and not prev_pass:
                if i + 1 >= n:                      # 익일이 없는 마지막 봉 → 비교 불가
                    skipped_last_bar += 1
                    prev_pass = True; continue
                oc, ei, av, rz, nt = simulate_dante(close, low, ma112, i, close[i], B224)
                oo, ei2, av2, rz2, nt2 = simulate_dante(close, low, ma112, i, openp[i+1], B224)
                d = dates[i]
                b = float(breadth.get(d, float("nan")))
                bm = float(breadth_ma20.get(d, float("nan")))
                rec = {
                    "code": code, "entry": d, "year": d[:4],
                    "outcome": oc, "ret_pct": round(rz * 100, 2),
                    "outcome_open": oo, "ret_pct_open": round(rz2 * 100, 2),
                    "gap_pct": round((openp[i+1] / close[i] - 1) * 100, 2),
                    "breadth": None if math.isnan(b) else round(b * 100, 2),
                    "breadth_up": None if (math.isnan(b) or math.isnan(bm)) else bool(b > bm),
                    "score": sig["score"], "vol_shrink": sig["vol_shrink"],
                    "candle": sig["candle"], "above224": abs(sig["above_ma224_pct"]),
                    "align": (sig["ma20"] > sig["ma60"] > sig["ma112"]),
                }
                rec["_pass"] = sum(1 for fn in GATES.values() if fn(rec))
                sigs.append(rec)
            prev_pass = bool(passed)

    log(f"신호 {len(sigs)}개 (마지막 봉 신호 {skipped_last_bar}건은 익일 없음으로 제외)\n")
    out = {"since": a.since, "cache_start": a.cache_start, "signals": len(sigs)}

    # ═══ A안 ═══════════════════════════════════════════════════
    print("=" * 78)
    print("A안 — 당일 종가 집행 vs 익일 시가 집행 (동일 신호 · 진입가만 다름)")
    print("=" * 78)
    print(HEAD)
    ac = agg(sigs, "outcome"); ao = agg(sigs, "outcome_open")
    print(row("당일 종가 집행 (현행)", ac))
    print(row("익일 시가 집행 (A안)", ao))
    p1, p2, z, p = ztest(ac["wins"], ac["closed"], ao["wins"], ao["closed"])
    print(f"\n  승률 차 {(p1-p2)*100:+.1f}%p · z={z:.2f} · p={p:.4f} "
          f"{'✅ 유의' if p < 0.05 else '❌ 유의하지 않음'}")
    gaps = [s["gap_pct"] for s in sigs]
    gs = pd.Series(gaps)
    print(f"  익일 시가 갭: 평균 {gs.mean():+.2f}% · 중앙값 {gs.median():+.2f}% · "
          f"상승갭 {sum(1 for g in gaps if g > 0)}건 / 하락갭 {sum(1 for g in gaps if g < 0)}건")
    out["A"] = {"close_exec": ac, "open_exec": ao, "z": round(z, 3), "p": round(p, 4),
                "gap_mean": round(float(gs.mean()), 3), "gap_median": round(float(gs.median()), 3)}

    print("\n  [전부충족 신호만] — 실제 A안 운용 조건")
    print(HEAD)
    full = [s for s in sigs if s["_pass"] == 5]
    fc = agg(full, "outcome"); fo = agg(full, "outcome_open")
    print(row("당일 종가 집행", fc))
    print(row("익일 시가 집행", fo))
    p1, p2, z2, p_ = ztest(fc["wins"], fc["closed"], fo["wins"], fo["closed"])
    print(f"  승률 차 {(p1-p2)*100:+.1f}%p · p={p_:.4f} "
          f"{'✅ 유의' if p_ < 0.05 else '❌ 유의하지 않음'}")
    out["A_full"] = {"close_exec": fc, "open_exec": fo, "p": round(p_, 4)}

    print("\n  [연도별 방향]")
    print(f"{'연도':<8}{'당일 승률%':>12}{'익일 승률%':>12}{'당일 기대값%':>14}{'익일 기대값%':>14}")
    for y in sorted({s["year"] for s in sigs}):
        g = [s for s in sigs if s["year"] == y]
        r1 = agg(g, "outcome"); r2 = agg(g, "outcome_open")
        print(f"{y:<8}{r1['win_rate_pct']:>12}{r2['win_rate_pct']:>12}"
              f"{r1['expectancy_pct']:>14}{r2['expectancy_pct']:>14}")

    # ═══ C안 ═══════════════════════════════════════════════════
    print("\n" + "=" * 78)
    print("C안 — 국면(시장 breadth) 게이트")
    print("=" * 78)
    have = [s for s in sigs if s["breadth"] is not None]
    bs = pd.Series([s["breadth"] for s in have])
    qs = [bs.quantile(q) for q in (0.25, 0.5, 0.75)]
    print(f"  breadth 사분위: Q1 {qs[0]:.1f}% · 중앙 {qs[1]:.1f}% · Q3 {qs[2]:.1f}%  "
          f"(신호 {len(have)}건 · 과열임계 {PB_TH['regime_overheat_breadth']*100:.0f}%)\n")

    print("  [수준별 — breadth 사분위]")
    print(HEAD)
    buckets = {
        f"Q4 최고 (>{qs[2]:.0f}%)": [s for s in have if s["breadth"] > qs[2]],
        f"Q3 ({qs[1]:.0f}~{qs[2]:.0f}%)": [s for s in have if qs[1] < s["breadth"] <= qs[2]],
        f"Q2 ({qs[0]:.0f}~{qs[1]:.0f}%)": [s for s in have if qs[0] < s["breadth"] <= qs[1]],
        f"Q1 최저 (≤{qs[0]:.0f}%)": [s for s in have if s["breadth"] <= qs[0]],
    }
    cb = {}
    for k, g in buckets.items():
        r = agg(g); cb[k] = r; print(row(k, r))
    out["C_level"] = cb

    print("\n  [방향별 — breadth가 자기 MA20 위/아래]")
    print(HEAD)
    up = [s for s in have if s["breadth_up"] is True]
    dn = [s for s in have if s["breadth_up"] is False]
    ru, rd = agg(up), agg(dn)
    print(row("breadth 상승 (MA20 위)", ru))
    print(row("breadth 하락 (MA20 아래)", rd))
    p1, p2, z3, p3 = ztest(ru["wins"], ru["closed"], rd["wins"], rd["closed"])
    print(f"  승률 차 {(p1-p2)*100:+.1f}%p · z={z3:.2f} · p={p3:.4f} "
          f"{'✅ 유의' if p3 < 0.05 else '❌ 유의하지 않음'}")
    out["C_direction"] = {"up": ru, "down": rd, "p": round(p3, 4)}

    print("\n  [연도별 방향 일관성 — breadth 상승 vs 하락]")
    print(f"{'연도':<8}{'상승 승률%':>12}{'n':>6}{'하락 승률%':>12}{'n':>6}  방향")
    agree = 0
    yrs = sorted({s["year"] for s in have})
    for y in yrs:
        u = agg([s for s in up if s["year"] == y]); d_ = agg([s for s in dn if s["year"] == y])
        ok = u["win_rate_pct"] > d_["win_rate_pct"]
        agree += ok
        print(f"{y:<8}{u['win_rate_pct']:>12}{u['closed']:>6}{d_['win_rate_pct']:>12}{d_['closed']:>6}"
              f"  {'상승 우세' if ok else '하락 우세'}")
    print(f"  → 방향 일치 {agree}/{len(yrs)} 연도")
    out["C_direction_years"] = f"{agree}/{len(yrs)}"

    # ═══ 결합 ═══════════════════════════════════════════════════
    print("\n" + "=" * 78)
    print("A + C 결합 — 무엇을 켰을 때 가장 좋은가 (기대값 순)")
    print("=" * 78)
    print(HEAD)
    combos = {
        "현행 (전부충족·당일종가)": (full, "outcome"),
        "A안만 (전부충족·익일시가)": (full, "outcome_open"),
        "C안만 (전부충족·breadth상승)": ([s for s in full if s["breadth_up"]], "outcome"),
        "A+C (전부충족·상승·익일)": ([s for s in full if s["breadth_up"]], "outcome_open"),
        "C안만 (게이트무시·breadth상승)": ([s for s in sigs if s["breadth_up"]], "outcome"),
    }
    res = {}
    for k, (g, key) in combos.items():
        r = agg(g, key); res[k] = r
    for k, r in sorted(res.items(), key=lambda x: -x[1]["expectancy_pct"]):
        print(row(k, r))
    out["combined"] = res

    path = os.path.join(DATA, "entry_timing_regime.json")
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    log(f"\n저장: {path}")


if __name__ == "__main__":
    main()
