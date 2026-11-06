# -*- coding: utf-8 -*-
"""밥그릇3 설명 차트 — 스캐너와 같은 판정으로 구간·조건을 다시 계산해 PNG 1장으로 그린다.

사용: python -X utf8 bowl3_chart.py <코드|종목명> --out <png> [--pull YYYYMMDD] [--notes notes.json]
  · 표준출력 마지막 줄 = 사실 JSON (설명 문구는 이 값만 쓴다)
  · notes.json = {"title": "...", "read": ["줄", ...], "warn": ["줄", ...]}  (없으면 해당 상자 생략)
"""
import argparse, glob, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]          # jck_stock
sys.path.insert(0, str(ROOT))
import numpy as np
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from screen_rr_breakout import load, ma
from screen_bowl3_quality import find_pullbacks, PEAK_LB, DROP, BOX_RATIO, BOX_BELOW, VOL_X

plt.rcParams["font.family"] = "Malgun Gothic"; plt.rcParams["axes.unicode_minus"] = False
FIX = str.maketrans({"−": "-", "✔": "O", "✅": "O", "❌": "X", "⚠": "!", "️": ""})  # 맑은 고딕에 없는 글자
f0 = lambda x: f"{x:,.0f}"
md = lambda d: f"{d[4:6]}-{d[6:]}"

ap = argparse.ArgumentParser()
ap.add_argument("stock"); ap.add_argument("--out", required=True)
ap.add_argument("--pull"); ap.add_argument("--notes")
A = ap.parse_args()

bars, names, _ = load()
code = A.stock if A.stock in bars else next((c for c, n in names.items() if n == A.stock), None)
if not code:
    sys.exit(f"종목 없음: {A.stock}")
rows = sorted(bars[code]); name = names[code]
evs = find_pullbacks(rows)
if A.pull:
    evs = [e for e in evs if e["pull_date"] == A.pull]
if not evs:
    sys.exit(f"{name}({code}) — 밥그릇3 눌림 양봉 신호 없음" + (f" (눌림일 {A.pull})" if A.pull else ""))
e = evs[-1]

a = np.array([r[1:] for r in rows], dtype=float); o, h, l, c, v, cap = a.T
D = [r[0] for r in rows]
m20, m112, m224 = ma(c, 20), ma(c, 112), ma(c, 224); top, bot = np.fmax(m112, m224), np.fmin(m112, m224); v20 = ma(v, 20)
k, b, n = e["k"], D.index(e["breakout_date"]), len(c) - 1
s0 = max(0, b - PEAK_LB); p = s0 + int(np.argmax(h[s0:b - 20])); t = p + int(np.argmin(l[p:b]))   # 스캐너와 같은 고점·저점
hi = k + int(np.argmax(h[k:])) if k < n else k

# 수급·공시·재무 = 최신 스캐너 산출물, 없으면 백테스트 수급 캐시
q = {}
for f in sorted(glob.glob(str(ROOT / "data" / "bowl3_quality_*.json")), reverse=True):
    d = json.load(open(f, encoding="utf-8"))
    q = next((r for r in d["passed"] + d["rejected"] if r["code"] == code and r["pull_date"] == e["pull_date"]), {})
    if q:
        q["_src"] = Path(f).name; break
flow = q.get("flow_5d")
if flow is None:
    fc = ROOT / "data" / "bt_bowl3_flow_cache.json"
    flow = json.load(open(fc, encoding="utf-8")).get(f"{code}_{e['pull_date']}") if fc.exists() else None
plan = json.load(open(ROOT / "data" / "bowl3_state.json", encoding="utf-8"))["managed"].get(code)

