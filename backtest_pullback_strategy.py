# -*- coding: utf-8 -*-
"""
backtest_pullback_strategy.py — 눌림 승률 상향 전략 백테스트 (5개 전략 비교)

baseline(현행) 눌림 신호를 동일 캐시(2025-01-15~)로 생성하되, 승률을 높일
후보 전략을 진입·청산 양면에서 변형해 승률·기대값을 비교한다.

전략:
  1) 목표 현실화   : 익절 목표 +20% → +10/+12/+15% (낮은 바 = 도달 잦음)
  5b)단계 익절     : 평단 +10% 도달 시 절반 익절 + 잔량 112선 트레일
  2) 정배열        : MA20>MA60>MA112 게이트
  3) 반등 확인 진입: 첫 충족일 진입 대신, 이후 3일 내 '전일 고가 돌파'한 날 진입
                     (= 미세 저항 재돌파 = 돌파-눌림 융합 근사)
  4) 시장 레짐     : 진입일 시장 브레드스(시총 1000억+ 종목 중 112선 위 비율) ≥ 50%
  9) 조합          : 정배열+반등확인+레짐 (+20% / +10%)

승=익절 목표 도달 / 패=112선 종가 이탈 / 단테 1:1:2 분할.
기대값은 실제 실현수익률 평균(청산 신호 기준)으로 산출.

사용: python backtest_pullback_strategy.py [--since 20250115]
"""
from __future__ import annotations
import argparse, json, os, sys
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
from backtest_scanners import (load_ticker_frames, _excluded, B_DIP,
                               MIN_CAP, SCORE_CUT, CONTIGUOUS_START)
from collect_theme_flow import evaluate_pullback_signal

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
REGIME_MIN_BREADTH = 0.50      # 시장 브레드스 게이트
CONFIRM_WINDOW = 3             # 반등확인: 이후 N일 내 전일 고가 돌파


def log(m): print(m, flush=True)


# --- 변형 시뮬레이터 (단테 1:1:2 분할, 목표 파라미터화) ---
def _simulate(close, low, ma112, entry_i, entry_close, main_target, win_ret):
    tranches = [(entry_close, 1.0)]
    b_target = entry_close * (1 - B_DIP)
    b_filled = m_filled = False
    def avg():
        tw = sum(w for _, w in tranches)
        return sum(p * w for p, w in tranches) / tw
    n = len(close)
    for j in range(entry_i + 1, n):
        if not b_filled and low[j] <= b_target:
            tranches.append((b_target, 1.0)); b_filled = True
        if not m_filled and main_target and low[j] <= main_target:
            tranches.append((main_target, 2.0)); m_filled = True
        a = avg()
        if close[j] >= a * (1 + win_ret):
            return ("win", win_ret * 100)
        if not (ma112[j] != ma112[j]) and close[j] < ma112[j]:
            return ("loss", (close[j] / a - 1.0) * 100)
    a = avg()
    return ("open", (close[n - 1] / a - 1.0) * 100)


def _simulate_staged(close, low, ma112, entry_i, entry_close, main_target, first_tp=0.10):
    """평단 +first_tp 도달 시 절반 익절(확정), 잔량은 112선 종가 이탈까지 트레일."""
    tranches = [(entry_close, 1.0)]
    b_target = entry_close * (1 - B_DIP)
    b_filled = m_filled = False
    def avg():
        tw = sum(w for _, w in tranches)
        return sum(p * w for p, w in tranches) / tw
    n = len(close)
    half_sold = False; banked = 0.0
    for j in range(entry_i + 1, n):
        if not b_filled and low[j] <= b_target:
            tranches.append((b_target, 1.0)); b_filled = True
        if not m_filled and main_target and low[j] <= main_target:
            tranches.append((main_target, 2.0)); m_filled = True
        a = avg()
        if not half_sold and close[j] >= a * (1 + first_tp):
            banked = first_tp * 0.5; half_sold = True       # 절반 +first_tp 확정
        if not (ma112[j] != ma112[j]) and close[j] < ma112[j]:
            rem = (close[j] / a - 1.0)
            if half_sold:
                return ("win", (banked + rem * 0.5) * 100)  # 절반 익절 = 승으로 분류
            return ("loss", rem * 100)
    # 데이터 끝: 잔량 미실현은 계상 안 함(룩어헤드/마킹 방지). 절반 익절분만 확정 승으로.
    if half_sold:
        return ("win", banked * 100)        # 확정 +5%(=+10%×절반)만 인정, 잔량은 무시
    a = avg()
    return ("open", (close[n - 1] / a - 1.0) * 100)


def build_breadth(frames, meta):
    """일자별 시장 브레드스 = (시총 1000억+ 종목 중 112선 위) / (유효). 트레일링 ma112 — 룩어헤드 없음."""
    above = {}; total = {}
    for code, df in frames.items():
        cap_won = (meta.get(code, {}) or {}).get("market_cap") or 0
        if cap_won / 1e8 < MIN_CAP:
            continue
        if len(df) < 112:
            continue
        close = df["close"].to_numpy()
        ma112 = df["close"].rolling(112, min_periods=112).mean().to_numpy()
        dates = df["date"].tolist()
        for i in range(len(df)):
            if pd.isna(ma112[i]):
                continue
            d = dates[i]
            total[d] = total.get(d, 0) + 1
            if close[i] > ma112[i]:
                above[d] = above.get(d, 0) + 1
    return {d: (above.get(d, 0) / total[d]) for d in total if total[d] > 0}


