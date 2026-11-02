"""
screen_quality_value.py — 우량주 발굴 심사 (영상 기법, 업종 보정 없음)

심사 6조건 (모두 동일 가중, 보정 전면 없음):
  1. 성장성   : 매출액 3개년 매년 증가
  2. 수익성   : 당기순이익 ≥ 50억 + 흑자 지속(3개년 >0)
  3. 안정성A  : 부채총계 / 자기자본 ≤ 100%
  4. 안정성B  : 부채총계 / 자산총계 < 80%
  5. 자산증가 : 자산총계 매년 증가
  6. 가격위치 : 52주 고점 대비 -25%↑ 하락 AND 52주 위치 하위 40% (바겐세일)
              ※ 주도주(반도체·로봇 등 고점주)는 ①②로 자연 배제

등급: A=6/6 · B=5/6 · C=4/6 · D≤3/6

데이터:
  재무 = DART fnlttSinglAcntAll (직전 사업보고서 1콜로 3개년 동시)
  가격 = KRX OHLCV 일별 캐시 (data/krx_ohlcv_cache/*.json, 52주 집계)

사용:
  python screen_quality_value.py 039490 005380       # 단일/복수 종목 심사
  python screen_quality_value.py --scan --date 20260626   # 전체 스캔 → A등급 나열
  python screen_quality_value.py --scan --date 20260626 --grade B  # B 이상
"""
from __future__ import annotations

import json
import sys
import argparse
import datetime as dt
from pathlib import Path

from dart_openapi import DARTClient, DARTAPIError

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).parent
KRX_CACHE = ROOT / "data" / "krx_ohlcv_cache"
OUT_DIR = ROOT / "data" / "quality_screen"
OUT_DIR.mkdir(parents=True, exist_ok=True)

NET_INC_MIN = 5_000_000_000      # 당기순이익 50억 (매년 · 강화 2026-06-29)
MKTCAP_FLOOR = 50_000_000_000    # 시총 500억 (스캔 사전필터 · 화자 기준)
DD_MIN = 0.35                    # 52주 고점 대비 -35% 이상 하락 (강화 2026-06-29)
POS52_MAX = 0.40                # 52주 위치 하위 40%
GRADE = {6: "A", 5: "B", 4: "C"}


def grade_of(passed: int) -> str:
    return GRADE.get(passed, "D")


# ----------------------------- KRX 52주 가격위치 -----------------------------

def _biz_files(end_dd: str, days: int = 364):
    """end_dd(YYYYMMDD) 기준 직전 days일 구간의 캐시 파일 경로 목록."""
    end = dt.datetime.strptime(end_dd, "%Y%m%d").date()
    bgn = end - dt.timedelta(days=days)
    out = []
    for p in KRX_CACHE.glob("*.json"):
        stem = p.stem  # 20260626_KOSPI
        try:
            d = dt.datetime.strptime(stem.split("_")[0], "%Y%m%d").date()
        except (ValueError, IndexError):
            continue
        if bgn <= d <= end:
            out.append((d, p))
    return [p for _, p in sorted(out)]


