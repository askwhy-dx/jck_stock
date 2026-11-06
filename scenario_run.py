# -*- coding: utf-8 -*-
"""16:30·19:00 [1s] 시나리오 그림 — 🎯손익비(auto_intake 관리 종목) + 🍚밥그릇3(bowl3_state) 를 그 시각 차트로 그려
Google Drive 폴더에 종목당 파일 1개로 덮어쓰고, 링크를 data/scenario_state.json 의 link 에 남긴다.

사용: python -X utf8 scenario_run.py [--no-upload] [--only 코드,...]
  · 시나리오는 처음 잡힌 날(편입일 KRX 종가) 고정 · 이후 회차는 진행 판정만(scenario_lib)
  · 실패해도 종료코드 0 (회차를 막지 않음) — 실패 종목은 link 를 비워 둔다
"""
import argparse, json, os, subprocess, sys, importlib.util, traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import numpy as np
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from screen_rr_breakout import load, ma
import scenario_lib as SL

plt.rcParams["font.family"] = "Malgun Gothic"; plt.rcParams["axes.unicode_minus"] = False
FOLDER = "1Dxyf3_z93hoyclDcvnLKiixtZlWbCZP7"          # 「대시보드 시나리오」 (회사 계정 소유)
OUT = ROOT / "data" / "scenario_png"
f0 = lambda x: f"{x:,.0f}"; md = lambda d: f"{d[4:6]}-{d[6:]}"


