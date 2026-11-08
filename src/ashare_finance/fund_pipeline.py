from __future__ import annotations

import math
import os
import hashlib
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .fund_client import FundClient
from .funds import (
    build_holding_events,
    build_stock_consensus,
    normalize_portfolio_name,
    parse_fund_report,
    report_identity,
    select_research_funds,
    share_class_preference,
    write_fund_dataset,
)


_ALLOWED_TYPES = {"股票型", "混合型-偏股", "混合型-灵活"}
_EXCLUDED_PARTS = ("指数", "ETF", "联接", "量化", "FOF", "QDII", "港股", "沪港深")


def _expected_latest_report_period(as_of: date) -> str:
    if as_of >= date(as_of.year, 10, 31):
        return f"{as_of.year}-09-30"
    if as_of >= date(as_of.year, 8, 31):
        return f"{as_of.year}-06-30"
    if as_of >= date(as_of.year, 4, 30):
        return f"{as_of.year}-03-31"
    return f"{as_of.year - 1}-09-30"


def _preselect_candidates(
    rows: Iterable[dict[str, Any]],
    catalog: dict[str, dict[str, Any]],
    limit: int,
    as_of: date,
) -> list[dict[str, Any]]:
    eligible_by_portfolio: dict[str, dict[str, Any]] = {}
    for row in rows:
        code = str(row.get("code") or "")
        name = str(row.get("name") or "")
        fund_type = str((catalog.get(code) or {}).get("fund_type") or "")
        inception = str(row.get("inception_date") or "")[:10]
        if (
            fund_type not in _ALLOWED_TYPES
            or any(part.lower() in name.lower() for part in _EXCLUDED_PARTS)
            or row.get("return_3y") is None
            or not inception
            or (as_of - date.fromisoformat(inception)).days < 365 * 3
        ):
            continue
        portfolio_name = normalize_portfolio_name(name)
        current = eligible_by_portfolio.get(portfolio_name)
        priority = (share_class_preference(name), -float(row["return_3y"]), code)
        if current is None or priority < current["priority"]:
            eligible_by_portfolio[portfolio_name] = {"row": row, "priority": priority}
    eligible = [entry["row"] for entry in eligible_by_portfolio.values()]
    eligible.sort(key=lambda row: (-float(row.get("return_3y") or -math.inf), str(row.get("code") or "")))
    return eligible[:limit]