def build_price_position(end_dd: str) -> dict:
    """52주 고/저 집계 + end_dd 당일 종가·시총·종목명 → code별 가격위치 dict."""
    files = _biz_files(end_dd)
    if not files:
        raise RuntimeError(f"KRX 캐시에 {end_dd} 기준 52주 데이터 없음")
    hi: dict[str, float] = {}
    lo: dict[str, float] = {}
    meta: dict[str, dict] = {}
    end_iso = end_dd
    for p in files:
        is_end = p.stem.split("_")[0] == end_iso
        try:
            rows = json.loads(p.read_text(encoding="utf-8")).get("rows", [])
        except Exception:
            continue
        for r in rows:
            code = r.get("code")
            if not code:
                continue
            h, l = r.get("high"), r.get("low")
            if h:
                hi[code] = max(hi.get(code, 0), h)
            if l:
                lo[code] = min(lo.get(code, 10**12), l)
            if is_end:
                meta[code] = {
                    "name": r.get("name"), "market": r.get("market"),
                    "close": r.get("close"), "market_cap": r.get("market_cap"),
                }
    out = {}
    for code, m in meta.items():
        close = m.get("close")
        h, l = hi.get(code), lo.get(code)
        if not (close and h and l and h > l):
            continue
        drawdown = (h - close) / h           # 고점 대비 낙폭
        pos52 = (close - l) / (h - l)        # 0=52저, 1=52고
        out[code] = {
            **m, "high52": h, "low52": l,
            "drawdown": round(drawdown, 4), "pos52": round(pos52, 4),
            "bargain": bool(drawdown >= DD_MIN and pos52 <= POS52_MAX),
        }
    return out


# ----------------------------- 재무 6조건 채점 -----------------------------

def _default_bsns_year(end_dd: str) -> str:
    """직전 확정 사업보고서 연도 (4월 이후=전년, 이전=전전년)."""
    d = dt.datetime.strptime(end_dd, "%Y%m%d").date()
    return str(d.year - 1 if d.month >= 4 else d.year - 2)


def _incr(seq) -> bool | None:
    """[전전, 전, 당] 중 None 제외하고 2개 이상 + 순증가 여부."""
    vals = [v for v in seq if v is not None]
    if len(vals) < 2:
        return None
    return all(vals[i] < vals[i + 1] for i in range(len(vals) - 1))


# DART 분기/반기 reprt + 마감(분기말+45일) — 가용 시점 순서대로 시도
_Q_AVAIL = [((11, 15), "11014", "3Q"), ((8, 15), "11012", "반기"), ((5, 16), "11013", "1Q")]


def latest_quarter(dart: DARTClient, corp: str, end_dd: str):
    """기준일에 제출됐을 가장 최근 분기/반기 보고서를 찾아 financial_full 반환.

    반환: (fin dict, bsns_year, 보고서명) — net_inc[-1]=당기YTD·[-2]=전년동기YTD,
          liab/equity/assets[-1]=최신 재무상태표 스냅샷. 못 찾으면 (None, None, None).
    """
    d = dt.datetime.strptime(end_dd, "%Y%m%d").date()
    md = (d.month, d.day)
    cands = []
    for avail, rc, nm in _Q_AVAIL:
        if md >= avail:
            cands.append((str(d.year), rc, nm))
    # 전년도 분기 폴백 (연초 등)
    cands += [(str(d.year - 1), rc, nm) for _, rc, nm in _Q_AVAIL]
    for yr, rc, nm in cands:
        fin = dart.financial_full(corp, yr, rc)
        if fin.get("ok") and fin["net_inc"][-1] is not None:
            return fin, yr, nm
    return None, None, None


