# -*- coding: utf-8 -*-
"""
screen_ma_breakout_followthrough.py — 장기이평 폭발형 돌파 + 3일 추종(상승추세 유지) 스캐너

돌파 정의는 screen_long_ma_breakout.py 와 동일:
  종가가 112 또는 224일선을 '그날' 상향돌파 + 당일 등락≥+10% + 거래량≥20일평균 3배.

추가 조건(상승추세 유지, 기본 hold=3영업일):
  ① 돌파 후 N영업일 종가가 '돌파한 선' 위 유지(선 아래로 되밀리지 않음)
  ② D+N 종가 ≥ 돌파일 종가 (돌파분 반납 금지)

기준일(--date)을 끝점으로, 돌파일은 D+N ≤ 기준일 인 것만 후보가 된다.

사용 예:
  python screen_ma_breakout_followthrough.py --date 20260608
  python screen_ma_breakout_followthrough.py --date 20260608 --hold 3 --window 20
  python screen_ma_breakout_followthrough.py --date 20260608 --both
"""
from __future__ import annotations
import argparse, glob, json, os, sys, datetime as dt
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "data", "krx_ohlcv_cache")
CONTIGUOUS_START = "20250115"


def load_frames(end_date: str):
    rc, rv, names, mk = [], [], {}, {}
    for f in sorted(glob.glob(os.path.join(CACHE, "*.json"))):
        d = os.path.basename(f).split("_")[0]
        if d < CONTIGUOUS_START or d > end_date:
            continue
        market = "KOSPI" if "KOSPI" in os.path.basename(f) else "KOSDAQ"
        for r in json.load(open(f, encoding="utf-8")).get("rows", []):
            c, cl, vol, nm = r.get("code"), r.get("close"), r.get("volume"), r.get("name")
            if c and cl is not None:
                rc.append((d, c, cl)); rv.append((d, c, vol or 0))
                names[c] = nm; mk[c] = market
    close = pd.DataFrame(rc, columns=["date", "code", "v"]).pivot_table(
        index="date", columns="code", values="v", aggfunc="last").sort_index()
    volm = pd.DataFrame(rv, columns=["date", "code", "v"]).pivot_table(
        index="date", columns="code", values="v", aggfunc="last").sort_index()
    return close, volm, names, mk