def _corporate_actions(dividends: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    latest: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in dividends:
        code = str(row.get("code") or "")
        ex_date = str(row.get("ex_date") or "")[:10]
        period = str(row.get("period") or "")[:10]
        if not code or not ex_date:
            continue
        key = (code, period, ex_date)
        current = latest.get(key)
        if current is None or str(row.get("plan_date") or "") >= str(current.get("plan_date") or ""):
            latest[key] = row
    result: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in latest.values():
        code = str(row.get("code") or "")
        result[code].append(row)
    return dict(result)


def run_fund_pipeline(
    *,
    output: Path,
    stocks: list[dict[str, Any]],
    dividends: list[dict[str, Any]],
    client: Any | None = None,
    target_count: int = 24,
    candidate_limit: int = 96,
    report_limit: int = 4,
    as_of: date | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    if target_count <= 0 or candidate_limit < target_count:
        raise ValueError("基金研究池参数无效")

    cache_dir = Path(os.environ.get("ASHARE_FUND_CACHE_DIR", ".cache/fund-reports"))
    fund_client = client or FundClient(progress=print, cache_dir=cache_dir)
    today = as_of or date.today()
    expected_latest_period = _expected_latest_report_period(today)
    catalog_rows = fund_client.fetch_catalog()
    catalog = {row["code"]: row for row in catalog_rows if row.get("code")}
    rank_rows = fund_client.fetch_rankings()
    candidates = _preselect_candidates(rank_rows, catalog, candidate_limit, today)
    if len(candidates) < target_count:
        raise RuntimeError(f"基金候选池不足 {len(candidates)}/{target_count}")

    details: dict[str, dict[str, Any]] = {}
    selected: list[dict[str, Any]] = []
    batch_size = target_count * 2
    reports_by_fund: dict[str, list[dict[str, Any]]] = {}
    valid_funds: list[dict[str, Any]] = []
    processed_codes: set[str] = set()
    report_attempts = 0

    def load_reports(fund: dict[str, Any]) -> None:
        nonlocal report_attempts
        code = fund["code"]
        report_attempts += 1
        try:
            notices = sorted(fund_client.fetch_notices(code), key=lambda row: row["publish_date"], reverse=True)[:report_limit]
            reports: list[dict[str, Any]] = []
            for notice in notices:
                document: dict[str, Any] | None = None
                try:
                    document = fund_client.fetch_notice_content(notice["art_code"])
                    reports.append(parse_fund_report(
                        fund_code=code,
                        title=document["title"],
                        content=document["content"],
                        announcement_date=notice["publish_date"],
                        source_url=document.get("pdf_url") or notice["pdf_url"],
                        known_manager_id=fund.get("manager_id"),
                        known_manager_name=fund.get("manager_name"),
                        known_managers=fund.get("managers"),
                    ))
                except Exception as error:
                    title = (document or {}).get("title") or notice["title"]
                    content = (document or {}).get("content") or ""
                    report_period, report_type = report_identity(title)
                    reports.append({
                        "fund_code": code,
                        "title": title,
                        "report_period": report_period,
                        "report_type": report_type,
                        "scope": None,
                        "announcement_date": notice["publish_date"],
                        "source_url": (document or {}).get("pdf_url") or notice["pdf_url"],
                        "source_hash": hashlib.sha256(content.encode("utf-8")).hexdigest() if content else None,
                        "parser_version": "fund-report-v1",
                        "parse_status": "parse_failed" if content else "report_missing",
                        "parse_error": str(error),
                        "manager_id": None,
                        "manager_name": None,
                        "manager_ids": [],
                        "manager_names": [],
                        "disclosed_nav_pct": None,
                        "holdings": [],
                    })
            reports = sorted(reports, key=lambda row: row["report_period"])
            parsed_reports = [report for report in reports if report.get("parse_status") == "parsed"]
            if len(parsed_reports) < 2:
                raise RuntimeError("可解析定期报告少于2份")
            if any(report.get("parse_status") != "parsed" for report in reports[-2:]):
                raise RuntimeError("最新两期定期报告存在解析失败")
            if reports[-1]["report_period"] < expected_latest_period:
                raise RuntimeError(f"最新报告期陈旧 {reports[-1]['report_period']}，应至少为 {expected_latest_period}")
            reports_by_fund[code] = reports
        except Exception as error:
            print(f"基金报告跳过 {code}: {error}")
        print(f"基金报告: {report_attempts}/{len(processed_codes)}")

    fetched_count = 0
    while len(valid_funds) < target_count and fetched_count < len(candidates):
        batch_end = min(fetched_count + batch_size, len(candidates))
        for offset, row in enumerate(candidates[fetched_count:batch_end], start=fetched_count + 1):
            code = row["code"]
            try:
                details[code] = fund_client.fetch_details(code)
            except Exception as error:
                print(f"基金详情跳过 {code}: {error}")
            if offset == batch_end or offset % 20 == 0:
                print(f"基金详情: {offset}/{len(candidates)}")
        fetched_count = batch_end
        selected = select_research_funds(
            candidates[:fetched_count],
            catalog,
            details,
            limit=fetched_count,
            as_of=today.isoformat(),
        )
        for fund in selected:
            code = fund["code"]
            if code in processed_codes:
                continue
            processed_codes.add(code)
            load_reports(fund)
        valid_funds = [fund for fund in selected if fund["code"] in reports_by_fund][:target_count]

    if len(valid_funds) < target_count:
        raise RuntimeError(f"基金报告覆盖不足 {len(valid_funds)}/{target_count}")

    actions = _corporate_actions(dividends)
    events: list[dict[str, Any]] = []
    for fund in valid_funds:
        parsed_reports = [report for report in reports_by_fund[fund["code"]] if report.get("parse_status") == "parsed"]
        events.extend(build_holding_events(fund, parsed_reports, actions))
    consensus = build_stock_consensus(events, stocks)
    timestamp = generated_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return write_fund_dataset(output, valid_funds, reports_by_fund, events, consensus, timestamp)