def draw_rr(code, m, bars, names, out):
    """손익비 시나리오 — 신호 60일 전부터 + 시나리오 구간"""
    rows, basis = SL.with_today(m["name"], sorted(bars[code]))
    D = [r[0] for r in rows]; o, h, l, c, v, _ = np.array([r[1:] for r in rows], float).T
    m20, m60, m112, m224 = ma(c, 20), ma(c, 60), ma(c, 112), ma(c, 224); v20 = ma(v, 20); n = len(c) - 1
    rr = m["rr"]; sg = D.index(rr["signal_date"]) if rr["signal_date"] in D else n
    S_ = SL.load_state(); sc = S_.get(code)
    if not sc or sc["base_date"] not in D:
        bd = m["intake_krx_date"] if m["intake_krx_date"] in D else D[n]
        sc = SL.make("rr", m["name"], m, bd, float(c[D.index(bd)]), bd, 20)   # 기한 = 편입 후 20거래일 미체결 (auto_intake EXPIRE_DAYS)
        S_[code] = sc; SL.save_state(S_)
    trend = lambda i: "종가 < MA20·MA60" if c[i] < m20[i] and c[i] < m60[i] else None   # auto_intake 퇴출 ①
    J = SL.judge(sc, D, h, l, c, code, trend)
    bs = D.index(sc["base_date"]); w0 = max(0, sg - 60); X = np.arange(w0, n + 1)
    SC = max(1.0, (bs - w0) * 0.45 / SL.FUT); ix = SL.xmap(w0, bs, SC)
    FW = max(SL.FUT, n - bs + 2) * SC; XR = ix(bs) + FW + 12 * SC
    P = sc["plan"]; lv = [P["stop"] * 0.99, P["target"] * 1.02, *[y for q in sc["paths"].values() for _, y in q]]
    lo, hh = min([l[w0:].min(), *lv]), max([h[w0:].max(), *lv]); pad = hh - lo; ylo, yhi = lo - pad * 0.12, hh + pad * 0.12
    fig = plt.figure(figsize=(17, 10.5), dpi=130)
    ax = fig.add_axes([0.045, 0.30, 0.63, 0.62]); vx = fig.add_axes([0.045, 0.08, 0.63, 0.20], sharex=ax)
    for i in X:
        col = "#d62728" if c[i] >= o[i] else "#1f77b4"; wk = 0.7 * (SC if i > bs else 1)
        ax.vlines(ix(i), l[i], h[i], color=col, lw=0.8 if i <= bs else 1.6)
        ax.add_patch(Rectangle((ix(i) - wk / 2, min(o[i], c[i])), wk, max(abs(c[i] - o[i]), pad * 0.002), color=col))
        vx.bar(ix(i), v[i], color=col, width=wk)
    XS = [ix(i) for i in X]
    for arr, col, lab, lw in ((m20, "#2ca02c", "20일선", 1.2), (m60, "#8c8c8c", "60일선", 1.2), (m112, "#ff9f1c", "112일선", 2), (m224, "#7b2cbf", "224일선", 2)):
        ax.plot(XS, arr[w0:], color=col, lw=lw, label=lab, alpha=1 if lw == 2 else 0.7)
    ax.set_xlim(-2, XR); ax.set_ylim(ylo, yhi)
    SL.draw_future(ax, sc, J, ix, bs, SC, ylo, pad, f0)
    kw = dict(textcoords="offset points", fontsize=10.5, weight="bold", va="center", bbox=dict(boxstyle="round,pad=0.35", fc="white"))
    ax.annotate(f"신호 {md(D[sg])} 장기선 돌파\n거래량 {v[sg] / v20[sg - 1]:.2f}배", (ix(sg), l[sg]), xytext=(-40, -110), ha="right", color="#d62728",
                arrowprops=dict(arrowstyle="->", color="#d62728", lw=1.4), **{**kw, "bbox": {**kw["bbox"], "ec": "#d62728"}})
    ax.annotate(f"{md(D[n])} 종가 {f0(c[n])} ({(c[n] / c[n - 1] - 1) * 100:+.1f}%)", (ix(n), c[n]), xytext=(40, -150), ha="left", color="#000",
                arrowprops=dict(arrowstyle="->", color="#000", lw=1.4), **{**kw, "bbox": {**kw["bbox"], "ec": "#000"}})
    ax.legend(loc="upper left", fontsize=9.5, ncol=4); ax.grid(alpha=0.25)
    ax.set_title(f"{m['name']}({code}) — 손익비 시나리오 (신호 {md(D[sg])})", fontsize=17, weight="bold", loc="left")
    st = max(10, int(np.ceil(XR / 18 / 10)) * 10)
    tk = [i for i in X if i <= bs and (i - w0) % st == 0] + [i for i in X if i > bs and ((i - bs) % max(1, round(st / SC)) == 0 or i == n)]
    tk = [i for j, i in enumerate(tk) if j == len(tk) - 1 or ix(tk[j + 1]) - ix(i) > XR / 22]   # 붙은 날짜 글자 겹침 방지(마지막 봉 우선)
    vx.set_xticks([ix(i) for i in tk]); vx.set_xticklabels([md(D[i]) for i in tk], fontsize=9)
    plt.setp(ax.get_xticklabels(), visible=False); vx.set_ylabel("거래량"); vx.grid(alpha=0.25)
    sd = fig.add_axes([0.70, 0.08, 0.29, 0.84]); sd.axis("off")
    blocks = [("손익비 신호", "#000", [("#222", f"테마 {' · '.join(rr.get('themes') or ['-'])}"),
                                     ("#222", f"과거 유사 {rr['n']}건 · 승률 {rr['win'] * 100:.1f}% · 기대값 {rr['ev_pct']:+.2f}%"),
                                     ("#222", f"재무 {'3년 성장' if rr.get('fin_ok') else rr.get('fin') or '-'} · 공시 {rr.get('disclosure') or '-'}")]),
              (f"가격 계획 ({m['intake_date'][5:]} 편입 고정)", "#000", [("#222", f"1차 {f0(P['p1'])} / 2차 {f0(P['p2'])} / 3차 {f0(P['p3'])}"),
                                                                    ("#222", f"손절 {f0(P['stop'])} · 목표 {f0(P['target'])} · 1차 손익비 {m['rr1']}")]),
              (f"시나리오 진행 ({basis})", "#b8860b", SL.status_lines(sc, J, f0, "체결 없이 종가 > 목표 또는 20거래일 → 퇴출"))]
    y = 0.985
    for hd, hc, lines in blocks:
        sd.text(0, y, hd, fontsize=13.5, weight="bold", color=hc); y -= 0.04
        for col, t in lines:
            sd.text(0.01, y, t, fontsize=10.2, color=col, weight="normal" if col in ("#222", "#555") else "bold"); y -= 0.03
        y -= 0.03
    sd.text(0.01, 0.0, "경로는 편입일 가격 계획으로 그린 예시(확률 아님)\n소멸 경로는 흐리게, 지금 가장 가까운 경로는 굵게", fontsize=9.5, color="#666", va="bottom")
    fig.savefig(out, facecolor="white"); plt.close(fig)


