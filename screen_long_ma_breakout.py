# -*- coding: utf-8 -*-
"""
screen_long_ma_breakout.py — 장기이평(112·224일선) 폭발형 돌파 스캐너

정의: 한켐(457370) 2026-05-22 유형
  종가가 112일선 또는 224일선을 '그날' 상향돌파(전일 종가 ≤ 선, 당일 종가 > 선)
  + 당일 등락 ≥ +10%  + 당일 거래량 ≥ 20일평균의 3배.

사용 예:
  python screen_long_ma_breakout.py                 # 최신 캐시일 당일 스캔
  python screen_long_ma_breakout.py --date 20260608 # 특정일 기준
  python screen_long_ma_breakout.py --refresh        # KRX에서 최근일 받아와 캐시 갱신 후 스캔
  python screen_long_ma_breakout.py --window 8       # 기준일 포함 최근 8영업일 구간 스캔
  python screen_long_ma_breakout.py --both           # 112·224 '동시' 돌파만
  python screen_long_ma_breakout.py --ret 15 --vmult 5   # 기준 강화(등락≥15%, 거래량≥5배)
"""
from __future__ import annotations
import argparse, glob, json, os, sys, datetime as dt
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "data", "krx_ohlcv_cache")
# 연속 캐시 시작일(2024년치 없음). 이전을 섞으면 캐시 공백으로 MA224가 왜곡되므로 고정.
CONTIGUOUS_START = "20250115"


def refresh_recent(days_back: int = 7):
    """KRX OpenAPI에서 최근 영업일들을 받아 캐시에 채운다(이미 있으면 건너뜀)."""
    try:
        from krx_openapi import KRXClient
    except Exception as e:
        print(f"[refresh] krx_openapi 로드 실패: {e}"); return
    cli = KRXClient()
    d = dt.date.today()
    fetched = 0
    for _ in range(days_back + 5):
        if d.weekday() < 5:  # 평일만
            bas = d.strftime("%Y%m%d")
            for mkt in ("KOSPI", "KOSDAQ"):
                p = os.path.join(CACHE, f"{bas}_{mkt}.json")
                if not os.path.exists(p):
                    try:
                        rows = cli.daily_ohlcv_raw(bas, mkt)
                        if rows: fetched += 1; print(f"[refresh] {bas} {mkt} {len(rows)}종")
                    except Exception as e:
                        print(f"[refresh] {bas} {mkt} 실패: {e}")
        d -= dt.timedelta(days=1)
        if fetched and d.strftime("%Y%m%d") < CONTIGUOUS_START: break


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
        print("캐시에 데이터가 없습니다. --refresh 로 받아오거나 날짜를 확인하세요."); return
    anchor = args.date or dates[-1]
    if anchor not in dates:
        print(f"{anchor} 데이터 없음(영업일/캐시 확인). 최신일={dates[-1]}"); return
    ai = dates.index(anchor)
    win_idx = range(max(1, ai - args.window + 1), ai + 1)

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
        for i in win_idx:
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
            if ret >= RET and vr >= VMULT and (cv / max(A, B) - 1) <= MAXGAP:
                line = "112+224" if (x112 and x224) else ("112" if x112 else "224")
                res.append((dates[i], c, nm, mk[c], int(cv), round(ret * 100, 1), round(vr, 1), line))
                break

    res.sort(key=lambda r: (r[0], -r[5]))
    mode = "동시" if args.both else "112 또는 224"
    rng = anchor if args.window == 1 else f"{dates[win_idx.start]}~{anchor}"
    print(f"[장기이평 폭발형 돌파] {mode} | 등락≥+{args.ret}% | 거래량≥{args.vmult}배 | 기준 {rng}")
    print(f"{'돌파일':<10}{'코드':<7}{'종목명':<14}{'시장':<7}{'종가':>9}{'등락%':>7}{'거래배':>7}  돌파선")
    for d, c, nm, m, px, ret, vr, line in res:
        print(f"{d:<10}{c:<7}{nm:<14}{m:<7}{px:>9,}{ret:>6}%{vr:>6}x  {line}")
    print(f"총 {len(res)}종")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="기준일 YYYYMMDD (기본: 최신 캐시일)")
    ap.add_argument("--window", type=int, default=1, help="기준일 포함 스캔 영업일수 (기본 1=당일만)")
    ap.add_argument("--both", action="store_true", help="112·224 동시 돌파만")
    ap.add_argument("--ret", type=float, default=10.0, help="최소 당일 등락%% (기본 10)")
    ap.add_argument("--vmult", type=float, default=3.0, help="최소 거래량 배수(20일평균 대비, 기본 3)")
    ap.add_argument("--maxgap", type=float, default=30.0, help="돌파일 종가-기준선 최대 이격%% (동전주 제외, 기본 30)")
    ap.add_argument("--refresh", action="store_true", help="스캔 전 KRX에서 최근 영업일 캐시 갱신")
    a = ap.parse_args()
    if a.refresh:
        refresh_recent()
    scan(a)
