# -*- coding: utf-8 -*-
"""
fetch_krx_history.py — KRX OpenAPI 일별 OHLCV 과거 구간 일괄 캐시 다운로드

기존 캐시(data/krx_ohlcv_cache)는 2025-01-15~ 만 연속이라 2024년 백테스트가 불가능했다.
본 스크립트로 과거 구간을 채워 연속 구간을 앞으로 확장한다.

  · 주말은 건너뛴다. 휴장일은 rows=[] 로 응답 → **파일을 만들지 않는다**
    (빈 캐시가 남으면 screen_quality_value 가 기준일로 잡아 등급이 통째로 하향되는
     §4.12.6 ② 함정에 걸린다)
  · 이미 rows 가 있는 캐시 파일은 건너뛴다(재실행 안전)

사용:
  python fetch_krx_history.py --start 20230102 --end 20250114
"""
from __future__ import annotations
import argparse, datetime as dt, json, os, sys, threading, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
from krx_openapi import KRXClient, KRXAPIError

CACHE = Path(__file__).parent / "data" / "krx_ohlcv_cache"


def daterange(start: str, end: str):
    d = dt.datetime.strptime(start, "%Y%m%d").date()
    e = dt.datetime.strptime(end, "%Y%m%d").date()
    while d <= e:
        if d.weekday() < 5:            # 월~금
            yield d.strftime("%Y%m%d")
        d += dt.timedelta(days=1)


def has_rows(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        return bool(json.loads(path.read_text(encoding="utf-8")).get("rows"))
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--workers", type=int, default=4, help="병렬 워커 수 (429 발생 시 낮출 것)")
    a = ap.parse_args()

    days = list(daterange(a.start, a.end))
    jobs = [(d, mk) for d in days for mk in ("KOSPI", "KOSDAQ")]
    total = len(jobs)
    cnt = {"done": 0, "skipped": 0, "holiday": 0, "failed": 0, "n": 0}
    lock = threading.Lock()
    local = threading.local()
    t0 = time.time()

    def client():
        if not hasattr(local, "cli"):
            local.cli = KRXClient()
        return local.cli

    def work(job):
        d, mk = job
        path = CACHE / f"{d}_{mk}.json"
        if has_rows(path):
            key = "skipped"
        else:
            try:
                rows = client().daily_ohlcv_raw(d, mk)
                if rows:
                    key = "done"
                else:
                    key = "holiday"
                    if path.exists():
                        path.unlink()   # 빈 캐시 파일 제거 (§4.12.6 ② 함정)
            except KRXAPIError as e:
                key = "failed"
                print(f"  x {d} {mk}: {str(e)[:120]}", flush=True)
        with lock:
            cnt[key] += 1
            cnt["n"] += 1
            if cnt["n"] % 100 == 0:
                el = time.time() - t0
                rate = cnt["n"] / el if el else 0
                eta = (total - cnt["n"]) / rate if rate else 0
                print(f"  {cnt['n']}/{total}  신규 {cnt['done']} · 기존 {cnt['skipped']} · "
                      f"휴장 {cnt['holiday']} · 실패 {cnt['failed']}  ({el:.0f}s · ETA {eta:.0f}s)",
                      flush=True)

    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        list(ex.map(work, jobs))
    done, skipped, holiday, failed = cnt["done"], cnt["skipped"], cnt["holiday"], cnt["failed"]

    print(f"\n완료: 요청 {total}건 → 신규 {done} · 기존 {skipped} · 휴장 {holiday} · 실패 {failed}")
    print(f"소요 {time.time()-t0:.0f}s · 캐시 {CACHE}")


if __name__ == "__main__":
    main()
