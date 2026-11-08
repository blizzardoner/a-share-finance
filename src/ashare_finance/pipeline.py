from __future__ import annotations

import json
import math
import shutil
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Iterable


def _date(value: Any) -> str | None:
    if not value:
        return None
    return str(value)[:10]


def _num(value: Any) -> int | float | None:
    return value if isinstance(value, (int, float)) else None


def _positive_finite(value: Any) -> bool:
    number = _num(value)
    return number is not None and math.isfinite(float(number)) and number > 0


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


def year_windows(since: str, today: date) -> list[tuple[str, str]]:
    start = date.fromisoformat(since)
    windows: list[tuple[str, str]] = []
    for year in range(start.year, today.year + 1):
        left = start if year == start.year else date(year, 1, 1)
        right = today if year == today.year else date(year, 12, 31)
        windows.append((left.isoformat(), right.isoformat()))
    return windows


def ranking_end_year(as_of: date) -> int:
    return as_of.year - (1 if as_of.month >= 5 else 2)


def dividend_ranking_end_year(as_of: date) -> int:
    return as_of.year - (1 if as_of.month >= 9 else 2)


def dedupe_financials(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (row.get("code") or "", row.get("period") or "")
        if not all(key):
            continue
        current = latest.get(key)
        if current is None or (row.get("notice_date") or "") >= (current.get("notice_date") or ""):
            latest[key] = row
    return list(latest.values())


def filter_recent_companies(
    stocks: Iterable[dict[str, Any]],
    financials: Iterable[dict[str, Any]],
    cutoff: str,
) -> list[dict[str, Any]]:
    recent_codes = {
        row["code"]
        for row in financials
        if row.get("code") and (row.get("period") or "") >= cutoff
    }
    return [stock for stock in stocks if stock.get("code") in recent_codes]


def merge_stock_quotes(
    stocks: Iterable[dict[str, Any]],
    quotes: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    stock_list = list(stocks)
    if not stock_list:
        raise RuntimeError("公司目录为空")
    quote_map = {quote.get("code"): quote for quote in quotes if quote.get("code")}
    if not quote_map:
        raise RuntimeError("当前行情目录为空")
    directory_codes = {stock.get("code") for stock in stock_list if stock.get("code")}
    quote_codes = set(quote_map)
    overlap_codes = quote_codes & directory_codes
    missing_directory = sorted(quote_codes - directory_codes)
    if not overlap_codes:
        preview = ", ".join(missing_directory[:10])
        suffix = f" 等{len(missing_directory)}家" if len(missing_directory) > 10 else ""
        raise RuntimeError(f"行情代码缺少公司目录 {preview}{suffix}")
    if len(overlap_codes) / len(quote_codes) < 0.9:
        raise RuntimeError(f"行情与公司目录重合不足 {len(overlap_codes)}/{len(quote_codes)}")
    market_fields = ("price", "change_pct", "market_cap", "float_market_cap")
    return [
        {
            **stock,
            **{
                field: quote_map[stock["code"]].get(field)
                for field in market_fields
            },
        }
        for stock in stock_list
        if stock.get("code") in quote_map
    ]


def require_market_caps(stocks: Iterable[dict[str, Any]]) -> None:
    missing = [
        str(stock.get("code"))
        for stock in stocks
        if not _positive_finite(stock.get("market_cap"))
    ]
    if missing:
        preview = ", ".join(missing[:10])
        suffix = f" 等{len(missing)}家" if len(missing) > 10 else ""
        raise RuntimeError(f"当前A股市值缺失 {preview}{suffix}")


def _listed_for_full_window(stock: dict[str, Any], start_year: int) -> bool:
    listing_date = stock.get("listing_date")
    return bool(listing_date and listing_date <= f"{start_year}-01-01")


def build_dividend_rankings(
    stocks: Iterable[dict[str, Any]],
    dividends: Iterable[dict[str, Any]],
    end_year: int,
    periods: tuple[int, ...] = (1, 3, 5, 10),
) -> dict[int, list[dict[str, Any]]]:
    annual_cash: dict[str, dict[int, float]] = defaultdict(lambda: defaultdict(float))
    actions: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in dividends:
        period = row.get("period") or ""
        if "实施" not in str(row.get("progress") or "") or not period[:4].isdigit():
            continue
        cash_per_10 = _num(row.get("cash_per_10"))
        total_shares = _num(row.get("total_shares"))
        if not cash_per_10 or not total_shares:
            continue
        action_date = row.get("ex_date") or row.get("record_date") or "undated"
        key = (row.get("code"), period, action_date)
        current = actions.get(key)
        if current is None or (row.get("plan_date") or "") >= (current.get("plan_date") or ""):
            actions[key] = row
    for row in actions.values():
        period = str(row["period"])
        cash_per_10 = float(row["cash_per_10"])
        total_shares = float(row["total_shares"])
        annual_cash[str(row.get("code"))][int(period[:4])] += cash_per_10 / 10 * total_shares

    rankings: dict[int, list[dict[str, Any]]] = {}
    stock_list = list(stocks)
    for years in periods:
        start_year = end_year - years + 1
        rows: list[dict[str, Any]] = []
        for stock in stock_list:
            market_cap = _num(stock.get("market_cap"))
            if not market_cap or market_cap <= 0 or not _listed_for_full_window(stock, start_year):
                continue
            yearly = annual_cash.get(stock["code"], {})
            payouts = [yearly.get(year, 0.0) for year in range(start_year, end_year + 1)]
            rows.append({
                "code": stock["code"],
                "yield": sum(payouts) / years / market_cap * 100,
                "dividend_years": sum(value > 0 for value in payouts),
            })
        rankings[years] = sorted(rows, key=lambda row: (-row["yield"], row["code"]))
    return rankings


def _percentiles(rows: list[dict[str, Any]], key: str) -> dict[str, float]:
    ordered = sorted((float(row[key]), row["code"]) for row in rows)
    if len(ordered) == 1:
        return {rows[0]["code"]: 100.0}
    result: dict[str, float] = {}
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][0] == ordered[index][0]:
            end += 1
        percentile = (index + (end - index - 1) / 2) / (len(ordered) - 1) * 100
        for _, code in ordered[index:end]:
            result[code] = percentile
        index = end
    return result


def build_performance_rankings(
    stocks: Iterable[dict[str, Any]],
    annual_financials: Iterable[dict[str, Any]],
    end_year: int,
    periods: tuple[int, ...] = (1, 3, 5, 10),
) -> dict[int, list[dict[str, Any]]]:
    by_company: dict[str, dict[int, dict[str, Any]]] = defaultdict(dict)
    for row in annual_financials:
        period = row.get("period") or ""
        if period[:4].isdigit() and (
            row.get("report_type") == "年报" or period.endswith("-12-31")
        ):
            by_company[str(row.get("code"))][int(period[:4])] = row

    rankings: dict[int, list[dict[str, Any]]] = {}
    for years in periods:
        baseline_year = end_year - years
        candidates: list[dict[str, Any]] = []
        for stock in stocks:
            if not _listed_for_full_window(stock, baseline_year):
                continue
            company = by_company.get(stock["code"], {})
            required = [company.get(year) for year in range(baseline_year, end_year + 1)]
            if any(row is None for row in required):
                continue
            baseline, latest = required[0], required[-1]
            revenue_start, revenue_end = _num(baseline.get("revenue")), _num(latest.get("revenue"))
            profit_start, profit_end = _num(baseline.get("net_profit")), _num(latest.get("net_profit"))
            target = required[1:]
            roes = [_num(row.get("roe")) for row in target]
            profits = [_num(row.get("net_profit")) for row in target]
            if not revenue_start or not revenue_end or not profit_start or not profit_end:
                continue
            if (
                min(revenue_start, revenue_end, profit_start, profit_end) <= 0
                or any(value is None for value in roes)
                or any(value is None for value in profits)
            ):
                continue
            candidates.append({
                "code": stock["code"],
                "revenue_cagr": ((revenue_end / revenue_start) ** (1 / years) - 1) * 100,
                "profit_cagr": ((profit_end / profit_start) ** (1 / years) - 1) * 100,
                "avg_roe": sum(roes) / years,
                "profitable_years": sum(value > 0 for value in profits),
            })

        metric_weights = {
            "revenue_cagr": 0.3,
            "profit_cagr": 0.3,
            "avg_roe": 0.3,
            "profitable_years": 0.1,
        }
        percentiles = {key: _percentiles(candidates, key) for key in metric_weights} if candidates else {}
        for row in candidates:
            row["score"] = sum(percentiles[key][row["code"]] * weight for key, weight in metric_weights.items())
        rankings[years] = sorted(candidates, key=lambda row: (-row["score"], row["code"]))
    return rankings


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


def write_dataset(
    output: Path,
    documents: dict[str, dict[str, Any]],
    generated_at: str,
    rankings: dict[str, dict[int, list[dict[str, Any]]]] | None = None,
    ranking_end_year: int | dict[str, int] | None = None,
) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    companies_dir = output / "companies"
    rankings_dir = output / "rankings"
    if companies_dir.exists():
        shutil.rmtree(companies_dir)
    if rankings_dir.exists():
        shutil.rmtree(rankings_dir)
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
    for kind, by_period in (rankings or {}).items():
        end_year = ranking_end_year.get(kind) if isinstance(ranking_end_year, dict) else ranking_end_year
        for years, rows in by_period.items():
            _write_json(rankings_dir / f"{kind}-{years}.json", {
                "meta": {
                    "generated_at": generated_at,
                    "kind": kind,
                    "years": years,
                    "end_year": end_year,
                    "row_count": len(rows),
                },
                "rows": rows,
            })
    return metadata