def scan(args):
    close, volm, names, mk = load_frames(args.date or "99999999")
    dates = list(close.index)
    if not dates:
        print("캐시에 데이터가 없습니다."); return
    anchor = args.date or dates[-1]
    if anchor not in dates:
        print(f"{anchor} 데이터 없음(영업일/캐시 확인). 최신일={dates[-1]}"); return
    ai = dates.index(anchor)
    hold = args.hold
    # 돌파일은 D+hold <= anchor 이어야 추종 검증 가능 → 돌파 탐색 상한 = ai-hold
    last_bo = ai - hold
    if last_bo < 1:
        print("추종 검증할 영업일이 부족합니다(캐시 길이 확인)."); return
    first_bo = max(1, last_bo - args.window + 1)

    m112 = close.rolling(112, min_periods=112).mean()
    m224 = close.rolling(224, min_periods=224).mean()
    vavg = volm.rolling(20, min_periods=20).mean()
    RET, VMULT, MAXGAP = args.ret / 100.0, args.vmult, args.maxgap / 100.0

    res = []
    for c in close.columns:
        nm = names.get(c, "")
        if not nm or "스팩" in nm or nm.endswith("우") or nm.endswith("우B"):
            continue
        cl, a, b, vv, va = close[c], m112[c], m224[c], volm[c], vavg[c]
        for i in range(first_bo, last_bo + 1):
            pv, cv = cl.iloc[i - 1], cl.iloc[i]
            A, B, pA, pB = a.iloc[i], b.iloc[i], a.iloc[i - 1], b.iloc[i - 1]
            if pd.isna(A) or pd.isna(B) or pd.isna(pv) or pv == 0:
                continue
            x112 = pv <= pA and cv > A
            x224 = pv <= pB and cv > B
            hit = (x112 and x224) if args.both else (x112 or x224)
            if not hit:
                continue
            ret = cv / pv - 1
            vr = (vv.iloc[i] / va.iloc[i]) if (not pd.isna(va.iloc[i]) and va.iloc[i] > 0) else 0
            if not (ret >= RET and vr >= VMULT and (cv / max(A, B) - 1) <= MAXGAP):
                continue

            # ── 3일(hold) 추종 검증 ──
            held = True
            for k in range(1, hold + 1):
                fc = cl.iloc[i + k]
                fA, fB = a.iloc[i + k], b.iloc[i + k]
                if pd.isna(fc):
                    held = False; break
                # 돌파한 선 위 유지
                if x112 and not (fc > fA): held = False; break
                if x224 and not (fc > fB): held = False; break
            if not held:
                continue
            d3 = cl.iloc[i + hold]
            if pd.isna(d3) or d3 < cv:   # 돌파분 반납 금지
                continue

            # ── 기준일(anchor) 추세 건전성: 돌파 후 추세 붕괴 종목 제외 ──
            ac = cl.iloc[ai]                 # 기준일 종가
            aA, aB = a.iloc[ai], b.iloc[ai]  # 기준일 112/224선
            if not args.no_trend:
                if pd.isna(ac) or pd.isna(aA) or pd.isna(aB):
                    continue
                if not (ac > aA and ac > aB):          # 기준일에 두 장기선 위 유지
                    continue
                if args.slope_lookback > 0:            # 112선 상승 기울기(0이면 생략)
                    sl = ai - args.slope_lookback
                    if sl < 0 or pd.isna(a.iloc[sl]) or aA <= a.iloc[sl]:
                        continue

            line = "112+224" if (x112 and x224) else ("112" if x112 else "224")
            hold_ret = d3 / cv - 1            # 돌파일 종가 대비 D+hold 수익률
            cur_ret = cl.iloc[ai] / cv - 1    # 돌파일 종가 대비 기준일 수익률(현재까지)
            res.append((dates[i], c, nm, mk[c], int(cv), round(ret * 100, 1),
                        round(vr, 1), line, int(d3), round(hold_ret * 100, 1),
                        int(cl.iloc[ai]), round(cur_ret * 100, 1)))
            break

    res.sort(key=lambda r: (-r[11], r[0]))
    mode = "동시" if args.both else "112 또는 224"
    trend = "OFF" if args.no_trend else f"기준일 112·224선 위+112상승({args.slope_lookback}일)"
    print(f"[돌파+{hold}일 추종 유지 / 추세필터 {trend}] {mode} | 돌파 등락≥+{args.ret}% | "
          f"거래량≥{args.vmult}배 | 돌파탐색 {dates[first_bo]}~{dates[last_bo]} | 기준일 {anchor}")
    print(f"{'돌파일':<10}{'코드':<7}{'종목명':<14}{'시장':<7}{'돌파종가':>9}{'등락%':>7}"
          f"{'거래배':>7}  {'돌파선':<9}{'기준일가':>9}{'현재%':>7}")
    for d, c, nm, m, px, ret, vr, line, d3, hr, ap_, cr in res:
        print(f"{d:<10}{c:<7}{nm:<14}{m:<7}{px:>9,}{ret:>6}%{vr:>6}x  {line:<9}{ap_:>9,}{cr:>6}%")
    print(f"총 {len(res)}종")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="추종검증 끝점(기준일) YYYYMMDD (기본: 최신 캐시일)")
    ap.add_argument("--hold", type=int, default=3, help="돌파 후 추종 검증 영업일수 (기본 3)")
    ap.add_argument("--window", type=int, default=20, help="돌파일 탐색 영업일 범위 (기본 20)")
    ap.add_argument("--both", action="store_true", help="112·224 동시 돌파만")
    ap.add_argument("--ret", type=float, default=10.0, help="최소 돌파일 등락%% (기본 10)")
    ap.add_argument("--vmult", type=float, default=3.0, help="최소 거래량 배수 (기본 3)")
    ap.add_argument("--maxgap", type=float, default=30.0, help="돌파일 종가-기준선 최대 이격%% (기본 30)")
    ap.add_argument("--slope-lookback", type=int, default=5, help="112선 상승 기울기 판정 영업일 (기본 5)")
    ap.add_argument("--no-trend", action="store_true", help="기준일 추세 건전성 필터 끄기")
    a = ap.parse_args()
    scan(a)