def score_stock(code: str, dart: DARTClient, pos: dict | None, bsns_year: str,
                quarterly: bool = False, end_dd: str | None = None) -> dict:
    """단일 종목 6조건 채점 → 스코어카드 dict.

    quarterly=True: 최신 분기/반기 보고서로 순익(TTM)·부채/자본/자산(최신 스냅샷)을 갱신.
    매출·자산 3개년 추세는 연간 사업보고서 유지.
    """
    corp = dart.to_corp_code(code)
    card = {"code": code, "name": (pos or {}).get("name"), "checks": {}, "passed": 0,
            "grade": "D", "bsns_year": bsns_year, "price_pos": pos}
    if not corp:
        card["error"] = "corp_code 없음"
        return card
    fin = dart.financial_full(corp, bsns_year)
    if not fin.get("ok"):
        card["error"] = "재무 조회 실패"
        return card

    sales, net, assets = fin["sales"], fin["net_inc"], fin["assets"]
    liab, equity = fin.get("liab"), fin.get("equity")
    net_vals = [v for v in net if v is not None]

    # --- 분기/반기 반영 (옵션) ---
    ttm_net = None
    q_basis = None
    if quarterly and end_dd:
        qfin, q_year, q_nm = latest_quarter(dart, corp, end_dd)
        if qfin:
            q_basis = f"{q_year} {q_nm}"
            # 부채/자본/자산 = 최신 재무상태표 스냅샷으로 교체
            if qfin.get("liab") is not None:
                liab = qfin["liab"]
            if qfin.get("equity"):
                equity = qfin["equity"]
            if qfin["assets"][-1]:
                assets = assets[:-1] + [qfin["assets"][-1]]  # 최신 자산 갱신
            # TTM 순익 = 직전연간 + (당기YTD − 전년동기YTD)
            q_cur, q_prev = qfin["net_inc"][-1], qfin["net_inc"][-2]
            if net_vals and q_cur is not None and q_prev is not None:
                ttm_net = net[-1] + (q_cur - q_prev)

    c = card["checks"]
    # 1. 성장성 (매출 증가)
    c["1_매출증가"] = bool(_incr(sales))
    # 2. 수익성 (당기순익 *매년* 50억 + 분기반영 시 TTM도 ≥50억)
    cond2 = bool(len(net_vals) >= 2 and min(net_vals) >= NET_INC_MIN)
    if ttm_net is not None:
        cond2 = cond2 and (ttm_net >= NET_INC_MIN)
    c["2_순익50억"] = cond2
    # 3. 부채/자본 ≤100%
    c["3_부채자본"] = bool(liab is not None and equity and equity > 0 and (liab / equity) <= 1.0)
    # 4. 부채/자산 <80%
    c["4_부채자산"] = bool(liab is not None and assets[-1] and assets[-1] > 0 and (liab / assets[-1]) < 0.8)
    # 5. 자산증가
    c["5_자산증가"] = bool(_incr(assets))
    # 6. 가격위치 (바겐세일)
    c["6_가격위치"] = bool(pos and pos.get("bargain"))

    card["passed"] = sum(1 for v in c.values() if v)
    card["grade"] = grade_of(card["passed"])
    if q_basis:
        card["quarter_basis"] = q_basis
    card["metrics"] = {
        "sales": sales, "net_inc": net, "assets": assets, "ttm_net_inc": ttm_net,
        "debt_to_equity": round(liab / equity, 3) if (liab is not None and equity) else None,
        "debt_to_assets": round(liab / assets[-1], 3) if (liab is not None and assets[-1]) else None,
        "drawdown": (pos or {}).get("drawdown"), "pos52": (pos or {}).get("pos52"),
    }
    return card


# ------------------- 펀더멘탈 ①~⑤ soft 게이트 (돌·눌·재 크로스필터) -------------------
# ⑥ 가격위치(바겐세일)를 **의도적으로 제외**한다.
#   돌·눌·재 대상은 이미 224선을 돌파해 52주 상단권으로 올라온 주이고,
#   ⑥은 "52주 하위 40%·낙폭 35%↑"를 찾으므로 방향이 정반대다.
#   2026-07-14 읽기전용 시뮬(돌·눌·재 통과 12종 실측): ⑥ 포함 시 3/12(25%)만 생존 =
#   75% 부당 탈락. ①~⑤만 쓰면 ≥4/5에서 8/12(67%) 생존 → 커버리지 고갈 없음.
#   근거·실증 = 돌눌재_보강안_CwH_우량주게이트.md §0
FUND_GRADE = {5: "A", 4: "B"}    # 5/5=🅰️우량 · 4/5=🅱️ · ≤3=배지 없음
FUND_BADGE_CUT = 4               # soft badge 노출 컷 (2026-07-15 사용자 확정: 우량 ≥4/5)


def fund_grade_of(passed: int) -> str | None:
    return FUND_GRADE.get(passed)


