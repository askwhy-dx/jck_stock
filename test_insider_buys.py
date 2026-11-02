"""screen_insider_buys.pullback_after_cross 자체 시험 (네트워크·캐시 불필요). 실행: PY test_insider_buys.py"""
import screen_insider_buys as s
from screen_insider_buys import pullback_after_cross

s.CROSS_WITHIN = 60  # 판정 논리만 본다(운영값 7일과 무관)

# 하락(100→80) 220일 → 급등해 112선 돌파 → 고점 후 112선 근처로 눌림
base = [100 - i * 20 / 220 for i in range(220)]
up = [80 + i * 2 for i in range(15)]          # 80→108
down = [108 - i * 2.5 for i in range(1, 9)]   # 105.5→88
cl = base + up + down
r = pullback_after_cross(cl)
assert r and r["ma"] == 112 and r["dd"] <= -5, r

# 고점에서 안 눌림 → 제외
assert pullback_after_cross(base + up) is None

# 데이터 부족(112+60 미만) → 제외
assert pullback_after_cross(cl[-150:]) is None
print("ok", r)