F = dict(code=code, name=name, krx_last=D[n],
         peak=dict(date=D[p], high=h[p]), trough=dict(date=D[t], low=l[t], drop_pct=round((1 - l[t] / h[p]) * 100, 1),
         line_at_trough=round(float(bot[t]))),
         fall_days=t - p, box_days=b - t, below224_pct=round(float(np.mean(c[t:b] < m224[t:b])) * 100),
         breakout=dict(date=D[b], close=c[b], vol_x=round(v[b] / v20[b - 1], 2), cap_eok=round(cap[b] / 1e8)),
         pull=dict(date=D[k], low=l[k], close=c[k], low_vs_line_pct=round((l[k] / top[k] - 1) * 100, 1), days_after_bo=k - b),
         after_high=dict(date=D[hi], high=h[hi], vs_pull_close_pct=round((h[hi] / c[k] - 1) * 100, 1)),
         last=dict(date=D[n], close=c[n], chg_pct=round((c[n] / c[n - 1] - 1) * 100, 1), line=round(float(top[n])),
                   vs_line_pct=round((c[n] / top[n] - 1) * 100, 1), ma20=round(float(m20[n])),
                   vs_after_high_pct=round((c[n] / h[hi] - 1) * 100, 1), vol=v[n], vol20=round(float(v20[n]))),
         flow_5d=flow, negative=[x.get("matched") for x in q.get("negative") or []] if q else None,
         fin=q.get("fin"), net_inc=q.get("net_inc"), passed=q.get("passed"), reason=q.get("reason"),
         quality_src=q.get("_src"), plan=plan)
notes = json.load(open(A.notes, encoding="utf-8")) if A.notes else {}

# ── 차트 ──
w0 = max(0, p - 20); X = np.arange(w0, n + 1); ix = lambda i: i - w0
lo, hh = l[w0:].min(), h[w0:].max(); pad = (hh - lo)
fig = plt.figure(figsize=(17, 10.5), dpi=130)
ax = fig.add_axes([0.045, 0.30, 0.63, 0.62]); vx = fig.add_axes([0.045, 0.08, 0.63, 0.20], sharex=ax)
for i in X:
    col = "#d62728" if c[i] >= o[i] else "#1f77b4"
    ax.vlines(ix(i), l[i], h[i], color=col, lw=0.8)
    ax.add_patch(Rectangle((ix(i) - 0.35, min(o[i], c[i])), 0.7, max(abs(c[i] - o[i]), pad * 0.002), color=col))
    vx.bar(ix(i), v[i], color=col, width=0.7)
ax.plot(X - w0, m112[w0:], color="#ff9f1c", lw=2, label="112일선"); ax.plot(X - w0, m224[w0:], color="#7b2cbf", lw=2, label="224일선")
ylo, yhi = lo - pad * 0.18, hh + pad * 0.16; ty = hh + pad * 0.09
for s, e2, col, txt in [(p, t, "#1f77b4", f"1구간 하락 · {t - p}거래일"), (t, b, "#999999", f"2구간 바닥 · {b - t}거래일"),
                        (b, n, "#2ca02c", "3구간 돌파 → 눌림 → 현재")]:
    ax.axvspan(ix(s), ix(e2), color=col, alpha=0.08)
    ax.text(ix((s + e2) / 2) if col != "#2ca02c" else ix(n), ty, txt, ha="center" if col != "#2ca02c" else "right", fontsize=12, weight="bold", color="#555" if col == "#999999" else col)

placed = []
def note(i, y, txt, col, tries):
    """tries = (dx, dy) 후보 — 앞 상자와 안 겹치고 차트 안에 드는 첫 자리"""
    r = fig.canvas.get_renderer(); box = ax.get_window_extent(r); first_in = None
    grid = sorted(((x, y_) for x in range(-320, 321, 40) for y_ in range(-240, 241, 40) if abs(y_) >= 40),
                  key=lambda q: (q[0] - tries[0][0]) ** 2 + (q[1] - tries[0][1]) ** 2)   # 후보가 다 막히면 가까운 빈자리
    for dx, dy in tries + grid:
        an = mk(i, y, txt, col, dx, dy); bb = tb(an, r); an.remove()
        inside = box.x0 <= bb.x0 and bb.x1 <= box.x1 and box.y0 <= bb.y0 and bb.y1 <= box.y1
        if inside and not any(bb.overlaps(q) for q in placed):
            break
        if inside and first_in is None:
            first_in = (dx, dy)
    else:   # 빈자리 없음 → 차트 안 첫 자리(겹침 허용), 그것도 없으면 첫 후보
        dx, dy = first_in or tries[0]
    placed.append(tb(mk(i, y, txt, col, dx, dy), r))
