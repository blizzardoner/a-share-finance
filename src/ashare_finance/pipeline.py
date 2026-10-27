from __future__ import annotations

import json
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


def _date(value: Any) -> str | None:
    if not value:
        return None
    return str(value)[:10]


def _num(value: Any) -> int | float | None:
    return value if isinstance(value, (int, float)) else None


def _exchange(code: str, market_id: Any = None) -> str:
    if code.startswith(("4", "8", "92")):
        return "BJ"
    if market_id == 1 or code.startswith(("5", "6", "9")):
        return "SH"
    return "SZ"


def normalize_stock(row: dict[str, Any]) -> dict[str, Any]:
    is_org_row = bool(row.get("SECURITY_CODE"))
    code = str(row.get("SECURITY_CODE") or row.get("f12") or "").zfill(6)
    secucode = row.get("SECUCODE")
    exchange = str(secucode).split(".")[-1] if secucode and "." in str(secucode) else _exchange(code, row.get("f13"))
    result = {
        "code": code,
        "secucode": f"{code}.{exchange}",
        "name": row.get("SECURITY_NAME_ABBR") or row.get("f14") or "",
        "exchange": exchange,
        "industry": row.get("EM2016") or row.get("f100") or None,
        "price": _num(row.get("f2")),
        "change_pct": _num(row.get("f3")),
        "market_cap": _num(row.get("f20")),
        "float_market_cap": _num(row.get("f21")),
    }
    if is_org_row:
        result.update({
            "security_type": row.get("SECURITY_TYPE") or None,
            "market_name": row.get("TRADE_MARKET") or None,
            "listing_date": _date(row.get("LISTING_DATE")),
            "pinyin": row.get("EXPAND_NAME_PINYIN") or None,
        })
    return result


FINANCIAL_FIELDS = {
    "period": ("REPORT_DATE", _date),
    "period_name": ("REPORT_DATE_NAME", lambda x: x or None),
    "report_type": ("REPORT_TYPE", lambda x: x or None),
    "notice_date": ("NOTICE_DATE", _date),
    "org_type": ("ORG_TYPE", lambda x: x or None),
    "currency": ("CURRENCY", lambda x: x or "CNY"),
    "revenue": ("TOTALOPERATEREVE", _num),
    "net_profit": ("PARENTNETPROFIT", _num),
    "adjusted_profit": ("KCFJCXSYJLR", _num),
    "revenue_yoy": ("TOTALOPERATEREVETZ", _num),
    "net_profit_yoy": ("PARENTNETPROFITTZ", _num),
    "adjusted_profit_yoy": ("KCFJCXSYJLRTZ", _num),
    "quarter_revenue_yoy": ("DJD_TOI_YOY", _num),
    "quarter_net_profit_yoy": ("DJD_DPNP_YOY", _num),
    "eps": ("EPSJB", _num),
    "adjusted_eps": ("EPSKCJB", _num),
    "bps": ("BPS", _num),
    "operating_cash_per_share": ("MGJYXJJE", _num),
    "roe": ("ROEJQ", _num),
    "roic": ("ROIC", _num),
    "gross_margin": ("XSMLL", _num),
    "net_margin": ("XSJLL", _num),
    "debt_ratio": ("ZCFZL", _num),
    "current_ratio": ("LD", _num),
    "quick_ratio": ("SD", _num),
    "assets": ("TOTAL_ASSETS_PK", _num),
    "liabilities": ("LIABILITY", _num),
    "equity": ("TOTAL_EQUITY_PK", _num),
    "operating_cash_flow": ("NETCASH_OPERATE_PK", _num),
    "investing_cash_flow": ("NETCASH_INVEST_PK", _num),
    "financing_cash_flow": ("NETCASH_FINANCE_PK", _num),
    "total_shares": ("TOTAL_SHARE", _num),
    "total_deposits": ("TOTALDEPOSITS", _num),
    "gross_loans": ("GROSSLOANS", _num),
    "nonperforming_loan_ratio": ("NONPERLOAN", _num),
}


def normalize_financial(row: dict[str, Any]) -> dict[str, Any]:
    result = {"code": str(row.get("SECURITY_CODE") or "").zfill(6)}
    result.update({target: transform(row.get(source)) for target, (source, transform) in FINANCIAL_FIELDS.items()})
    return result


def normalize_dividend(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "code": str(row.get("SECURITY_CODE") or "").zfill(6),
        "period": _date(row.get("REPORT_DATE")),
        "progress": row.get("ASSIGN_PROGRESS") or None,
        "plan": row.get("IMPL_PLAN_PROFILE") or None,
        "plan_date": _date(row.get("PLAN_NOTICE_DATE") or row.get("NOTICE_DATE")),
        "record_date": _date(row.get("EQUITY_RECORD_DATE")),
        "ex_date": _date(row.get("EX_DIVIDEND_DATE")),
        "cash_per_10": _num(row.get("PRETAX_BONUS_RMB")),
        "bonus_per_10": _num(row.get("BONUS_RATIO")),
        "transfer_per_10": _num(row.get("IT_RATIO")),
        "eps": _num(row.get("BASIC_EPS")),
        "bps": _num(row.get("BVPS")),
        "total_shares": _num(row.get("TOTAL_SHARES")),
    }


def build_company_documents(
    stocks: Iterable[dict[str, Any]],
    financials: Iterable[dict[str, Any]],
    dividends: Iterable[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    stock_map = {stock["code"]: stock for stock in stocks if stock.get("code")}
    finance_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
    dividend_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in financials:
        if row.get("code") in stock_map and row.get("period"):
            finance_map[row["code"]].append(row)
    for row in dividends:
        if row.get("code") in stock_map and row.get("period"):
            dividend_map[row["code"]].append(row)
    documents: dict[str, dict[str, Any]] = {}
    for code in sorted(stock_map):
        documents[code] = {
            "stock": stock_map[code],
            "financials": sorted(finance_map[code], key=lambda item: item["period"], reverse=True),
            "dividends": sorted(dividend_map[code], key=lambda item: (item["period"], item.get("ex_date") or ""), reverse=True),
        }
    return documents


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def write_dataset(output: Path, documents: dict[str, dict[str, Any]], generated_at: str) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    companies_dir = output / "companies"
    if companies_dir.exists():
        shutil.rmtree(companies_dir)
    stocks = [documents[code]["stock"] for code in sorted(documents)]
    metadata = {
        "generated_at": generated_at,
        "company_count": len(stocks),
        "financial_row_count": sum(len(doc["financials"]) for doc in documents.values()),
        "dividend_row_count": sum(len(doc["dividends"]) for doc in documents.values()),
        "source": "东方财富公开数据接口",
    }
    _write_json(output / "stocks.json", {"meta": metadata, "stocks": stocks})
    _write_json(output / "meta.json", metadata)
    for code, document in documents.items():
        _write_json(companies_dir / code[:2] / f"{code}.json", {"meta": metadata, **document})
    return metadata
