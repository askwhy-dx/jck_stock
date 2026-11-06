# -*- coding: utf-8 -*-
"""시나리오 그림 공용 — 고정 시나리오(처음 잡힌 회차에 1회 생성) · 진행 판정 · 미래 구간 그리기.

  · 상태 파일 data/scenario_state.json  {code: {kind, name, base_date, base_close, plan, paths, deadline_from, deadline_days, drive_id}}
  · 오늘 봉 = 토스 일봉 txt(data/api_chart/{종목}_{날짜}.txt, 16:30·19:00 [1] 단계가 씀) 중 KRX 캐시 마지막 날 뒤 봉 — 토스 재호출 없음
  · 판정은 규칙만(값 추정 없음): 체결 = 실제 체결(§6) 우선, 없으면 저가 ≤ 1차
"""
import json, re, glob
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "data" / "scenario_state.json"
FUT = 20                                   # 미래 구간 거래일
COL = {"A": "#2ca02c", "B": "#ff7f0e", "C": "#1f77b4"}
LAB = {"A": "A 체결→목표", "B": "B 미체결 상승", "C": "C 체결 뒤 손절"}


def load_state():
    return json.load(open(STATE, encoding="utf-8")) if STATE.exists() else {}


def save_state(st):
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def with_today(name, rows):
    """KRX rows(date,o,h,l,c,v,cap) 뒤에 토스 일봉 txt 의 더 최근 봉을 붙인다. 반환 = (rows, 기준 문구)"""
    fs = sorted(glob.glob(str(ROOT / "data" / "api_chart" / f"{name}_????-??-??.txt")))
    if not fs:
        return rows, f"KRX {rows[-1][0][4:6]}-{rows[-1][0][6:]} 종가"
    txt = open(fs[-1], encoding="utf-8").read().splitlines()
    when = (re.search(r"(\d{4}-\d\d-\d\d \d\d:\d\d) 조회", txt[0]) or [None, ""])[1]
    i0 = next(i for i, s in enumerate(txt) if s.startswith("날짜,"))
    add = []
    for s in txt[i0 + 1:]:
        d, o, h, l, c, v = s.split(",")[:6]
        d = d.replace("-", "")
        if d > rows[-1][0]:
            add.append((d, float(o), float(h), float(l), float(c), float(v), rows[-1][6]))
    if not add:
        return rows, f"KRX {rows[-1][0][4:6]}-{rows[-1][0][6:]} 종가"
    return rows + add, f"토스 {when[5:]} (NXT 포함)"


def actual_fill(code):
    """§6 라인업의 실제 체결일(YYYYMMDD) — 「(코드) **체결 1차 MM-DD」"""
    md = (ROOT / "AI단테.md").read_text(encoding="utf-8")
    m = re.search(rf"\({code}\)\s*\*\*체결 1차 (\d\d)-(\d\d)", md)
    return f"2026{m[1]}{m[2]}" if m else None   # ponytail: 연도 고정, 해를 넘기는 체결이 생기면 §6 에 연도 표기 추가


def make(kind, name, plan, base_date, base_close, deadline_from, deadline_days):
    """처음 잡힌 회차에 1회 — 경로는 가격 계획만으로(예시, 확률 아님)"""
    mid = (plan["p1"] + plan["p3"]) / 2
    return dict(kind=kind, name=name, base_date=base_date, base_close=base_close,
                plan={k: plan[k] for k in ("p1", "p2", "p3", "stop", "target")},
                paths={"A": [[4, mid], [11, plan["target"]]], "B": [[5, plan["target"] * 1.015]],
                       "C": [[4, mid], [9, plan["stop"] * 0.99]]},
                deadline_from=deadline_from, deadline_days=deadline_days)


def path_at(sc, key, d):
    """기준일 d 거래일 뒤 경로 값(선형 보간, 끝 뒤는 끝값)"""
    pts = [[0, sc["base_close"]]] + sc["paths"][key]
    for (d0, y0), (d1, y1) in zip(pts, pts[1:]):
        if d <= d1:
            return y0 + (y1 - y0) * (d - d0) / (d1 - d0)
    return pts[-1][1]


def judge(sc, D, h, l, c, code, exit_fn=None):
    """→ dict(st={A,B,C: alive|dead|done}, fill, why, near, left)"""
    P = sc["plan"]; b = D.index(sc["base_date"]); n = len(c) - 1
    fill = actual_fill(code)
    fill = fill if fill and fill > sc["base_date"] else next((D[i] for i in range(b + 1, n + 1) if l[i] <= P["p1"]), None)
    left = sc["deadline_days"] - sum(1 for d in D if sc["deadline_from"] < d)
    done = why = None
    if fill:
        for i in range(max(b + 1, D.index(fill) if fill in D else b + 1), n + 1):
            if c[i] < P["stop"]:
                done, why = "C", f"{D[i][4:6]}-{D[i][6:]} 종가 < 손절"; break
            if h[i] >= P["target"]:
                done, why = "A", f"{D[i][4:6]}-{D[i][6:]} 목표 도달"; break
    else:
        for i in range(b + 1, n + 1):
            if c[i] > P["target"]:
                done, why = "B", f"{D[i][4:6]}-{D[i][6:]} 종가 > 목표 (미체결 퇴출)"; break
        if not done and exit_fn:   # 손익비: 미체결 추세 이탈 퇴출 (auto_intake ①)
            for i in range(b + 1, n + 1):
                if exit_fn(i):
                    done, why = "X", f"{D[i][4:6]}-{D[i][6:]} {exit_fn(i)} (미체결 퇴출)"; break
        if not done and left <= 0:
            done, why = "B", "기한 만료 (미체결 퇴출)"
    if done:
        st = {k: "done" if k == done else "dead" for k in "ABC"}
    else:
        st = {"A": "alive", "B": "dead" if fill else "alive", "C": "alive"}
    alive = [k for k in "ABC" if st[k] == "alive"]
    near = min(alive, key=lambda k: abs(c[n] - path_at(sc, k, n - b))) if alive and n > b else None
    return dict(st=st, fill=fill, why=why, near=near, left=left, done=done, dl_off=left + (n - b))