def grade_fundamental(code: str, dart: DARTClient | None = None,
                      bsns_year: str | None = None, end_dd: str | None = None) -> dict:
    """펀더멘탈 ①~⑤만 집계 (⑥ 가격위치 제외) → soft 게이트용 스코어.

    반환: {passed:0~5, grade:"A"|"B"|None, badge:bool, details:{조건:bool}, error?}
    badge=True는 passed >= FUND_BADGE_CUT(4)일 때만. **pass/fail을 뒤집지 않는다** —
    호출부는 배지·정렬에만 쓰고 종목을 탈락시키면 안 된다.
    """
    if dart is None:
        dart = DARTClient()
    if end_dd is None:
        end_dd = dt.date.today().strftime("%Y%m%d")
    if bsns_year is None:
        bsns_year = _default_bsns_year(end_dd)

    # pos=None → ⑥은 자동 False가 되지만 아래에서 아예 집계 대상에서 뺀다.
    card = score_stock(code, dart, None, bsns_year, quarterly=False, end_dd=end_dd)
    if card.get("error"):
        return {"passed": 0, "grade": None, "badge": False, "details": {},
                "error": card["error"], "bsns_year": bsns_year}

    details = {k: v for k, v in card["checks"].items() if not k.startswith("6_")}
    passed = sum(1 for v in details.values() if v)
    grade = fund_grade_of(passed)
    return {
        "passed": passed, "grade": grade, "badge": passed >= FUND_BADGE_CUT,
        "details": details, "bsns_year": bsns_year,
        "metrics": card.get("metrics"), "name": card.get("name"),
    }


def fmt_won(v):
    if v is None:
        return "?"
    return f"{v/1e8:,.0f}억"


def print_card(card: dict):
    g = card["grade"]
    emoji = {"A": "🅰️", "B": "🅱️", "C": "🅲", "D": "🅳"}.get(g, "?")
    name = card.get("name") or card["code"]
    qb = f" · 분기반영 {card['quarter_basis']}" if card.get("quarter_basis") else ""
    print(f"\n{emoji} {name} ({card['code']}) — {g}등급 · {card['passed']}/6 통과{qb}")
    if card.get("error"):
        print(f"   ⚠️ {card['error']}")
        return
    c, m = card["checks"], card["metrics"]
    mark = lambda b: "✅" if b else "❌"
    ttm = f" · TTM {fmt_won(m['ttm_net_inc'])}" if m.get("ttm_net_inc") is not None else ""
    print(f"   {mark(c['1_매출증가'])} 매출 {[fmt_won(x) for x in m['sales']]}")
    print(f"   {mark(c['2_순익50억'])} 순익 {[fmt_won(x) for x in m['net_inc']]} (≥50억){ttm}")
    print(f"   {mark(c['3_부채자본'])} 부채/자본 {m['debt_to_equity']} (≤1.0)")
    print(f"   {mark(c['4_부채자산'])} 부채/자산 {m['debt_to_assets']} (<0.8)")
    print(f"   {mark(c['5_자산증가'])} 자산 {[fmt_won(x) for x in m['assets']]}")
    dd, p52 = m.get('drawdown'), m.get('pos52')
    dd_s = f"낙폭 {dd*100:.0f}%·위치 {p52*100:.0f}%" if dd is not None else "가격데이터 없음"
    print(f"   {mark(c['6_가격위치'])} 가격위치 {dd_s}")


