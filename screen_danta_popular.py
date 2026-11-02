# -*- coding: utf-8 -*-
"""
screen_danta_popular.py — ⚡ 단타인기 · 거래대금×인기 (2026-10-28 사용자 지시, 평일 09:10 run_danta.bat)

  거래대금 : 네이버 증권 m.stock.naver.com /api/stocks/priceTop/{KOSPI,KOSDAQ} — 일반 주식만(ETF·ETN 제외) 합쳐 거래대금 순 20위
  인기     : 네이버 증권 /api/stocks/searchTop/all (인기 검색 종목) — 일반 주식만 순위 매김
  조건     : 거래대금 20위 안 + 인기 순위 < 거래대금 순위 (거래대금보다 관심이 앞선 종목) · 정렬 = 거래대금 순위
  매매     : 3거래일 보유 단타 (사용자) — 진입·손절·목표 산식 없음
⚠️ 백테스트 불가 — 네이버 인기 순위는 과거 이력이 없다. 매일 data/danta_pop_{날짜}.json 을 쌓아 나중에 성과를 잰다.

출력: data/danta_pop_{date}.json → build_danta_tab.js · Slack 1장
사용: python screen_danta_popular.py [--dry-run] [--no-slack]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

API = "https://m.stock.naver.com/api/stocks"
TOPN = 20
KST = dt.timezone(dt.timedelta(hours=9))
log = lambda m: print(f"[danta_pop] {m}", flush=True)


def get(path: str) -> list:
    req = urllib.request.Request(f"{API}/{path}", headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return [s for s in json.load(r)["stocks"] if s["stockEndType"] == "stock"]


def screen() -> tuple[list, list]:
    value = sorted(get("priceTop/KOSPI?page=1&pageSize=60") + get("priceTop/KOSDAQ?page=1&pageSize=60"),
                   key=lambda s: -float(s["accumulatedTradingValueRaw"]))[:TOPN]
    pop = {s["itemCode"]: i + 1 for i, s in enumerate(get("searchTop/all?page=1&pageSize=100"))}
    rows = [dict(code=s["itemCode"], name=s["stockName"], market="코스피" if s["sosok"] == "0" else "코스닥",
                 value_rank=i + 1, pop_rank=pop.get(s["itemCode"]), price=int(float(s["closePriceRaw"])),
                 chg_pct=float(s["fluctuationsRatio"]), value_eok=round(float(s["accumulatedTradingValueRaw"]) / 1e8))
            for i, s in enumerate(value)]
    return rows, [r for r in rows if r["pop_rank"] and r["pop_rank"] < r["value_rank"]]


def slack(text: str, dry: bool):
    if dry:
        log("Slack 생략(--no-slack/--dry-run)\n" + text)
        return
    env = {}
    for line in (ROOT.parent / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    url = env.get("SLACK_WEBHOOK_URL")
    if not url:
        log("SLACK_WEBHOOK_URL 없음 — 생략")
        return
    body = json.dumps({"username": env.get("SLACK_USERNAME") or "단테 자동분석", "icon_emoji": env.get("SLACK_ICON_EMOJI") or ":chart_with_upwards_trend:", "text": text}).encode()
    with urllib.request.urlopen(urllib.request.Request(url, body, {"Content-Type": "application/json"}), timeout=20) as r:
        log(f"Slack {r.status}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-slack", action="store_true")
    a = ap.parse_args()
    now = dt.datetime.now(KST)
    top, picks = screen()
    assert len(top) == TOPN, f"거래대금 순위 {len(top)}종뿐 — 네이버 응답 확인"
    out = dict(date=now.strftime("%Y-%m-%d"), time=now.strftime("%H:%M"), top=top, picks=picks)
    for r in picks:
        log(f"{r['name']}({r['code']}) 거래대금 {r['value_rank']}위 · 인기 {r['pop_rank']}위 · {r['chg_pct']:+.2f}%")
    log(f"거래대금 {TOPN}위 중 {len(picks)}종")
    if not a.dry_run:
        (ROOT / "data" / f"danta_pop_{out['date']}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = [f"• [{r['market']}] *{r['name']}* — 거래대금 {r['value_rank']}위 · 인기 {r['pop_rank']}위 · {r['chg_pct']:+.2f}% · {r['value_eok']:,}억" for r in picks]
    slack(f"⚡ *단타인기 {out['time']}* — 거래대금 {TOPN}위 안, 인기 순위가 더 높은 종목 {len(picks)}종 (3일 보유)\n" + ("\n".join(lines) or "없음"),
          a.no_slack or a.dry_run)


if __name__ == "__main__":
    main()
