"""Screen KOSDAQ 150 constituents for financial quality near 52-week lows.

Output is JSON only. The constituent universe comes from the PLUS KOSDAQ150
ETF portfolio, prices come from the local KRX OpenAPI cache, and financial
statements come from OpenDART.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import sys
from pathlib import Path
from typing import Any

import requests

from dart_openapi import DARTAPIError, DARTClient


ROOT = Path(__file__).resolve().parent
KRX_CACHE = ROOT / "data" / "krx_ohlcv_cache"
OUT_DIR = ROOT / "reports"
ETF_ID = "006318"
COMPOSITION_URL = "https://www.plusetf.co.kr/api/v1/product/pdf/list"
COMPOSITION_PAGE_URL = "https://www.plusetf.co.kr/product/detail?n=006318"

THEME_BY_CODE = {
    "376300": {
        "themes": ["K-POP", "팬 플랫폼", "구독형 앱", "글로벌 팬덤"],
        "summary": "DearU bubble 기반 유료 구독형 팬 커뮤니케이션 플랫폼",
        "source_url": "https://dear-u.co/en/pages/about_overview.php",
    },
    "035900": {
        "themes": ["K-POP", "엔터테인먼트", "글로벌 투어", "아티스트 IP·MD"],
        "summary": "아티스트 음반·공연과 글로벌 투어 연계 MD·IP 사업",
        "source_url": "https://www.jype.com/ko/Board/Detail?gubun=irdata&jbst_sq=6984",
    },
    "058470": {
        "themes": ["반도체", "IC 테스트 소켓", "프로브 핀", "AI 반도체"],
        "summary": "반도체 검사용 IC 테스트 소켓과 스프링 컨택트 프로브",
        "source_url": "https://leeno.com/",
    },
    "082920": {
        "themes": ["리튬일차전지", "스마트미터", "방산", "고온전지"],
        "summary": "스마트미터·군용 등에 쓰이는 리튬일차전지와 특수전지",
        "source_url": "https://www.vitzrocell.com/sub/sub02_01.php?cat_no=3&idx=9&mode=view&offset=0",
    },
    "101490": {
        "themes": ["반도체 소재", "블랭크마스크", "EUV", "펠리클"],
        "summary": "반도체·디스플레이용 블랭크마스크와 EUV 소재 개발",
        "source_url": "https://www.snstech.co.kr/renew/html/sub01_01.asp",
    },
    "214150": {
        "themes": ["미용 의료기기", "HIFU", "RF", "글로벌 피부미용"],
        "summary": "HIFU 슈링크와 RF 볼뉴머 중심의 미용 의료기기",
        "source_url": "https://classys.co.kr/company/classys/",
    },
    "042000": {
        "themes": ["이커머스", "유튜브 쇼핑", "크리에이터 커머스", "AI 커머스"],
        "summary": "온라인 스토어 구축과 유튜브 쇼핑 연동 중심의 이커머스 플랫폼",
        "source_url": "https://www.cafe24.com/youtubeshopping/about.html",
    },
}

ACCOUNT_IDS = {
    "revenue": ("ifrs-full_Revenue", "ifrs_Revenue", "dart_Revenue"),
    "operating_income": ("dart_OperatingIncomeLoss",),
    "net_income": (
        "ifrs-full_ProfitLossAttributableToOwnersOfParent",
        "ifrs_ProfitLossAttributableToOwnersOfParent",
        "ifrs-full_ProfitLoss",
        "ifrs_ProfitLoss",
    ),
    "assets": ("ifrs-full_Assets", "ifrs_Assets"),
    "liabilities": ("ifrs-full_Liabilities", "ifrs_Liabilities"),
    "equity": (
        "ifrs-full_EquityAttributableToOwnersOfParent",
        "ifrs_EquityAttributableToOwnersOfParent",
        "ifrs-full_Equity",
        "ifrs_Equity",
    ),
    "operating_cash_flow": (
        "ifrs-full_CashFlowsFromUsedInOperatingActivities",
        "ifrs_CashFlowsFromUsedInOperatingActivities",
    ),
}

ACCOUNT_NAMES = {
    "revenue": ("매출액", "수익(매출액)", "영업수익", "매출"),
    "operating_income": ("영업이익", "영업이익(손실)", "영업손익"),
    "net_income": (
        "지배기업의소유주에게귀속되는당기순이익(손실)",
        "지배기업소유주지분순이익",
        "당기순이익",
        "당기순이익(손실)",
        "당기순손익",
    ),
    "assets": ("자산총계",),
    "liabilities": ("부채총계",),
    "equity": ("지배기업의소유주에게귀속되는자본", "자본총계", "자본총계(자기자본)"),
    "operating_cash_flow": ("영업활동현금흐름", "영업활동으로인한현금흐름"),
}

STATEMENT_DIVISIONS = {
    "revenue": {"CIS", "IS"},
    "operating_income": {"CIS", "IS"},
    "net_income": {"CIS", "IS"},
    "assets": {"BS"},
    "liabilities": {"BS"},
    "equity": {"BS"},
    "operating_cash_flow": {"CF"},
}


def configure_stdout() -> None:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass


def parse_amount(value: Any) -> int | None:
    if value in (None, "", "-"):
        return None
    text = str(value).replace(",", "").strip()
    if text.startswith("(") and text.endswith(")"):
        text = f"-{text[1:-1]}"
    try:
        return int(text)
    except ValueError:
        return None


def safe_div(numerator: float | int | None, denominator: float | int | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator


def rounded(value: float | None, digits: int = 4) -> float | None:
    return round(value, digits) if value is not None and math.isfinite(value) else None


def latest_price_date(requested: str | None) -> str:
    candidates = []
    for path in KRX_CACHE.glob("*_KOSDAQ.json"):
        stem = path.stem.split("_")[0]
        if len(stem) == 8 and stem.isdigit() and (not requested or stem <= requested):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if payload.get("rows"):
                candidates.append(stem)
    if not candidates:
        raise RuntimeError("KRX_PRICE_CACHE_NOT_FOUND")
    return max(candidates)


def fetch_constituents(as_of: str) -> list[dict[str, Any]]:
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": COMPOSITION_PAGE_URL,
        "Accept": "application/json",
    }
    response = requests.get(
        COMPOSITION_URL,
        params={"n": ETF_ID, "page": 0, "d": as_of, "pageSize": 200},
        headers=headers,
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("content") or []
    constituents = []
    for row in rows:
        raw_code = str(row.get("jmCd") or "").strip()
        if not raw_code:
            continue
        code = raw_code.zfill(6)
        if len(code) != 6 or not code.isalnum():
            continue
        constituents.append(
            {
                "code": code,
                "name": row.get("jmNm"),
                "index_weight_pct": rounded(float(row["ratio"]), 4) if row.get("ratio") is not None else None,
            }
        )
    # ETF PDF files include the cash row in their displayed total. Temporary
    # index adjustments can therefore leave 149 equity rows between reviews.
    if not 145 <= len(constituents) <= 150:
        raise RuntimeError(f"KOSDAQ150_CONSTITUENT_COUNT_INVALID:{len(constituents)}")
    return constituents


def load_price_history(as_of: str, lookback_days: int) -> tuple[dict[str, list[dict]], list[str]]:
    end = dt.datetime.strptime(as_of, "%Y%m%d").date()
    start = end - dt.timedelta(days=lookback_days)
    by_code: dict[str, list[dict]] = {}
    dates = []
    for path in sorted(KRX_CACHE.glob("*_KOSDAQ.json")):
        stem = path.stem.split("_")[0]
        if len(stem) != 8 or not stem.isdigit():
            continue
        day = dt.datetime.strptime(stem, "%Y%m%d").date()
        if day < start or day > end:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        rows = payload.get("rows") or []
        if not rows:
            continue
        dates.append(stem)
        for row in rows:
            code = str(row.get("code") or "").zfill(6)
            if len(code) == 6 and code.isalnum() and row.get("close"):
                by_code.setdefault(code, []).append(row)
    return by_code, dates


def lag_return(closes: list[float], sessions: int) -> float | None:
    if len(closes) <= sessions or not closes[-sessions - 1]:
        return None
    return closes[-1] / closes[-sessions - 1] - 1


def mean_tail(values: list[float], length: int) -> float | None:
    if len(values) < length:
        return None
    return sum(values[-length:]) / length


def price_metrics(history: list[dict]) -> dict[str, Any]:
    rows = sorted(history, key=lambda row: row["date"])
    closes = [float(row["close"]) for row in rows if row.get("close")]
    highs = [float(row["high"]) for row in rows if row.get("high")]
    lows = [float(row["low"]) for row in rows if row.get("low")]
    latest = rows[-1]
    close = closes[-1]
    high52 = max(highs)
    low52 = min(lows)
    width = high52 - low52
    ma5 = mean_tail(closes, 5)
    ma20 = mean_tail(closes, 20)
    ma60 = mean_tail(closes, 60)
    ma112 = mean_tail(closes, 112)
    ma120 = mean_tail(closes, 120)
    ma224 = mean_tail(closes, 224)
    high_index, high_row = max(enumerate(rows), key=lambda item: float(item[1]["high"]))
    low_index, low_row = min(enumerate(rows), key=lambda item: float(item[1]["low"]))
    drawdown = safe_div(high52 - close, high52)
    position = safe_div(close - low52, width)
    gap_to_ma224 = safe_div(close - ma224, ma224) if ma224 is not None else None
    return_20d = lag_return(closes, 20)
    above_ma20 = bool(ma20 is not None and close >= ma20)
    if above_ma20 and return_20d is not None and return_20d > 0:
        bottom_status = "TURNING_UP"
    elif above_ma20:
        bottom_status = "STABILIZING"
    else:
        bottom_status = "FALLING_OR_UNCONFIRMED"
    return {
        "date": latest.get("date"),
        "close": int(close),
        "market_cap_krw": latest.get("market_cap"),
        "high_52w": int(high52),
        "high_52w_date": high_row.get("date"),
        "sessions_since_52w_high": len(rows) - 1 - high_index,
        "low_52w": int(low52),
        "low_52w_date": low_row.get("date"),
        "sessions_since_52w_low": len(rows) - 1 - low_index,
        "drawdown_from_52w_high_pct": rounded(drawdown * 100 if drawdown is not None else None, 2),
        "position_in_52w_range_pct": rounded(position * 100 if position is not None else None, 2),
        "return_5d_pct": rounded((lag_return(closes, 5) or 0) * 100, 2) if lag_return(closes, 5) is not None else None,
        "return_20d_pct": rounded((return_20d or 0) * 100, 2) if return_20d is not None else None,
        "return_60d_pct": rounded((lag_return(closes, 60) or 0) * 100, 2) if lag_return(closes, 60) is not None else None,
        "ma5": rounded(ma5, 2),
        "ma20": rounded(ma20, 2),
        "ma60": rounded(ma60, 2),
        "ma112": rounded(ma112, 2),
        "ma120": rounded(ma120, 2),
        "ma224": rounded(ma224, 2),
        "gap_to_ma224_pct": rounded(gap_to_ma224 * 100 if gap_to_ma224 is not None else None, 2),
        "above_ma20": above_ma20,
        "bottom_status": bottom_status,
        "trading_sessions": len(closes),
    }


def dante_phase(price: dict[str, Any]) -> dict[str, Any]:
    gap = price.get("gap_to_ma224_pct")
    drawdown = price.get("drawdown_from_52w_high_pct")
    since_high = price.get("sessions_since_52w_high")
    close = price.get("close")
    ma20 = price.get("ma20")
    ma60 = price.get("ma60")
    if gap is None or drawdown is None or close is None or ma20 is None:
        return {"phase": "UNKNOWN", "detail": "MA224 또는 가격 데이터 부족", "confidence": "LOW"}
    if drawdown >= 35 and since_high is not None and since_high <= 160:
        return {
            "phase": "4구간 후반",
            "detail": f"최근 52주 고점 후 {since_high}거래일, 고점 대비 {drawdown:.2f}% 조정, MA224 괴리 {gap:+.2f}%",
            "confidence": "MEDIUM",
        }
    if gap > 15 and ma60 is not None and close >= ma20 >= ma60:
        return {
            "phase": "3구간",
            "detail": f"MA224를 {gap:.2f}% 상회하고 MA20·MA60 상승 배열",
            "confidence": "MEDIUM",
        }
    if gap >= 0:
        return {
            "phase": "2구간 후반",
            "detail": f"MA224를 {gap:.2f}% 상회해 3구간 전환 시도",
            "confidence": "MEDIUM",
        }
    if gap >= -15 and close >= ma20:
        return {
            "phase": "2구간 전반",
            "detail": f"MA20 위에서 안정화 중이며 MA224까지 {abs(gap):.2f}%",
            "confidence": "MEDIUM",
        }
    if close >= ma20:
        return {
            "phase": "1구간 후반",
            "detail": f"MA224를 {abs(gap):.2f}% 하회하지만 MA20 위에서 바닥 안정화 시도",
            "confidence": "MEDIUM",
        }
    return {
        "phase": "1구간",
        "detail": f"MA20과 MA224 아래로 바닥 형성 미확인, MA224 괴리 {gap:+.2f}%",
        "confidence": "LOW",
    }


def add_theme_and_dante(row: dict[str, Any]) -> None:
    theme = THEME_BY_CODE.get(row["code"])
    row["themes"] = theme["themes"] if theme else None
    row["theme_summary"] = theme["summary"] if theme else None
    row["theme_source_url"] = theme["source_url"] if theme else None
    row["dante"] = dante_phase(row["price_metrics"])


def row_for(rows: list[dict], metric: str) -> dict | None:
    allowed = STATEMENT_DIVISIONS[metric]
    eligible = [row for row in rows if (row.get("sj_div") or "").strip() in allowed]
    for account_id in ACCOUNT_IDS[metric]:
        for row in eligible:
            if (row.get("account_id") or "").strip() == account_id:
                return row
    wanted_names = {name.replace(" ", "") for name in ACCOUNT_NAMES[metric]}
    for row in eligible:
        name = (row.get("account_nm") or "").replace(" ", "").strip()
        if name.startswith("연결"):
            name = name[2:]
        if name in wanted_names:
            return row
    return None


def cached_statement(dart: DARTClient, corp_code: str, year: str, report_code: str) -> tuple[list[dict], str]:
    cache_dir = ROOT / "data" / "kosdaq150_bottom" / "dart"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{corp_code}_{year}_{report_code}.json"
    if cache_path.exists():
        age = dt.datetime.now() - dt.datetime.fromtimestamp(cache_path.stat().st_mtime)
        if age < dt.timedelta(days=7):
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
            return payload.get("rows") or [], payload.get("fs_div") or "UNKNOWN"
    last_error: Exception | None = None
    for fs_div in ("CFS", "OFS"):
        try:
            payload = dart._get(
                "fnlttSinglAcntAll.json",
                {"corp_code": corp_code, "bsns_year": year, "reprt_code": report_code, "fs_div": fs_div},
            )
            rows = payload.get("list") or []
            if rows:
                cache_path.write_text(
                    json.dumps({"fs_div": fs_div, "rows": rows}, ensure_ascii=False),
                    encoding="utf-8",
                )
                return rows, fs_div
        except DARTAPIError as exc:
            last_error = exc
    if last_error:
        raise last_error
    return [], "UNKNOWN"


def extract_annual(rows: list[dict], fs_div: str, year: str) -> dict[str, Any]:
    out: dict[str, Any] = {"year": year, "fs_div": fs_div}
    for metric in ACCOUNT_IDS:
        row = row_for(rows, metric)
        out[metric] = [
            parse_amount(row.get("bfefrmtrm_amount")) if row else None,
            parse_amount(row.get("frmtrm_amount")) if row else None,
            parse_amount(row.get("thstrm_amount")) if row else None,
        ]
    return out


def extract_quarter(rows: list[dict], fs_div: str, year: str) -> dict[str, Any]:
    out: dict[str, Any] = {"year": year, "quarter": "Q1", "fs_div": fs_div}
    for metric in ACCOUNT_IDS:
        row = row_for(rows, metric)
        out[metric] = parse_amount(row.get("thstrm_amount")) if row else None
        if row and metric in {"revenue", "operating_income", "net_income", "operating_cash_flow"}:
            prior_value = row.get("frmtrm_q_amount") or row.get("frmtrm_amount")
        else:
            prior_value = row.get("frmtrm_amount") if row else None
        out[f"{metric}_prior_year"] = parse_amount(prior_value)
    return out


def financial_metrics(annual: dict, quarter: dict) -> tuple[dict[str, Any], dict[str, bool | None]]:
    revenue = annual["revenue"]
    operating_income = annual["operating_income"]
    net_income = annual["net_income"]
    assets = annual["assets"]
    liabilities = annual["liabilities"][-1]
    equity = annual["equity"][-1]
    operating_cash_flow = annual["operating_cash_flow"]
    revenue_latest = revenue[-1]
    op_latest = operating_income[-1]
    net_latest = net_income[-1]
    ocf_latest = operating_cash_flow[-1]
    sales_change_3y = safe_div(revenue_latest, revenue[0])
    q_op = quarter.get("operating_income")
    q_net = quarter.get("net_income")
    q_op_prior = quarter.get("operating_income_prior_year")
    q_revenue = quarter.get("revenue")
    q_revenue_prior = quarter.get("revenue_prior_year")

    checks: dict[str, bool | None] = {
        "annual_profit_positive": None if op_latest is None or net_latest is None else op_latest > 0 and net_latest > 0,
        "q1_profit_positive": None if q_op is None or q_net is None else q_op > 0 and q_net > 0,
        "debt_to_equity_lte_100pct": None if liabilities is None or not equity else liabilities / equity <= 1.0,
        "operating_cash_flow_positive": None if ocf_latest is None else ocf_latest > 0,
        "operating_margin_gte_5pct": None if op_latest is None or not revenue_latest else op_latest / revenue_latest >= 0.05,
        "three_year_sales_not_down_over_10pct": None if sales_change_3y is None else sales_change_3y >= 0.90,
        "profit_positive_at_least_two_of_three_years": None
        if sum(v is not None for v in operating_income) < 2 or sum(v is not None for v in net_income) < 2
        else sum(v is not None and v > 0 for v in operating_income) >= 2
        and sum(v is not None and v > 0 for v in net_income) >= 2,
    }
    comparable = [value for value in checks.values() if value is not None]
    score = sum(value is True for value in comparable)
    metrics = {
        "annual_year": annual["year"],
        "annual_fs_div": annual["fs_div"],
        "revenue_krw_3y": revenue,
        "operating_income_krw_3y": operating_income,
        "net_income_krw_3y": net_income,
        "operating_cash_flow_krw_3y": operating_cash_flow,
        "assets_krw_latest": assets[-1],
        "liabilities_krw_latest": liabilities,
        "equity_krw_latest": equity,
        "debt_to_equity_pct": rounded((safe_div(liabilities, equity) or 0) * 100, 2)
        if safe_div(liabilities, equity) is not None
        else None,
        "operating_margin_pct": rounded((safe_div(op_latest, revenue_latest) or 0) * 100, 2)
        if safe_div(op_latest, revenue_latest) is not None
        else None,
        "net_margin_pct": rounded((safe_div(net_latest, revenue_latest) or 0) * 100, 2)
        if safe_div(net_latest, revenue_latest) is not None
        else None,
        "roe_proxy_pct": rounded((safe_div(net_latest, equity) or 0) * 100, 2)
        if safe_div(net_latest, equity) is not None
        else None,
        "revenue_change_3y_pct": rounded((sales_change_3y - 1) * 100, 2) if sales_change_3y is not None else None,
        "q1_year": quarter.get("year"),
        "q1_fs_div": quarter.get("fs_div"),
        "q1_revenue_krw": q_revenue,
        "q1_operating_income_krw": q_op,
        "q1_net_income_krw": q_net,
        "q1_revenue_yoy_pct": rounded(((safe_div(q_revenue, q_revenue_prior) or 0) - 1) * 100, 2)
        if safe_div(q_revenue, q_revenue_prior) is not None
        else None,
        "q1_operating_income_yoy_pct": rounded(((safe_div(q_op, q_op_prior) or 0) - 1) * 100, 2)
        if safe_div(q_op, q_op_prior) is not None and q_op_prior > 0
        else None,
        "financial_checks_passed": score,
        "financial_checks_available": len(comparable),
    }
    return metrics, checks


def finance_grade(score: int, available: int) -> str:
    if available < 5:
        return "DATA_INSUFFICIENT"
    ratio = score / available
    if ratio >= 0.90:
        return "A"
    if ratio >= 0.75:
        return "B"
    if ratio >= 0.60:
        return "C"
    return "D"


def screened_candidate(row: dict[str, Any]) -> bool:
    checks = row["financial_checks"]
    metrics = row["financial_metrics"]
    mandatory = (
        checks.get("annual_profit_positive") is True
        and checks.get("q1_profit_positive") is True
        and checks.get("debt_to_equity_lte_100pct") is True
        and checks.get("operating_cash_flow_positive") is True
    )
    return bool(mandatory and metrics["financial_checks_passed"] >= 5)


def add_valuation_and_risks(row: dict[str, Any]) -> None:
    fm = row["financial_metrics"]
    pm = row["price_metrics"]
    market_cap = pm.get("market_cap_krw")
    net_income = fm["net_income_krw_3y"][-1]
    equity = fm.get("equity_krw_latest")
    pe = safe_div(market_cap, net_income) if net_income is not None and net_income > 0 else None
    pb = safe_div(market_cap, equity) if equity is not None and equity > 0 else None
    fm["annual_pe_proxy_x"] = rounded(pe, 2)
    fm["annual_pb_proxy_x"] = rounded(pb, 2)
    risks = []
    if not pm.get("above_ma20"):
        risks.append("BELOW_MA20")
    if (fm.get("q1_operating_income_yoy_pct") or 0) < -30:
        risks.append("Q1_OPERATING_INCOME_YOY_BELOW_MINUS_30PCT")
    if (fm.get("roe_proxy_pct") or 0) < 5:
        risks.append("ROE_PROXY_BELOW_5PCT")
    if pe is not None and pe > 40:
        risks.append("ANNUAL_PE_PROXY_ABOVE_40X")
    if (pm.get("return_20d_pct") or 0) > 20:
        risks.append("RETURN_20D_ABOVE_20PCT")
    row["risk_flags"] = risks


def rank_score(row: dict[str, Any]) -> float:
    fm = row["financial_metrics"]
    pm = row["price_metrics"]
    financial = 45 * fm["financial_checks_passed"] / max(fm["financial_checks_available"], 1)
    operating_margin = min(max(fm["operating_margin_pct"] or 0, 0), 30) / 30 * 10
    roe = min(max(fm["roe_proxy_pct"] or 0, 0), 25) / 25 * 10
    debt = min(max(100 - (fm["debt_to_equity_pct"] or 100), 0), 100) / 100 * 5
    q1_yoy = fm.get("q1_operating_income_yoy_pct")
    earnings_momentum = min(max((q1_yoy if q1_yoy is not None else 0) + 30, 0), 80) / 80 * 5
    bottom = min(max(pm["drawdown_from_52w_high_pct"] - 25, 0), 35) / 35 * 10
    position = min(max(35 - pm["position_in_52w_range_pct"], 0), 35) / 35 * 5
    stabilization = 5 if pm["above_ma20"] else 0
    return round(
        financial + operating_margin + roe + debt + earnings_momentum + bottom + position + stabilization,
        2,
    )


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    columns = [
        "rank",
        "code",
        "name",
        "themes",
        "theme_summary",
        "dante_phase",
        "dante_detail",
        "dante_confidence",
        "overall_score",
        "financial_grade",
        "close",
        "drawdown_from_52w_high_pct",
        "position_in_52w_range_pct",
        "above_ma20",
        "bottom_status",
        "ma224",
        "gap_to_ma224_pct",
        "annual_year",
        "operating_margin_pct",
        "net_margin_pct",
        "debt_to_equity_pct",
        "roe_proxy_pct",
        "annual_pe_proxy_x",
        "annual_pb_proxy_x",
        "q1_operating_income_yoy_pct",
        "risk_flags",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            flat = {key: row.get(key) for key in ("rank", "code", "name", "overall_score", "financial_grade")}
            flat["themes"] = "|".join(row.get("themes") or [])
            flat["theme_summary"] = row.get("theme_summary")
            flat["dante_phase"] = row.get("dante", {}).get("phase")
            flat["dante_detail"] = row.get("dante", {}).get("detail")
            flat["dante_confidence"] = row.get("dante", {}).get("confidence")
            for key in columns:
                if key in row["price_metrics"]:
                    flat[key] = row["price_metrics"][key]
                if key in row["financial_metrics"]:
                    flat[key] = row["financial_metrics"][key]
            flat["risk_flags"] = "|".join(row.get("risk_flags") or [])
            writer.writerow({key: flat.get(key) for key in columns})


def run(args: argparse.Namespace) -> dict[str, Any]:
    as_of = latest_price_date(args.date)
    annual_year = str(int(as_of[:4]) - 1)
    quarter_year = as_of[:4]
    constituents = fetch_constituents(as_of)
    history, price_dates = load_price_history(as_of, args.lookback_days)

    price_screen = []
    missing_price = []
    for constituent in constituents:
        code = constituent["code"]
        if code not in history:
            missing_price.append(code)
            continue
        metrics = price_metrics(history[code])
        row = {**constituent, "price_metrics": metrics}
        if (
            metrics["drawdown_from_52w_high_pct"] is not None
            and metrics["position_in_52w_range_pct"] is not None
            and metrics["drawdown_from_52w_high_pct"] >= args.min_drawdown_pct
            and metrics["position_in_52w_range_pct"] <= args.max_range_position_pct
        ):
            price_screen.append(row)

    dart = DARTClient()
    analyzed = []
    errors = []
    for row in price_screen:
        try:
            corp_code = dart.to_corp_code(row["code"])
            if not corp_code:
                raise RuntimeError("DART_CORP_CODE_NOT_FOUND")
            annual_rows, annual_fs = cached_statement(dart, corp_code, annual_year, "11011")
            quarter_rows, quarter_fs = cached_statement(dart, corp_code, quarter_year, "11013")
            if not annual_rows or not quarter_rows:
                raise RuntimeError("DART_STATEMENT_NOT_FOUND")
            annual = extract_annual(annual_rows, annual_fs, annual_year)
            quarter = extract_quarter(quarter_rows, quarter_fs, quarter_year)
            metrics, checks = financial_metrics(annual, quarter)
            row["financial_metrics"] = metrics
            row["financial_checks"] = checks
            row["financial_grade"] = finance_grade(
                metrics["financial_checks_passed"], metrics["financial_checks_available"]
            )
            row["selected"] = screened_candidate(row)
            add_valuation_and_risks(row)
            add_theme_and_dante(row)
            row["overall_score"] = rank_score(row)
            analyzed.append(row)
        except Exception as exc:
            errors.append({"code": row["code"], "name": row["name"], "error": str(exc)})

    selected = sorted(
        (row for row in analyzed if row["selected"]),
        key=lambda row: (-row["overall_score"], row["price_metrics"]["position_in_52w_range_pct"]),
    )
    for index, row in enumerate(selected, 1):
        row["rank"] = index
    if args.shortlist_codes:
        shortlist_codes = [code.strip().zfill(6) for code in args.shortlist_codes.split(",") if code.strip()]
        selected_by_code = {row["code"]: row for row in selected}
        missing_shortlist = [code for code in shortlist_codes if code not in selected_by_code]
        if missing_shortlist:
            raise RuntimeError(f"SHORTLIST_CODE_NOT_SELECTED:{','.join(missing_shortlist)}")
        reported_selected = []
        for index, code in enumerate(shortlist_codes, 1):
            copied = dict(selected_by_code[code])
            copied["rank"] = index
            reported_selected.append(copied)
    else:
        reported_selected = selected
    all_analyzed = sorted(analyzed, key=lambda row: (-row["overall_score"], row["code"]))
    for index, row in enumerate(all_analyzed, 1):
        row["screen_rank"] = index

    result = {
        "status": "OK",
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "as_of_price_date": as_of,
        "universe": {
            "name": "KOSDAQ 150",
            "constituent_count": len(constituents),
            "composition_date": constituents and as_of,
        },
        "criteria": {
            "bottom_zone": {
                "drawdown_from_52w_high_gte_pct": args.min_drawdown_pct,
                "position_in_52w_range_lte_pct": args.max_range_position_pct,
                "lookback_calendar_days": args.lookback_days,
            },
            "financial_quality": {
                "required": [
                    f"{annual_year} annual operating profit and net income are positive",
                    f"{quarter_year} Q1 operating profit and net income are positive",
                    "debt-to-equity ratio is at most 100%",
                    f"{annual_year} operating cash flow is positive",
                    "at least 5 of 7 financial checks pass",
                ],
                "additional_checks": [
                    "2025 operating margin is at least 5%",
                    "2025 revenue is not more than 10% below 2023 revenue",
                    "operating profit and net income are positive in at least 2 of 3 annual periods",
                ],
            },
            "dante_phase_estimate": {
                "1구간": "MA224 아래 바닥 형성·안정화",
                "2구간": "저가 박스권 또는 MA224 돌파 시도",
                "3구간": "MA224 돌파 후 주상승",
                "4구간": "최근 급등 고점 이후 분배·ABC 조정",
                "note": "가격과 이동평균선 기반 정량 추정이며 거래량·수급·수동 파동 확인 전 확정값이 아님",
            },
        },
        "counts": {
            "price_dates_loaded": len(set(price_dates)),
            "missing_price": len(missing_price),
            "bottom_zone": len(price_screen),
            "financial_analyzed": len(analyzed),
            "selected": len(selected),
            "reported": min(len(reported_selected), args.top),
            "errors": len(errors),
        },
        "selected": reported_selected[: args.top],
        "all_bottom_zone": all_analyzed,
        "errors": errors,
        "sources": [
            {
                "name": "PLUS KOSDAQ150 portfolio",
                "url": COMPOSITION_PAGE_URL,
                "role": "current constituent universe",
            },
            {
                "name": "KRX OpenAPI local cache",
                "path": str(KRX_CACHE),
                "role": "daily OHLCV and market capitalization",
            },
            {
                "name": "OpenDART financial statement API",
                "url": "https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS003&apiId=2019020",
                "role": f"{annual_year} annual and {quarter_year} Q1 consolidated financial statements",
            },
            {
                "name": "AI단테 local rules",
                "path": str(ROOT.parent / "AI단테.md"),
                "role": "four-phase classification definition",
            },
        ],
        "limitations": [
            "A low 52-week range position does not prove that a durable market bottom has formed.",
            "Dante phase is a quantitative estimate and requires manual chart, volume, supply-demand, and wave confirmation.",
            "The screen excludes qualitative business risk and post-report events.",
            "Financial-company accounting and one-off gains can reduce comparability.",
        ],
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    output_suffix = "_enriched" if args.shortlist_codes else ""
    json_path = OUT_DIR / f"kosdaq150_bottom{output_suffix}_{as_of}.json"
    csv_path = OUT_DIR / f"kosdaq150_bottom{output_suffix}_{as_of}.csv"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(csv_path, reported_selected[: args.top])
    result["output_files"] = {"json": str(json_path), "csv": str(csv_path)}
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> int:
    configure_stdout()
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="Maximum price date in YYYYMMDD format")
    parser.add_argument("--lookback-days", type=int, default=370)
    parser.add_argument("--min-drawdown-pct", type=float, default=25.0)
    parser.add_argument("--max-range-position-pct", type=float, default=35.0)
    parser.add_argument("--top", type=int, default=15)
    parser.add_argument("--shortlist-codes", help="Comma-separated stock codes to report in the given order")
    args = parser.parse_args()
    try:
        result = run(args)
        print(
            json.dumps(
                {
                    "status": result["status"],
                    "as_of_price_date": result["as_of_price_date"],
                    "counts": result["counts"],
                    "output_files": result["output_files"],
                },
                ensure_ascii=False,
            )
        )
        return 0
    except Exception as exc:
        print(json.dumps({"status": "ERROR", "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
