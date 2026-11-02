# -*- coding: utf-8 -*-
"""
ess_screen.py — ESS 관련주 중 2023~2025 매출·영업이익·당기순이익 모두 증가 종목 스크리닝.

2025 사업보고서(reprt_code=11011) 1콜로 bfefrmtrm(2023)/frmtrm(2024)/thstrm(2025) 3개년 동시 확보.
연결(CFS) 우선, 없으면 별도(OFS) 폴백.
"""
import io, sys, zipfile
import xml.etree.ElementTree as ET
import sys, os; sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))  # tools/ → stock/
from dart_openapi import DARTClient, DARTAPIError

sys.stdout.reconfigure(encoding="utf-8")

# ESS(에너지저장장치) 관련주 후보 — 종목명 기준 (코드는 DART corp map으로 자동 해석)
ESS_NAMES = [
    "삼성SDI", "LG에너지솔루션", "서진시스템", "피엔티", "서남", "이엔플러스",
    "이랜텍", "아모그린텍", "LS ELECTRIC", "효성중공업", "HD현대일렉트릭",
    "현대에너지솔루션", "한화솔루션", "신성이엔지", "원익피앤이", "세방전지",
    "코스모신소재", "대명에너지", "삼화콘덴서", "삼화전기", "필에너지",
    "SK이터닉스", "LS일렉트릭", "비츠로셀", "웰크론한텍", "이수페타시스",
    "SG", "국일신동", "테라사이언스", "인화정공", "레이크머티리얼즈",
    "포스코퓨처엠", "엘앤에프", "에코프로비엠", "상신이디피", "신흥에스이씨",
    "덕성", "코윈테크", "브이티", "롯데에너지머티리얼즈",
]


def build_name_map(dart):
    """corpCode.xml → {정규화된 corp_name: stock_code} (상장사만)."""
    raw = dart._get("corpCode.xml", {}, want_bytes=True)
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        xml_bytes = zf.read(zf.namelist()[0])
    root = ET.fromstring(xml_bytes)
    m = {}
    for el in root.findall("list"):
        name = (el.findtext("corp_name") or "").strip()
        stock = (el.findtext("stock_code") or "").strip()
        if name and stock:
            m[name] = stock
    return m


def norm(s):
    return s.replace(" ", "").replace("(주)", "").upper()


def parse_amt(s):
    if not s or s == "-":
        return None
    try:
        return int(str(s).replace(",", "").replace("(", "-").replace(")", ""))
    except ValueError:
        return None


def three_year_pnl(dart, corp, year="2025"):
    """매출/영업이익/당기순이익 3개년 [2023, 2024, 2025] 반환."""
    for fs in ("CFS", "OFS"):
        params = {"corp_code": corp, "bsns_year": year, "reprt_code": "11011", "fs_div": fs}
        try:
            data = dart._get("fnlttSinglAcntAll.json", params)
        except DARTAPIError:
            continue
        rows = data.get("list", []) or []
        if not rows:
            continue
        out = {"sales": [None]*3, "op": [None]*3, "net": [None]*3, "fs": fs}
        def triple(r):
            return [parse_amt(r.get("bfefrmtrm_amount")),
                    parse_amt(r.get("frmtrm_amount")),
                    parse_amt(r.get("thstrm_amount"))]
        for r in rows:
            nm = (r.get("account_nm") or "").strip().replace(" ", "")
            sj = (r.get("sj_div") or "").strip()
            nm2 = nm[2:] if nm.startswith("연결") else nm
            if sj in ("CIS", "IS"):
                if nm in ("매출액", "수익(매출액)", "영업수익", "매출") and out["sales"] == [None]*3:
                    out["sales"] = triple(r)
                elif nm2 in ("영업이익", "영업이익(손실)", "영업손실") and out["op"] == [None]*3:
                    out["op"] = triple(r)
                elif nm2 in ("당기순이익", "당기순이익(손실)", "당기순이익(순손실)", "당기순손익") and out["net"] == [None]*3:
                    out["net"] = triple(r)
        if any(v is not None for v in out["sales"]):
            return out
    return None


def strictly_up(seq):
    vals = [v for v in seq]
    if any(v is None for v in vals):
        return None  # 판정 불가
    return all(vals[i] < vals[i+1] for i in range(len(vals)-1))


def won(v):
    if v is None:
        return "  ?   "
    return f"{v/1e8:,.0f}억"


def main():
    dart = DARTClient()
    print("종목명→코드 매핑 로드 중…")
    nmap = build_name_map(dart)
    norm_map = {norm(k): v for k, v in nmap.items()}

    resolved = []
    for name in ESS_NAMES:
        code = norm_map.get(norm(name))
        if code:
            resolved.append((name, code))
        else:
            print(f"  ⚠️ 코드 미해석: {name}")

    winners = []
    print(f"\n{'종목':<16}{'코드':<8}{'매출 23→24→25':<26}{'영업이익':<26}{'순이익':<26}판정")
    print("-" * 120)
    for name, code in resolved:
        corp = dart.to_corp_code(code)
        if not corp:
            print(f"{name:<16}{code:<8}corp_code 없음")
            continue
        try:
            p = three_year_pnl(dart, corp)
        except DARTAPIError as e:
            print(f"{name:<16}{code:<8}재무 오류 {e}")
            continue
        if not p:
            print(f"{name:<16}{code:<8}재무 데이터 없음")
            continue
        us, uo, un = strictly_up(p["sales"]), strictly_up(p["op"]), strictly_up(p["net"])
        allup = us and uo and un
        mark = "✅ 3개 모두 증가" if allup else ""
        s = " → ".join(won(x) for x in p["sales"])
        o = " → ".join(won(x) for x in p["op"])
        n = " → ".join(won(x) for x in p["net"])
        print(f"{name:<16}{code:<8}{s:<26}{o:<26}{n:<26}{mark}")
        if allup:
            winners.append((name, code, p))

    print("\n" + "=" * 60)
    print(f"■ 23~25년 매출·영업이익·당기순이익 3개 모두 증가: {len(winners)}종")
    for name, code, p in winners:
        print(f"   ✅ {name} ({code})")


if __name__ == "__main__":
    main()
