# -*- coding: utf-8 -*-
"""
diag_entry_gate.py — §4.2 진입조건 게이트가 왜 안 열리는지 실측 진단

"매수시점이 잘 안 나온다"의 원인이 ① 시장이 나빠서인지 ② 게이트 설계 결함인지
가르려면, 조건별 통과율을 회차 단위로 세어봐야 한다. 전 종목이 늘 같은 조건에서
막히고 있다면 그건 필터가 아니라 **차단기**다.

data/dante_analysis_*.json 의 stocks[].entry_conditions 를 전수 집계한다.
회차 시각(파일명 HHMM)으로 장중/장마감후를 갈라, 시각 의존 결함도 같이 본다.

사용: python diag_entry_gate.py [--since 2026-07-01]
"""
from __future__ import annotations
import argparse, glob, json, os, re, sys
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")

KEYS = ["price", "bullish_candle", "volume", "foreign_net_buy", "program_net_buy"]
LABEL = {"price": "가격", "bullish_candle": "양봉", "volume": "거래량",
         "foreign_net_buy": "외국인 순매수", "program_net_buy": "프로그램 순매수"}


def norm(v):
    raw = v.get("status") if isinstance(v, dict) else v
    s = str(raw or "").strip().lower()
    if s in ("pass", "true", "충족"):
        return "pass"
    if s in ("fail", "false", "미충족"):
        return "fail"
    if s in ("not_required", "not-required", "해당없음"):
        return "n/a"
    return "unknown"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-06-01")
    a = ap.parse_args()

    files = sorted(glob.glob(os.path.join(DATA, "dante_analysis_*.json")))
    stat = {k: defaultdict(int) for k in KEYS}
    slot_stat = {"장중": {k: defaultdict(int) for k in KEYS},
                 "장마감후": {k: defaultdict(int) for k in KEYS}}
    rounds = 0
    rows_total = 0
    all_pass_rows = 0
    all_pass_rounds = 0
    fail_combo = defaultdict(int)
    per_round = []

    for f in files:
        m = re.search(r"dante_analysis_(\d{4}-\d{2}-\d{2})_(\d{4})\.json$", os.path.basename(f))
        if not m:
            continue
        day, hhmm = m.group(1), m.group(2)
        if day < a.since:
            continue
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        stocks = [s for s in d.get("stocks", []) if isinstance(s.get("entry_conditions"), dict)]
        if not stocks:
            continue
        rounds += 1
        slot = "장마감후" if hhmm >= "1540" else "장중"
        r_pass = 0
        for s in stocks:
            ec = s["entry_conditions"]
            rows_total += 1
            sts = {k: norm(ec.get(k)) for k in KEYS}
            for k, v in sts.items():
                stat[k][v] += 1
                slot_stat[slot][k][v] += 1
            failed = tuple(sorted(k for k in KEYS if sts[k] != "pass"))
            if not failed:
                all_pass_rows += 1
                r_pass += 1
            else:
                fail_combo[failed] += 1
        if r_pass:
            all_pass_rounds += 1
        per_round.append((day, hhmm, slot, len(stocks), r_pass))

    print(f"=== 대상: {rounds}개 회차 · 종목-회차 {rows_total}건 ({a.since}~) ===\n")

    print("=== ① 조건별 통과율 (전체) ===")
    print(f"{'조건':<16}{'pass':>7}{'fail':>7}{'unknown':>9}{'n/a':>6}{'통과율%':>9}")
    for k in KEYS:
        c = stat[k]
        tot = sum(c.values()) - c["n/a"]
        pr = c["pass"] / tot * 100 if tot else 0
        print(f"{LABEL[k]:<16}{c['pass']:>7}{c['fail']:>7}{c['unknown']:>9}{c['n/a']:>6}{pr:>9.1f}")

    print(f"\n=== ② 5개 전부 충족 = 실제 매수 신호 ===")
    print(f"  종목-회차 {rows_total}건 중 전부 충족 **{all_pass_rows}건** "
          f"({all_pass_rows/rows_total*100 if rows_total else 0:.2f}%)")
    print(f"  회차 {rounds}개 중 1종이라도 충족한 회차 **{all_pass_rounds}개** "
          f"({all_pass_rounds/rounds*100 if rounds else 0:.1f}%)")

    print(f"\n=== ③ 장중 vs 장마감후 — 시각 의존 결함 점검 ===")
    print(f"{'조건':<16}{'장중 통과율%':>14}{'장마감후 통과율%':>18}")
    for k in KEYS:
        out = []
        for slot in ("장중", "장마감후"):
            c = slot_stat[slot][k]
            tot = sum(c.values()) - c["n/a"]
            out.append(c["pass"] / tot * 100 if tot else 0)
        print(f"{LABEL[k]:<16}{out[0]:>14.1f}{out[1]:>18.1f}")

    print(f"\n=== ④ 막은 조건 조합 상위 10 (무엇이 실제 병목인가) ===")
    print(f"{'미충족 조합':<46}{'건수':>7}{'비중%':>8}")
    for combo, n in sorted(fail_combo.items(), key=lambda x: -x[1])[:10]:
        nm = " + ".join(LABEL[c] for c in combo)
        print(f"{nm:<46}{n:>7}{n/rows_total*100:>8.1f}")

    # 단독 병목: 이 조건만 빼면 통과했을 건수
    print(f"\n=== ⑤ '이 조건 하나만 아니었으면 통과' 건수 ===")
    print(f"{'조건':<16}{'건수':>7}{'전체 대비%':>12}")
    solo = defaultdict(int)
    for combo, n in fail_combo.items():
        if len(combo) == 1:
            solo[combo[0]] += n
    for k in KEYS:
        print(f"{LABEL[k]:<16}{solo[k]:>7}{solo[k]/rows_total*100:>12.2f}")

    out = {
        "since": a.since, "rounds": rounds, "rows": rows_total,
        "all_pass_rows": all_pass_rows, "all_pass_rounds": all_pass_rounds,
        "by_condition": {k: dict(stat[k]) for k in KEYS},
        "by_slot": {s: {k: dict(slot_stat[s][k]) for k in KEYS} for s in slot_stat},
        "fail_combos": {" + ".join(c): n for c, n in sorted(fail_combo.items(), key=lambda x: -x[1])},
        "solo_blockers": dict(solo),
    }
    p = os.path.join(DATA, "entry_gate_diagnosis.json")
    json.dump(out, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n저장: {p}")


if __name__ == "__main__":
    main()
