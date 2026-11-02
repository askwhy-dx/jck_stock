# -*- coding: utf-8 -*-
"""check_krx_ready.py — 아침 recommend 회차 전 KRX 전일(직전 영업일) 데이터 공개 여부.

exit 0 = 공개됨(KOSPI 행 있음) / exit 1 = 아직 없음.
run_daily_dante.bat 이 08:15 에 1 을 받으면 morning 마커를 지우고 끝내 08:30 트리거가 재시도한다 (2026-10-01).
캐시는 건드리지 않는다(빈 파일을 남기지 않으려고 _post 직접 호출).
"""
import datetime as dt
import json
import sys
from pathlib import Path

from krx_openapi import KRXClient

ROOT = Path(__file__).parent
hol = {d for ds in json.loads((ROOT / "config" / "holiday_kr.json").read_text(encoding="utf-8"))["holidays"].values() for d in ds}

d = dt.date.today()
while True:
    d -= dt.timedelta(days=1)
    if d.weekday() < 5 and d.isoformat() not in hol:
        break
bas = d.strftime("%Y%m%d")
c = KRXClient()
n = len(c._extract_rows(c._post("sto/stk_bydd_trd", {"basDd": bas})))
print(f"[KRX ready] basDd={bas} KOSPI rows={n} -> {'READY' if n else 'NOT READY'}", flush=True)
sys.exit(0 if n else 1)
