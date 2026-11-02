"""
dart_openapi.py — DART OpenAPI (opendart.fss.or.kr) 클라이언트 + 캐시

사용:
    from dart_openapi import DARTClient
    cli = DARTClient()  # .env 의 DART_API_KEY 자동 로드
    disclosures = cli.recent_disclosures('005930', days=90)

엔드포인트:
    /api/list.json          — 공시검색 (corp_code, 기간, 보고서 종류 필터)
    /api/corpCode.xml       — 종목코드 ↔ corp_code 매핑 (ZIP, 1회 다운로드 캐시)

분류 코드:
    pblntf_ty (대분류)
      A = 정기공시 (사업·반기·분기보고서)
      B = 주요사항보고서 (유증·CB·BW·감자 등)
      F = 자산양수도 / 합병
      I = 거래소공시 / 단일판매·공급계약 등
"""
from __future__ import annotations

import io
import json
import os
import sys
import time
import zipfile
import datetime as dt
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


BASE_URL = "https://opendart.fss.or.kr/api"
DEFAULT_TIMEOUT = 30
RATE_LIMIT_SLEEP = 0.15  # DART 1일 10,000 호출 — 약 7req/sec 안전 마진

# 악재 보고서명 패턴 (주요사항보고서 B군)
NEGATIVE_REPORT_PATTERNS = [
    "유상증자결정",
    "전환사채권발행결정",
    "신주인수권부사채권발행결정",
    "교환사채권발행결정",
    "감자결정",
    "주식분할결정",          # 중립이나 단기 변동성 — 알림용
    "횡령",
    "배임",
    "관리종목지정",
    "거래정지",
    "상장폐지",
    "감사의견부적정",
    "감사의견의견거절",
]

# 정기보고서 패턴 (실적 검증용)
PERIODIC_REPORT_PATTERNS = [
    "사업보고서",
    "반기보고서",
    "분기보고서",
]

# 호재 보고서 패턴 (추가 가산점 — §4.7 3단계)
POSITIVE_REPORT_PATTERNS = [
    "자기주식취득결정",
    "자기주식소각결정",
    "주식배당결정",
    "현금ㆍ현물배당결정",
    "단일판매ㆍ공급계약체결",
    "신규시설투자등의결정",
    "타법인주식및출자증권취득결정",
    "영업(잠정)실적(공정공시)",
]

# 임원 장내매수 — 간부진만. 회장·대주주(isu_main_shrholdr≠'-')·사외이사는 제외 (2026-10-16 사용자 지정)
# 일반 직원은 공시 대상이 아니라 잡을 수 없다.
# 2026-10-16 추가: 대표이사 제외 + 보유지분 5% 이상(대주주급 — 10% 미만이라 주요주주 표시가 안 되는 오너가) 제외
INSIDER_EXCLUDE_TITLES = ("회장", "대표", "사외이사", "독립이사")
INSIDER_MAX_STAKE_PCT = 5.0
INSIDER_DOC_MAX = 20  # 종목당 원문 조회 상한(최신순) — 삼성전자급은 90일 수백 건

# DART reprt_code (정기보고서 종류)
REPRT_CODES = {
    "11011": "사업보고서",       # 연간
    "11012": "반기보고서",       # 2분기 누적
    "11013": "1분기보고서",
    "11014": "3분기보고서",
}


class DARTAPIError(RuntimeError):
    pass


