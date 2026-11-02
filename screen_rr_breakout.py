"""screen_rr_breakout.py — 🎯 손익비 돌파 (2026-10-19 사용자 지시)

08:30 [B1g]. 결과는 data/rr_breakout_{오늘}.json, 상위 최대 5종(picks).
picks는 exposure.js(key 'rr')를 거쳐 Slack 노출 종목에 들어가고, auto_intake.js가 스윙 탭에 자동 편입한다.
이 스캐너에서 나온 종목만 자동 편입 대상이다.

신호 (최근 15거래일 안에 · 2026-10-19 10→15 아래를 모두 만족한 날):
    돌파선 L = max(MA112, MA224)
    전날 종가 <= L 이고 당일 종가 > L
    양봉
    거래량 >= 전날까지 20일 평균의 2배
    시가총액 >= 1,000억 원
통과 (전일 확정봉 기준, 아래를 모두 만족):
    종가 > L
    손절 폭 2~5%  (손절 = L x 0.99. 목표 +10% 기준으로 손익비 2.0 이상)
    당일 양봉     (음봉 진입 금지)
    신호 봉 몸통 >= 50%
    과거 유사 사례 30건 이상이고 기대값 > 0
과거 유사 사례 (2022~ 전 종목):
    같은 신호가 나온 뒤 15일 안에 '손절 폭 2~5% + 양봉 + 선 위'인 첫날 종가에 진입한다.
    60일 안에 고가가 +10%에 먼저 닿으면 승(+10%), 종가가 손절가 이하로 먼저 마감하면 패(그날 종가),
    둘 다 아니면 60일째 종가로 정산한다.
    유사 = 신호 봉의 4개 구간(몸통·윗꼬리·거래량 배수·등락)이 모두 같은 사례.
DART (2026-10-19 A안 · 밥그릇3 screen_bowl3_quality 와 같은 판정):
    공시 = 올해 악재 공시(유증·CB·BW·감자·횡령·관리·거래정지 등) 있으면 제외 (조회 실패는 통과 + '미확인')
    재무 = 3개년 매출·순이익 연속 증가 여부를 배지로만 기록 (제외하지 않음)
"""
import glob
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).parent
DATA = ROOT / "data"
CACHE = DATA / "krx_ohlcv_cache"
TP, H, LB, TOPN, MIN_N = 0.10, 60, 15, 5, 30
EB, EU, EV, EC = [.5, .8], [.05, .15], [3, 10], [.06, .10, .20]


def ma(x, n):
    c = np.cumsum(np.insert(x, 0, 0))
    m = np.full(len(x), np.nan)
    m[n - 1:] = (c[n:] - c[:-n]) / n
    return m


def bins(b, u, vr, ch):
    return (int(np.digitize(b, EB)), int(np.digitize(u, EU)), int(np.digitize(vr, EV)), int(np.digitize(ch, EC)))


def load():
    bars, names, markets = {}, {}, {}
    for f in sorted(glob.glob(str(CACHE / "*_*.json"))):
        d = json.load(open(f, encoding="utf-8"))
        for r in d.get("rows") or []:
            if not r.get("close") or not r.get("open"):
                continue
            bars.setdefault(r["code"], []).append((r["date"], r["open"], r["high"], r["low"], r["close"], r["volume"], r.get("market_cap") or 0))
            names[r["code"]], markets[r["code"]] = r["name"], r["market"]
    return bars, names, markets


def themes_of():
    p = DATA / "naver_theme_map.json"
    out = {}
    if p.exists():
        for name, codes in json.load(open(p, encoding="utf-8")).get("themes", {}).items():
            for c in codes:
                out.setdefault(c, []).append(name.split("(")[0].strip())
    return out


def dart_screen(rows, today, krx_last):
    """기대값 순으로 공시 악재를 걸러 TOPN 을 채우고, 재무 배지를 붙인다."""
    from datetime import date
    from dart_openapi import DARTClient, DARTAPIError
    from screen_bowl3_quality import _default_bsns_year, _incr3
    dart = DARTClient()
    bsns_year = _default_bsns_year(krx_last)
    t = date.fromisoformat(today)
    disc_days = (t - date(t.year, 1, 1)).days + 1
    picks = []
    for r in rows:
        if len(picks) >= TOPN:
            break
        corp = dart.to_corp_code(r["code"])
        try:
            neg = DARTClient.find_negative(dart.recent_disclosures(r["code"], days=disc_days))
            r["disclosure"] = "청정" if not neg else "악재: " + ", ".join(sorted({n["matched"] for n in neg}))
        except DARTAPIError as e:
            neg, r["disclosure"] = [], f"미확인({e})"
        if neg:
            print(f"[rr]  제외 {r['name']}({r['code']}) — 올해 {r['disclosure']}")
            continue
        try:
            fin = dart.financial_full(corp, bsns_year) if corp else {}
            if fin.get("ok"):
                s_ok, n_ok = _incr3(fin.get("sales")), _incr3(fin.get("net_inc"))
                r["fin_ok"] = s_ok and n_ok
                r["fin"] = "3년 성장" if r["fin_ok"] else " · ".join(x for x, ok in (("매출", s_ok), ("순이익", n_ok)) if not ok) + " 증가 아님"
            else:
                r["fin_ok"], r["fin"] = None, "재무 없음"
        except DARTAPIError as e:
            r["fin_ok"], r["fin"] = None, f"재무 미확인({e})"
        picks.append(r)
    return picks


