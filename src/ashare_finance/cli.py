from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .client import EastmoneyClient
from .pipeline import (
    build_company_documents,
    build_dividend_rankings,
    build_performance_rankings,
    dividend_ranking_end_year,
    dedupe_financials,
    filter_recent_companies,
    merge_stock_quotes,
    normalize_dividend,
    normalize_financial,
    normalize_stock,
    ranking_end_year,
    require_market_caps,
    write_dataset,
    year_windows,
)


FINANCIAL_COLUMNS = [
    "SECUCODE", "SECURITY_CODE", "SECURITY_NAME_ABBR", "ORG_TYPE", "REPORT_DATE",
    "REPORT_DATE_NAME", "REPORT_TYPE", "NOTICE_DATE", "CURRENCY", "EPSJB", "EPSKCJB",
    "BPS", "MGJYXJJE", "TOTALOPERATEREVE", "PARENTNETPROFIT", "KCFJCXSYJLR",
    "TOTALOPERATEREVETZ", "PARENTNETPROFITTZ", "KCFJCXSYJLRTZ", "DJD_TOI_YOY",
    "DJD_DPNP_YOY", "ROEJQ", "ROIC", "XSMLL", "XSJLL", "ZCFZL", "LD", "SD",
    "TOTAL_ASSETS_PK", "LIABILITY", "TOTAL_EQUITY_PK", "NETCASH_OPERATE_PK",
    "NETCASH_INVEST_PK", "NETCASH_FINANCE_PK", "TOTAL_SHARE", "TOTALDEPOSITS",
    "GROSSLOANS", "NONPERLOAN",
]

DIVIDEND_COLUMNS = [
    "SECUCODE", "SECURITY_CODE", "SECURITY_NAME_ABBR", "REPORT_DATE", "ASSIGN_PROGRESS",
    "IMPL_PLAN_PROFILE", "PLAN_NOTICE_DATE", "NOTICE_DATE", "EQUITY_RECORD_DATE",
    "EX_DIVIDEND_DATE", "PRETAX_BONUS_RMB", "BONUS_RATIO", "IT_RATIO", "BASIC_EPS",
    "BVPS", "TOTAL_SHARES",
]


def _finance_params(filter_text: str) -> dict[str, Any]:
    return {
        "reportName": "RPT_F10_FINANCE_MAINFINADATA",
        "columns": ",".join(FINANCIAL_COLUMNS),
        "quoteColumns": "",
        "filter": filter_text,
        "pageSize": 500,
        "sortTypes": "-1,1",
        "sortColumns": "REPORT_DATE,SECURITY_CODE",
        "source": "HSF10",
        "client": "PC",
    }


def _dividend_params(filter_text: str) -> dict[str, Any]:
    return {
        "reportName": "RPT_SHAREBONUS_DET",
        "columns": ",".join(DIVIDEND_COLUMNS),
        "filter": filter_text,
        "pageSize": 500,
        "sortTypes": "-1,1",
        "sortColumns": "REPORT_DATE,SECURITY_CODE",
        "source": "WEB",
        "client": "WEB",
    }


