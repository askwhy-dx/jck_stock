# -*- coding: utf-8 -*-
"""
collect_breakout_flow.py — 08:15 회차 장기이평 폭발형 돌파 스캐너 (돌파 탭 전담)

눌림 스캐너(collect_theme_flow.py)와 대칭. 눌림이 '돌파 후 첫 눌림(거래량 감소)'을
잡는다면, 본 스캐너는 '방금 터진 폭발형 돌파(거래량 급증·큰 양봉)'를 잡는다.
정의 원본: 장기이평돌파.md (한켐 2026-05-22 유형).

유형 1 (당일 폭발):
  전일 종가 ≤ 112/224선, 당일 종가 > 선 + 당일 등락≥+10% + 거래량≥20일평균 3배.
유형 2 (3영업일 전 돌파 + 추세 유지):
  3영업일 전 동일 돌파 + 그 후 선 위 유지 + 돌파분 반납 없음 + 기준일 선 위.

점수화 (0~100, 신선도 제거판 — 통합 런너가 돌파일을 고정해 신선도가 상수라 무의미):
  +35 돌파 강도(등락)  : +17%↑ 35 / +13~17% 28 / +10~13% 20
  +35 거래량 급증      : 7배↑ 35 / 5~7배 27 / 4~5배 20 / 3~4배 12
  +20 돌파선           : 112+224 동시 20 / 224 단독 15 / 112 단독 10
  +10 추종·양봉        : 유형2 추종유지 10 / 유형1 양봉 6
노출 컷: score ≥ 80. 시총 ≥ 1,000억. 우선주·스팩·신규상장 제외. DART 악재컷.

생애주기 (breakout_pool.json · 매 08:15 자동 판정, 우선순위 졸업→이탈→만료):
  🎓 졸업    : 포착일(first_seen) 종가 대비 +20% 이상 → 졸업 카드 (성공)
  ❌ 이탈    : 돌파선(112/224) 종가 이탈 → 제거
  ⏳ 지속관찰: 졸업·이탈 아닌 채 5영업일 경과 → 탭에서 내려가되 추적 유지
  💰 진입    : 자동 이동 없음 — 사용자 요청 시에만 스윙 편입

데이터: data/krx_ohlcv_cache/{YYYYMMDD}_{MARKET}.json (오프라인 캐시 직접 로드).
산출:  data/breakout_flow_{YYYY-MM-DD}.json + data/breakout_pool.json

사용:
  python collect_breakout_flow.py                 # 최신 캐시일 기준
  python collect_breakout_flow.py --date 20260610 # 특정 기준일
  python collect_breakout_flow.py --dry-run       # 저장 X (미리보기)
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import sys
from pathlib import Path

import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "krx_ohlcv_cache"
POOL_PATH = DATA_DIR / "breakout_pool.json"
# 연속 캐시 시작일(2024년치 없음). 이전을 섞으면 MA224 왜곡되므로 고정 (장기이평돌파.md §3).
CONTIGUOUS_START = "20250115"
KST = dt.timezone(dt.timedelta(hours=9))

THRESHOLDS = {
    "min_ret": 0.10,                 # 최소 당일 등락
    "min_vmult": 3.0,                # 최소 거래량 배수(20일평균 대비)
    "max_gap": 0.30,                 # 돌파일 종가-기준선 최대 이격(동전주 제외)
    "hold": 3,                       # 유형2 추종 영업일
    "min_market_cap_billion": 1000,  # 시총 하한 (눌림과 동일)
    "min_history_days": 230,         # 224 이평 + 여유
    "display_score_cut": 80,         # 노출 컷
    "graduate_gain_pct": 20.0,       # 졸업: 포착일 대비 +20%
    "watch_after_days": 5,           # 5영업일 만료 → 지속관찰
}

# DART 악재컷 (선택 — 키 없으면 자동 스킵). 눌림 스캐너 구현 재사용.
try:
    from collect_theme_flow import apply_disclosure_filter as _dart_filter
except Exception:
    _dart_filter = None


def log(msg: str) -> None:
    ts = dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


# =============================================================================
# 캐시 로드 (오프라인) — screen_long_ma_breakout.py 와 동일 소스
# =============================================================================

def load_frames(end_date: str):
    """end_date(YYYYMMDD) 이하 연속 캐시 → close/volume/open 피벗 + 메타."""
    rc, rv, ro, names, mk, mcap = [], [], [], {}, {}, {}
    for f in sorted(glob.glob(str(CACHE_DIR / "*.json"))):
        d = os.path.basename(f).split("_")[0]
        if d < CONTIGUOUS_START or d > end_date:
            continue
        market = "KOSPI" if "KOSPI" in os.path.basename(f) else "KOSDAQ"
        for r in json.load(open(f, encoding="utf-8")).get("rows", []):
            c, cl, op, vol, nm = (r.get("code"), r.get("close"), r.get("open"),
                                  r.get("volume"), r.get("name"))
            if c and cl is not None:
                rc.append((d, c, cl)); rv.append((d, c, vol or 0)); ro.append((d, c, op))
                names[c] = nm; mk[c] = market
                if r.get("market_cap"):
                    mcap[c] = r["market_cap"]
    close = pd.DataFrame(rc, columns=["date", "code", "v"]).pivot_table(
        index="date", columns="code", values="v", aggfunc="last").sort_index()
    volm = pd.DataFrame(rv, columns=["date", "code", "v"]).pivot_table(
        index="date", columns="code", values="v", aggfunc="last").sort_index()
    opn = pd.DataFrame(ro, columns=["date", "code", "v"]).pivot_table(
        index="date", columns="code", values="v", aggfunc="last").sort_index()
    return close, volm, opn, names, mk, mcap


# =============================================================================
# 점수화
# =============================================================================

def score_breakout(ret, vmult, line, follow_through, is_yang) -> tuple[int, int]:
    """돌파 점수(0~100) + 별점(2~5). follow_through=유형2 추종 유지 여부."""
    s = 0
    # 돌파 강도 (등락)
    if ret >= 0.17:   s += 35
    elif ret >= 0.13: s += 28
    else:             s += 20            # min_ret 0.10 통과분
    # 거래량 급증
    if vmult >= 7:    s += 35
    elif vmult >= 5:  s += 27
    elif vmult >= 4:  s += 20
    else:             s += 12            # 3~4배
    # 돌파선
    if line == "112+224": s += 20
    elif line == "224":   s += 15
    else:                 s += 10        # 112
    # 추종·양봉
    if follow_through: s += 10
    elif is_yang:      s += 6
    s = min(100, max(0, s))

    if line == "112+224" and ret >= 0.17 and vmult >= 5:
        stars = 5
    elif ret >= 0.13 and vmult >= 4:
        stars = 4
    elif line != "112":
        stars = 4 if follow_through else 3
    else:
        stars = 3
    return s, stars


# =============================================================================
# 돌파 평가 (유형1 당일 / 유형2 D-hold)
# =============================================================================

def _excluded(nm: str) -> bool:
    return (not nm) or ("스팩" in nm) or nm.endswith("우") or nm.endswith("우B")


def scan_breakouts(close, volm, opn, names, mk, mcap, anchor: str) -> list[dict]:
    """anchor일 기준 유형1·유형2 돌파 통과 종목(점수·시총·DART 제외 전)."""
    dates = list(close.index)
    ai = dates.index(anchor)
    hold = THRESHOLDS["hold"]
    m112 = close.rolling(112, min_periods=112).mean()
    m224 = close.rolling(224, min_periods=224).mean()
    vavg = volm.rolling(20, min_periods=20).mean()
    RET, VMULT, MAXGAP = THRESHOLDS["min_ret"], THRESHOLDS["min_vmult"], THRESHOLDS["max_gap"]

    out = []
    for c in close.columns:
        nm = names.get(c, "")
        if _excluded(nm):
            continue
        cl, a, b, vv, va, op = close[c], m112[c], m224[c], volm[c], vavg[c], opn[c]

        def eval_day(i):
            pv, cv = cl.iloc[i - 1], cl.iloc[i]
            A, B, pA, pB = a.iloc[i], b.iloc[i], a.iloc[i - 1], b.iloc[i - 1]
            if pd.isna(A) or pd.isna(B) or pd.isna(pv) or pv == 0:
                return None
            x112 = pv <= pA and cv > A
            x224 = pv <= pB and cv > B
            if not (x112 or x224):
                return None
            ret = cv / pv - 1
            vr = (vv.iloc[i] / va.iloc[i]) if (not pd.isna(va.iloc[i]) and va.iloc[i] > 0) else 0
            if not (ret >= RET and vr >= VMULT and (cv / max(A, B) - 1) <= MAXGAP):
                return None
            line = "112+224" if (x112 and x224) else ("112" if x112 else "224")
            ov = op.iloc[i]
            is_yang = bool((not pd.isna(ov)) and cv > ov)
            return {"i": i, "ret": ret, "vr": vr, "line": line, "x112": x112,
                    "x224": x224, "close": int(cv), "is_yang": is_yang}

        # --- 당일 돌파만 (유형2 D-3 유지조건 폐기 2026-06-11: 며칠 전 돌파 추적은
        #     지속관찰 카드가 연속적으로 대체. 사용자 지시) ---
        sig = eval_day(ai)
        if not sig:
            continue
        kind, follow = "type1", False

        score, stars = score_breakout(sig["ret"], sig["vr"], sig["line"], follow, sig["is_yang"])
        mcap_won = mcap.get(c, 0) or 0
        out.append({
            "code": c, "name": nm, "market": mk.get(c, ""),
            "kind": kind, "line": sig["line"],
            "breakout_date": dates[sig["i"]],
            "breakout_close": sig["close"],
            "ret_pct": round(sig["ret"] * 100, 1),
            "vmult": round(sig["vr"], 1),
            "is_yang": sig["is_yang"],
            "follow_through": follow,
            "score": score, "stars": stars,
            "market_cap_billion": round(mcap_won / 1e8) if mcap_won else 0,
            "current_close": int(cl.iloc[ai]),
        })
    return out


# =============================================================================
# 영속 원장 (breakout_pool.json) — 생애주기 자동 판정
# =============================================================================

def load_pool() -> dict:
    if POOL_PATH.exists():
        try:
            p = json.loads(POOL_PATH.read_text(encoding="utf-8"))
            if isinstance(p, dict) and isinstance(p.get("stocks"), dict):
                return p
        except Exception as e:
            log(f"[WARN] breakout_pool 로드 실패: {e} → 빈 풀")
    return {"version": "breakout_v1", "updated": None, "stocks": {}}


def save_pool(pool: dict) -> None:
    POOL_PATH.write_text(json.dumps(pool, ensure_ascii=False, indent=2), encoding="utf-8")


def _bdays_between(d0: str, d1: str, all_dates: list[str]) -> int:
    """캐시 영업일 기준 d0~d1 사이 경과 영업일수 (d0 포함 안 함)."""
    try:
        i0, i1 = all_dates.index(d0), all_dates.index(d1)
        return max(0, i1 - i0)
    except ValueError:
        return 0


def update_pool(pool: dict, today_hits: list[dict], close, anchor: str,
                lineup_codes: set[str]) -> dict:
    """① 기존 풀 전 종목 생애주기 재판정 → ② 신규 통과 편입."""
    stocks = pool.setdefault("stocks", {})
    all_dates = list(close.index)
    m112 = close.rolling(112, min_periods=112).mean()
    m224 = close.rolling(224, min_periods=224).mean()
    today_by_code = {h["code"]: h for h in today_hits}
    GAIN = THRESHOLDS["graduate_gain_pct"] / 100.0
    WATCH = THRESHOLDS["watch_after_days"]

    def classify(entry: dict) -> None:
        """active/watching 엔트리 → 졸업/이탈/지속관찰/유지 자동 판정."""
        code = entry["code"]
        if code not in close.columns:
            return  # 데이터 결손 — 상태 유지
        cur = close[code].loc[anchor]
        if pd.isna(cur):
            return
        cur = float(cur)
        entry["current_close"] = int(cur)
        entry["promoted"] = "swing" if code in lineup_codes else None
        ref = entry.get("ref_close") or entry.get("breakout_close")
        # 고수위(peak) 갱신 — 한번 달성한 졸업은 영구 유지 (2026-06-11 사용자 확정)
        r = (cur / ref - 1) if ref else 0.0
        peak = max(entry.get("peak_return", 0.0), r)
        entry["peak_return"] = round(peak, 4)
        # ① 졸업 2단 (고수위 기준): +20% → 졸업②, +10% → 졸업① (영구, ①은 ②로 승급 가능)
        if peak >= 0.20:
            entry["status"] = "졸업20"; entry["graduated_date"] = entry.get("graduated_date") or anchor
            entry["gain_pct"] = round(peak * 100, 1); return
        if peak >= 0.10:
            entry["status"] = "졸업10"; entry["graduated_date"] = entry.get("graduated_date") or anchor
            entry["gain_pct"] = round(peak * 100, 1); return
        # ② 이탈: 돌파선 종가 이탈 (졸업 전에만)
        line = entry.get("line", "112")
        A = m112[code].loc[anchor] if code in m112.columns else None
        B = m224[code].loc[anchor] if code in m224.columns else None
        broke = False
        if line in ("112", "112+224") and A is not None and not pd.isna(A) and cur < float(A):
            broke = True
        if line in ("224", "112+224") and B is not None and not pd.isna(B) and cur < float(B):
            broke = True
        if broke:
            entry["status"] = "이탈"
            entry["removed_date"] = anchor
            return
        # ③ 노출 = 돌파(포착) 당일만. 당일 이후는 지속관찰로 이동해 추세 이탈 감시
        #    (눌림과 동일한 '조건 기반' — 시간 5영업일 규칙 폐기, 2026-06-11 사용자 지시)
        if entry.get("first_seen") == anchor:
            entry["status"] = "active"          # 🟢 노출 (당일)
        else:
            entry["status"] = "지속관찰"          # ⏳ 당일 이후 → 추세 이탈 감시

    # --- Step A: 기존 풀 재판정 (졸업②·이탈은 종결 · 졸업①은 ②승급 위해 재평가) ---
    for code, entry in stocks.items():
        if entry.get("status") in ("졸업20", "이탈"):
            entry["promoted"] = "swing" if code in lineup_codes else entry.get("promoted")
            continue
        classify(entry)

    # --- Step B: 오늘 신규 통과(80점+) 편입 ---
    for h in today_hits:
        code = h["code"]
        if code in stocks:
            # 이미 추적 중이면 점수·시세만 최신화 (상태는 classify가 관리)
            ex = stocks[code]
            ex["score"], ex["stars"] = h["score"], h["stars"]
            continue
        stocks[code] = {
            "code": code, "name": h["name"], "market": h["market"],
            "kind": h["kind"], "line": h["line"],
            "status": "active",
            "first_seen": anchor,
            "breakout_date": h["breakout_date"],
            "breakout_close": h["breakout_close"],
            "ref_close": h["breakout_close"],   # +20% 졸업 기준가 = 포착일(돌파일) 종가
            "ret_pct": h["ret_pct"], "vmult": h["vmult"], "line_": h["line"],
            "score": h["score"], "stars": h["stars"],
            "market_cap_billion": h["market_cap_billion"],
            "current_close": h["current_close"],
            "peak_return": 0.0,
            "promoted": "swing" if code in lineup_codes else None,
        }
    pool["updated"] = anchor
    return pool


def load_lineup_codes() -> set[str]:
    p = ROOT / "stocks.json"
    if not p.exists():
        return set()
    try:
        return {s["code"] for s in json.loads(p.read_text(encoding="utf-8")).get("stocks", [])}
    except Exception:
        return set()


# =============================================================================
# Main
# =============================================================================

def run(anchor: str | None, dry_run: bool, run_date: dt.date | None = None) -> dict:
    close, volm, opn, names, mk, mcap = load_frames(anchor or "99999999")
    dates = list(close.index)
    if not dates:
        log("캐시에 데이터가 없습니다."); return {}
    anchor = anchor or dates[-1]
    if anchor not in dates:
        log(f"{anchor} 데이터 없음(영업일/캐시 확인). 최신일={dates[-1]}"); return {}
    # 파일명·result.date = run_date(오늘 KST). 눌림 theme_flow 규약과 동일하게 맞춰
    # 08:15 슬랙이 analysis.date(오늘)로 breakout_flow_{오늘}.json 을 찾도록 한다.
    run_date = run_date or dt.datetime.now(KST).date()
    run_iso = run_date.isoformat()
    log(f"=== 폭발형 돌파 스캐너 시작 (데이터일 {anchor} · 발행일 {run_iso}) · "
        f"유니버스 {len(close.columns)}종 ===")

    hits = scan_breakouts(close, volm, opn, names, mk, mcap, anchor)
    log(f"돌파 통과(필터 전) {len(hits)}종")

    # 시총 ≥ 1,000억 (메타 없으면 0 → 컷). 메타 부재 캐시 대비: 0이면 통과시키지 않되 경고.
    cap = THRESHOLDS["min_market_cap_billion"]
    have_cap = sum(1 for h in hits if h["market_cap_billion"] > 0)
    if have_cap == 0 and hits:
        log("[WARN] 캐시에 시총 메타 없음 → 시총 필터 스킵")
        capped = hits
    else:
        capped = [h for h in hits if h["market_cap_billion"] >= cap]
    log(f"시총 ≥ {cap}억 필터 후 {len(capped)}종 (컷 {len(hits) - len(capped)})")

    # DART 악재컷 (키 없으면 스킵). collect_theme_flow.apply_disclosure_filter는
    # candidates[*].code/score 인터페이스만 사용하므로 그대로 재사용 가능.
    n_before_dart = len(capped)
    dart_cut_names: list[str] = []
    if _dart_filter is not None:
        try:
            kept = _dart_filter(capped, {})
            kept_codes = {h["code"] for h in kept}
            dart_cut_names = [f'{h["name"]}({h["code"]}) {h["score"]}점'
                              for h in capped if h["code"] not in kept_codes]
            capped = kept
        except Exception as e:
            log(f"[WARN] DART 필터 스킵: {e}")

    # 노출 컷 (score ≥ 80)
    cut = THRESHOLDS["display_score_cut"]
    passed = [h for h in capped if h["score"] >= cut]
    passed.sort(key=lambda h: -h["score"])
    log(f"노출 컷(≥{cut}점) 후 {len(passed)}종")

    # 퍼널·근접미달 기록 (2026-08-07) — 종전엔 0종일 때 어느 단계에서 걸렸는지가
    # 산출물에 안 남아 "스캐너가 죽은 것"과 "필터에 걸린 것"이 구분되지 않았다.
    near_miss = sorted([h for h in capped if h["score"] < cut],
                       key=lambda h: -h["score"])[:5]
    funnel = {
        "raw": len(hits),
        "cap_cut": len(hits) - n_before_dart,
        "after_cap": n_before_dart,
        "dart_cut": n_before_dart - len(capped),
        "dart_cut_detail": dart_cut_names,
        "after_dart": len(capped),
        "score_cut": len(capped) - len(passed),
        "score_cut_threshold": cut,
        "passed": len(passed),
        "top_near_miss_score": near_miss[0]["score"] if near_miss else None,
    }

    # 원장 갱신 (생애주기)
    pool = load_pool()
    lineup_codes = load_lineup_codes()
    pool = update_pool(pool, passed, close, anchor, lineup_codes)
    if not dry_run:
        save_pool(pool)

    stocks = list(pool["stocks"].values())
    active = sorted([s for s in stocks if s.get("status") == "active"],
                    key=lambda s: -(s.get("score") or 0))
    watching = sorted([s for s in stocks if s.get("status") == "지속관찰"],
                      key=lambda s: -(s.get("score") or 0))
    grad10 = sorted([s for s in stocks if s.get("status") == "졸업10"],
                    key=lambda s: s.get("graduated_date") or "", reverse=True)
    grad20 = sorted([s for s in stocks if s.get("status") == "졸업20"],
                    key=lambda s: s.get("graduated_date") or "", reverse=True)
    removed = sorted([s for s in stocks if s.get("status") == "이탈"],
                     key=lambda s: s.get("removed_date") or "", reverse=True)

    result = {
        "date": run_iso, "anchor": anchor,
        "data_source": "krx_ohlcv_cache (offline)",
        "scanner_version": "breakout_v2 (2026-06-11 당일 돌파 + 졸업 2단 생애주기)",
        "thresholds": THRESHOLDS,
        "universe_size": len(close.columns),
        "funnel": funnel,              # 🔎 단계별 컷 내역 (0종 사유 규명용)
        "near_miss": near_miss,        # 🔸 시총·DART는 통과했는데 점수컷만 미달
        "scan_passed_today": passed,   # 오늘 80점+ 통과 (참고)
        "active": active,              # 🟢 노출 (당일)
        "watching": watching,          # ⏳ 지속관찰
        "graduated10": grad10,         # 🎓 졸업① (+10%)
        "graduated20": grad20,         # 🎓 졸업② (+20%)
        "removed": removed,            # ❌ 이탈
        "stats": {
            "scanned": len(close.columns),
            "passed_today": len(passed),
            "active": len(active), "watching": len(watching),
            "graduated10": len(grad10), "graduated20": len(grad20), "removed": len(removed),
            "pool_total": len(stocks),
        },
    }

    if not dry_run:
        out = DATA_DIR / f"breakout_flow_{run_iso}.json"
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"breakout_flow 저장: {out}")
    else:
        log("--dry-run: 저장 생략. active 미리보기:")
        print(json.dumps(result["active"][:5], ensure_ascii=False, indent=2)[:2000])

    log(f"=== 완료 (노출 {len(active)} / 졸업①{len(grad10)} 졸업②{len(grad20)} / "
        f"지속관찰 {len(watching)} / 이탈 {len(removed)}) ===")
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="데이터 기준일 YYYYMMDD (기본: 최신 캐시일). 지정 시 발행일도 동일(백테스트)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    rd = dt.date(int(a.date[:4]), int(a.date[4:6]), int(a.date[6:8])) if a.date else None
    run(a.date, a.dry_run, run_date=rd)


if __name__ == "__main__":
    main()
