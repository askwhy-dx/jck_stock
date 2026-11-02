# -*- coding: utf-8 -*-
"""
backtest_scanners.py — 눌림 vs 돌파 스캐너 손익 백테스트 (즉시 비교)

캐시(2025-01-15~)를 하루씩 리플레이하며 두 스캐너의 신호를 생성하고, 각 신호를
'페이퍼 트레이드'로 전진 추적해 손익을 집계한다.

규칙 (2026-06-11 사용자 확정):
  - 진입가 = 포착일 종가(ref)
  - 졸업(승): 고수위 수익률이 **+10% 도달**하면 인정 (한번 달성=영구 유지). 청산 +10%.
              그 후 +20%까지 갔는지는 정보로 기록.
  - 이탈(패): +10% 도달 전에 추세선(돌파=돌파선 / 눌림=112선) 종가 이탈 → 손실 확정.
  - 미청산(open): 둘 다 아닌 채 데이터 끝.
  - 노출 컷 score ≥ 80 (편입 필터). 시총 ≥ 1,000억. 우선주·스팩 제외.

산출: data/scanner_scorecard.json
  스캐너별 신호수 · 졸업률(+10) · 추가 +20 도달률 · 이탈률 · 미청산 · 평균손익 · 기대값 · 평균보유일

사용:
  python backtest_scanners.py                  # 전체 캐시 리플레이
  python backtest_scanners.py --since 20251201 # 시작일 제한(빠른 테스트)
"""
from __future__ import annotations
import argparse, glob, json, os, sys, datetime as dt
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(ROOT, "data", "krx_ohlcv_cache")
DATA = os.path.join(ROOT, "data")
CONTIGUOUS_START = "20250115"

# 스캐너 정의 재사용
from collect_breakout_flow import score_breakout, THRESHOLDS as BO_TH
from collect_theme_flow import evaluate_pullback_signal, THRESHOLDS as PB_TH

WIN_RET = 0.20        # 청산 인정(졸업) 수익률 (2026-06-11 +10%→+20% 상향)
HIGH_RET = 0.30       # +30% 추가 도달 정보
MIN_CAP = 1000        # 시총 억
SCORE_CUT = 80


def log(m): print(m, flush=True)