tb = lambda an, r: (an.update_positions(r), an.get_bbox_patch().get_window_extent(r).expanded(1.08, 1.2))[1]   # 글상자만(화살표 제외)
def mk(i, y, txt, col, dx, dy):
    return ax.annotate(txt.translate(FIX), (ix(i), y), xytext=(dx, dy), textcoords="offset points", fontsize=10.5, color=col,
                       weight="bold", ha="left" if dx >= 0 else "right", va="center", arrowprops=dict(arrowstyle="->", color=col, lw=1.4),
                       bbox=dict(boxstyle="round,pad=0.35", fc="white", ec=col))
UP = [(25, 30), (-25, 30), (25, 90), (-25, 90), (40, 150), (-40, 150)]
DN = [(25, -60), (-25, -60), (25, -120), (-25, -120), (40, -180), (-40, -180)]
ax.set_xlim(-2, ix(n) + 14); ax.set_ylim(ylo, yhi)
note(p, h[p], f"① 고점 {f0(h[p])} ({md(D[p])})", "#1f77b4", [(25, 5)] + DN)
note(t, l[t], f"② 저점 {f0(l[t])} ({md(D[t])})\n고점 대비 -{F['trough']['drop_pct']}%", "#1f77b4", [(30, 50)] + UP)
note(n, c[n], f"⑥ {md(D[n])} 종가 {f0(c[n])} ({F['last']['chg_pct']:+}%)\n장기선({f0(top[n])}) {F['last']['vs_line_pct']:+}%",
     "#000000", [(-60, -100), (-60, -170), (-60, 100), (-60, 170), (-200, -100), (-200, 100)])
if hi > k + 1 and hi < n - 1:
    note(hi, h[hi], f"⑤ {md(D[hi])} 고점 {f0(h[hi])}\n눌림일 종가 대비 {F['after_high']['vs_pull_close_pct']:+}%", "#d62728", [(-30, 25)] + UP)
note(b, c[b], f"③ {md(D[b])} 장기선 돌파\n양봉 · 거래량 {F['breakout']['vol_x']}배", "#d62728",
     [(-40, 110), (-40, 170), (-120, 60), (-40, -150), (-160, -110), (-40, 230)])
note(k, l[k], f"④ {md(D[k])} 첫 눌림 양봉\n저가가 선 {F['pull']['low_vs_line_pct']:+}%", "#2ca02c", [(20, -95), (-20, -95), (60, -45), (-60, -45)] + DN + UP)
if plan:
    x0 = ix(max(k, n - 25)) / (ix(n) + 14 + 2)
    ax.axhspan(plan["p3"], plan["p1"], xmin=x0, color="#ffd60a", alpha=0.45)
    for y, lab, col in [(plan["p1"], f"1차 {f0(plan['p1'])}", "#b8860b"), (plan["stop"], f"손절 {f0(plan['stop'])}", "#d62728"),
                        (plan["target"], f"목표 {f0(plan['target'])}", "#2ca02c")]:
        ax.axhline(y, xmin=x0, color=col, ls="--", lw=1.3); ax.text(ix(n) + 1, y, lab, va="bottom", fontsize=9.5, color=col)
