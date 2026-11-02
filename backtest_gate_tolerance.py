# -*- coding: utf-8 -*-
"""
backtest_gate_tolerance.py — 「진입 게이트 전부 충족」 vs 「1개 미충족 허용」 승률·기대값 비교

사용자 질문(2026-08-25): "매수할 때 모든 조건을 충족하는 것과 1개 정도 미충족돼도
매수하는 것의 이익 확률이 어떻게 되는지" → 게이트 충족 개수(0~5)별로 승률/기대값을 갈라 본다.

⚠️ 게이트 구성이 라이브 §4.2 진입조건 5종과 다르다 (2026-08-25 데이터 제약):
    라이브 5종 = price / bullish_candle / volume / foreign_net_buy / program_net_buy
    - foreign_net_buy·program_net_buy 는 **과거 이력 확보 불가**.
      KRX OpenAPI(data-dbg)에 투자자별·프로그램 엔드포인트가 없고(404 7종 확인),
      pykrx 투자자 함수는 KRX 로그인 게이트로 전부 빈 DF 반환(2026-08-25 실측).
    - price 는 백테스트에서 「포착일 종가 = 진입가」라 항상 pass = 측정 불가.
    → 그래서 OHLCV로 재현 가능한 **눌림 신호 품질 게이트 5종**으로 같은 구조의 질문을 던진다.
      결론은 "게이트 하나를 빼먹으면 확률이 얼마나 나빠지나"라는 일반 효과이고,
      수급 게이트(외국인·프로그램) 자체의 효과는 이 백테스트로 말할 수 없다.

게이트 5종 (backtest_pullback_improve.py 의 ①~⑤와 동일 정의):
    ① score ≥ 90        상위 신호 선별 (baseline 은 §4.2 통일 컷 80)
    ② vol_shrink ≤ 0.7  매도 압력 확실히 약화
    ③ |224선 이격| ≤ 3% 지지선 정확도
    ④ 양봉              포착일 종가 > 시가
    ⑤ 정배열            MA20 > MA60 > MA112

매매 모델: simulate_dante (단테 1:1:2 분할 · 평단 +20% 종가 도달 익절 / 112선 종가 이탈 손절)

사용: python backtest_gate_tolerance.py [--since 20240101]
"""
from __future__ import annotations
import argparse, json, os, sys
from itertools import combinations

sys.stdout.reconfigure(encoding="utf-8")
from backtest_scanners import WIN_RET
from backtest_pullback_improve import gen_signals

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def log(m): print(m, flush=True)


GATES = {
    "①score≥90": lambda s: s["score"] >= 90,
    "②거래량수축≤0.7": lambda s: s["vol_shrink"] <= 0.7,
    "③224선±3%": lambda s: s["above224"] <= 3,
    "④양봉": lambda s: s["candle"] == "양봉",
    "⑤정배열": lambda s: s["align"],
}
NAMES = list(GATES)


def agg(sigs):
    """승/패/미청산 분해 + 승률·평균손실·기대값. open 은 승률 분모에서 제외."""
    n = len(sigs)
    wins = [s for s in sigs if s["outcome"] == "win"]
    losses = [s for s in sigs if s["outcome"] == "loss"]
    closed = len(wins) + len(losses)
    avg_loss = sum(s["ret_pct"] for s in losses) / len(losses) if losses else 0.0
    wr = len(wins) / closed if closed else 0.0
    lr = len(losses) / closed if closed else 0.0
    days = [s["days"] for s in wins + losses]
    return {
        "signals": n, "wins": len(wins), "losses": len(losses), "open": n - closed,
        "closed": closed,
        "win_rate_pct": round(wr * 100, 1),
        "avg_loss_pct": round(avg_loss, 2),
        "expectancy_pct": round(wr * (WIN_RET * 100) + lr * avg_loss, 2),
        "avg_days": round(sum(days) / len(days), 1) if days else 0,
    }