def save_card(card: dict):
    (OUT_DIR / f"{card['code']}.json").write_text(
        json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")


# ----------------------------- 모드 -----------------------------

def run_single(codes: list[str], end_dd: str, quarterly: bool = False):
    dart = DARTClient()
    bsns_year = _default_bsns_year(end_dd)
    try:
        pos_all = build_price_position(end_dd)
    except RuntimeError as e:
        print(f"⚠️ {e} — 가격위치 조건은 미평가")
        pos_all = {}
    for code in codes:
        code = code.zfill(6)
        card = score_stock(code, dart, pos_all.get(code), bsns_year, quarterly, end_dd)
        print_card(card)
        save_card(card)


def run_scan(end_dd: str, min_grade: str = "A", limit: int | None = None, quarterly: bool = False):
    dart = DARTClient()
    bsns_year = _default_bsns_year(end_dd)
    print(f"[1/3] KRX 52주 가격위치 집계 ({end_dd}) …")
    pos_all = build_price_position(end_dd)
    print(f"      종목 {len(pos_all)}개 가격위치 산출 완료")

    # 사전필터: 시총 500억+ AND 바겐세일(가격위치) 통과 → DART 검사 대상
    cand = {code: p for code, p in pos_all.items()
            if (p.get("market_cap") or 0) >= MKTCAP_FLOOR and p.get("bargain")}
    cand = dict(sorted(cand.items(), key=lambda kv: -kv[1]["drawdown"]))
    if limit:
        cand = dict(list(cand.items())[:limit])
    print(f"[2/3] 사전필터(시총≥500억 + 바겐세일) → {len(cand)}종 DART 재무 검사 …")

    want = {"A": 6, "B": 5, "C": 4}.get(min_grade, 6)
    results, done = [], 0
    for code, p in cand.items():
        done += 1
        try:
            card = score_stock(code, dart, p, bsns_year, quarterly, end_dd)
        except DARTAPIError as e:
            print(f"   ({done}/{len(cand)}) {code} 재무 오류: {e}")
            continue
        save_card(card)
        if card["passed"] >= want:
            results.append(card)
        if done % 25 == 0:
            print(f"      {done}/{len(cand)} 검사 (현재 {min_grade}+ {len(results)}종)")

    results.sort(key=lambda c: (-c["passed"], -(c["price_pos"] or {}).get("drawdown", 0)))
    scan_path = OUT_DIR / f"scan_{end_dd}.json"
    scan_path.write_text(json.dumps({
        "date": end_dd, "bsns_year": bsns_year, "min_grade": min_grade,
        "scanned": len(cand), "matched": len(results),
        "results": results,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n[3/3] ===== {min_grade}등급 이상 {len(results)}종 (재무기준 {bsns_year}년 · 시세 {end_dd}) =====")
    for card in results:
        print_card(card)
    print(f"\n결과 저장: {scan_path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("codes", nargs="*", help="종목코드 (단일/복수 심사)")
    ap.add_argument("--scan", action="store_true", help="전체 스캔 모드")
    ap.add_argument("--date", default=None, help="기준 영업일 YYYYMMDD (기본: 최신 캐시)")
    ap.add_argument("--grade", default="A", help="스캔 최소 등급 (A/B/C)")
    ap.add_argument("--limit", type=int, default=None, help="스캔 대상 상한")
    ap.add_argument("--quarterly", action="store_true", help="최신 분기/반기 재무 반영(순익 TTM·부채 스냅샷)")
    args = ap.parse_args()

    end_dd = args.date
    if not end_dd:
        # 장중/장전에 만들어진 당일 캐시는 rows=0인 경우가 있어 그대로 쓰면
        # 전 종목 가격위치가 실패(=등급 하향)한다. rows가 있는 최신 파일로 폴백.
        for p in sorted(KRX_CACHE.glob("*_KOSPI.json"), reverse=True):
            try:
                if json.loads(p.read_text(encoding="utf-8")).get("rows"):
                    end_dd = p.stem.split("_")[0]
                    break
            except Exception:
                continue
    if not end_dd:
        print("기준일 미지정 + 캐시 없음"); sys.exit(1)

    if args.scan:
        run_scan(end_dd, args.grade.upper(), args.limit, args.quarterly)
    elif args.codes:
        run_single(args.codes, end_dd, args.quarterly)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
