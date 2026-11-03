# -*- coding: utf-8 -*-
"""
screen_bowl3_quality.py — 🍚 밥그릇3 = 3구간 눌림 (08:30 [B1d] · KRX 전일 확정 기준, 2026-10-21 개정 · 같은 날 박스 기간 0.5배·120선 제외)

KRX 캐시 전 종목에서 밥그릇 1·2구간을 거쳐 3구간으로 출발(장기선 돌파)한 뒤 장기선까지 눌린 종목을 찾는다.
장기선 = max(MA112, MA224). (120선은 백테스트 53/53 동일 판정이라 제외 — 3구간 자리에선 224선이 늘 위)

  1구간 바닥   : 돌파 전 500거래일 고점 → 저점 −40% 이상, 저점이 112·224선 모두 아래
  2구간 매집   : 저점~돌파 기간 ≥ 고점~저점 기간 × 0.5 · 그동안 종가 70% 이상이 224선 아래
  3구간 출발   : 전일 종가 ≤ 장기선 < 당일 종가 · 양봉 · 거래량 ≥ 20일 평균 1.5배 · 시총 ≥ 1,000억
  3구간 눌림   : 돌파 후 20거래일 안 처음으로 저가가 장기선 −1%~+5% 안에 든 날 (그 사이 장기선 −1% 종가 이탈·+20% 초과 상승 없음)
                 + 그날 양봉
  수급         : 눌림일 포함 5거래일 외국인+기관 순매수 합계 > 0 (토스 Open API, ../jck_stock-usa/toss.py)
  공시         : 올해(1/1~오늘) 악재 공시 0건 (DART, 종전과 같은 판정) — 제외
  재무         : 3개년 매출·순이익 연속 증가 — **표시만**(fin_ok 배지, 탈락 아님 · 사용자 A안)
  가드         : 상장주식수 30% 넘게 바뀐 종목(분할·병합·감자) 제외 · 같은 종목 180일 안 재신호 제외

노출 기간 = 눌림일부터 60거래일 (26-11-03 20→60 · 백테스트 측정 기간은 20거래일). score = 수급 강도(외국인+기관 5일 순매수 ÷ 20일 평균 거래량, %) — 정렬용.

근거 (2026-10-21 백테스트, KRX 2022-10~2026-10 · 시장 대비 20일 초과수익 중앙값):
  1·2·3구간 눌림 138건 −0.2% → +수급 107건 +0.5% → +양봉 53건 +2.1% → +공시 45건 +1.9%.
  박스 기간 1:1 → 0.5배 완화: 53건 → 111건, 중앙 +2.1% → +1.1%, +10% 도달률 47% → 52% (단일 완화 중 채택).
  재무 3년 성장을 필터로 걸면 53건 → 3건(밥그릇 1구간은 실적 악화 뒤가 대부분) → 표시만.
⚠️ 2026-10-21 이전 산출물(돌파·눌림 pool + 재무 필터)과 통과 건수·성과를 비교하지 말 것.

출력: data/bowl3_quality_{date}.json — passed/rejected 형식은 종전과 같음(build_bowl3_tab.js · slack_send.js · exposure.js 가 읽음)

사용:
  python screen_bowl3_quality.py              # 오늘 날짜로 저장
  python screen_bowl3_quality.py --dry-run    # 저장 없이 콘솔 출력
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import numpy as np

from dart_openapi import DARTClient, DARTAPIError
from screen_rr_breakout import load, ma

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
sys.path.insert(0, str(ROOT.parent / "jck_stock-usa"))   # 토스 Open API 클라이언트 (토큰은 클라이언트당 1개 — stock-usa 스크립트와 동시 실행 금지)

DROP, PEAK_LB, BOX_BELOW, BOX_RATIO, VOL_X, CAP = 0.40, 500, 0.70, 0.5, 1.5, 1e11
PB_DAYS, PB_LO, PB_HI, RUN_MAX = 20, -0.01, 0.05, 0.20
SHOW_DAYS, DEDUP_DAYS, FLOW_DAYS = 60, 180, 5   # SHOW_DAYS 20→60 (26-11-03 사용자 지시)


def log(msg: str):
    ts = dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}")


def _default_bsns_year(anchor_iso: str) -> str:
    """직전 확정 사업보고서 연도 (4월 이후=전년, 이전=전전년)."""
    d = dt.datetime.strptime(anchor_iso.replace("-", ""), "%Y%m%d").date()
    return str(d.year - 1 if d.month >= 4 else d.year - 2)


def _incr3(seq) -> bool:
    """[전전, 전, 당] 3개가 모두 존재하고 연속 순증가(strict)인지."""
    return bool(seq) and len(seq) == 3 and all(v is not None for v in seq) and seq[0] < seq[1] < seq[2]


def find_pullbacks(rows: list) -> list[dict]:
    """한 종목 일봉에서 3구간 눌림(양봉) 이벤트 전부. 180일 재신호 제외까지 적용."""
    a = np.array([r[1:] for r in rows], dtype=float)
    o, h, l, c, v, cap = a.T
    m112, m224 = ma(c, 112), ma(c, 224)
    top, bot = np.fmax(m112, m224), np.fmin(m112, m224)
    v20 = ma(v, 20)
    sh = np.where(c > 0, cap / c, np.nan)
    jump = np.abs(np.diff(sh) / sh[:-1]) > 0.3
    n, out = len(c), []
    for b in range(225, n):
        if not (c[b - 1] <= top[b - 1] and c[b] > top[b] and c[b] > o[b] and v[b] >= VOL_X * v20[b - 1] and cap[b] >= CAP):
            continue
        s0 = max(0, b - PEAK_LB)
        if jump[s0:min(n - 1, b + PB_DAYS)].any():
            continue
        p = s0 + int(np.argmax(h[s0:b - 20]))
        t = p + int(np.argmin(l[p:b]))
        if l[t] > h[p] * (1 - DROP) or not l[t] < bot[t]:
            continue
        if (b - t) < BOX_RATIO * (t - p) or np.mean(c[t:b] < m224[t:b]) < BOX_BELOW:
            continue
        for k in range(b + 1, min(b + 1 + PB_DAYS, n)):
            if c[k] < top[k] * 0.99 or h[b:k + 1].max() > c[b] * (1 + RUN_MAX):
                break
            if top[k] * (1 + PB_LO) <= l[k] <= top[k] * (1 + PB_HI) and c[k] >= top[k] * 0.99:
                out.append(dict(k=k, breakout_date=rows[b][0], pull_date=rows[k][0], line=float(top[k]),
                                v20=float(v20[k]), drop=float(1 - l[t] / h[p]), up=bool(c[k] > o[k])))
                break
    kept, last = [], None
    for e in out:   # 같은 종목 180일 안 재신호 제외 → 그다음 양봉만 (백테스트와 같은 순서)
        d = dt.datetime.strptime(e["pull_date"], "%Y%m%d")
        if last and (d - last).days < DEDUP_DAYS:
            continue
        last = d
        kept.append(e)
    return [e for e in kept if e["up"]]


def flow_5d(code: str, pull_date: str) -> float | None:
    """눌림일 포함 5거래일 외국인+기관 순매수 합계 (주). 실패 시 None."""
    import toss
    d = f"{pull_date[:4]}-{pull_date[4:6]}-{pull_date[6:]}"
    try:
        rec = [r for r in toss.get(f"/api/v1/stocks/{code}/investor-trading", until=d)["records"] if r["date"] <= d][:FLOW_DAYS]
    except (Exception, SystemExit) as e:
        log(f"  [WARN] 토스 수급 조회 실패 {code}: {str(e)[:120]}")
        return None
    if not rec:
        return None
    g = lambda r, k: float((r.get(k) or {}).get("netBuyVolume") or 0)
    return sum(g(r, "foreigner") + g(r, "institution") for r in rec)


def run(dry_run: bool):
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
        n = len(rows)
        for e in find_pullbacks(rows):
            if e["k"] >= n - SHOW_DAYS:   # 눌림일이 최근 SHOW_DAYS(60)거래일 안
                cands.append(dict(code=code, name=names[code], market=markets[code], close=rows[-1][4],
                                  cap=rows[-1][6], dplus=n - 1 - e["k"], **e))
    log(f"KRX {last} · 3구간 눌림(양봉) 최근 {SHOW_DAYS}거래일 {len(cands)}종 → 수급·공시 심사")

    dart = DARTClient()
    bsns_year = _default_bsns_year(today)
    disc_days = (dt.date.fromisoformat(today) - dt.date(int(today[:4]), 1, 1)).days + 1
    passed, rejected = [], []
    for c in cands:
        res = {"code": c["code"], "name": c["name"], "market": c["market"], "score": None,
               "bucket": "3구간 눌림", "line": round(c["line"]), "line_": "112+224",
               "breakout_date": c["breakout_date"], "pull_date": c["pull_date"], "dplus": c["dplus"],
               "current_close": round(c["close"]), "market_cap_billion": round(c["cap"] / 1e8),
               "drop_pct": round(c["drop"] * 100, 1), "flow_5d": None,
               "fin_ok": None, "fin": "-", "sales": None, "net_inc": None, "bsns_year": bsns_year,
               "negative": [], "passed": False, "reason": None}
        fi = flow_5d(c["code"], c["pull_date"])
        res["flow_5d"] = fi
        if fi is None:
            res["reason"] = "수급 판정 불가(토스 조회 실패)"
        elif fi <= 0:
            res["reason"] = f"외국인+기관 5일 순매도 {fi:,.0f}주"
        if res["reason"]:
            rejected.append(res); continue
        res["score"] = round(fi / c["v20"] * 100, 1) if c["v20"] else 0.0
        corp = dart.to_corp_code(c["code"])
        if not corp:
            res["reason"] = "corp_code 없음"
            rejected.append(res); continue
        try:
            neg = DARTClient.find_negative(dart.recent_disclosures(c["code"], days=disc_days))
        except DARTAPIError as e:
            res["reason"] = f"공시 조회 실패({e})"
            rejected.append(res); continue
        if neg:
            res["negative"] = neg
            res["reason"] = "당해년도 악재 공시: " + ", ".join(sorted({x["matched"] for x in neg}))
            rejected.append(res); continue
        try:   # 재무 = 표시만 (탈락 아님)
            fin = dart.financial_full(corp, bsns_year)
            res["sales"], res["net_inc"] = fin.get("sales"), fin.get("net_inc")
            res["fin_ok"] = bool(fin.get("ok") and _incr3(fin.get("sales")) and _incr3(fin.get("net_inc")))
            res["fin"] = "3년 성장" if res["fin_ok"] else ("재무 없음" if not fin.get("ok") else "3년 연속 증가 아님")
        except DARTAPIError as e:
            res["fin"] = f"재무 조회 실패({e})"
        res["passed"] = True
        res["reason"] = "3구간 눌림 양봉 + 외국인·기관 순매수 + 공시 청정"
        passed.append(res)

    passed.sort(key=lambda r: -(r.get("score") or 0))
    result = {
        "title": "밥그릇3 = 3구간 눌림",
        "date": today, "anchor": last,
        "criteria": {"rules": ["1구간 −40% 하락·저점 장기선 아래", "2구간 박스(하락 기간 0.5배 이상)·224선 아래 70%",
                               "3구간 장기선(112·224) 동시 돌파·양봉·거래량 1.5배·시총 1,000억",
                               "돌파 후 20일 안 장기선 −1%~+5% 첫 눌림 + 양봉", "외국인+기관 5일 순매수 > 0",
                               "올해 악재 공시 0건", "재무 3년 성장 = 표시만"],
                     "show_days": SHOW_DAYS, "financial_bsns_year": bsns_year, "disclosure_year": int(today[:4])},
        "passed": passed, "rejected": rejected,
        "stats": {"universe": len(cands), "passed": len(passed), "rejected": len(rejected)},
    }
    if dry_run:
        log("--dry-run: 저장 생략")
    else:
        out = DATA_DIR / f"bowl3_quality_{today}.json"
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"저장: {out}")
    log(f"=== 완료 (통과 {len(passed)} / 대상 {len(cands)}) ===")
    for r in passed:
        log(f"   🍚 {r['name']}({r['code']}) 눌림 {r['pull_date']} D+{r['dplus']} · 수급 {r['score']}% · 재무 {r['fin']}")
    for r in rejected:
        log(f"   ❌ {r['name']}({r['code']}) 눌림 {r['pull_date']} — {r['reason']}")
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="저장 없이 콘솔 출력")
    a = ap.parse_args()
    try:
        run(a.dry_run)
    except (FileNotFoundError, DARTAPIError) as e:
        log(f"!! 실패: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
