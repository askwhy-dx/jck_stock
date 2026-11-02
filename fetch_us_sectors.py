"""
fetch_us_sectors.py — 선반영(디커플링) 에이전트: 미국 섹터/대표주 밤사이 수급 수집 (테스트 v0.1)

데이터 소스: Yahoo Finance chart API (query1.finance.yahoo.com/v8/finance/chart)
  - 무료, API 키 불필요. stooq는 봇차단(JS검증)으로 무인 다운로드 불가하여 채택.
  - 07:30 KST 시점 미 정규장(05:00 마감) 종가 확정 데이터.

산출: data/us_overnight_{YYYY-MM-DD(KST)}.json
  심볼별: 최근 종가/전일/등락% · 5MA 위 여부(상승추세) · 20일 평균 대비 거래량 · 밤사이 급락/급등 플래그

사용:
  python fetch_us_sectors.py                 # 오늘(KST) 기준 수집
  python fetch_us_sectors.py --dry-run       # 저장 X, 콘솔 출력만
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
CONFIG = ROOT / "config" / "decouple_map.json"
DATA_DIR.mkdir(exist_ok=True)
KST = dt.timezone(dt.timedelta(hours=9))

YH_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=3mo&interval=1d"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
TIMEOUT = 20


def log(msg: str) -> None:
    ts = dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def fetch_symbol(sym: str, retries: int = 3) -> dict | None:
    """Yahoo chart API → 최근 일봉 지표. 실패 시 None."""
    url = YH_URL.format(sym=sym)
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            if r.status_code != 200:
                time.sleep(0.6 * attempt)
                continue
            res = r.json().get("chart", {}).get("result")
            if not res:
                return None
            res = res[0]
            quote = res["indicators"]["quote"][0]
            closes = [c for c in quote.get("close", []) if c is not None]
            vols = [v for v in quote.get("volume", []) if v is not None]
            if len(closes) < 6:
                return None
            last, prev = closes[-1], closes[-2]
            ma5 = sum(closes[-5:]) / 5
            ma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else None
            chg_pct = (last / prev - 1) * 100 if prev else 0.0
            vol_last = vols[-1] if vols else None
            vol_avg20 = (sum(vols[-20:]) / 20) if len(vols) >= 20 else None
            vol_ratio = (vol_last / vol_avg20) if (vol_last and vol_avg20) else None
            return {
                "symbol": sym,
                "currency": res.get("meta", {}).get("currency"),
                "last_close": round(last, 2),
                "prev_close": round(prev, 2),
                "change_pct": round(chg_pct, 2),
                "ma5": round(ma5, 2),
                "ma20": round(ma20, 2) if ma20 else None,
                "above_ma5": bool(last > ma5),
                "above_ma20": bool(ma20 and last > ma20),
                "vol_ratio": round(vol_ratio, 2) if vol_ratio else None,
                "candle": "양봉" if last >= prev else "음봉",
            }
        except requests.RequestException:
            time.sleep(0.6 * attempt)
        except (KeyError, ValueError, TypeError) as e:
            log(f"  [WARN] {sym} 파싱 실패: {e}")
            return None
    log(f"  [WARN] {sym} 수집 실패 (재시도 {retries}회 초과)")
    return None


def run(dry_run: bool = False) -> dict:
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    params = cfg["params"]
    drop_thr = params["us_overnight_drop_pct"]
    rise_thr = params["us_overnight_rise_pct"]

    # 매핑에 등장하는 모든 US 심볼 수집 (중복 제거)
    symbols: list[str] = []
    for t in cfg["themes"]:
        symbols += t["us"].get("etf", []) + t["us"].get("leaders", [])
    symbols = list(dict.fromkeys(symbols))
    log(f"=== US 섹터 수급 수집 시작 — 심볼 {len(symbols)}개 ===")

    quotes: dict[str, dict] = {}
    for sym in symbols:
        q = fetch_symbol(sym)
        if q:
            quotes[sym] = q
        time.sleep(0.15)  # rate-limit 여유
    log(f"수집 완료: {len(quotes)}/{len(symbols)}개")

    # 테마별 섹터 상태 집계 (ETF 기준 추세 + 밤사이 등락)
    theme_status = []
    for t in cfg["themes"]:
        etfs = [quotes[s] for s in t["us"].get("etf", []) if s in quotes]
        leaders = [quotes[s] for s in t["us"].get("leaders", []) if s in quotes]
        proxy = etfs[0] if etfs else (leaders[0] if leaders else None)
        if not proxy:
            theme_status.append({"key": t["key"], "label": t["label"],
                                 "us_available": False})
            continue
        # 섹터 평균 등락 (ETF + 대표주)
        members = etfs + leaders
        avg_chg = round(sum(m["change_pct"] for m in members) / len(members), 2)
        uptrend = proxy["above_ma5"]          # 5MA 위 = 상승추세 유효
        overnight_drop = avg_chg <= drop_thr  # 밤사이 장대음봉 급락
        overnight_rise = avg_chg >= rise_thr
        theme_status.append({
            "key": t["key"], "label": t["label"], "us_available": True,
            "proxy_symbol": proxy["symbol"],
            "us_uptrend": bool(uptrend),
            "us_avg_change_pct": avg_chg,
            "us_overnight_drop": bool(overnight_drop),
            "us_overnight_rise": bool(overnight_rise),
            "members": members,
        })
        flag = "🔻급락" if overnight_drop else ("🔺급등" if overnight_rise else "→보합")
        log(f"  [{t['label']}] proxy={proxy['symbol']} 추세={'▲' if uptrend else '▽'} "
            f"평균등락={avg_chg:+.2f}% {flag}")

    result = {
        "run_date_kst": dt.datetime.now(KST).date().isoformat(),
        "source": "Yahoo Finance chart API",
        "params": params,
        "quotes": quotes,
        "themes": theme_status,
    }

    if not dry_run:
        out = DATA_DIR / f"us_overnight_{result['run_date_kst']}.json"
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"저장: {out}")
    else:
        log("--dry-run: 저장 생략")
    log("=== 완료 ===")
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    try:
        run(dry_run=args.dry_run)
    except Exception as e:
        log(f"[FATAL] {e}")
        sys.exit(1)