def gen_signals(since):
    frames, meta = load_ticker_frames()
    log(f"종목 {len(frames)}개 로드. 시장 브레드스 산출 중 ...")
    breadth = build_breadth(frames, meta)
    log(f"브레드스 일자 {len(breadth)}일. 눌림 신호 생성 중 ...")

    sigs = []
    for code, df in frames.items():
        m = meta.get(code, {})
        nm = m.get("name", "")
        if _excluded(nm) or len(df) < 230:
            continue
        close = df["close"].to_numpy(); low = df["low"].to_numpy()
        high = df["high"].to_numpy(); vol = df["volume"].to_numpy()
        ma112 = df["close"].rolling(112, min_periods=112).mean().to_numpy()
        ma224 = df["close"].rolling(224, min_periods=224).mean().to_numpy()
        n = len(df); prev_pass = False
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
                d = df["date"].iat[i]
                align = (sig["ma20"] > sig["ma60"] > sig["ma112"])
                regime_ok = breadth.get(d, 0.0) >= REGIME_MIN_BREADTH
                # 반등확인: 이후 CONFIRM_WINDOW일 내 전일(i) 고가 돌파한 첫날 j 진입
                cj = None
                for j in range(i+1, min(i+1+CONFIRM_WINDOW, n)):
                    if close[j] > high[i]:
                        cj = j; break
                rec = {
                    "code": code, "date": d, "align": bool(align), "regime_ok": bool(regime_ok),
                    "base":  _simulate(close, low, ma112, i, close[i], B224, 0.20),
                    "t10":   _simulate(close, low, ma112, i, close[i], B224, 0.10),
                    "t12":   _simulate(close, low, ma112, i, close[i], B224, 0.12),
                    "t15":   _simulate(close, low, ma112, i, close[i], B224, 0.15),
                    "staged":_simulate_staged(close, low, ma112, i, close[i], B224, 0.10),
                    "confirmed": cj is not None,
                    "cf20":  _simulate(close, low, ma112, cj, close[cj], B224, 0.20) if cj is not None else None,
                    "cf10":  _simulate(close, low, ma112, cj, close[cj], B224, 0.10) if cj is not None else None,
                }
                sigs.append(rec)
            prev_pass = bool(passed)
    return sigs


def agg(sigs, key):
    """key = 결과 슬롯 이름. None 결과(미진입)는 제외."""
    res = [s[key] for s in sigs if s.get(key) is not None]
    wins = [r for r in res if r[0] == "win"]
    losses = [r for r in res if r[0] == "loss"]
    closed = len(wins) + len(losses)
    wr = len(wins) / closed if closed else 0
    avg_win = sum(r[1] for r in wins) / len(wins) if wins else 0
    avg_loss = sum(r[1] for r in losses) / len(losses) if losses else 0
    exp = sum(r[1] for r in (wins + losses)) / closed if closed else 0  # 실제 실현수익 평균
    return {
        "signals": len(res), "wins": len(wins), "losses": len(losses), "open": len(res) - closed,
        "win_rate_pct": round(wr * 100, 1),
        "avg_win_pct": round(avg_win, 2), "avg_loss_pct": round(avg_loss, 2),
        "expectancy_pct": round(exp, 2),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=CONTIGUOUS_START)
    a = ap.parse_args()
    sigs = gen_signals(a.since)
    log(f"baseline 눌림 신호 {len(sigs)}개\n")

    A = lambda s: s["align"]
    R = lambda s: s["regime_ok"]
    C = lambda s: s["confirmed"]

    # (라벨, 부분집합 필터, 결과키)
    variants = [
        ("0. baseline (+20%)",            lambda s: True, "base"),
        ("1a. 목표 +10%",                  lambda s: True, "t10"),
        ("1b. 목표 +12%",                  lambda s: True, "t12"),
        ("1c. 목표 +15%",                  lambda s: True, "t15"),
        ("5b. 단계익절(+10%절반·트레일)",  lambda s: True, "staged"),
        ("2. 정배열 (+20%)",               A, "base"),
        ("3. 반등확인 (+20%)",             C, "cf20"),
        ("4. 레짐≥50% (+20%)",            R, "base"),
        ("9a. 정배열+반등확인+레짐(+20%)", lambda s: A(s) and R(s) and C(s), "cf20"),
        ("9b. 정배열+반등확인+레짐(+10%)", lambda s: A(s) and R(s) and C(s), "cf10"),
        ("9c. 정배열+레짐 +단계익절",      lambda s: A(s) and R(s), "staged"),
    ]

    rows = {}
    print(f"{'전략':<30}{'신호':>5}{'승':>4}{'패':>4}{'승률%':>7}{'평균익%':>8}{'평균손%':>8}{'기대값%':>8}")
    for name, filt, key in variants:
        r = agg([s for s in sigs if filt(s)], key)
        rows[name] = r
        print(f"{name:<30}{r['signals']:>5}{r['wins']:>4}{r['losses']:>4}"
              f"{r['win_rate_pct']:>7}{r['avg_win_pct']:>8}{r['avg_loss_pct']:>8}{r['expectancy_pct']:>8}")

    out = os.path.join(DATA, "pullback_strategy_scorecard.json")
    json.dump({"since": a.since, "regime_min_breadth": REGIME_MIN_BREADTH,
               "confirm_window": CONFIRM_WINDOW, "variants": rows},
              open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    log(f"\n저장: {out}")


if __name__ == "__main__":
    main()