def xmap(w0, b, SC):
    """기준일까지 1거래일 = 1칸, 기준일 뒤(시나리오 구간)는 SC 칸 — 긴 과거 차트에서도 미래가 보이게"""
    return lambda i: (i - w0) if i <= b else (b - w0) + (i - b) * SC


def draw_future(ax, sc, J, ix, b, SC, ylo, pad, f0):
    """미래 구간 배경·가격선·경로. 소멸 경로는 흐리게, 가장 가까운 경로는 굵게."""
    P = sc["plan"]; x0, x1 = ix(b), ix(b) + FUT * SC
    ax.axvspan(x0 + 0.5, x1, color="#000", alpha=0.035)
    ax.text((x0 + x1) / 2, ylo + pad * 0.03, "시나리오 구간 (예시 경로 · 확률 아님)", ha="center", fontsize=10, color="#666")
    ax.fill_between([x0 - 25, x1], P["p3"], P["p1"], color="#ffd60a", alpha=0.4)
    for y, lab, col in [(P["p1"], f"1차 {f0(P['p1'])}", "#b8860b"), (P["stop"], f"손절 {f0(P['stop'])}", "#d62728"),
                        (P["target"], f"목표 {f0(P['target'])}", "#2ca02c")]:
        ax.plot([x0 - 25, x1], [y, y], color=col, ls="--", lw=1.3)
        ax.text(x1 + 1, y, lab, va="bottom", fontsize=9.5, color=col)
    if not J["done"] and 0 < J["dl_off"] <= FUT:   # 미체결 퇴출 기한
        ax.axvline(x0 + J["dl_off"] * SC, color="#2ca02c", ls=":", lw=1.5)
        ax.text(x0 + J["dl_off"] * SC, ylo + pad * 0.09, "기한\n(미체결 퇴출)", ha="center", fontsize=9, color="#2ca02c", bbox=dict(fc="white", ec="none"))
    for k, va in (("A", "top"), ("B", "bottom"), ("C", "center")):
        s = J["st"][k]; a = {"alive": 1, "done": 1, "dead": 0.18}[s]
        xs = [x0] + [x0 + d * SC for d, _ in sc["paths"][k]]; ys = [sc["base_close"]] + [y for _, y in sc["paths"][k]]
        lw = 3.4 if (k == J["near"] or s == "done") else 2.0
        ax.plot(xs, ys, color=COL[k], ls="--", lw=lw, alpha=a)
        ax.annotate("", (xs[-1], ys[-1]), (xs[-2], ys[-2]), arrowprops=dict(arrowstyle="-|>", color=COL[k], lw=lw, alpha=a))
        tag = {"done": " 확정", "dead": " (소멸)", "alive": " ◀ 근접" if k == J["near"] else ""}[s]
        ax.text(xs[-1] + SC, ys[-1] - pad * {"top": 0.015, "bottom": -0.015, "center": 0}[va], LAB[k] + tag, color=COL[k],
                fontsize=11, weight="bold", va=va, alpha=max(a, 0.35), bbox=dict(boxstyle="round,pad=0.2", fc="white", ec=COL[k], alpha=max(a, 0.35)))
    return x1


def status_lines(sc, J, f0, rule_b):
    """오른쪽 칸 「진행 상황」"""
    P = sc["plan"]; st = J["st"]
    word = {"alive": "진행 중", "dead": "소멸", "done": "확정"}
    out = [("#555", f"시나리오 고정 {sc['base_date'][4:6]}-{sc['base_date'][6:]} 종가 {f0(sc['base_close'])} 기준"),
           ("#555", f"체결: {J['fill'][4:6] + '-' + J['fill'][6:] if J['fill'] else '없음'}"
                    + ("" if J["done"] else f" · 기한 남은 {max(J['left'], 0)}거래일"))]
    if J["done"]:
        out.append(("#000", f"결과: {LAB.get(J['done'], '퇴출')} — {J['why']}"))
    out += [(COL["A"], f"A {word[st['A']]}  {f0(P['p1'])} 이하 체결 → 목표 {f0(P['target'])}"),
            (COL["B"], f"B {word[st['B']]}  {rule_b}"),
            (COL["C"], f"C {word[st['C']]}  체결 뒤 종가 < 손절 {f0(P['stop'])}")]
    if J["near"]:
        out.append(("#000", f"지금 가장 가까운 경로: {LAB[J['near']]}"))
    return out