ax.legend(loc="lower left", fontsize=11); ax.grid(alpha=0.25)
ax.set_title((notes.get("title") or f"{name}({code}) — 밥그릇3 (눌림 {md(D[k])})").translate(FIX), fontsize=17, weight="bold", loc="left")
st = max(10, len(X) // 140 * 10); tk = [i for i in X if (i - w0) % st == 0]; vx.set_xticks([ix(i) for i in tk]); vx.set_xticklabels([md(D[i]) for i in tk], fontsize=9)
plt.setp(ax.get_xticklabels(), visible=False); vx.set_ylabel("거래량"); vx.grid(alpha=0.25)
vx.text(ix(b), v[b], f" {F['breakout']['vol_x']}배", fontsize=9, color="#d62728", va="bottom", weight="bold")

# ── 오른쪽: 조건표 + 설명 ──
sd = fig.add_axes([0.70, 0.08, 0.29, 0.84]); sd.axis("off")
sd.text(0, 0.985, "스캐너 통과 조건", fontsize=14, weight="bold")
ok = lambda x: "O" if x else ("?" if x is None else "X")
neg = F["negative"]
rows_ = [(ok(F["trough"]["drop_pct"] >= DROP * 100 and l[t] < bot[t]), "① 고점→저점 -40% 이상 · 저점이 장기선 아래",
          f"-{F['trough']['drop_pct']}% · {f0(l[t])} < {f0(bot[t])}"),
         (ok(b - t >= BOX_RATIO * (t - p) and F["below224_pct"] >= BOX_BELOW * 100), "② 바닥 ≥ 하락×0.5 · 224선 아래 70%",
          f"{b - t}일 ≥ {BOX_RATIO * (t - p):.0f}일 · {F['below224_pct']}%"),
         (ok(F["breakout"]["vol_x"] >= VOL_X and cap[b] >= 1e11), "③ 동시 돌파·양봉·거래량 1.5배·시총 1천억",
          f"{F['breakout']['vol_x']}배 · {f0(F['breakout']['cap_eok'])}억"),
         ("O", "④ 20일 안 첫 눌림 양봉 (선 -1~+5%)", f"{md(D[k])} · 돌파 {k - b}일 뒤 · 선 {F['pull']['low_vs_line_pct']:+}%"),
         (ok(None if flow is None else flow > 0), "외국인+기관 5일 순매수", "미조회" if flow is None else f"{flow:+,.0f}주"),
         (ok(None if neg is None else not neg), "올해 악재 공시 0건", "미조회" if neg is None else (f"{len(neg)}건 " + ", ".join(sorted(set(neg)))) if neg else "0건"),
         ("O" if F["fin"] == "3년 성장" else ("?" if not F["fin"] else "X"), "재무 3년 성장 (표시만)", F["fin"] or "미조회")]
for j, (m, a_, val) in enumerate(rows_):
    y = 0.93 - j * 0.052
    sd.text(0, y, m, fontsize=13, weight="bold", color={"O": "#2ca02c", "X": "#d62728"}.get(m, "#999"))
    sd.text(0.07, y, a_, fontsize=10.5); sd.text(0.07, y - 0.022, str(val).translate(FIX), fontsize=10, color="#555")
if notes.get("read"):
    sd.text(0, 0.535, "어떻게 읽나", fontsize=13, weight="bold", color="#2ca02c")
    sd.text(0, 0.51, "\n".join("· " + s for s in notes["read"]).translate(FIX), fontsize=10.2, va="top", linespacing=1.45)
if notes.get("warn"):
    sd.add_patch(Rectangle((-0.02, 0.0), 1.02, 0.2, fc="#fff3cd", ec="#e0a800", transform=sd.transAxes))
    sd.text(0.01, 0.18, "주의할 점", fontsize=13, weight="bold", color="#b8860b")
    sd.text(0.01, 0.155, "\n".join("· " + s for s in notes["warn"]).translate(FIX), fontsize=9.8, va="top", linespacing=1.45)
fig.savefig(A.out, facecolor="white")
print(json.dumps(F, ensure_ascii=False, default=float))
