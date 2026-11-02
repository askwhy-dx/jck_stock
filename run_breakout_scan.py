# -*- coding: utf-8 -*-
"""
run_breakout_scan.py — 장기이평 돌파 2개 유형 통합 런너

유형 1) 당일 폭발형 돌파            → screen_long_ma_breakout.py (기준일 당일 돌파)
유형 2) N일 전 돌파 + 현재 추세 유지 → screen_ma_breakout_followthrough.py
         (기준일에서 정확히 hold 영업일 전 돌파 → 그 후 추종 + 기준일에 선 위 유지)

사용 예:
  python run_breakout_scan.py                  # 최신 캐시일 기준, 둘 다
  python run_breakout_scan.py --date 20260608  # 특정일 기준
  python run_breakout_scan.py --date 20260608 --hold 3
"""
from __future__ import annotations
import argparse, subprocess, sys, os

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable


def run(script, extra):
    p = os.path.join(HERE, script)
    sys.stdout.flush()
    subprocess.run([PY, p] + extra, cwd=HERE)
    print("=" * 78)
    sys.stdout.flush()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="기준일 YYYYMMDD (기본: 최신 캐시일)")
    ap.add_argument("--hold", type=int, default=3, help="유형2: 돌파 후 추종/기준일까지 영업일수 (기본 3)")
    ap.add_argument("--both", action="store_true", help="112·224 동시 돌파만")
    a, _ = ap.parse_known_args()

    common = []
    if a.date:
        common += ["--date", a.date]
    if a.both:
        common += ["--both"]

    print("### 유형 1) 당일 폭발형 돌파 (기준일 당일)")
    sys.stdout.flush()
    run("screen_long_ma_breakout.py", common)

    print("\n### 유형 2) %d영업일 전 돌파 + 현재 추세 유지" % a.hold)
    # 정확히 hold 전 돌파만(window 1), 신선한 돌파라 112선 슬로프 조건은 끔(slope 0)
    run("screen_ma_breakout_followthrough.py",
        common + ["--hold", str(a.hold), "--window", "1", "--slope-lookback", "0"])