def fmt_row(label, r, width=22):
    return (f"{label:<{width}}{r['signals']:>6}{r['wins']:>5}{r['losses']:>5}{r['open']:>6}"
            f"{r['win_rate_pct']:>8}{r['avg_loss_pct']:>10}{r['expectancy_pct']:>9}{r['avg_days']:>7}")


HEAD = (f"{'구분':<22}{'신호':>6}{'승':>5}{'패':>5}{'미청산':>6}"
        f"{'승률%':>8}{'평균손실%':>10}{'기대값%':>9}{'보유일':>7}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="20240101")
    ap.add_argument("--cache-start", default="20221021",
                    help="캐시 로드 시작일. 기본 20221021(캐시 전체). MA224 워밍업 229봉이 필요해 since보다 최소 1년은 앞서야 한다.")
    a = ap.parse_args()

    log(f"눌림 신호 생성 (since={a.since}) ...")
    sigs = gen_signals(a.since, a.cache_start)
    log(f"baseline 신호 {len(sigs)}개\n")

    # 게이트 통과 여부 부착
    for s in sigs:
        s["_gates"] = {k: bool(fn(s)) for k, fn in GATES.items()}
        s["_pass"] = sum(s["_gates"].values())
        s["_failed"] = [k for k, v in s["_gates"].items() if not v]

    out = {"since": a.since, "win_ret_pct": WIN_RET * 100, "total_signals": len(sigs)}

    # ── A. 충족 개수별 ─────────────────────────────────────────────
    print("=== A. 게이트 충족 개수별 (단테 1:1:2 · 평단 +20% 익절 / 112선 손절) ===")
    print(HEAD)
    by_count = {}
    for c in range(5, -1, -1):
        grp = [s for s in sigs if s["_pass"] == c]
        if not grp:
            continue
        r = agg(grp)
        by_count[c] = r
        print(fmt_row(f"{c}/5 충족", r))
    out["by_pass_count"] = by_count

    # ── B. 실제 의사결정: 5/5만 살까 vs 4/5까지 살까 ────────────────
    print("\n=== B. 매수 기준별 (누적) — 실제 의사결정 ===")
    print(HEAD)
    policies = {
        "5/5 전부 충족만": [s for s in sigs if s["_pass"] == 5],
        "4/5 이상 (1개 허용)": [s for s in sigs if s["_pass"] >= 4],
        "3/5 이상 (2개 허용)": [s for s in sigs if s["_pass"] >= 3],
        "전체 (게이트 무시)": sigs,
    }
    pol = {}
    for name, grp in policies.items():
        r = agg(grp)
        pol[name] = r
        print(fmt_row(name, r))
    out["policies"] = pol

    # ── C. 4/5일 때 어느 조건을 빼먹었나 ────────────────────────────
    print("\n=== C. 4/5 충족 — 미충족 조건별 (하나만 빼먹었을 때 무엇이 가장 비싼가) ===")
    print(HEAD)
    miss = {}
    four = [s for s in sigs if s["_pass"] == 4]
    for name in NAMES:
        grp = [s for s in four if s["_failed"] == [name]]
        if not grp:
            continue
        r = agg(grp)
        miss[name] = r
        print(fmt_row(f"{name} 만 미충족", r))
    out["miss_one"] = miss

    # ── D. 게이트 단독 기여도 (있을 때 vs 없을 때) ──────────────────
    print("\n=== D. 게이트 단독 효과 (해당 조건만 충족 요구 / 그 조건만 미충족) ===")
    print(HEAD)
    solo = {}
    for name, fn in GATES.items():
        on = agg([s for s in sigs if s["_gates"][name]])
        off = agg([s for s in sigs if not s["_gates"][name]])
        solo[name] = {"pass": on, "fail": off}
        print(fmt_row(f"{name} 충족", on))
        print(fmt_row(f"{name} 미충족", off))
    out["solo"] = solo

    path = os.path.join(DATA, "gate_tolerance_scorecard.json")
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    log(f"\n저장: {path}")


if __name__ == "__main__":
    main()
