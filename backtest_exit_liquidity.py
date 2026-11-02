# -*- coding: utf-8 -*-
"""
backtest_exit_liquidity.py — 제안 A(D구간 분할 익절) + 제안 B(거래대금 유동성 필터) 검증

원문(유튜브 대본) 스윙 A·B·C·D 구간 룰 중 단테에 결손인 2건을 도입 전 백테스트한다.
  · 제안 A = D구간: 목표 1차(평단 +10%) 도달 시 절반 익절 → 잔량 트레일링(MA20/MA10)
  · 제안 B = 선별: 신호일 시장 전체 거래대금 순위 상위 N위 이내만 채택

비교 기준(baseline)은 현행 룰 = 평단 +20% 전량 익절 / 112선 종가 이탈 손절
(backtest_scanners.simulate_dante 와 동일 로직을 재구현·확장).

분할 진입은 전 변형 공통: 1차a 포착일 종가 / 1차b -1.5% / 3차 메인 지지선(눌림=224선, 돌파=돌파선), 1:1:2.
절반 익절 발효 시 잔여 분할매수는 중단(=트레일링 모드 전환, §6 운영 관행 반영).

승/패 판정은 전 변형 공통으로 **실현손익 부호**로 통일한다(baseline의 "+20% 도달=승"과 동치).

사용:
  python backtest_exit_liquidity.py                 # 전체 연속캐시(2025-01-15~)
  python backtest_exit_liquidity.py --since 20251201
"""
from __future__ import annotations
import argparse, glob, json, os, sys, datetime as dt
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(ROOT, "data", "krx_ohlcv_cache")
DATA = os.path.join(ROOT, "data")

from backtest_scanners import (_excluded, MIN_CAP, SCORE_CUT, CONTIGUOUS_START,
                               WIN_RET, B_DIP)
from collect_breakout_flow import score_breakout, THRESHOLDS as BO_TH
from collect_theme_flow import evaluate_pullback_signal

HALF_TARGET = 0.10     # 제안 A: 목표 1차 = 평단 +10% (절반 익절)
RANK_TOP = 300         # 순위 맵 보관 상한(그 밖은 999로 취급)


def log(m): print(m, flush=True)