def fetch_selected(client: EastmoneyClient, stocks: list[dict[str, Any]], since: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    financials: list[dict[str, Any]] = []
    dividends: list[dict[str, Any]] = []
    for index, stock in enumerate(stocks, start=1):
        secucode = stock["secucode"]
        code = stock["code"]
        client.progress(f"公司 {index}/{len(stocks)} · {code} {stock['name']}")
        financials.extend(client.fetch_pages(client.SECURITIES_URL, _finance_params(f'(SECUCODE="{secucode}")(REPORT_DATE>=\'{since}\')'), f"财务 {code}"))
        dividends.extend(client.fetch_pages(client.DATACENTER_URL, _dividend_params(f'(SECURITY_CODE="{code}")(REPORT_DATE>=\'{since}\')'), f"分红 {code}"))
    return financials, dividends


def run_pipeline(output: Path, since: str = "2021-01-01", codes: set[str] | None = None, min_interval: float = 0.12) -> dict[str, Any]:
    client = EastmoneyClient(min_interval=min_interval, progress=print)
    as_of = date.today()
    performance_end_year = ranking_end_year(as_of)
    dividend_end_year = dividend_ranking_end_year(as_of)
    performance_start = f"{performance_end_year - 10}-01-01"
    dividend_start = f"{dividend_end_year - 9}-01-01"
    if codes:
        directory_rows = client.fetch_org_directory(codes)
        stocks = [
            normalize_stock(row)
            for row in directory_rows
            if "A股" in str(row.get("SECURITY_TYPE") or "")
            and row.get("SECUCODE", "").endswith((".SH", ".SZ", ".BJ"))
        ]
        stocks = [stock for stock in stocks if stock["code"] in codes]
        missing = sorted(codes - {stock["code"] for stock in stocks})
        if missing:
            raise ValueError(f"非当前A股代码: {', '.join(missing)}")
        quote_rows = [normalize_stock(row) for row in client.fetch_selected_stock_quotes(codes)]
        stocks = merge_stock_quotes(stocks, quote_rows)
        all_financial_raw, all_dividend_raw = fetch_selected(client, stocks, performance_start)
        financial_raw = [row for row in all_financial_raw if str(row.get("REPORT_DATE") or "") >= since]
        historical_annual_raw = all_financial_raw
        dividend_raw = [row for row in all_dividend_raw if str(row.get("REPORT_DATE") or "") >= dividend_start]
    else:
        directory_rows = client.fetch_org_directory()
        stocks = [
            normalize_stock(row)
            for row in directory_rows
            if "A股" in str(row.get("SECURITY_TYPE") or "")
            and row.get("SECUCODE", "").endswith((".SH", ".SZ", ".BJ"))
            and str(row.get("SECURITY_CODE") or "").isdigit()
            and row.get("LISTING_DATE")
            and not str(row.get("SECURITY_NAME_ABBR") or "").endswith("退")
        ]
        quote_rows = [normalize_stock(row) for row in client.fetch_stocks()]
        stocks = merge_stock_quotes(stocks, quote_rows, min_market_cap_coverage=0.9)
        financial_raw = []
        dividend_raw = []
        for start_date, end_date in year_windows(since, as_of):
            date_filter = f"(REPORT_DATE>='{start_date}')(REPORT_DATE<='{end_date}')"
            financial_raw.extend(client.fetch_pages(
                client.SECURITIES_URL,
                _finance_params(date_filter),
                f"全市场财务 {start_date[:4]}",
            ))
        for start_date, end_date in year_windows(dividend_start, as_of):
            date_filter = f"(REPORT_DATE>='{start_date}')(REPORT_DATE<='{end_date}')"
            dividend_raw.extend(client.fetch_pages(
                client.DATACENTER_URL,
                _dividend_params(date_filter),
                f"全市场分红 {start_date[:4]}",
            ))
        historical_annual_raw = []
        for year in range(int(performance_start[:4]), int(since[:4])):
            annual_filter = (
                f"(REPORT_DATE>='{year}-01-01')(REPORT_DATE<='{year}-12-31')"
                '(REPORT_TYPE="年报")'
            )
            historical_annual_raw.extend(client.fetch_pages(
                client.SECURITIES_URL,
                _finance_params(annual_filter),
                f"历史年报 {year}",
            ))
    financials = dedupe_financials(normalize_financial(row) for row in financial_raw)
    dividends = [normalize_dividend(row) for row in dividend_raw]
    annual_financials = dedupe_financials([
        *(normalize_financial(row) for row in historical_annual_raw),
        *(row for row in financials if row.get("report_type") == "年报" or str(row.get("period") or "").endswith("-12-31")),
    ])
    if not codes:
        cutoff = f"{as_of.year - 1}-09-30"
        stocks = filter_recent_companies(stocks, financials, cutoff)
        print(f"近期财报筛选: {len(stocks)} 家（截止线 {cutoff}）")
    require_market_caps(stocks)
    documents = build_company_documents(
        stocks,
        financials,
        dividends,
    )
    rankings = {
        "dividend": build_dividend_rankings(stocks, dividends, dividend_end_year),
        "performance": build_performance_rankings(stocks, annual_financials, performance_end_year),
    }
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return write_dataset(
        output, documents, generated_at,
        rankings=rankings,
        ranking_end_year={"dividend": dividend_end_year, "performance": performance_end_year},
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="抓取东方财富A股财务与分红数据，生成GitHub Pages静态分片")
    parser.add_argument("--output", default="public/data", help="输出目录")
    parser.add_argument("--since", default="2021-01-01", help="财务数据起始日期")
    parser.add_argument("--codes", default="", help="仅抓取逗号分隔的股票代码，用于本地验收")
    parser.add_argument("--min-interval", type=float, default=0.12, help="请求最小间隔秒数")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    codes = {code.strip() for code in args.codes.split(",") if code.strip()} or None
    result = run_pipeline(Path(args.output), args.since, codes, args.min_interval)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
