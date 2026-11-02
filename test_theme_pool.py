"""collect_theme_rotation.update_pool 생애주기 자가 검증 (합성 시세). python test_theme_pool.py"""
import collect_theme_rotation as m

days = [f"202609{d:02d}" for d in range(1, 21)] + ["20260921"]  # 21거래일, 20일선 = 100
px = {d: {"A": 100, "B": 100, "C": 100, "D": 100} for d in days}
lo = {d: dict(px[d]) for d in days}
pick = lambda c: dict(code=c, name=c, theme="T", market_cap_billion=1000, close=100, ma10=100, ma20=100)

# 편입: 저가 99 < 10일선 100 인 D 만 즉시 🟢
lo[days[-1]] = {"A": 101, "B": 101, "C": 101, "D": 99}
px[days[-1]] = {"A": 102, "B": 102, "C": 102, "D": 102}
pool = m.update_pool({}, [pick(c) for c in "ABCD"], days, px, lo, "2026-09-22")
assert {c: v["status"] for c, v in pool["items"].items()} == {"A": "waiting", "B": "waiting", "C": "waiting", "D": "touched"}

# 다음날: A +10% 졸업 · B 20일선 이탈 · C 10일선 터치
d2 = "20260922"; days2 = days + [d2]
px[d2] = {"A": 112.2, "B": 90, "C": 101, "D": 101}
lo[d2] = {"A": 111, "B": 89, "C": 99, "D": 100}
pool = m.update_pool(pool, [], days2, px, lo, "2026-09-23")
st = {h["code"]: h["status"] for h in pool["history"]}
assert st == {"A": "graduated", "B": "removed"}, st
assert pool["items"]["C"]["status"] == "touched" and pool["items"]["D"]["status"] == "touched"

# 같은 기준일 재실행 = 판정 안 함 (중복 누적 없음)
pool = m.update_pool(pool, [], days2, px, lo, "2026-09-23")
assert len(pool["history"]) == 2

# EXPIRE_DAYS 거래일 경과 = 만료
extra = [f"202610{d:02d}" for d in range(1, m.EXPIRE_DAYS + 1)]
for d in extra:
    px[d] = {"C": 101, "D": 101}; lo[d] = {"C": 100.5, "D": 100.5}
pool = m.update_pool(pool, [], days2 + extra, px, lo, "2026-10-21")
assert {h["code"]: h["status"] for h in pool["history"]}.get("C") == "expired"

v = m.pool_view(pool, [], extra[-1])
assert v["stats"]["graduated"] == 1 and v["stats"]["removed"] == 1 and v["stats"]["win_rate"] == 50
print("OK — update_pool 생애주기 5종 판정 통과")