class DARTClient:
    def __init__(self, api_key: Optional[str] = None, cache_dir: Optional[Path] = None):
        self.api_key = (api_key or self._load_env_key()).strip()
        if not self.api_key:
            raise DARTAPIError(
                "DART_API_KEY 가 .env 또는 환경변수에 없습니다. "
                "opendart.fss.or.kr 에서 키 발급 후 stock/.env 에 'DART_API_KEY=xxx' 추가하세요."
            )
        self.cache_dir = Path(cache_dir) if cache_dir else (Path(__file__).parent / "data" / "dart_cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self._corp_map: dict[str, str] | None = None  # stock_code(6) -> corp_code(8)

    @staticmethod
    def _load_env_key() -> str:
        env_path = Path(__file__).parent / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                if k.strip() == "DART_API_KEY":
                    return v.strip().strip('"').strip("'")
        return os.environ.get("DART_API_KEY", "")

    # --------------------------- 저수준 호출 ---------------------------

    def _get(self, endpoint: str, params: dict, retries: int = 3, want_bytes: bool = False):
        url = f"{BASE_URL}/{endpoint.lstrip('/')}"
        params = {"crtfc_key": self.api_key, **params}
        last_err = None
        for attempt in range(1, retries + 1):
            try:
                r = self.session.get(url, params=params, timeout=DEFAULT_TIMEOUT)
                if r.status_code == 429:
                    time.sleep(1.5 * attempt)
                    continue
                if r.status_code != 200:
                    raise DARTAPIError(f"HTTP {r.status_code}: {r.text[:200]}")
                time.sleep(RATE_LIMIT_SLEEP)
                if want_bytes:
                    return r.content
                data = r.json()
                # DART 표준 응답: status="000" 성공
                status = data.get("status", "")
                if status == "013":  # 조회된 데이터 없음 — 정상
                    return data
                if status not in ("000", "013"):
                    raise DARTAPIError(f"DART status={status} message={data.get('message')}")
                return data
            except requests.RequestException as e:
                last_err = e
                time.sleep(1.0 * attempt)
        raise DARTAPIError(f"요청 실패 ({endpoint}): {last_err}")

    # --------------------------- corp_code 매핑 ---------------------------

    def _load_corp_map(self) -> dict[str, str]:
        """corpCode.xml(ZIP)을 받아 stock_code → corp_code 맵 구성. 캐시 7일."""
        if self._corp_map is not None:
            return self._corp_map
        cache_path = self.cache_dir / "corp_map.json"
        if cache_path.exists():
            mtime = dt.datetime.fromtimestamp(cache_path.stat().st_mtime)
            if dt.datetime.now() - mtime < dt.timedelta(days=7):
                try:
                    self._corp_map = json.loads(cache_path.read_text(encoding="utf-8"))
                    return self._corp_map
                except Exception:
                    pass
        # 다운로드
        raw = self._get("corpCode.xml", {}, want_bytes=True)
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            inner = zf.namelist()[0]
            xml_bytes = zf.read(inner)
        root = ET.fromstring(xml_bytes)
        m: dict[str, str] = {}
        for el in root.findall("list"):
            stock = (el.findtext("stock_code") or "").strip()
            corp = (el.findtext("corp_code") or "").strip()
            if stock and corp:
                m[stock] = corp
        self._corp_map = m
        cache_path.write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
        return m

    def to_corp_code(self, stock_code: str) -> str | None:
        m = self._load_corp_map()
        return m.get(stock_code.zfill(6))

    # --------------------------- 공시검색 ---------------------------

    def list_disclosures(self, corp_code: str, bgn_de: str, end_de: str,
                        pblntf_ty: Optional[str] = None) -> list[dict]:
        """기간별 공시 목록. pblntf_ty: A(정기)/B(주요사항) 등 단일 카테고리 필터."""
        params = {"corp_code": corp_code, "bgn_de": bgn_de, "end_de": end_de,
                  "page_count": 100}
        if pblntf_ty:
            params["pblntf_ty"] = pblntf_ty
        data = self._get("list.json", params)
        return data.get("list", []) or []

    def recent_disclosures(self, stock_code: str, days: int = 90) -> list[dict]:
        """단축코드 기준 직전 N일 모든 공시 (간편 메서드). 캐시 24시간."""
        corp = self.to_corp_code(stock_code)
        if not corp:
            return []
        end = dt.date.today()
        bgn = end - dt.timedelta(days=days)
        cache_path = self.cache_dir / f"disc_{stock_code}_{end.isoformat()}.json"
        if cache_path.exists():
            mtime = dt.datetime.fromtimestamp(cache_path.stat().st_mtime)
            if dt.datetime.now() - mtime < dt.timedelta(hours=24):
                try:
                    return json.loads(cache_path.read_text(encoding="utf-8"))
                except Exception:
                    pass
        rows = self.list_disclosures(corp, bgn.strftime("%Y%m%d"), end.strftime("%Y%m%d"))
        cache_path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        return rows

    # --------------------------- 임원 장내매수 ---------------------------

    def _market_buys(self, rcept_no: str) -> tuple[int, int]:
        """소유상황보고서 원문 세부변동내역의 「장내매수(+)」 행 → (수량, 금액). 원문은 불변이라 영구 캐시.
        스톡옵션 행사·우리사주 인출·주식보상 등 다른 사유(+)는 세지 않는다."""
        cache_path = self.cache_dir / f"insider_{rcept_no}.json"
        if cache_path.exists():
            return tuple(json.loads(cache_path.read_text(encoding="utf-8")))
        import re
        raw = self._get("document.xml", {"rcept_no": rcept_no}, want_bytes=True)
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            text = " ".join(zf.read(n).decode("utf-8", "ignore") for n in zf.namelist())
        text = re.sub(r"[\s|]*(?:<[^>]+>[\s|]*)+", "|", text)
        qty = amt = 0
        # 장내매수(+)|변동일|종류|변동전|증감|변동후|단가
        for m in re.finditer(r"장내매수\(\+\)\|[^|]+\|[^|]+\|[\d,]+\|([\d,]+)\|[\d,]+\|([\d,]+)", text):
            q, p = int(m.group(1).replace(",", "")), int(m.group(2).replace(",", ""))
            qty += q
            amt += q * p
        cache_path.write_text(json.dumps([qty, amt]), encoding="utf-8")
        return qty, amt

    def insider_buys(self, stock_code: str, days: int = 90) -> list[dict]:
        """직전 N일 임원(간부진) 장내매수 보고. 회장·대주주·사외이사 제외."""
        corp = self.to_corp_code(stock_code)
        if not corp:
            return []
        since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
        rows = self._get("elestock.json", {"corp_code": corp}).get("list", [])
        hits = [x for x in rows
                if x.get("rcept_dt", "") >= since
                and x.get("isu_main_shrholdr", "-") == "-"
                and not any(k in x.get("isu_exctv_ofcps", "") for k in INSIDER_EXCLUDE_TITLES)
                and float("".join(ch for ch in x.get("sp_stock_lmp_rate", "") if ch.isdigit() or ch == ".") or 0) < INSIDER_MAX_STAKE_PCT
                and x.get("sp_stock_lmp_irds_cnt", "").replace(",", "").isdigit()
                and x["sp_stock_lmp_irds_cnt"] != "0"]
        hits.sort(key=lambda x: x["rcept_dt"], reverse=True)
        out = []
        for x in hits[:INSIDER_DOC_MAX]:
            qty, amt = self._market_buys(x["rcept_no"])
            if qty:
                out.append({"repror": x.get("repror"), "ofcps": x.get("isu_exctv_ofcps"),
                            "rcept_dt": x["rcept_dt"], "qty": qty, "amount": amt,
                            "stake_pct": x.get("sp_stock_lmp_rate")})
        return out

    # --------------------------- 재무제표 ---------------------------

    def financial_summary(self, corp_code: str, bsns_year: str, reprt_code: str) -> dict:
        """단일회사 주요계정 (영업이익·매출액·당기순이익) + 전년 동기 비교 자동.

        reprt_code: 11011(사업)/11012(반기)/11013(1Q)/11014(3Q)
        반환: { 'op_yoy_pct': float|None, 'sales_yoy_pct': float|None,
                'op_thstrm': int, 'op_frmtrm': int, 'reprt': str, 'bsns_year': str }
        DART 응답에서 thstrm_amount(당기) vs frmtrm_amount(전년 동기) 자동 비교.
        데이터 누락 시 None.
        """
        cache_path = self.cache_dir / f"fin_{corp_code}_{bsns_year}_{reprt_code}.json"
        if cache_path.exists():
            mtime = dt.datetime.fromtimestamp(cache_path.stat().st_mtime)
            if dt.datetime.now() - mtime < dt.timedelta(days=7):
                try:
                    return json.loads(cache_path.read_text(encoding="utf-8"))
                except Exception:
                    pass
        params = {"corp_code": corp_code, "bsns_year": bsns_year,
                  "reprt_code": reprt_code, "fs_div": "CFS"}  # 연결 우선
        try:
            data = self._get("fnlttSinglAcntAll.json", params)
        except DARTAPIError:
            # 연결 없으면 별도(OFS) 시도
            try:
                params["fs_div"] = "OFS"
                data = self._get("fnlttSinglAcntAll.json", params)
            except DARTAPIError:
                return {}
        rows = data.get("list", []) or []
        out = {"reprt": REPRT_CODES.get(reprt_code, reprt_code), "bsns_year": bsns_year,
               "op_yoy_pct": None, "sales_yoy_pct": None,
               "op_thstrm": None, "op_frmtrm": None,
               "sales_thstrm": None, "sales_frmtrm": None}

        def parse_amt(s):
            if not s or s == "-":
                return None
            try:
                return int(str(s).replace(",", "").replace("(", "-").replace(")", ""))
            except ValueError:
                return None

        for r in rows:
            nm = (r.get("account_nm") or "").strip().replace(" ", "")
            sj = (r.get("sj_div") or "").strip()
            # 손익계산서/포괄손익 시트만 (BS/CF/SCE 제외)
            if sj not in ("CIS", "IS"):
                continue
            if nm in ("영업이익", "영업이익(손실)", "영업손실"):
                if out["op_thstrm"] is None:
                    out["op_thstrm"] = parse_amt(r.get("thstrm_amount"))
                    out["op_frmtrm"] = parse_amt(r.get("frmtrm_amount"))
            elif nm in ("매출액", "수익(매출액)", "영업수익", "매출"):
                if out["sales_thstrm"] is None:
                    out["sales_thstrm"] = parse_amt(r.get("thstrm_amount"))
                    out["sales_frmtrm"] = parse_amt(r.get("frmtrm_amount"))

        if out["op_thstrm"] is not None and out["op_frmtrm"] is not None and out["op_frmtrm"] != 0:
            out["op_yoy_pct"] = round((out["op_thstrm"] / out["op_frmtrm"] - 1) * 100, 1)
        if out["sales_thstrm"] is not None and out["sales_frmtrm"] is not None and out["sales_frmtrm"] != 0:
            out["sales_yoy_pct"] = round((out["sales_thstrm"] / out["sales_frmtrm"] - 1) * 100, 1)

        cache_path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
        return out

    def financial_full(self, corp_code: str, bsns_year: str, reprt_code: str = "11011") -> dict:
        """우량주 심사용 풀 재무 (3개년 매출·당기순이익·자산 + 당기 부채/자본/자산).

        단일 호출(2025 사업보고서)로 thstrm(당기)/frmtrm(전기)/bfefrmtrm(전전기) 동시 확보.
        반환:
          { 'ok': bool,
            'sales':   [전전기, 전기, 당기]  # 매출액 3개년 (int|None)
            'net_inc': [전전기, 전기, 당기]  # 당기순이익 3개년
            'assets':  [전전기, 전기, 당기]  # 자산총계 3개년
            'liab':    당기 부채총계 (int|None)
            'equity':  당기 자기자본(자본총계) (int|None)
            'bsns_year': str }
        업종 보정 없음 — 호출측에서 그대로 룰 적용.
        """
        cache_path = self.cache_dir / f"finfull_{corp_code}_{bsns_year}_{reprt_code}.json"
        if cache_path.exists():
            mtime = dt.datetime.fromtimestamp(cache_path.stat().st_mtime)
            if dt.datetime.now() - mtime < dt.timedelta(days=7):
                try:
                    return json.loads(cache_path.read_text(encoding="utf-8"))
                except Exception:
                    pass
        params = {"corp_code": corp_code, "bsns_year": bsns_year,
                  "reprt_code": reprt_code, "fs_div": "CFS"}  # 연결 우선
        try:
            data = self._get("fnlttSinglAcntAll.json", params)
            if not (data.get("list") or []):
                raise DARTAPIError("CFS 빈 응답")
        except DARTAPIError:
            try:
                params["fs_div"] = "OFS"  # 별도 폴백
                data = self._get("fnlttSinglAcntAll.json", params)
            except DARTAPIError:
                out = {"ok": False, "bsns_year": bsns_year}
                cache_path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
                return out
        rows = data.get("list", []) or []

        def parse_amt(s):
            if not s or s == "-":
                return None
            try:
                return int(str(s).replace(",", "").replace("(", "-").replace(")", ""))
            except ValueError:
                return None

        def three(r):
            return [parse_amt(r.get("bfefrmtrm_amount")),
                    parse_amt(r.get("frmtrm_amount")),
                    parse_amt(r.get("thstrm_amount"))]

        out = {"ok": True, "bsns_year": bsns_year,
               "sales": [None, None, None], "net_inc": [None, None, None],
               "assets": [None, None, None], "liab": None, "equity": None}

        for r in rows:
            nm = (r.get("account_nm") or "").strip().replace(" ", "")
            sj = (r.get("sj_div") or "").strip()
            if sj in ("CIS", "IS"):
                # '연결당기순이익' 등 접두 정규화 ('계속영업'·'법인세차감전'은 미포함)
                nm2 = nm[2:] if nm.startswith("연결") else nm
                if nm in ("매출액", "수익(매출액)", "영업수익", "매출") and out["sales"] == [None, None, None]:
                    out["sales"] = three(r)
                elif nm2 in ("당기순이익", "당기순이익(손실)", "당기순이익(순손실)", "당기순손익",
                             "분기순이익", "분기순이익(손실)", "반기순이익", "반기순이익(손실)") and out["net_inc"] == [None, None, None]:
                    out["net_inc"] = three(r)
            elif sj == "BS":
                if nm == "자산총계" and out["assets"] == [None, None, None]:
                    out["assets"] = three(r)
                elif nm == "부채총계" and out["liab"] is None:
                    out["liab"] = parse_amt(r.get("thstrm_amount"))
                elif nm in ("자본총계", "자본총계(자기자본)") and out["equity"] is None:
                    out["equity"] = parse_amt(r.get("thstrm_amount"))

        cache_path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
        return out

    # --------------------------- 분류 헬퍼 ---------------------------

    @staticmethod
    def find_negative(disclosures: list[dict]) -> list[dict]:
        """악재 공시(유증/CB/BW/감자/횡령 등) 추출."""
        out = []
        for d in disclosures:
            name = d.get("report_nm", "")
            for pat in NEGATIVE_REPORT_PATTERNS:
                if pat in name:
                    out.append({"report_nm": name, "rcept_dt": d.get("rcept_dt"),
                                "rcept_no": d.get("rcept_no"), "matched": pat})
                    break
        return out

    @staticmethod
    def find_periodic(disclosures: list[dict]) -> list[dict]:
        """정기보고서(사업·반기·분기) 추출. 정정보고서 제외."""
        out = []
        for d in disclosures:
            name = d.get("report_nm", "")
            if "[기재정정]" in name or "[첨부정정]" in name:
                continue
            for pat in PERIODIC_REPORT_PATTERNS:
                if pat in name:
                    out.append({"report_nm": name, "rcept_dt": d.get("rcept_dt"),
                                "rcept_no": d.get("rcept_no"), "matched": pat})
                    break
        return out

    @staticmethod
    def latest_annual(disclosures: list[dict]) -> dict | None:
        """가장 최근 사업보고서 (연간 확정실적). reprt_code='11011' 검증용."""
        anns = [d for d in disclosures if "사업보고서" in (d.get("report_nm") or "")
                and "[기재정정]" not in d.get("report_nm", "")
                and "[첨부정정]" not in d.get("report_nm", "")]
        if not anns:
            return None
        latest = sorted(anns, key=lambda x: x["rcept_dt"], reverse=True)[0]
        # 보고서명에서 사업연도 추출 — 예: "사업보고서 (2025.12)" → bsns_year=2025
        nm = latest.get("report_nm", "")
        import re
        m = re.search(r"\((\d{4})\.\d{2}\)", nm)
        bsns_year = m.group(1) if m else None
        return {"report_nm": nm, "rcept_dt": latest["rcept_dt"],
                "rcept_no": latest["rcept_no"], "bsns_year": bsns_year,
                "reprt_code": "11011"}

    @staticmethod
    def latest_quarterly(disclosures: list[dict]) -> dict | None:
        """가장 최근 분기/반기 보고서 (사업보고서 제외)."""
        import re
        qs = []
        for d in disclosures:
            nm = d.get("report_nm") or ""
            if "[기재정정]" in nm or "[첨부정정]" in nm:
                continue
            if "분기보고서" in nm or "반기보고서" in nm:
                qs.append(d)
        if not qs:
            return None
        latest = sorted(qs, key=lambda x: x["rcept_dt"], reverse=True)[0]
        nm = latest["report_nm"]
        # 사업연도 + 분기 추출 — "분기보고서 (2026.03)" → 2026, 1Q
        m = re.search(r"\((\d{4})\.(\d{2})\)", nm)
        bsns_year, mm = (m.group(1), m.group(2)) if m else (None, None)
        if "반기보고서" in nm:
            reprt_code = "11012"
        elif mm == "03":
            reprt_code = "11013"
        elif mm == "09":
            reprt_code = "11014"
        else:
            reprt_code = "11013"
        return {"report_nm": nm, "rcept_dt": latest["rcept_dt"],
                "rcept_no": latest["rcept_no"], "bsns_year": bsns_year,
                "reprt_code": reprt_code}

    @staticmethod
    def find_positive(disclosures: list[dict]) -> list[dict]:
        """호재 공시 (자사주 취득/소각, 단일판매계약, 배당 등)."""
        out = []
        for d in disclosures:
            nm = d.get("report_nm", "")
            if "[기재정정]" in nm or "[첨부정정]" in nm:
                continue
            for pat in POSITIVE_REPORT_PATTERNS:
                if pat in nm:
                    out.append({"report_nm": nm, "rcept_dt": d.get("rcept_dt"),
                                "rcept_no": d.get("rcept_no"), "matched": pat})
                    break
        return out


def healthcheck() -> bool:
    """연결 확인. 삼성전자(005930) 최근 30일 공시 1건 이상 확인."""
    try:
        cli = DARTClient()
    except DARTAPIError as e:
        print(f"[DART healthcheck FAIL] {e}")
        return False
    try:
        rows = cli.recent_disclosures("005930", days=30)
        print(f"[DART healthcheck] 삼성전자(005930) 최근 30일 공시 {len(rows)}건")
        if rows:
            sample = rows[0]
            print(f"  샘플: {sample.get('rcept_dt')} {sample.get('report_nm')}")
        return True
    except DARTAPIError as e:
        print(f"[DART healthcheck FAIL] {e}")
        return False


if __name__ == "__main__":
    ok = healthcheck()
    sys.exit(0 if ok else 1)
