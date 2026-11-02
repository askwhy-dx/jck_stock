# -*- coding: utf-8 -*-
"""
eval_forward.py — forward-test 스캐너 손익 평가 (요약 탭 #scanner-eval 소스)

라이브 원장(breakout_pool·recommend_pool)에 스케줄(08:15)이 쌓은 실제 신호를
**대시보드 졸업 엔진과 동일 기준**으로 재판정해 눌림 vs 돌파 손익을 집계한다.
(2026-06-17 사용자 확정: 평가표 승 == 대시보드 졸업② 카운트로 통일)

판정 기준 (collect_breakout_flow.classify / derive_lifecycle 복제):
  - 기준가 = 포착일 종가(ref_close) · 단일 진입 (분할매수·평단 폐기)
  - 🎓 승 = peak(종가/ref_close − 1) ≥ +10% (졸업① 이상, 손절보다 우선·영구)
  - ❌ 패 = 졸업 전 돌파선(line) 종가 이탈 (돌파=entry line / 눌림=224 지지)
  - ⏳ 진행중 = 그 외

산출: data/forward_scorecard.json  (claude_dante_analyze.js renderScannerEval이 읽어 카드 렌더)

사용: python eval_forward.py
"""
from __future__ import annotations
import json, os, sys, datetime as dt
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
from backtest_scanners import load_ticker_frames

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")

GRAD = 0.10   # 대시보드 졸업① 기준 (포착일 종가 +10% 이상 = 승, 2026-06-17 사용자 하향)


def log(m): print(m, flush=True)


def _pool(f):
    p = os.path.join(DATA, f)
    if not os.path.exists(p):
        return {}
    try:
        return json.load(open(p, encoding="utf-8")).get("stocks", {})
    except Exception:
        return {}


def collect_signals():
    """원장 → 신호 [(scanner, code, entry_dd(YYYYMMDD), ref, line)]."""
    sigs = []
    for e in _pool("breakout_pool.json").values():
        ed = (e.get("breakout_date") or e.get("first_seen") or "").replace("-", "")
        if ed and e.get("breakout_close"):
            sigs.append(("breakout", e["code"], ed, float(e["breakout_close"]), e.get("line", "112")))
    for e in _pool("recommend_pool.json").values():
        # 눌림 first_seen은 발행일(ISO) — 진입 데이터일 = 그 날짜 이하 최근 영업일(아래서 해소)
        fs = (e.get("first_seen") or "").replace("-", "")
        if fs and e.get("ref_close"):
            sigs.append(("pullback", e["code"], fs, float(e["ref_close"]), "224"))
    return sigs


def simulate_graduation(close, ma112, ma224, entry_i, ref, line):
    """대시보드 졸업 엔진 복제 (단일 진입·포착일 종가 기준).
      승 = peak(종가/ref − 1) ≥ +20% (졸업②, 이탈보다 우선)
      패 = 졸업 전 돌파선(line) 종가 이탈
      진행중 = 그 외
    반환: (outcome, exit_i, peak, exit_ret)
    """
    n = len(close)
    peak = 0.0
    for j in range(entry_i + 1, n):
        r = close[j] / ref - 1.0
        if r > peak:
            peak = r
        if peak >= GRAD:                                    # 🎓 졸업①(+10%) 이상 → 승 (우선·영구)
            return ("win", j, peak, peak)
        A, B = ma112[j], ma224[j]
        broke = False
        if line in ("112", "112+224") and not pd.isna(A) and close[j] < A:
            broke = True
        if line in ("224", "112+224") and not pd.isna(B) and close[j] < B:
            broke = True
        if broke:                                           # ❌ 돌파선 종가 이탈 → 패
            return ("loss", j, peak, r)
    return ("open", n - 1, peak, close[n - 1] / ref - 1.0)


def run():
    frames, meta = load_ticker_frames()
    out = {"breakout": [], "pullback": []}
    for scanner, code, edd, ref, line in collect_signals():
        df = frames.get(code)
        if df is None or len(df) < 230:
            continue
        dates = df["date"].tolist()
        # 진입 영업일: edd 이하 최근 캐시 영업일 (눌림 발행일이 미래일 수 있어 해소)
        cand = [d for d in dates if d <= edd]
        if not cand:
            continue
        i = dates.index(cand[-1])
        if i < 224:
            continue
        close = df["close"].to_numpy()
        ma112 = df["close"].rolling(112, min_periods=112).mean().to_numpy()
        ma224 = df["close"].rolling(224, min_periods=224).mean().to_numpy()
        if pd.isna(ma112[i]) or pd.isna(ma224[i]):
            continue
        outcome, exit_i, peak, exit_ret = simulate_graduation(close, ma112, ma224, i, ref, line)
        out[scanner].append({"code": code, "entry": cand[-1], "outcome": outcome,
                             "ret_pct": round(exit_ret * 100, 2),
                             "peak_pct": round(peak * 100, 2), "days": exit_i - i})

    # 코드→종목명 (최신 KRX 캐시) — 승 종목 노출용
    names = {}
    try:
        cache = os.path.join(DATA, "krx_ohlcv_cache")
        latest = sorted(f for f in os.listdir(cache) if f.endswith("_KOSPI.json") or f.endswith("_KOSDAQ.json"))
        for f in latest[-2:]:
            for r in json.load(open(os.path.join(cache, f), encoding="utf-8")).get("rows", []):
                if r.get("code") and r.get("name"):
                    names[r["code"]] = r["name"]
    except Exception:
        pass

    def agg(sigs):
        n = len(sigs)
        wins = [s for s in sigs if s["outcome"] == "win"]
        losses = [s for s in sigs if s["outcome"] == "loss"]
        opens = [s for s in sigs if s["outcome"] == "open"]
        closed = len(wins) + len(losses)
        avg_loss = sum(s["ret_pct"] for s in losses) / len(losses) if losses else 0
        win_rate = len(wins) / closed if closed else 0
        loss_rate = len(losses) / closed if closed else 0
        return {
            "signals": n, "closed": closed, "open": len(opens),
            "wins": len(wins), "losses": len(losses),
            "win_rate_pct": round(win_rate * 100, 1),
            "avg_loss_pct": round(avg_loss, 2),
            "expectancy_pct": round(win_rate * (GRAD * 100) + loss_rate * avg_loss, 2),
            "avg_days_to_win": round(sum(s["days"] for s in wins) / len(wins), 1) if wins else 0,
            # 🏆 승 종목 detail (눌림/돌파 노출용)
            "wins_detail": [{"code": s["code"], "name": names.get(s["code"], s["code"]),
                             "entry": s["entry"], "peak_pct": s["peak_pct"], "days": s["days"]}
                            for s in sorted(wins, key=lambda x: -x["peak_pct"])],
        }

    sc = {
        "generated": dt.datetime.now().isoformat(timespec="seconds"),
        "method": "대시보드 졸업 기준 · 포착일 종가 +10% 졸업① 이상 = 승 / 돌파선 종가 이탈 = 패 (forward-test)",
        "breakout": agg(out["breakout"]),
        "pullback": agg(out["pullback"]),
    }
    p = os.path.join(DATA, "forward_scorecard.json")
    json.dump(sc, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    log(f"forward_scorecard 저장: {p}")
    log(json.dumps({"breakout": sc["breakout"], "pullback": sc["pullback"]}, ensure_ascii=False, indent=2))
    return sc


if __name__ == "__main__":
    run()
