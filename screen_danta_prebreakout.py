# -*- coding: utf-8 -*-
"""
screen_danta_prebreakout.py — ⚡ 단타 · 돌파임박 (08:30 [B1h] · KRX 전일 확정봉 · 2026-10-21 신설)

차트와 수급만 본다 (공시·재무 안 봄 · 사용자 지시). 08:30 에만 돈다 (15:30·19:00 제외).
  차트 : 종가가 장기선 max(MA112, MA224) −10% 이내 아래 · 양봉 · 몸통 50% 이상 · 종가 > MA20·MA60 · MA20 이 5일 전보다 위
         · 시총 1,000억 이상 · 최근 60일 상장주식수 30% 넘는 변동(분할·병합·감자) 없음 · 같은 종목 20거래일 안 재신호 제외
  수급 : 외국인+기관 5일 순매수 > 0 이고 당일(신호일) 외국인+기관 순매수 > 0 (토스 Open API, ../jck_stock-usa/toss.py)
  정렬 : 수급 강도(5일 순매수 ÷ 20일 평균 거래량 %) 순 최대 5종 — 밥그릇3과 같은 기준

매매 규칙(사용자 지시 10-21): 다음 날 시가 매수 → 최대 3거래일 · +5% 도달 시 익절 · 아니면 3일째 종가 청산.
⚠️ 백테스트(2024-10-21~2026-10-20): 이 조건 2,353건 +5% 도달 24% · 평균 −0.05%(비용 0.25% 뒤 −0.30%) · 손절 −3%/−5% 추가 시 −0.22%/−0.06%.
   아무 종목을 같은 규칙으로 사면 +5% 도달 29% · −0.03% → 우위 없음. 다음 날 시가→종가만 보면 −0.11%(시장 −0.16%).

출력: data/danta_{date}.json  {date, anchor, criteria, picks:[...], stats}
사용: python screen_danta_prebreakout.py [--dry-run]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import numpy as np

from screen_rr_breakout import load, ma

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT.parent / "jck_stock-usa"))   # 토큰 1개 — stock-usa 스크립트와 동시 실행 금지

GAP, BODY, CAP, TOPN, REPEAT = 0.10, 0.5, 1e11, 5, 20


def signal(rows: list, i: int, o, h, l, c, L, m20, m60, cap, jump) -> bool:
    return (cap[i] >= CAP and L[i] * (1 - GAP) <= c[i] < L[i] and c[i] > o[i]
            and (c[i] - o[i]) / max(h[i] - l[i], 1e-9) >= BODY
            and c[i] > m20[i] and c[i] > m60[i] and m20[i] > m20[i - 5]
            and not jump[max(0, i - 60):i + 1].any())


def flow(code: str, d: str):
    import toss
    iso = f"{d[:4]}-{d[4:6]}-{d[6:]}"
    rec = [r for r in toss.get(f"/api/v1/stocks/{code}/investor-trading", until=iso)["records"] if r["date"] <= iso][:5]
    g = lambda r: sum(float((r.get(k) or {}).get("netBuyVolume") or 0) for k in ("foreigner", "institution"))
    return (sum(map(g, rec)), g(rec[0]) if rec and rec[0]["date"] == iso else None) if rec else (None, None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).strftime("%Y-%m-%d")
    bars, names, markets = load()
    last = max(r[-1][0] for r in bars.values())
    cands = []
    for code, rows in bars.items():
        if len(rows) < 300:
            continue
        rows.sort()
        if rows[-1][0] != last:
            continue
        o, h, l, c, v, cap = np.array([r[1:] for r in rows], dtype=float).T
        L, m20, m60, v20 = np.fmax(ma(c, 112), ma(c, 224)), ma(c, 20), ma(c, 60), ma(v, 20)
        sh = np.where(c > 0, cap / c, np.nan)
        jump = np.abs(np.diff(sh) / sh[:-1]) > 0.3
        n = len(c)
        args = (o, h, l, c, L, m20, m60, cap, jump)
        if not signal(rows, n - 1, *args) or any(signal(rows, k, *args) for k in range(n - REPEAT, n - 1)):
            continue
        cands.append(dict(code=code, name=names[code], market=markets[code], close=round(c[-1]), line=round(L[-1]),
                          gap_pct=round((1 - c[-1] / L[-1]) * 100, 1), vol_x=round(v[-1] / v20[-2], 2), v20=float(v20[-1]),
                          cap_billion=round(cap[-1] / 1e8)))
    print(f"[danta] KRX {last} · 차트 조건 {len(cands)}종 → 수급 심사")
    picks, rejected = [], []
    for x in cands:
        try:
            f5, f1 = flow(x["code"], last)
        except (Exception, SystemExit) as e:
            f5, f1 = None, None
            print(f"  [WARN] 수급 조회 실패 {x['name']}: {str(e)[:120]}")
        x.update(flow_5d=f5, flow_1d=f1)
        if f5 is None or f1 is None:
            x["reason"] = "수급 판정 불가"
        elif f5 <= 0 or f1 <= 0:
            x["reason"] = f"외국인+기관 5일 {f5:,.0f} · 당일 {f1:,.0f}주"
        else:
            x["score"] = round(f5 / x["v20"] * 100, 1) if x["v20"] else 0.0
            picks.append(x)
            continue
        rejected.append(x)
    picks.sort(key=lambda r: -r["score"])
    out = {"title": "⚡ 단타 · 돌파임박", "date": today, "anchor": last,
           "criteria": ["종가 장기선(max MA112·224) −10% 이내 아래", "양봉·몸통 50%+", "종가 > MA20·MA60 · MA20 상승",
                        "시총 1,000억+ · 분할 가드 · 20거래일 재신호 제외", "외국인+기관 5일 > 0 · 당일 > 0", "수급 강도 순 5종"],
           "picks": picks[:TOPN], "rejected": rejected, "stats": {"chart": len(cands), "passed": len(picks)}}
    for r in picks[:TOPN]:
        print(f"  ⚡ {r['name']}({r['code']}) 종가 {r['close']:,} · 장기선 {r['line']:,} (−{r['gap_pct']}%) · 수급 {r['score']}%")
    for r in rejected:
        print(f"  ❌ {r['name']}({r['code']}) — {r['reason']}")
    if a.dry_run:
        print("[danta] --dry-run: 저장 생략")
        return
    p = ROOT / "data" / f"danta_{today}.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[danta] 저장: {p} (통과 {len(picks)} → 표시 {len(out['picks'])})")


if __name__ == "__main__":
    main()
