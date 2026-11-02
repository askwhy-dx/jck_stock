"""
collect_theme_rotation.py — 08:15 회차 🧭 테마 탭 (테마 순환 + 눌림 대기 테마 + 길목 종목)

산출:  data/theme_rotation_{YYYY-MM-DD}.json  (파일명 = 실행일 KST, breakout_flow 규약과 동일)
입력:  data/krx_ohlcv_cache/{YYYYMMDD}_{KOSPI|KOSDAQ}.json  (collect_theme_flow.py 가 채움)
       data/naver_theme_map.json  (네이버 증권 테마→구성종목, 7일 지나면 재수집)

정의 (달력 주 단위, 중앙값 기준 — 급등 1종목에 휘둘리지 않게):
  - 최근 4주. 최근 주가 2거래일 이하이면 직전 주에 합침 (연휴·월요일 회차 대응)
  - 거래대금비 = 최근 주 일평균 ÷ 직전 주 일평균
  - 테마 범위: config/semi_process_map.csv 의 group 3개(반도체 소부장·전공정·후공정)가 곧 테마. 제외 종목은 안 본다
  - 이상치: 창 기간 중 하루라도 |등락| > 35% (병합·재개 의심) → 테마 집계에서 제외
  - 눌림 대기 테마: 20일선 위 비율 ≥ 75% · 거래대금비(W0÷W1) ≤ 0.9 · 고점대비 중앙 ≤ -2%
                    → 20거래일 누적 중앙값 순 상위 THEME_TOP
  - 길목 종목: 종가 > 20일선 · 20일선 이격 ≤ 25% · 거래대금비 ≤ 0.9 · 시총 ≥ 1,000억
               · 종가 ≤ 100만원(100만÷평단 사이징 1주 이상) → W1 거래대금 순 테마당 PICKS (테마 간 중복 제외)
  - 1차 길목 = 10일선, 2차 길목 = 20일선 (종가 대비 %)
  - 추적 풀(data/theme_pick_pool.json): 편입 후 매일 판정 — ⏳대기 → 🟢길목 도달(저가 ≤ 10일선)
    → 🎓졸업(종가 ≥ 편입가 +10%) / ❌이탈(종가 < 20일선) / ⌛만료(20거래일). 끝난 종목은 history 누적

사용:
  python collect_theme_rotation.py            # 오늘(KST) 실행일로 저장
  python collect_theme_rotation.py --dry-run  # 저장 없이 요약만 출력
  python collect_theme_rotation.py --refresh-map
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import statistics as st
import sys
import time
import urllib.request
from pathlib import Path

# bat 콘솔은 cp949 — 로그 이모지(🟢)에서 죽어 테마 파일이 저장 안 되던 문제 (2026-10-01)
sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "krx_ohlcv_cache"
MAP_PATH = DATA_DIR / "naver_theme_map.json"
POOL_PATH = DATA_DIR / "theme_pick_pool.json"   # 길목 종목 추적 풀 (영속)
KST = dt.timezone(dt.timedelta(hours=9))

NWIN, SHORT_WEEK = 4, 2
OUTLIER_PCT = 35
MIN_THEME_N = 5
THEME_ABOVE20 = 0.75
THEME_TV_RATIO = 0.9
THEME_OFF_HIGH = -2.0
THEME_TOP = 5
PICK_MAX_GAP20 = 25.0
PICK_TV_RATIO = 0.9
PICK_MIN_CAP = 1000e8
PICK_MAX_PRICE = 1_000_000
PICKS = 3
MAP_MAX_AGE_DAYS = 7
GRAD_PCT = 10        # 졸업: 편입일 종가 대비 +10% (돌파 탭 졸업①과 동일)
REMOVE_BUF_PCT = 0   # 이탈: 종가 < 20일선 × (1 - 이 값%). 9월 재생 0%→승률18% · 3%→31% · 5%→49% (실패 1건 손실은 커짐)
EXPIRE_DAYS = 20     # 만료: 편입 후 20거래일 동안 졸업·이탈 없음
RECENT_DAYS = 14     # 졸업·이탈·만료 종목 화면 유지 기간(달력일)
# 테마 범위 = 반도체 소부장·전공정·후공정만 (26-09-30 사용자 지시). 네이버엔 전/후공정 테마가 없어 종목별 분류표로 묶는다.
# 분류표 = config/semi_process_map.csv (group: 소부장·전공정·후공정·제외). 아래 4개 네이버 테마에 새로 들어온 종목은 미분류 경고.
THEME_ALLOW = ("반도체 장비", "반도체 재료/부품", "HBM(고대역폭메모리)", "반도체 기판(FC-BGA/PCB/MLB 등)")
SEMI_MAP_PATH = ROOT / "config" / "semi_process_map.csv"
SEMI_GROUPS = ("소부장", "전공정", "후공정")


def log(msg: str) -> None:
    print(f"[{dt.datetime.now(KST):%H:%M:%S}] {msg}", flush=True)


def fetch_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return json.loads(urllib.request.urlopen(req, timeout=20).read())


def load_theme_map(refresh: bool) -> dict:
    fresh = MAP_PATH.exists() and (time.time() - MAP_PATH.stat().st_mtime) < MAP_MAX_AGE_DAYS * 86400
    if fresh and not refresh:
        return json.loads(MAP_PATH.read_text(encoding="utf-8"))["themes"]
    try:
        groups, p = [], 1
        while True:
            d = fetch_json(f"https://m.stock.naver.com/api/stocks/theme?page={p}&pageSize=50")
            groups += d["groups"]; p += 1
            if not d["groups"] or len(groups) >= d["totalCount"]:
                break
        themes = {}
        for g in groups:
            codes, p = [], 1
            while len(codes) < g["totalCount"]:
                d = fetch_json(f"https://m.stock.naver.com/api/stocks/theme/{g['no']}?page={p}&pageSize=100")
                if not d["stocks"]:
                    break
                codes += [s["itemCode"] for s in d["stocks"]]; p += 1
            themes[g["name"]] = codes
        # 장 시작 전엔 구성종목이 전부 빈 목록으로 와서 빈 사전이 저장된 이력 (2026-10-14 08:33, 264테마 전부 0종)
        if sum(map(len, themes.values())) == 0:
            raise RuntimeError("테마 구성종목 전부 0종")
        MAP_PATH.write_text(json.dumps({"fetched_at": dt.datetime.now(KST).isoformat(), "themes": themes},
                                       ensure_ascii=False), encoding="utf-8")
        log(f"네이버 테마 매핑 재수집: {len(themes)}개")
        return themes
    except Exception as e:  # 네트워크 실패 시 옛 매핑이라도 사용
        if MAP_PATH.exists():
            log(f"[WARN] 테마 매핑 재수집 실패({e}) — 기존 매핑 사용")
            return json.loads(MAP_PATH.read_text(encoding="utf-8"))["themes"]
        raise


def semi_theme_map(tmap: dict) -> dict:
    """네이버 테마 대신 분류표의 group(소부장·전공정·후공정)을 테마로 쓴다."""
    with open(SEMI_MAP_PATH, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    naver = {c for k in THEME_ALLOW for c in tmap.get(k, [])}
    new = naver - {r["code"] for r in rows}
    if new:
        log(f"[WARN] 분류표 미분류 {len(new)}종 — {SEMI_MAP_PATH.name} 에 추가 필요: {sorted(new)}")
    return {f"반도체 {g}": [r["code"] for r in rows if r["group"] == g] for g in SEMI_GROUPS}


def load_prices(max_days: int = 230):
    days = sorted({f.name[:8] for f in CACHE_DIR.glob("*_KOSPI.json")}, reverse=True)
    px, tv, lo, meta, used = {}, {}, {}, {}, []
    for d in days:
        rows = []
        for m in ("KOSPI", "KOSDAQ"):
            f = CACHE_DIR / f"{d}_{m}.json"
            if f.exists():
                rows += json.loads(f.read_text(encoding="utf-8")).get("rows", [])
        if len(rows) < 1000:  # 휴장일 빈 파일
            continue
        px[d], tv[d], lo[d] = {}, {}, {}
        for r in rows:
            if r.get("close"):
                c = r["code"]
                px[d][c] = r["close"]; tv[d][c] = r.get("trading_value") or 0; lo[d][c] = r.get("low") or r["close"]
                meta.setdefault(c, (r["name"], r.get("market_cap") or 0, r.get("market")))
        used.append(d)
        if len(used) >= max_days:
            break
    return sorted(used), px, tv, lo, meta


def build(days, px, tv, meta, tmap) -> dict:
    # 달력 주(월~금) 단위. 최근 주가 SHORT_WEEK 거래일 이하이면 직전 주에 합친다 (연휴·월요일 회차 대응)
    groups = []
    for d in days:
        wk = dt.date(int(d[:4]), int(d[4:6]), int(d[6:])).isocalendar()[:2]
        if groups and groups[-1][0] == wk:
            groups[-1][1].append(d)
        else:
            groups.append((wk, [d]))
    groups = [g for _, g in groups]
    if len(groups) > 1 and len(groups[-1]) <= SHORT_WEEK:
        tail = groups.pop()
        groups[-1] = groups[-1] + tail
    if len(groups) < NWIN + 1:
        raise RuntimeError(f"주 {len(groups)}개 < 필요 {NWIN + 1}개")
    G = groups[-NWIN:]
    D = [groups[-NWIN - 1][-1]] + [d for g in G for d in g]   # D[0] = 기준 종가
    wins = [(D[0] if i == 0 else G[i - 1][-1], g[-1]) for i, g in enumerate(G)]  # 오래된→최근
    last, prev = D[-1], D[-2]

    def ret(c, a, b):
        return (px[b][c] / px[a][c] - 1) * 100

    full = [c for c in px[last] if all(c in px[d] for d in D)]
    bad = {c for c in full if any(abs(ret(c, D[i - 1], D[i])) > OUTLIER_PCT for i in range(1, len(D)))}
    ok = set(full) - bad

    def ma(c, n):
        s = [px[d][c] for d in days[-n:] if c in px[d]]
        return sum(s) / len(s) if len(s) == n else None

    def tvavg(c, ds):
        return sum(tv[d].get(c, 0) for d in ds) / len(ds)

    w0, w1 = G[-1], G[-2]
    stock = {}
    for c in ok:
        m20, m10 = ma(c, 20), ma(c, 10)
        if not m20:
            continue
        cl = px[last][c]
        t1 = tvavg(c, w1)
        stock[c] = dict(
            close=cl, ma10=round(m10), ma20=round(m20), ma224=ma(c, 224),
            gap20=(cl / m20 - 1) * 100, gap10=(cl / m10 - 1) * 100,
            off_high=(cl / max(px[d][c] for d in D[-20:]) - 1) * 100,
            tv_ratio=tvavg(c, w0) / t1 if t1 else 0, tv_w1=t1,
            day=ret(c, prev, last), cum=ret(c, D[0], last),
        )

    themes = []
    for name, codes in tmap.items():
        cs = [c for c in codes if c in stock]
        if len(cs) < MIN_THEME_N:
            continue
        wk = [st.median(ret(c, a, b) for c in cs) for a, b in wins]
        wk_up = [sum(ret(c, a, b) > 0 for c in cs) / len(cs) for a, b in wins]
        themes.append(dict(
            theme=name, n=len(cs), weeks=[round(x, 2) for x in wk], weeks_up=[round(x, 2) for x in wk_up],
            cum=round(st.median(stock[c]["cum"] for c in cs), 2),
            day=round(st.median(stock[c]["day"] for c in cs), 2),
            above20=round(sum(stock[c]["gap20"] > 0 for c in cs) / len(cs), 2),
            gap20=round(st.median(stock[c]["gap20"] for c in cs), 2),
            off_high=round(st.median(stock[c]["off_high"] for c in cs), 2),
            tv_ratio=round(st.median(stock[c]["tv_ratio"] for c in cs), 2),
            _codes=cs,
        ))

    waiting = [t for t in themes if t["above20"] >= THEME_ABOVE20 and t["tv_ratio"] <= THEME_TV_RATIO
               and t["off_high"] <= THEME_OFF_HIGH]
    waiting = sorted(waiting, key=lambda t: -t["cum"])[:THEME_TOP]

    picks, taken = [], set()
    for t in waiting:
        cands = [c for c in t["_codes"] if c not in taken]
        lead = max(t["_codes"], key=lambda c: stock[c]["tv_w1"])
        ok_c = [c for c in cands
                if 0 < stock[c]["gap20"] <= PICK_MAX_GAP20 and stock[c]["tv_ratio"] <= PICK_TV_RATIO
                and meta[c][1] >= PICK_MIN_CAP and stock[c]["close"] <= PICK_MAX_PRICE]
        for c in sorted(ok_c, key=lambda c: -stock[c]["tv_w1"])[:PICKS]:
            taken.add(c)
            s = stock[c]
            picks.append(dict(
                theme=t["theme"], code=c, name=meta[c][0], market=meta[c][2],
                market_cap_billion=round(meta[c][1] / 1e8), close=s["close"],
                ma10=s["ma10"], ma20=s["ma20"], to_ma10=round((s["ma10"] / s["close"] - 1) * 100, 1),
                to_ma20=round((s["ma20"] / s["close"] - 1) * 100, 1),
                tv_ratio=round(s["tv_ratio"], 2), tv_w1_billion=round(s["tv_w1"] / 1e8),
                gap224=None if not s["ma224"] else round((s["close"] / s["ma224"] - 1) * 100, 1),
                off_high=round(s["off_high"], 1), cum=round(s["cum"], 1), is_leader=(c == lead),
            ))

    for t in themes:
        t.pop("_codes")
    fmt = lambda d: f"{d[4:6]}/{d[6:]}"
    return dict(
        basis_date=last,
        windows=[f"{fmt(g[0])}~{fmt(g[-1])}" for g in G],
        market=dict(weeks=[round(st.median(ret(c, a, b) for c in ok), 2) for a, b in wins],
                    day=round(st.median(ret(c, prev, last) for c in ok), 2)),
        excluded_outliers=sorted(meta[c][0] for c in bad),
        rules=dict(theme=f"반도체 소부장·전공정·후공정(분류표) · 20일선 위 ≥{THEME_ABOVE20:.0%} · 거래대금비 ≤{THEME_TV_RATIO} · 고점대비 ≤{THEME_OFF_HIGH}% · 누적순 상위 {THEME_TOP}",
                   pick=f"20일선 위·이격 ≤{PICK_MAX_GAP20:.0f}% · 거래대금비 ≤{PICK_TV_RATIO} · 시총 ≥1,000억 · 종가 ≤100만 · 직전주 거래대금순 {PICKS}종"),
        theme_count=len(themes),
        groups=[{k: t[k] for k in ("theme", "n", "above20", "tv_ratio", "off_high", "cum")} for t in themes],
        waiting=waiting,
        picks=picks,
    )


def ma_at(px, days, c, n):
    s = [px[d][c] for d in days[-n:] if c in px[d]]
    return sum(s) / n if len(s) == n else None


def update_pool(pool: dict, picks: list, days, px, lo, run_iso: str) -> dict:
    """길목 종목 생애주기. 한 번 편입되면 오늘 조건에서 빠져도 끝날 때까지 추적한다.
    ⏳ waiting → 🟢 touched(저가 ≤ 10일선, 유지) → 🎓 graduated(종가 ≥ 편입가 +10%)
    / ❌ removed(종가 < 20일선) / ⌛ expired(편입 후 EXPIRE_DAYS 거래일 경과)."""
    last = days[-1]
    items = pool.setdefault("items", {})
    hist = pool.setdefault("history", [])
    if pool.get("basis_date") == last:          # 같은 기준일 재실행 = 판정 생략, 신규 편입만
        judge = False
    else:
        judge = True
    for c, it in list(items.items()):
        if not judge or c not in px[last]:
            continue
        cl, low = px[last][c], lo[last].get(c, px[last][c])
        m10, m20 = ma_at(px, days, c, 10), ma_at(px, days, c, 20)
        it.update(close=cl, ma10=round(m10) if m10 else None, ma20=round(m20) if m20 else None,
                  gain=round((cl / it["entry_close"] - 1) * 100, 1), basis_date=last)
        it["peak"] = max(it.get("peak", it["gain"]), it["gain"])
        held = sum(1 for d in days if d > it["entry_basis"])
        end = None
        if cl >= it["entry_close"] * (1 + GRAD_PCT / 100):
            end = "graduated"
        elif m20 and cl < m20 * (1 - REMOVE_BUF_PCT / 100):
            end = "removed"
        elif held >= EXPIRE_DAYS:
            end = "expired"
        elif m10 and low <= m10 and it["status"] == "waiting":
            it["status"], it["touched_date"] = "touched", last
        if end:
            it.update(status=end, end_date=last, held_days=held)
            hist.append(it)
            del items[c]
    for p in picks:                              # 신규 편입 (추적 중이면 건드리지 않음)
        c = p["code"]
        if c in items:
            items[c]["theme"] = p["theme"]
            continue
        items[c] = dict(code=c, name=p["name"], theme=p["theme"], market_cap_billion=p["market_cap_billion"],
                        entry_date=run_iso, entry_basis=last, entry_close=p["close"], close=p["close"],
                        ma10=p["ma10"], ma20=p["ma20"], gain=0.0, peak=0.0, basis_date=last,
                        status="touched" if lo[last].get(c, p["close"]) <= p["ma10"] else "waiting")
        if items[c]["status"] == "touched":
            items[c]["touched_date"] = last
    pool["basis_date"] = last
    return pool


def pool_view(pool: dict, picks: list, basis: str) -> dict:
    today = {p["code"]: p for p in picks}
    act = []
    for it in pool["items"].values():
        v = dict(it)
        t = today.get(it["code"])
        v["in_today"] = bool(t)
        for k in ("to_ma10", "to_ma20", "tv_ratio", "gap224", "off_high", "is_leader", "tv_w1_billion"):
            v[k] = t.get(k) if t else None
        if not t and it.get("ma10"):
            v["to_ma10"] = round((it["ma10"] / it["close"] - 1) * 100, 1)
            v["to_ma20"] = round((it["ma20"] / it["close"] - 1) * 100, 1) if it.get("ma20") else None
        act.append(v)
    b = dt.date(int(basis[:4]), int(basis[4:6]), int(basis[6:]))
    recent = [h for h in pool["history"]
              if (b - dt.date(int(h["end_date"][:4]), int(h["end_date"][4:6]), int(h["end_date"][6:]))).days <= RECENT_DAYS]
    n = {k: sum(h["status"] == k for h in pool["history"]) for k in ("graduated", "removed", "expired")}
    done = n["graduated"] + n["removed"]
    order = lambda v: (v["theme"], -(v.get("tv_w1_billion") or 0))
    return dict(
        touched=sorted([v for v in act if v["status"] == "touched"], key=order),
        waiting=sorted([v for v in act if v["status"] == "waiting"], key=order),
        graduated=[h for h in recent if h["status"] == "graduated"][::-1],
        ended=[h for h in recent if h["status"] != "graduated"][::-1],
        stats=dict(**n, win_rate=round(n["graduated"] / done * 100) if done else None),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--refresh-map", action="store_true")
    a = ap.parse_args()
    tmap = load_theme_map(a.refresh_map)
    tmap = semi_theme_map(tmap)
    days, px, tv, lo, meta = load_prices()
    res = build(days, px, tv, meta, tmap)
    run_iso = dt.datetime.now(KST).date().isoformat()
    pool = json.loads(POOL_PATH.read_text(encoding="utf-8")) if POOL_PATH.exists() else {}
    pool = update_pool(pool, res["picks"], days, px, lo, run_iso)
    res["pool"] = pool_view(pool, res["picks"], res["basis_date"])
    res["date"] = run_iso
    res["generated_at"] = dt.datetime.now(KST).isoformat(timespec="seconds")
    pv = res["pool"]
    log(f"기준일 {res['basis_date']} · 눌림대기 테마 {len(res['waiting'])}개 · 오늘 길목 {len(res['picks'])}종 · "
        f"추적 🟢{len(pv['touched'])} ⏳{len(pv['waiting'])} · 누적 🎓{pv['stats']['graduated']} ❌{pv['stats']['removed']} ⌛{pv['stats']['expired']}")
    if not a.dry_run:
        out = DATA_DIR / f"theme_rotation_{run_iso}.json"
        out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        POOL_PATH.write_text(json.dumps(pool, ensure_ascii=False, indent=1), encoding="utf-8")
        log(f"저장: {out} · 풀 {POOL_PATH.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
