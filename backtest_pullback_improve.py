# -*- coding: utf-8 -*-
"""
backtest_pullback_improve.py — 눌림 승률 개선 필터 백테스트 (단테 분할매수 기준)

backtest_scanners.py 와 동일한 단테 1:1:2 분할(평단 +20% 익절 / 112선 손절)로
눌림 신호를 평가하되, 개선 필터 조합별 승률·기대값을 비교한다.

개선 필터:
  ① score ≥ 90 (상위 신호 선별)
  ② vol_shrink ≤ 0.7 (강한 매도 약화만)
  ③ 224선 ±3% 근접 (지지선 정확)
  ④ 통과일 양봉
  ⑤ 정배열 (MA20>MA60>MA112)

신호는 한 번만 생성(전체 sig 속성 포함)하고 필터별로 재집계한다.

사용: python backtest_pullback_improve.py [--since 20251201]
"""
from __future__ import annotations
import argparse, json, os, sys
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
from backtest_scanners import (load_ticker_frames, simulate_dante, _excluded,
                               WIN_RET, MIN_CAP, SCORE_CUT, CONTIGUOUS_START)
from collect_theme_flow import evaluate_pullback_signal

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def log(m): print(m, flush=True)


def gen_signals(since: str, start: str | None = None):
    """start: 캐시 로드 시작일. None 이면 CONTIGUOUS_START(20250115) — 2024 구간은 비게 된다."""
    frames, meta = load_ticker_frames(start)
    sigs = []
    for code, df in frames.items():
        m = meta.get(code, {})
        nm = m.get("name", "")
        if _excluded(nm) or len(df) < 230:
            continue
        close = df["close"].to_numpy(); low = df["low"].to_numpy(); vol = df["volume"].to_numpy()
        ma112 = df["close"].rolling(112, min_periods=112).mean().to_numpy()
        ma224 = df["close"].rolling(224, min_periods=224).mean().to_numpy()
        n = len(df)
        prev_pass = False
        for i in range(229, n):
            if df["date"].iat[i] < since:
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
                outcome, exit_i, avgp, realized, ntr = simulate_dante(close, low, ma112, i, close[i], B224)
                sigs.append({
                    "code": code, "entry": df["date"].iat[i],
                    "outcome": outcome, "ret_pct": round(realized*100, 2), "tranches": ntr, "days": exit_i - i,
                    # 필터 속성
                    "score": sig["score"], "vol_shrink": sig["vol_shrink"], "candle": sig["candle"],
                    "above224": abs(sig["above_ma224_pct"]),
                    "align": (sig["ma20"] > sig["ma60"] > sig["ma112"]),
                    "double_bottom": bool(sig.get("double_bottom")),
                    "db_vol_div": bool(sig.get("db_vol_div")),
                })
            prev_pass = bool(passed)
    return sigs


def agg(sigs):
    n = len(sigs)
    wins = [s for s in sigs if s["outcome"] == "win"]
    losses = [s for s in sigs if s["outcome"] == "loss"]
    closed = len(wins) + len(losses)
    avg_loss = sum(s["ret_pct"] for s in losses) / len(losses) if losses else 0
    wr = len(wins) / closed if closed else 0
    lr = len(losses) / closed if closed else 0
    return {
        "signals": n, "wins": len(wins), "losses": len(losses), "open": n - closed,
        "win_rate_pct": round(wr*100, 1),
        "avg_loss_pct": round(avg_loss, 2),
        "expectancy_pct": round(wr*(WIN_RET*100) + lr*avg_loss, 2),
    }


# 필터 술어
F = {
    "①score≥90": lambda s: s["score"] >= 90,
    "②vol≤0.7": lambda s: s["vol_shrink"] <= 0.7,
    "③224±3%": lambda s: s["above224"] <= 3,
    "④양봉": lambda s: s["candle"] == "양봉",
    "⑤정배열": lambda s: s["align"],
    "⑥쌍바닥": lambda s: s["double_bottom"],
    "⑥b쌍바닥+거래량다이버": lambda s: s["double_bottom"] and s["db_vol_div"],
    "단일V(쌍바닥X)": lambda s: not s["double_bottom"],
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=CONTIGUOUS_START)
    a = ap.parse_args()
    log("눌림 신호 생성(1회) ...")
    sigs = gen_signals(a.since)
    log(f"baseline 신호 {len(sigs)}개")

    variants = {"baseline(80컷)": sigs}
    for name, fn in F.items():
        variants[name] = [s for s in sigs if fn(s)]
    rec = [s for s in sigs if F["①score≥90"](s) and F["②vol≤0.7"](s) and F["④양봉"](s)]
    variants["추천 ①+②+④"] = rec
    allf = [s for s in sigs if all(fn(s) for fn in F.values())]
    variants["전체 ①~⑤"] = allf

    rows = {name: agg(v) for name, v in variants.items()}
    print("\n=== 눌림 개선 필터별 (단테 분할매수 · 평단 +20% 익절 / 112선 손절) ===")
    print(f"{'변형':<16}{'신호':>5}{'승':>5}{'패':>5}{'승률%':>7}{'평균손실%':>9}{'기대값%':>8}")
    for name, r in rows.items():
        print(f"{name:<16}{r['signals']:>5}{r['wins']:>5}{r['losses']:>5}"
              f"{r['win_rate_pct']:>7}{r['avg_loss_pct']:>9}{r['expectancy_pct']:>8}")

    out = os.path.join(DATA, "pullback_improve_scorecard.json")
    json.dump({"since": a.since, "variants": rows}, open(out, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    log(f"\n저장: {out}")


if __name__ == "__main__":
    main()
