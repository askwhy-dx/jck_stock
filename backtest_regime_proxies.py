# -*- coding: utf-8 -*-
"""
backtest_regime_proxies.py — C안 재심: breadth 말고 다른 국면 지표는 되는가

1차 결과(backtest_entry_timing_regime.py): 시장 breadth(>MA112) 로는 국면 게이트가 안 먹혔다
(방향별 p=0.80 · 연도 일치 1/3). 그런데 연도 효과 자체는 실재한다
(2024 승률 11.5%/기대값 -0.78% ↔ 2025 21.8%/+1.85%).
→ "국면이 없다"가 아니라 "breadth 가 그 국면을 못 잡는다"일 수 있다. 지표를 갈아 재심한다.

후보 국면 지표 (전부 캐시 OHLCV 로만 산출 · 추가 API 없음):
  R1 breadth112   시총1000억+ 중 종가>MA112 비율            (1차에서 쓴 것 · 대조군)
  R2 breadth224   같은 정의, MA224 기준                      (더 긴 추세)
  R3 idx_ma120    동일가중 시장지수 > 자기 MA120
  R4 idx_ret20    동일가중 시장지수 20일 수익률 > 0
  R5 vol_regime   시장 일간수익률 20일 표준편차 (저변동 = 하위 50%)

각 지표를 신호일 기준으로 붙여 ON/OFF 승률을 z검정하고, 연도 방향 일치를 함께 본다.
판정: ✅ 채택 = p<0.05 AND 3/3 연도 일치 / ⚠️ 보류 = p<0.05 이나 방향 갈림 / ❌ 기각 = p>=0.05

사용: python backtest_regime_proxies.py [--since 20240101] [--cache-start 20221021]
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
CAP_MIN = PB_TH["min_market_cap_billion"]


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


def agg(sigs):
    wins = [s for s in sigs if s["outcome"] == "win"]
    losses = [s for s in sigs if s["outcome"] == "loss"]
    closed = len(wins) + len(losses)
    avg_loss = sum(s["ret_pct"] for s in losses) / len(losses) if losses else 0.0
    wr = len(wins) / closed if closed else 0.0
    lr = len(losses) / closed if closed else 0.0
    return {"signals": len(sigs), "wins": len(wins), "losses": len(losses), "closed": closed,
            "win_rate_pct": round(wr * 100, 1), "avg_loss_pct": round(avg_loss, 2),
            "expectancy_pct": round(wr * (WIN_RET * 100) + lr * avg_loss, 2)}


def build_regimes(frames, meta):
    """날짜별 국면 지표 5종. 전부 캐시 OHLCV 로만 만든다."""
    above112 = defaultdict(int); above224 = defaultdict(int); tot = defaultdict(int)
    ret_sum = defaultdict(float); ret_cnt = defaultdict(int)
    for code, df in frames.items():
        m = meta.get(code, {})
        if (m.get("market_cap") or 0) / 1e8 < CAP_MIN or len(df) < 224:
            continue
        c = df["close"].astype(float)
        d = df["date"].to_numpy()
        ma112 = c.rolling(112, min_periods=112).mean()
        ma224 = c.rolling(224, min_periods=224).mean()
        ok = ma224.notna().to_numpy()
        cv = c.to_numpy()
        for k in np.nonzero(ok)[0]:
            tot[d[k]] += 1
            if cv[k] > ma112.iat[k]: above112[d[k]] += 1
            if cv[k] > ma224.iat[k]: above224[d[k]] += 1
        r = c.pct_change().to_numpy()
        for k in np.nonzero(ok)[0]:
            if not np.isnan(r[k]):
                ret_sum[d[k]] += r[k]; ret_cnt[d[k]] += 1

    dates = sorted(tot)
    b112 = pd.Series({d: above112[d] / tot[d] for d in dates}).sort_index()
    b224 = pd.Series({d: above224[d] / tot[d] for d in dates}).sort_index()
    mret = pd.Series({d: (ret_sum[d] / ret_cnt[d] if ret_cnt[d] else 0.0) for d in dates}).sort_index()
    idx = (1 + mret).cumprod()                       # 동일가중 시장지수
    reg = pd.DataFrame({
        "b112": b112, "b224": b224, "idx": idx,
        "idx_ma120": idx.rolling(120, min_periods=120).mean(),
        "idx_ret20": idx.pct_change(20),
        "vol20": mret.rolling(20, min_periods=20).std(),
        "b112_ma20": b112.rolling(20, min_periods=20).mean(),
        "b224_ma20": b224.rolling(20, min_periods=20).mean(),
    })
    return reg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="20240101")
    ap.add_argument("--cache-start", default="20221021")
    a = ap.parse_args()

    log(f"캐시 로드 ...")
    frames, meta = load_ticker_frames(a.cache_start)
    log(f"종목 {len(frames)}개 · 국면 지표 산출 ...")
    reg = build_regimes(frames, meta)
    vol_med = reg["vol20"].median()
    log(f"국면 {len(reg)}일 ({reg.index[0]}~{reg.index[-1]}) · vol20 중앙값 {vol_med*100:.2f}%")

    log("신호 생성 ...")
    sigs = []
    for code, df in frames.items():
        m = meta.get(code, {}); nm = m.get("name", "")
        if _excluded(nm) or len(df) < 230:
            continue
        close = df["close"].to_numpy(); low = df["low"].to_numpy(); vol = df["volume"].to_numpy()
        dates = df["date"].to_numpy()
        ma112 = df["close"].rolling(112, min_periods=112).mean().to_numpy()
        ma224 = df["close"].rolling(224, min_periods=224).mean().to_numpy()
        prev = False
        for i in range(229, len(df)):
            if dates[i] < a.since: continue
            A, B = ma112[i], ma224[i]
            if pd.isna(A) or pd.isna(B) or B == 0: prev = False; continue
            if not (close[i] > A and 0 < close[i] / B - 1 <= 0.15): prev = False; continue
            v5 = vol[i-4:i+1].mean(); v20 = vol[i-19:i+1].mean()
            if v20 <= 0 or v5 / v20 > 1.0: prev = False; continue
            sig = evaluate_pullback_signal(df.iloc[:i+1], m)
            ok = sig.get("pass") and sig.get("score", 0) >= SCORE_CUT
            if ok and not prev:
                oc, ei, av, rz, nt = simulate_dante(close, low, ma112, i, close[i], B)
                d = dates[i]
                if d not in reg.index: prev = True; continue
                r = reg.loc[d]
                rec = {"code": code, "entry": d, "year": d[:4], "outcome": oc,
                       "ret_pct": round(rz * 100, 2),
                       "score": sig["score"], "vol_shrink": sig["vol_shrink"],
                       "candle": sig["candle"], "above224": abs(sig["above_ma224_pct"]),
                       "align": (sig["ma20"] > sig["ma60"] > sig["ma112"]),
                       "R1_breadth112": None if pd.isna(r.b112_ma20) else bool(r.b112 > r.b112_ma20),
                       "R2_breadth224": None if pd.isna(r.b224_ma20) else bool(r.b224 > r.b224_ma20),
                       "R3_idx_ma120": None if pd.isna(r.idx_ma120) else bool(r.idx > r.idx_ma120),
                       "R4_idx_ret20": None if pd.isna(r.idx_ret20) else bool(r.idx_ret20 > 0),
                       "R5_low_vol": None if pd.isna(r.vol20) else bool(r.vol20 <= vol_med)}
                rec["_pass"] = sum(1 for fn in GATES.values() if fn(rec))
                sigs.append(rec)
            prev = bool(ok)
    log(f"신호 {len(sigs)}개\n")

    RS = ["R1_breadth112", "R2_breadth224", "R3_idx_ma120", "R4_idx_ret20", "R5_low_vol"]
    DESC = {"R1_breadth112": "breadth112 > MA20 (개선 중)",
            "R2_breadth224": "breadth224 > MA20 (장기 개선)",
            "R3_idx_ma120": "시장지수 > MA120 (강세장)",
            "R4_idx_ret20": "시장지수 20일 수익 > 0 (상승 중)",
            "R5_low_vol": "시장 변동성 하위 50% (안정)"}
    years = sorted({s["year"] for s in sigs})
    out = {"since": a.since, "signals": len(sigs), "regimes": {}}

    for scope, subset in (("전체 신호", sigs), ("전부충족 신호만", [s for s in sigs if s["_pass"] == 5])):
        print("=" * 82)
        print(f"국면 지표 재심 — {scope} (n={len(subset)})")
        print("=" * 82)
        print(f"{'지표':<30}{'ON 승률%':>10}{'n':>6}{'OFF 승률%':>11}{'n':>6}{'차%p':>8}{'p값':>9}{'연도':>7}  판정")
        for R in RS:
            on = [s for s in subset if s[R] is True]
            off = [s for s in subset if s[R] is False]
            ra, rb = agg(on), agg(off)
            p1, p2, z, p = ztest(ra["wins"], ra["closed"], rb["wins"], rb["closed"])
            agree = 0
            for y in years:
                u = agg([s for s in on if s["year"] == y]); v = agg([s for s in off if s["year"] == y])
                if u["closed"] and v["closed"] and (u["win_rate_pct"] > v["win_rate_pct"]) == (p1 > p2):
                    agree += 1
            if p >= 0.05:      verdict = "❌ 기각"
            elif agree == len(years): verdict = "✅ 채택"
            else:              verdict = "⚠️ 보류(연도 갈림)"
            print(f"{DESC[R]:<30}{ra['win_rate_pct']:>10}{ra['closed']:>6}{rb['win_rate_pct']:>11}"
                  f"{rb['closed']:>6}{(p1-p2)*100:>+8.1f}{p:>9.4f}{f'{agree}/{len(years)}':>7}  {verdict}")
            if scope == "전부충족 신호만":
                out["regimes"][R] = {"on": ra, "off": rb, "p": round(p, 4),
                                     "years_agree": f"{agree}/{len(years)}", "verdict": verdict}
        print()

    print("=" * 82)
    print("참고 — 연도 효과는 실재하는가 (게이트/국면과 무관하게)")
    print("=" * 82)
    print(f"{'연도':<8}{'신호':>7}{'승률%':>8}{'기대값%':>10}")
    for y in years:
        r = agg([s for s in sigs if s["year"] == y])
        print(f"{y:<8}{r['signals']:>7}{r['win_rate_pct']:>8}{r['expectancy_pct']:>10}")

    p = os.path.join(DATA, "regime_proxies.json")
    json.dump(out, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    log(f"\n저장: {p}")


if __name__ == "__main__":
    main()