def main():
    today = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")
    bars, names, markets = load()
    last = max(r[-1][0] for r in bars.values())
    hist, cand = [], []
    for code, rows in bars.items():
        if len(rows) < 300 or rows[-1][0] != last:
            continue
        rows.sort()
        a = np.array([r[1:] for r in rows], dtype=float)
        o, h, l, c, v, cap = a.T
        L = np.fmax(ma(c, 112), ma(c, 224))
        v20 = ma(v, 20)
        n = len(c)
        for i in range(225, n):
            if cap[i] < 1e11 or not (c[i - 1] <= L[i - 1] and c[i] > L[i]) or c[i] <= o[i] or h[i] <= l[i]:
                continue
            rg = h[i] - l[i]
            f = ((c[i] - o[i]) / rg, (h[i] - c[i]) / rg, v[i] / v20[i - 1], c[i] / c[i - 1] - 1)
            if not f[2] >= 2:
                continue
            if i < n - H:
                for k in range(i, min(i + LB, n - H)):
                    sw = 1 - L[k] * 0.99 / c[k]
                    if 0.02 <= sw <= 0.05 and c[k] > o[k] and c[k] > L[k]:
                        break
                else:
                    continue
                S, T, res, win = L[k] * 0.99, c[k] * (1 + TP), c[k + H] / c[k] - 1, 0
                for j in range(k + 1, k + 1 + H):
                    if h[j] >= T:
                        res, win = TP, 1
                        break
                    if c[j] <= S:
                        res = c[j] / c[k] - 1
                        break
                hist.append(bins(*f) + (win, res))
            elif i >= n - LB:
                sw = 1 - L[-1] * 0.99 / c[-1]
                if c[-1] > L[-1] and 0.02 <= sw <= 0.05 and c[-1] > o[-1] and f[0] >= 0.5:
                    cand.append(dict(code=code, signal_date=rows[i][0], f=f, close=c[-1], line=L[-1], stop_pct=sw))
    Hh = np.array(hist, dtype=float)
    th = themes_of()
    best = {}
    for x in cand:
        key = np.array(bins(*x["f"]))
        m = (Hh[:, :4] == key).all(1)
        cnt = int(m.sum())
        if cnt < MIN_N or Hh[m, 5].mean() <= 0:
            continue
        b, u, vr, ch = x["f"]
        row = dict(code=x["code"], name=names[x["code"]], market=markets[x["code"]], signal_date=x["signal_date"],
                   close=round(x["close"]), line=round(x["line"]), line_="112+224", stop=round(x["line"] * 0.99),
                   stop_pct=round(x["stop_pct"] * 100, 2), rr=round(TP / x["stop_pct"], 2),
                   body=round(b, 3), upper=round(u, 3), vmult=round(vr, 2), chg_pct=round(ch * 100, 2),
                   n=cnt, win=round(float(Hh[m, 4].mean()), 3), ev_pct=round(float(Hh[m, 5].mean()) * 100, 2),
                   themes=th.get(x["code"], []))
        if x["code"] not in best or row["signal_date"] > best[x["code"]]["signal_date"]:
            best[x["code"]] = row
    picks = dart_screen(sorted(best.values(), key=lambda r: -r["ev_pct"]), today, last)
    out = dict(date=today, krx_date=last, hist_events=len(Hh),
               baseline=dict(win=round(float(Hh[:, 4].mean()), 3), ev_pct=round(float(Hh[:, 5].mean()) * 100, 2)),
               candidates=len(cand), picks=picks)
    if "--dry-run" not in sys.argv:
        (DATA / f"rr_breakout_{today}.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[rr] KRX {last} · 과거 {len(Hh)}건 · 후보 {len(cand)} · 선정 {len(picks)}")
    for r in picks:
        print(f"[rr]  {r['name']}({r['code']}) 신호 {r['signal_date']} 종가 {r['close']:,} 손절 {r['stop']:,}(-{r['stop_pct']}%) "
              f"손익비 {r['rr']} · n={r['n']} 승 {r['win']:.0%} 기대 {r['ev_pct']:+.2f}% · 공시 {r['disclosure']} · 재무 {r['fin']}")


if __name__ == "__main__":
    main()