# ────────────────────────────────────────── 데이터 로드
def load_frames_and_ranks(cache_since: str = CONTIGUOUS_START):
    """캐시 전체 → {code: DataFrame(+trading_value)}, meta, {date: {code: 거래대금순위}}"""
    rows, meta = {}, {}
    daily = {}   # date -> list[(code, trading_value)]
    for f in sorted(glob.glob(os.path.join(CACHE, "*.json"))):
        base = os.path.basename(f)
        d = base.split("_")[0]
        if d < cache_since:
            continue
        market = "KOSPI" if "KOSPI" in base else "KOSDAQ"
        for r in json.load(open(f, encoding="utf-8")).get("rows", []):
            c = r.get("code")
            if not c or r.get("close") is None:
                continue
            tv = r.get("trading_value") or 0
            rows.setdefault(c, []).append((d, r.get("open"), r.get("high"), r.get("low"),
                                           r.get("close"), r.get("volume") or 0, tv))
            daily.setdefault(d, []).append((c, tv))
            if c not in meta:
                meta[c] = {"name": r.get("name"), "market": market, "market_cap": r.get("market_cap")}
            elif r.get("market_cap"):
                meta[c]["market_cap"] = r.get("market_cap")

    frames = {}
    for c, rs in rows.items():
        df = pd.DataFrame(rs, columns=["date", "open", "high", "low", "close", "volume", "tval"]) \
               .sort_values("date").reset_index(drop=True)
        for col in ("open", "high", "low", "close", "volume", "tval"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        frames[c] = df

    ranks = {}
    for d, lst in daily.items():
        lst.sort(key=lambda x: -(x[1] or 0))
        ranks[d] = {c: i + 1 for i, (c, _) in enumerate(lst[:RANK_TOP])}
    return frames, meta, ranks


# ────────────────────────────────────────── 청산 시뮬
def simulate(close, low, ma112, ma_trail, entry_i, entry_close, main_target,
             mode: str, cap_at_win: bool = True, half_target: float = HALF_TARGET):
    """분할 1:1:2 진입 후 청산 시뮬.
      mode='base' : 평단 +20% 전량 익절 / 112선 종가 이탈 손절 (현행)
      mode='half' : 평단 +10% 절반 익절 → 잔량 트레일링(ma_trail) 이탈 청산
                    cap_at_win=True 면 잔량도 평단 +20% 도달 시 전량 청산(D3-B)
    반환: (realized, exit_i, status, half_done)
    """
    tranches = [(entry_close, 1.0)]
    b_target = entry_close * (1 - B_DIP)
    b_filled = m_filled = False
    half_done = False
    half_ret = 0.0
    n = len(close)

    def avg():
        tw = sum(w for _, w in tranches)
        return sum(p * w for p, w in tranches) / tw

    for j in range(entry_i + 1, n):
        # 분할 진입 (절반 익절 전까지만 — 트레일링 모드 전환 시 분할 종료)
        if not half_done:
            if not b_filled and low[j] <= b_target:
                tranches.append((b_target, 1.0)); b_filled = True
            if not m_filled and main_target and low[j] <= main_target:
                tranches.append((main_target, 2.0)); m_filled = True
        a = avg()

        if mode == "base":
            if close[j] >= a * (1 + WIN_RET):
                return (WIN_RET, j, "win", False)
            if not np.isnan(ma112[j]) and close[j] < ma112[j]:
                return (close[j] / a - 1.0, j, "stop", False)
            continue

        # mode == 'half'
        if not half_done:
            if close[j] >= a * (1 + half_target):
                half_ret = half_target          # 지정가 체결 가정(보수적)
                half_done = True
                continue                        # 같은 날 트레일링 판정은 다음 날부터
            if not np.isnan(ma112[j]) and close[j] < ma112[j]:
                return (close[j] / a - 1.0, j, "stop", False)
        else:
            if cap_at_win and close[j] >= a * (1 + WIN_RET):
                return (0.5 * half_ret + 0.5 * WIN_RET, j, "win_full", True)
            trail_hit = (not np.isnan(ma_trail[j])) and close[j] < ma_trail[j]
            hard_hit = (not np.isnan(ma112[j])) and close[j] < ma112[j]
            if trail_hit or hard_hit:
                rest = close[j] / a - 1.0
                return (0.5 * half_ret + 0.5 * rest, j, "trail", True)

    a = avg()
    if mode == "half" and half_done:
        return (0.5 * half_ret + 0.5 * (close[n - 1] / a - 1.0), n - 1, "open", True)
    return (close[n - 1] / a - 1.0, n - 1, "open", False)


# ────────────────────────────────────────── 신호 생성
def gen_signals(since: str, cache_since: str = CONTIGUOUS_START):
    frames, meta, ranks = load_frames_and_ranks(cache_since)
    log(f"종목 {len(frames)}개 · 순위일 {len(ranks)}일 로드")
    out = {"pullback": [], "breakout": []}

    for code, df in frames.items():
        m = meta.get(code, {})
        nm = m.get("name", "")
        if _excluded(nm) or len(df) < 230:
            continue
        n = len(df)
        close = df["close"].to_numpy(); openp = df["open"].to_numpy()
        low = df["low"].to_numpy(); vol = df["volume"].to_numpy()
        tval = df["tval"].to_numpy()
        s = df["close"]
        ma10 = s.rolling(10, min_periods=10).mean().to_numpy()
        ma20 = s.rolling(20, min_periods=20).mean().to_numpy()
        ma112 = s.rolling(112, min_periods=112).mean().to_numpy()
        ma224 = s.rolling(224, min_periods=224).mean().to_numpy()
        vavg = df["volume"].rolling(20, min_periods=20).mean().to_numpy()
        cap_won = m.get("market_cap") or 0
        cap_b = cap_won / 1e8 if cap_won else 0

        def liq(i):
            d = df["date"].iat[i]
            return {"rank": ranks.get(d, {}).get(code, 999),
                    "tval_uk": round((tval[i] or 0) / 1e8, 1)}

        # ── 돌파 신호
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
                main_t = A if line == "112" else (B if line == "224" else max(A, B))
                out["breakout"].append({"code": code, "name": nm, "entry": df["date"].iat[i],
                                        "i": i, "ref": float(cv), "main_t": float(main_t),
                                        "score": sc, **liq(i)})

        # ── 눌림 신호 (운영 필터 = score≥80 + vol_shrink≤0.7 + 양봉)
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
                out["pullback"].append({"code": code, "name": nm, "entry": df["date"].iat[i],
                                        "i": i, "ref": float(close[i]), "main_t": float(B224),
                                        "score": sig["score"],
                                        "vol_shrink": sig["vol_shrink"], "candle": sig["candle"],
                                        **liq(i)})
            prev_pass = bool(passed)

    # ── 눌림 2단계 반등확인 (§4.8.6) 적용 세트 = 현행 운영 스캐너와 동일 조건
    out["pullback_confirmed"] = []
    for s in out["pullback"]:
        df = frames[s["code"]]
        ss = df["close"]
        ma20 = ss.rolling(20, min_periods=20).mean().to_numpy()
        ma60 = ss.rolling(60, min_periods=60).mean().to_numpy()
        ma112 = ss.rolling(112, min_periods=112).mean().to_numpy()
        ma224 = ss.rolling(224, min_periods=224).mean().to_numpy()
        close = df["close"].to_numpy(); high = df["high"].to_numpy()
        i = s["i"]; setup_high = high[i]
        for j in range(i + 1, min(i + 4, len(df))):   # confirm_window = 3영업일
            if np.isnan(ma60[j]) or np.isnan(ma112[j]):
                continue
            if close[j] > setup_high and ma20[j] > ma60[j] > ma112[j]:
                d = df["date"].iat[j]
                t = out.setdefault("_tv", None)
                nsig = dict(s)
                nsig.update({"i": j, "entry": d, "ref": float(close[j]),
                             "main_t": float(ma224[j]),
                             "rank": ranks.get(d, {}).get(s["code"], 999),
                             "tval_uk": round((df["tval"].to_numpy()[j] or 0) / 1e8, 1)})
                out["pullback_confirmed"].append(nsig)
                break
    out.pop("_tv", None)

    return out, frames, meta


# ────────────────────────────────────────── 집계
def run_variants(sigs, frames, kind: str):
    """신호 리스트에 대해 청산 변형별 결과를 계산해 붙인다."""
    arrs = {}
    for s in sigs:
        code = s["code"]
        if code not in arrs:
            df = frames[code]
            ss = df["close"]
            arrs[code] = {
                "close": df["close"].to_numpy(), "low": df["low"].to_numpy(),
                "ma112": ss.rolling(112, min_periods=112).mean().to_numpy(),
                "ma20": ss.rolling(20, min_periods=20).mean().to_numpy(),
                "ma10": ss.rolling(10, min_periods=10).mean().to_numpy(),
            }
        a = arrs[code]
        i, ref, mt = s["i"], s["ref"], s["main_t"]
        s["base"] = simulate(a["close"], a["low"], a["ma112"], a["ma20"], i, ref, mt, "base")
        s["halfA20"] = simulate(a["close"], a["low"], a["ma112"], a["ma20"], i, ref, mt, "half", False)
        s["halfA10"] = simulate(a["close"], a["low"], a["ma112"], a["ma10"], i, ref, mt, "half", False)
        s["halfB20"] = simulate(a["close"], a["low"], a["ma112"], a["ma20"], i, ref, mt, "half", True)
        s["halfB10"] = simulate(a["close"], a["low"], a["ma112"], a["ma10"], i, ref, mt, "half", True)
    return sigs


def agg(sigs, key):
    closed = [s for s in sigs if s[key][2] != "open"]
    opens = [s for s in sigs if s[key][2] == "open"]
    wins = [s for s in closed if s[key][0] > 0]
    losses = [s for s in closed if s[key][0] <= 0]
    n = len(closed)
    aw = sum(s[key][0] for s in wins) / len(wins) * 100 if wins else 0
    al = sum(s[key][0] for s in losses) / len(losses) * 100 if losses else 0
    wr = len(wins) / n if n else 0
    exp_ = (sum(s[key][0] for s in closed) / n * 100) if n else 0
    days = [s[key][1] - s["i"] for s in closed]
    return {"signals": len(sigs), "closed": n, "open": len(opens),
            "wins": len(wins), "losses": len(losses),
            "win_rate_pct": round(wr * 100, 1),
            "avg_win_pct": round(aw, 2), "avg_loss_pct": round(al, 2),
            "expectancy_pct": round(exp_, 2),
            "avg_days": round(sum(days) / len(days), 1) if days else 0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=CONTIGUOUS_START, help="신호 생성 시작일")
    ap.add_argument("--cache-since", default=CONTIGUOUS_START, help="캐시 로드 시작일(MA224 워밍업 포함)")
    a = ap.parse_args()

    sigs, frames, meta = gen_signals(a.since, a.cache_since)
    for kind in ("pullback", "pullback_confirmed", "breakout"):
        run_variants(sigs[kind], frames, kind)
        log(f"{kind} 신호 {len(sigs[kind])}개")

    EXITS = [("현행 baseline(+20% 전량)", "base"),
             ("A: 절반+10% → MA20 트레일링", "halfA20"),
             ("A: 절반+10% → MA10 트레일링", "halfA10"),
             ("A': 절반+10% → MA20+상한20%", "halfB20"),
             ("A': 절반+10% → MA10+상한20%", "halfB10")]
    LIQS = [("전체(필터없음)", lambda s: True),
            ("B: 거래대금 100위내", lambda s: s["rank"] <= 100),
            ("B: 거래대금 200위내", lambda s: s["rank"] <= 200),
            ("B: 거래대금 300위내", lambda s: s["rank"] <= 300),
            ("B: 일 거래대금 100억+", lambda s: s["tval_uk"] >= 100)]

    result = {"generated": dt.datetime.now().isoformat(timespec="seconds"),
              "since": a.since,
              "defs": {"진입": "1:1:2 분할(1차a 종가·1차b -1.5%·3차 지지선)",
                       "승패": "실현손익 부호 (open 제외)",
                       "목표1차": f"평단 +{HALF_TARGET*100:.0f}% 지정가 절반 익절",
                       "손절": "112선 종가 이탈(공통)"},
              "scanners": {}}

    for kind, label in (("pullback", "🌊 눌림(셋업)"),
                        ("pullback_confirmed", "🌊 눌림 + 2단계 반등확인(현행 운영)"),
                        ("breakout", "💥 돌파")):
        block = {}
        for lname, lfn in LIQS:
            sub = [s for s in sigs[kind] if lfn(s)]
            block[lname] = {ename: agg(sub, ekey) for ename, ekey in EXITS}
        result["scanners"][kind] = block

        print(f"\n{'='*104}\n{label} 스캐너 (신호 {len(sigs[kind])}개 · {a.since}~)\n{'='*104}")
        for lname, _ in LIQS:
            r0 = block[lname][EXITS[0][0]]
            print(f"\n[{lname}]  청산신호 {r0['closed']}건 / 미청산 {r0['open']}건")
            print(f"  {'청산 룰':<28}{'승':>4}{'패':>4}{'승률%':>7}{'평균수익%':>10}{'평균손실%':>10}{'기대값%':>9}{'평균일':>7}")
            for ename, _k in EXITS:
                r = block[lname][ename]
                print(f"  {ename:<28}{r['wins']:>4}{r['losses']:>4}{r['win_rate_pct']:>7}"
                      f"{r['avg_win_pct']:>10}{r['avg_loss_pct']:>10}{r['expectancy_pct']:>9}{r['avg_days']:>7}")

    # -- 강건성 1) 기간 분할 (2025 vs 2026)
    print(f"\n{'='*104}\n[강건성 1] 기간 분할 - 필터없음 - 현행 vs A(MA20 트레일링)\n{'='*104}")
    print(f"  {'스캐너/기간':<30}{'룰':<12}{'승':>4}{'패':>4}{'승률%':>7}{'기대값%':>9}")
    for kind, label in (("pullback_confirmed", "눌림(운영)"), ("breakout", "돌파")):
        for pname, lo, hi in (("2024", "20240101", "20241231"),
                              ("2025", "20250101", "20251231"),
                              ("2026", "20260101", "20261231")):
            sub = [x for x in sigs[kind] if lo <= x["entry"] <= hi]
            if not sub:
                continue
            for rn, rk in (("현행", "base"), ("A(MA20)", "halfA20")):
                r = agg(sub, rk)
                print(f"  {label + ' ' + pname:<30}{rn:<12}{r['wins']:>4}{r['losses']:>4}"
                      f"{r['win_rate_pct']:>7}{r['expectancy_pct']:>9}")

    # -- 강건성 2) 절반 익절 임계값 민감도 (MA20 트레일링 고정)
    print(f"\n{'='*104}\n[강건성 2] 절반 익절 임계값 민감도 - MA20 트레일링 - 필터없음\n{'='*104}")
    arrs2 = {}
    for kind, label in (("pullback_confirmed", "눌림(운영)"), ("breakout", "돌파")):
        print(f"\n  [{label}]  {'임계값':<12}{'승':>4}{'패':>4}{'승률%':>7}{'평균수익%':>10}{'기대값%':>9}")
        for ht in (0.06, 0.08, 0.10, 0.12, 0.15):
            key = f"ht{int(ht*100)}"
            for x in sigs[kind]:
                c = x["code"]
                if c not in arrs2:
                    df = frames[c]; ss = df["close"]
                    arrs2[c] = {"close": df["close"].to_numpy(), "low": df["low"].to_numpy(),
                                "ma112": ss.rolling(112, min_periods=112).mean().to_numpy(),
                                "ma20": ss.rolling(20, min_periods=20).mean().to_numpy()}
                a2 = arrs2[c]
                x[key] = simulate(a2["close"], a2["low"], a2["ma112"], a2["ma20"],
                                  x["i"], x["ref"], x["main_t"], "half", False, ht)
            r = agg(sigs[kind], key)
            lbl = "평단 +" + str(int(ht*100)) + "%"
            print(f"     {lbl:<12}{r['wins']:>4}{r['losses']:>4}"
                  f"{r['win_rate_pct']:>7}{r['avg_win_pct']:>10}{r['expectancy_pct']:>9}")

    out = os.path.join(DATA, "exit_liquidity_scorecard.json")
    json.dump(result, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    log(f"\n저장: {out}")


if __name__ == "__main__":
    main()
