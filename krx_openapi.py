"""
krx_openapi.py — KRX OpenAPI (data-dbg.krx.co.kr) 클라이언트 + 일별 캐시

사용:
    from krx_openapi import KRXClient
    cli = KRXClient()  # .env 의 KRX_AUTH_KEY 자동 로드
    df = cli.daily_ohlcv('2026-05-20', market='KOSPI')

엔드포인트 (POST + JSON body + AUTH_KEY 헤더):
    /sto/stk_bydd_trd       — 유가증권(KOSPI) 일별매매정보
    /sto/ksq_bydd_trd       — 코스닥 일별매매정보
    /sto/stk_isu_base_info  — 유가증권 종목기본정보
    /sto/ksq_isu_base_info  — 코스닥 종목기본정보

요청 파라미터: { "basDd": "YYYYMMDD" }
응답 포맷: { "OutBlock_1": [ ... ] }  (KRX 표준, fallback 자동 처리)
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

import requests

# Windows cmd 한글·이모지 출력 보장
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

try:
    import pandas as pd
except ImportError:
    pd = None


BASE_URL = "https://data-dbg.krx.co.kr/svc/apis"
DEFAULT_TIMEOUT = 30
RATE_LIMIT_SLEEP = 0.12  # 10 req/sec 안전 마진

# KRX OpenAPI 표준 응답 필드 (참고: 실제 호출 후 확인 필요할 수 있음)
OHLCV_FIELDS = {
    "BAS_DD": "date",          # 영업일
    "ISU_CD": "code",          # 단축코드
    "ISU_NM": "name",          # 종목명
    "MKT_NM": "market",        # 시장
    "SECT_TP_NM": "sector",    # 업종 (참고용)
    "TDD_OPNPRC": "open",
    "TDD_HGPRC": "high",
    "TDD_LWPRC": "low",
    "TDD_CLSPRC": "close",
    "CMPPREVDD_PRC": "change",
    "FLUC_RT": "fluc_pct",
    "ACC_TRDVOL": "volume",
    "ACC_TRDVAL": "trading_value",
    "MKTCAP": "market_cap",
    "LIST_SHRS": "shares",
}


class KRXAPIError(RuntimeError):
    pass


class KRXClient:
    def __init__(self, auth_key: Optional[str] = None, cache_dir: Optional[Path] = None):
        self.auth_key = (auth_key or self._load_env_key()).strip()
        if not self.auth_key:
            raise KRXAPIError("KRX_AUTH_KEY 가 .env 또는 환경변수에 없습니다.")
        self.cache_dir = Path(cache_dir) if cache_dir else (Path(__file__).parent / "data" / "krx_ohlcv_cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers.update({
            "AUTH_KEY": self.auth_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    @staticmethod
    def _load_env_key() -> str:
        env_path = Path(__file__).parent / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                if k.strip() == "KRX_AUTH_KEY":
                    return v.strip().strip('"').strip("'")
        return os.environ.get("KRX_AUTH_KEY", "")

    # --------------------------- 저수준 호출 ---------------------------

    def _post(self, endpoint: str, payload: dict, retries: int = 3) -> dict:
        url = f"{BASE_URL}/{endpoint.lstrip('/')}"
        last_err = None
        for attempt in range(1, retries + 1):
            try:
                r = self.session.post(url, json=payload, timeout=DEFAULT_TIMEOUT)
                if r.status_code == 401:
                    raise KRXAPIError(f"인증 실패 (401) — AUTH_KEY 확인 또는 서비스 신청 필요: {endpoint}")
                if r.status_code == 429:
                    time.sleep(1.5 * attempt)
                    continue
                if r.status_code != 200:
                    raise KRXAPIError(f"HTTP {r.status_code}: {r.text[:200]}")
                data = r.json()
                time.sleep(RATE_LIMIT_SLEEP)
                return data
            except requests.RequestException as e:
                last_err = e
                time.sleep(1.0 * attempt)
        raise KRXAPIError(f"요청 실패 ({endpoint}): {last_err}")

    @staticmethod
    def _extract_rows(response: dict) -> list[dict]:
        """KRX 응답에서 데이터 행 추출. OutBlock_1 표준 + 폴백."""
        for key in ("OutBlock_1", "output", "data", "list"):
            v = response.get(key)
            if isinstance(v, list):
                return v
        # 일부 응답이 단일 객체 안에 list 를 더 깊게 둘 가능성에 대비
        for v in response.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
        return []

    @staticmethod
    def _normalize_row(row: dict) -> dict:
        """KRX 원본 필드 키 → 정규화 키 (snake_case 영문). 누락 필드는 None."""
        out = {}
        for src, dst in OHLCV_FIELDS.items():
            val = row.get(src)
            if val is None or val == "":
                out[dst] = None
                continue
            if dst in ("open", "high", "low", "close", "change", "volume", "trading_value",
                       "market_cap", "shares"):
                try:
                    out[dst] = int(str(val).replace(",", "").replace("+", "").replace("-", "-"))
                except ValueError:
                    out[dst] = None
            elif dst == "fluc_pct":
                try:
                    out[dst] = float(str(val).replace(",", ""))
                except ValueError:
                    out[dst] = None
            else:
                out[dst] = str(val).strip()
        # 원본 보존(분석 시 누락 대비)
        out["_raw"] = row
        return out

    # --------------------------- 고수준 API ---------------------------

    def daily_ohlcv_raw(self, bas_dd: str, market: str) -> list[dict]:
        """단일 영업일의 전 종목 OHLCV (정규화). 캐시 사용."""
        market = market.upper()
        if market not in ("KOSPI", "KOSDAQ"):
            raise ValueError("market must be KOSPI or KOSDAQ")
        cache_path = self.cache_dir / f"{bas_dd}_{market}.json"
        if cache_path.exists():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                if cached.get("rows"):
                    return cached["rows"]
            except Exception:
                pass
        endpoint = "sto/stk_bydd_trd" if market == "KOSPI" else "sto/ksq_bydd_trd"
        raw = self._post(endpoint, {"basDd": bas_dd})
        rows = [self._normalize_row(r) for r in self._extract_rows(raw)]
        cache_path.write_text(
            json.dumps({"bas_dd": bas_dd, "market": market, "rows": rows}, ensure_ascii=False),
            encoding="utf-8",
        )
        return rows

    def daily_ohlcv(self, bas_dd: str, market: str):
        """단일 영업일 전 종목 OHLCV DataFrame."""
        if pd is None:
            raise RuntimeError("pandas 미설치")
        rows = self.daily_ohlcv_raw(bas_dd, market)
        return pd.DataFrame(rows)

    def stock_base_info(self, bas_dd: str, market: str) -> list[dict]:
        """종목 기본정보 (시총·발행주식·업종)."""
        market = market.upper()
        endpoint = "sto/stk_isu_base_info" if market == "KOSPI" else "sto/ksq_isu_base_info"
        raw = self._post(endpoint, {"basDd": bas_dd})
        return self._extract_rows(raw)

    def index_ohlcv(self, bas_dd: str, idx_type: str = "krx") -> list[dict]:
        """지수 일별시세 (KRX/KOSPI/KOSDAQ 시리즈)."""
        endpoint = {
            "krx": "idx/krx_dd_trd",
            "kospi": "idx/kospi_dd_trd",
            "kosdaq": "idx/kosdaq_dd_trd",
        }.get(idx_type, "idx/krx_dd_trd")
        raw = self._post(endpoint, {"basDd": bas_dd})
        return self._extract_rows(raw)


def healthcheck() -> bool:
    """연결 확인. 직전 1~7 영업일 중 데이터가 있는 첫 날을 KOSPI·KOSDAQ 양쪽 호출."""
    import datetime as dt
    cli = KRXClient()
    d = dt.date.today()
    for _ in range(8):
        d -= dt.timedelta(days=1)
        if d.weekday() >= 5:
            continue
        bas_dd = d.strftime("%Y%m%d")
        try:
            kospi = cli.daily_ohlcv_raw(bas_dd, "KOSPI")
            kosdaq = cli.daily_ohlcv_raw(bas_dd, "KOSDAQ")
        except KRXAPIError as e:
            print(f"[KRX healthcheck] {bas_dd} 호출 실패: {e}")
            return False
        if kospi or kosdaq:
            print(f"[KRX healthcheck] {bas_dd} KOSPI {len(kospi)}종 · KOSDAQ {len(kosdaq)}종")
            sample = (kospi or kosdaq)[0]
            print(f"  샘플: {sample.get('name')} ({sample.get('code')}) 종가={sample.get('close')} 시총={sample.get('market_cap')}")
            return True
        print(f"[KRX healthcheck] {bas_dd} 빈 응답 — 다음 영업일로 폴백")
    print("[KRX healthcheck] 직전 7영업일 모두 빈 응답")
    return False


if __name__ == "__main__":
    import sys
    try:
        ok = healthcheck()
        sys.exit(0 if ok else 1)
    except Exception as e:
        print(f"[KRX healthcheck FAIL] {e}")
        sys.exit(1)