def load_ticker_frames(start: str | None = None):
    """캐시 전체(연속구간) → {code: DataFrame} + meta. MA224 위해 since로 자르지 않음.

    start: 캐시를 읽기 시작할 날짜(YYYYMMDD). 기본값 CONTIGUOUS_START(20250115).
      ⚠️ CONTIGUOUS_START 는 fetch_krx_history.py 로 과거를 백필하기 **전** 상수다.
         지금 캐시는 20221021~ 이 연속이라, 2024~2025 구간을 백테스트하려면
         start 를 명시로 낮춰 줘야 한다. 안 낮추면 MA224(229봉) 워밍업 때문에
         첫 신호가 2025-12월에야 생겨 "2024년부터"가 조용히 2026년만 보게 된다.
    """
    cut = start or CONTIGUOUS_START
    rows = {}
    meta = {}
    for f in sorted(glob.glob(os.path.join(CACHE, "*.json"))):
        d = os.path.basename(f).split("_")[0]
        if d < cut:
            continue
        market = "KOSPI" if "KOSPI" in os.path.basename(f) else "KOSDAQ"
        for r in json.load(open(f, encoding="utf-8")).get("rows", []):
            c = r.get("code")
            if not c or r.get("close") is None:
                continue
            rows.setdefault(c, []).append((d, r.get("open"), r.get("high"),
                                           r.get("low"), r.get("close"), r.get("volume") or 0))
            if c not in meta:
                meta[c] = {"name": r.get("name"), "market": market,
                           "market_cap": r.get("market_cap")}
            elif r.get("market_cap"):
                meta[c]["market_cap"] = r.get("market_cap")
    out = {}
    for c, rs in rows.items():
        df = pd.DataFrame(rs, columns=["date", "open", "high", "low", "close", "volume"]).sort_values("date").reset_index(drop=True)
        for col in ("open", "high", "low", "close", "volume"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        out[c] = df
    return out, meta


def _excluded(nm): return (not nm) or ("스팩" in nm) or nm.endswith("우") or nm.endswith("우B")


B_DIP = 0.015         # 1차b: 1차a 대비 -1.5% 눌림 (AI단테 §4.3 1차b -1~2%)

def simulate_dante(close, low, ma112, entry_i, entry_close, main_target):
    """단테 분할매수 1:1:2 시뮬 → (outcome, exit_i, avg, realized, n_tranches).
      1차a: 포착일 종가(w1) · 1차b: -1.5%(w1) · 2차 메인: 지지선(w2).
      익절: 평단 ×(1+WIN_RET) 종가 도달 · 손절: 112일선 종가 이탈.
    """
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
        if close[j] >= a * (1 + WIN_RET):                 # 평단 대비 +익절 → 승
            return ("win", j, a, WIN_RET, len(tranches))
        if not (ma112[j] != ma112[j]) and close[j] < ma112[j]:  # 112선 종가 이탈 → 손절
            return ("loss", j, a, close[j] / a - 1.0, len(tranches))
    a = avg()
    return ("open", n - 1, a, close[n - 1] / a - 1.0, len(tranches))


def backtest(since: str) -> dict:
    log(f"캐시 전체 로드 (신호는 {since}부터) ...")
    frames, meta = load_ticker_frames()
    log(f"종목 {len(frames)}개 로드. 신호 생성 중 ...")

    signals = {"breakout": [], "pullback": []}

    for code, df in frames.items():
        m = meta.get(code, {})
        nm = m.get("name", "")
        if _excluded(nm):
            continue
        n = len(df)
        if n < 230:
            continue
        close = df["close"].to_numpy()
        openp = df["open"].to_numpy()
        low = df["low"].to_numpy()
        vol = df["volume"].to_numpy()
        cap_won = m.get("market_cap") or 0
        cap_b = cap_won / 1e8 if cap_won else 0

        s = df["close"]
        ma112 = s.rolling(112, min_periods=112).mean().to_numpy()
        ma224 = s.rolling(224, min_periods=224).mean().to_numpy()
        vavg = df["volume"].rolling(20, min_periods=20).mean().to_numpy()

        # ===== 돌파 신호 (당일 돌파, 80+, 시총) =====
        if cap_b >= MIN_CAP:
            for i in range(224, n):
                if df["date"].iat[i] < since:
                    continue
                A, B, pA, pB = ma112[i], ma224[i], ma112[i-1], ma224[i-1]
                pv, cv = close[i-1], close[i]
                if pd.isna(A) or pd.isna(B) or pv == 0:
                    continue
                x112 = pv <= pA and cv > A
                x224 = pv <= pB and cv > B
                if not (x112 or x224):
                    continue
                ret = cv / pv - 1
                vr = vol[i] / vavg[i] if (not pd.isna(vavg[i]) and vavg[i] > 0) else 0
                if not (ret >= BO_TH["min_ret"] and vr >= BO_TH["min_vmult"]
                        and (cv / max(A, B) - 1) <= BO_TH["max_gap"]):
                    continue
                line = "112+224" if (x112 and x224) else ("112" if x112 else "224")
                is_yang = openp[i] and cv > openp[i]
                sc, _ = score_breakout(ret, vr, line, False, bool(is_yang))
                if sc < SCORE_CUT:
                    continue
                # 2차 메인 지지선 = 돌파한 선(동시는 둘 중 위=max) 값 (분할 진입 타깃)
                if line == "112":   main_t = A
                elif line == "224": main_t = B
                else:               main_t = max(A, B)
                outcome, exit_i, avgp, realized, ntr = simulate_dante(close, low, ma112, i, cv, main_t)
                signals["breakout"].append({
                    "code": code, "name": nm, "entry": df["date"].iat[i], "ref": int(cv),
                    "score": sc, "outcome": outcome, "exit": df["date"].iat[exit_i],
                    "avg": round(avgp), "tranches": ntr, "ret_pct": round(realized*100, 2),
                    "days": exit_i - i,
                })

        # ===== 눌림 신호 (pass & 80+, 스트릭 시작만) =====
        # 후보 사전필터(저비용): close>ma112 & 0<above224<=15% & vol_shrink<=1
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
            # 전체 평가 (정확)
            sub = df.iloc[:i+1]
            sig = evaluate_pullback_signal(sub, m)
            passed = sig.get("pass") and (sig.get("score", 0) >= SCORE_CUT)
            if passed and not prev_pass:
                # 2차 메인 지지선 = 224일선 (눌림 1차a 안착 후 깊은 눌림 타깃)
                outcome, exit_i, avgp, realized, ntr = simulate_dante(close, low, ma112, i, close[i], B224)
                signals["pullback"].append({
                    "code": code, "name": nm, "entry": df["date"].iat[i], "ref": int(close[i]),
                    "score": sig["score"], "outcome": outcome, "exit": df["date"].iat[exit_i],
                    "avg": round(avgp), "tranches": ntr, "ret_pct": round(realized*100, 2),
                    "days": exit_i - i,
                })
            prev_pass = bool(passed)

    # ===== 집계 =====
    def agg(sigs):
        n = len(sigs)
        wins = [s for s in sigs if s["outcome"] == "win"]
        losses = [s for s in sigs if s["outcome"] == "loss"]
        opens = [s for s in sigs if s["outcome"] == "open"]
        closed = len(wins) + len(losses)
        avg_loss = sum(s["ret_pct"] for s in losses) / len(losses) if losses else 0
        win_rate = len(wins) / closed if closed else 0
        loss_rate = len(losses) / closed if closed else 0
        # 기대값(청산 신호 기준): 승 +10% , 패 평균손실%
        expectancy = win_rate * (WIN_RET*100) + loss_rate * avg_loss
        avg_days_win = sum(s["days"] for s in wins) / len(wins) if wins else 0
        avg_tr = sum(s["tranches"] for s in sigs) / n if n else 0
        return {
            "signals": n, "closed": closed, "open": len(opens),
            "wins": len(wins), "losses": len(losses),
            "win_rate_pct": round(win_rate*100, 1),
            "avg_tranches": round(avg_tr, 2),
            "avg_loss_pct": round(avg_loss, 2),
            "expectancy_pct": round(expectancy, 2),
            "avg_days_to_win": round(avg_days_win, 1),
        }

    scorecard = {
        "generated": dt.datetime.now().isoformat(timespec="seconds"),
        "rule": {"strategy": "단테 분할매수 1:1:2", "tranche": "1차a 포착가 + 1차b -1.5% + 2차 메인 지지선",
                 "take_profit": f"평단 +{WIN_RET*100:.0f}%", "stop": "112일선 종가 이탈",
                 "score_cut": SCORE_CUT, "min_cap_billion": MIN_CAP},
        "range": {"since": since},
        "breakout": agg(signals["breakout"]),
        "pullback": agg(signals["pullback"]),
        "samples": {k: sorted(v, key=lambda x: x["entry"])[-8:] for k, v in signals.items()},
    }
    out = os.path.join(DATA, "dante_backtest_scorecard.json")
    json.dump(scorecard, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    log(f"단테 분할매수 백테스트 저장: {out} (대시보드 미패치 — 요약 카드는 forward-test 전담)")
    log(json.dumps({"breakout": scorecard["breakout"], "pullback": scorecard["pullback"]},
                   ensure_ascii=False, indent=2))
    return scorecard


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=CONTIGUOUS_START, help="시작일 YYYYMMDD (기본 연속캐시 시작)")
    a = ap.parse_args()
    backtest(a.since)