def drive():
    from dotenv import load_dotenv
    load_dotenv(ROOT.parent / ".env")   # 구글 인증 키는 워크스페이스(test) .env — jck_stock/.env 에는 없다
    p = os.path.join(os.environ["GOOGLE_AUTH_DIR"], "gdrive_shared.py")            # 회사 계정 = 폴더 소유자
    s = importlib.util.spec_from_file_location("gdrive_shared_company", p); g = importlib.util.module_from_spec(s); s.loader.exec_module(g)
    return g.drive_service()


def upload(dv, path, sc):
    """종목당 파일 1개 — 있으면 내용만 덮어써서 링크 유지"""
    from googleapiclient.http import MediaFileUpload
    media = MediaFileUpload(str(path), mimetype="image/png")
    if sc.get("drive_id"):
        try:
            dv.files().update(fileId=sc["drive_id"], media_body=media, supportsAllDrives=True).execute()
            return sc["drive_id"]
        except Exception:
            pass   # 지워졌으면 새로 만든다
    f = dv.files().create(body={"name": path.name, "parents": [FOLDER]}, media_body=media, fields="id", supportsAllDrives=True).execute()
    return f["id"]


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--no-upload", action="store_true"); ap.add_argument("--only")
    A = ap.parse_args(); only = set(A.only.split(",")) if A.only else None
    OUT.mkdir(parents=True, exist_ok=True)
    bars, names, _ = load()
    rr = {c: m for c, m in json.load(open(ROOT / "data" / "auto_intake_state.json", encoding="utf-8"))["managed"].items() if m.get("section") == "rr"}
    b3 = json.load(open(ROOT / "data" / "bowl3_state.json", encoding="utf-8"))["managed"]
    jobs = [("rr", c, m) for c, m in rr.items()] + [("bowl3", c, m) for c, m in b3.items()]
    dv = None if A.no_upload else drive()
    for kind, code, m in jobs:
        if only and code not in only:
            continue
        out = OUT / f"{code}_{m['name']}_시나리오.png"
        try:
            if kind == "rr":
                draw_rr(code, m, bars, names, out)
            else:
                r = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / ".claude/skills/bowl3-chart/scripts/bowl3_chart.py"), code, "--live", "--out", str(out)],
                                   capture_output=True, text=True, encoding="utf-8")
                if r.returncode:
                    raise RuntimeError(r.stderr.strip().splitlines()[-1] if r.stderr.strip() else r.stdout[-200:])
            st = SL.load_state(); sc = st[code]
            if dv:
                sc["drive_id"] = upload(dv, out, sc); sc["link"] = f"https://drive.google.com/file/d/{sc['drive_id']}/view"
            SL.save_state(st)
            print(f"[OK] {kind} {m['name']}({code}) {out.name} {sc.get('link', '(업로드 안 함)')}")
        except Exception as e:
            print(f"[FAIL] {kind} {m['name']}({code}) {e}"); traceback.print_exc(limit=1)


if __name__ == "__main__":
    main()
