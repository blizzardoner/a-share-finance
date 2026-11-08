from __future__ import annotations

import json
import hashlib
import re
import shutil
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Iterable


_ALLOWED_FUND_TYPES = {"股票型", "混合型-偏股", "混合型-灵活"}
_EXCLUDED_NAME_PARTS = ("指数", "ETF", "联接", "量化", "FOF", "QDII", "港股", "沪港深")
_HOLDING_ROW = re.compile(r"^\s*(\d+)\s+(\d{5,6})\s+(.+?)\s+([\d.]+)\s*$")
_GROUPED_INTEGER = r"(?:\d{1,3}(?:,\d{3})+|\d+)"
_HOLDING_VALUES = (
    re.compile(rf"^(?P<name>.+?)\s+(?P<shares>{_GROUPED_INTEGER})\s+(?P<value>{_GROUPED_INTEGER}\.\d{{2}})$"),
    re.compile(rf"^(?P<name>.+?)\s+(?P<shares>{_GROUPED_INTEGER})(?P<value>{_GROUPED_INTEGER}\.\d{{2}})$"),
)


def normalize_portfolio_name(name: str) -> str:
    text = re.sub(r"\s+", "", str(name or ""))
    text = re.sub(r"(?:人民币)?[（(]?[A-EIY][类]?[）)]?$", "", text, flags=re.IGNORECASE)
    return text.rstrip("-_ ")


def _percentiles(values: list[float]) -> list[float]:
    if len(values) <= 1:
        return [100.0] * len(values)
    ordered = sorted((value, index) for index, value in enumerate(values))
    result = [0.0] * len(values)
    left = 0
    while left < len(ordered):
        right = left + 1
        while right < len(ordered) and ordered[right][0] == ordered[left][0]:
            right += 1
        percentile = (left + (right - left - 1) / 2) / (len(ordered) - 1) * 100
        for _, index in ordered[left:right]:
            result[index] = percentile
        left = right
    return result


def share_class_preference(name: str) -> int:
    if re.search(r"(?:人民币)?[（(]?A[类]?[）)]?$", name, re.IGNORECASE):
        return 0
    if normalize_portfolio_name(name) == re.sub(r"\s+", "", name):
        return 1
    return 2


def report_identity(title: str) -> tuple[str, str]:
    match = re.search(r"(20\d{2})年", title)
    if not match:
        raise ValueError(f"无法识别基金报告年份: {title}")
    year = match.group(1)
    if "第1季度" in title:
        return f"{year}-03-31", "Q1"
    if "第3季度" in title:
        return f"{year}-09-30", "Q3"
    if "中期报告" in title or "半年度报告" in title:
        return f"{year}-06-30", "H1"
    if "年度报告" in title:
        return f"{year}-12-31", "FY"
    raise ValueError(f"无法识别基金报告类型: {title}")


def parse_fund_report(
    *,
    fund_code: str,
    title: str,
    content: str,
    announcement_date: str,
    source_url: str,
    known_manager_id: str | None = None,
    known_manager_name: str | None = None,
    known_managers: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    report_period, report_type = report_identity(title)
    scope_patterns = (
        (re.compile(r"前十名股票投资明\s*细"), "TOP10"),
        (re.compile(r"所有股票投资明\s*细"), "FULL"),
    )
    markers = [
        (match, scope)
        for pattern, scope in scope_patterns
        for match in pattern.finditer(content)
    ]
    if not markers:
        raise ValueError(f"基金报告未找到股票披露范围: {title}")
    marker_match, scope = max(markers, key=lambda item: item[0].start())
    start = marker_match.end()
    next_section = re.search(r"\n\s*\d+\.\d+\s+", content[start:])
    section = content[start:start + next_section.start()] if next_section else content[start:]
    candidate_rows = [
        (int(match.group(1)), match.group(2))
        for line in section.splitlines()
        if (match := re.match(r"^\s*(\d+)\s+(\d{5,6})\b", line))
    ]
    holdings: list[dict[str, Any]] = []
    for line in section.splitlines():
        match = _HOLDING_ROW.match(line)
        if not match:
            continue
        rank, stock_code, value_columns, nav_ratio = match.groups()
        values = next((pattern.match(value_columns) for pattern in _HOLDING_VALUES if pattern.match(value_columns)), None)
        if not values:
            continue
        stock_name = values.group("name").strip()
        shares = values.group("shares")
        market_value = values.group("value")
        holdings.append({
            "rank": int(rank),
            "stock_code": stock_code,
            "stock_name": stock_name,
            "shares": float(shares.replace(",", "")),
            "market_value": float(market_value.replace(",", "")),
            "nav_ratio": float(nav_ratio),
        })
    if not holdings:
        raise ValueError(f"基金报告持仓表为空或无法解析: {title}")
    parsed_rows = [(row["rank"], row["stock_code"]) for row in holdings]
    if parsed_rows != candidate_rows:
        raise ValueError(f"基金报告持仓表部分解析: {len(parsed_rows)}/{len(candidate_rows)} {title}")
    ranks = {row["rank"] for row in holdings}
    if min(ranks) == 1 and ranks != set(range(1, max(ranks) + 1)):
        raise ValueError(f"基金报告持仓排名不连续: {title}")
    manager_candidates = list(known_managers or [])
    if known_manager_id and known_manager_name and not any(item.get("id") == known_manager_id for item in manager_candidates):
        manager_candidates.append({"id": known_manager_id, "name": known_manager_name})
    confirmed_managers: list[dict[str, str]] = []
    manager_marker = "基金经理（或基金经理小组）简介"
    if manager_candidates and manager_marker in content:
        manager_start = content.rindex(manager_marker) + len(manager_marker)
        manager_end_match = re.search(r"\n\s*\d+\.\d+\s+", content[manager_start:])
        manager_section = (
            content[manager_start:manager_start + manager_end_match.start()]
            if manager_end_match else content[manager_start:]
        )
        confirmed_managers = [
            {"id": str(item["id"]), "name": str(item["name"])}
            for item in manager_candidates
            if item.get("id") and item.get("name") and str(item["name"]) in manager_section
        ]
    return {
        "fund_code": fund_code,
        "title": title,
        "report_period": report_period,
        "report_type": report_type,
        "scope": scope,
        "announcement_date": str(announcement_date)[:10],
        "source_url": source_url,
        "manager_id": confirmed_managers[0]["id"] if confirmed_managers else None,
        "manager_name": confirmed_managers[0]["name"] if confirmed_managers else None,
        "manager_ids": [item["id"] for item in confirmed_managers],
        "manager_names": [item["name"] for item in confirmed_managers],
        "source_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "parser_version": "fund-report-v1",
        "parse_status": "parsed",
        "disclosed_nav_pct": round(sum(row["nav_ratio"] for row in holdings), 6),
        "holdings": holdings,
    }


_EVENT_CONFIDENCE = {
    "CONFIRMED_NEW": 1.0,
    "CONFIRMED_EXIT": 1.0,
    "CONFIRMED_ADD": 0.85,
    "CONFIRMED_REDUCE": 0.85,
    "UNCHANGED": 0.75,
    "ENTERED_TOP10": 0.55,
    "DROPPED_FROM_TOP10": 0.45,
    "CENSORED": 0.0,
}


def _corporate_action_factor(
    stock_code: str,
    period_start: str,
    period_end: str,
    corporate_actions: dict[str, list[dict[str, Any]]],
) -> float:
    factor = 1.0
    for action in corporate_actions.get(stock_code, []):
        ex_date = str(action.get("ex_date") or "")[:10]
        if not (period_start < ex_date <= period_end):
            continue
        bonus = float(action.get("bonus_per_10") or 0)
        transfer = float(action.get("transfer_per_10") or 0)
        factor *= 1 + (bonus + transfer) / 10
    return factor


def build_holding_events(
    fund: dict[str, Any],
    reports: Iterable[dict[str, Any]],
    corporate_actions: dict[str, list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    ordered = sorted(reports, key=lambda report: report["report_period"])
    if len(ordered) < 2:
        return []
    previous, current = ordered[-2:]
    previous_map = {row["stock_code"]: row for row in previous.get("holdings", [])}
    current_map = {row["stock_code"]: row for row in current.get("holdings", [])}
    current_manager_ids = list(current.get("manager_ids") or ([current["manager_id"]] if current.get("manager_id") else []))
    current_manager_names = list(current.get("manager_names") or ([current["manager_name"]] if current.get("manager_name") else []))
    actions = corporate_actions or {}
    events: list[dict[str, Any]] = []
    for stock_code in sorted(previous_map.keys() | current_map.keys()):
        before = previous_map.get(stock_code)
        after = current_map.get(stock_code)
        share_factor = _corporate_action_factor(
            stock_code,
            previous["report_period"],
            current["report_period"],
            actions,
        )
        previous_adjusted = float(before["shares"]) * share_factor if before else None
        current_shares = float(after["shares"]) if after else None
        if before and after:
            tolerance = max(1e-6, abs(previous_adjusted) * 1e-6)
            if current_shares > previous_adjusted + tolerance:
                event_type = "CONFIRMED_ADD"
            elif current_shares < previous_adjusted - tolerance:
                event_type = "CONFIRMED_REDUCE"
            else:
                event_type = "UNCHANGED"
        elif after:
            if previous.get("scope") == "FULL":
                event_type = "CONFIRMED_NEW"
            elif current.get("scope") == "TOP10":
                event_type = "ENTERED_TOP10"
            else:
                event_type = "CENSORED"
        else:
            if current.get("scope") == "FULL":
                event_type = "CONFIRMED_EXIT"
            elif before and int(before.get("rank") or 999999) <= 10:
                event_type = "DROPPED_FROM_TOP10"
            else:
                event_type = "CENSORED"
        visible = after or before or {}
        events.append({
            "fund_code": fund.get("code"),
            "fund_name": fund.get("name"),
            "manager_id": current.get("manager_id"),
            "manager_name": current.get("manager_name"),
            "manager_ids": current_manager_ids,
            "manager_names": current_manager_names,
            "manager_attribution": "report_confirmed" if current_manager_ids else "unknown",
            "screen_score": fund.get("screen_score"),
            "stock_code": stock_code,
            "stock_name": visible.get("stock_name"),
            "event_type": event_type,
            "confidence": _EVENT_CONFIDENCE[event_type],
            "period_start": previous["report_period"],
            "report_period": current["report_period"],
            "announcement_date": current.get("announcement_date"),
            "previous_scope": previous.get("scope"),
            "current_scope": current.get("scope"),
            "previous_shares": float(before["shares"]) if before else None,
            "previous_shares_adjusted": previous_adjusted,
            "current_shares": current_shares,
            "shares_change": current_shares - previous_adjusted if before and after else None,
            "previous_nav_ratio": before.get("nav_ratio") if before else None,
            "current_nav_ratio": after.get("nav_ratio") if after else None,
            "share_factor": share_factor,
            "source_url": current.get("source_url"),
            "previous_source_url": previous.get("source_url"),
        })
    return events


_EVENT_STRENGTH = {
    "CONFIRMED_NEW": 1.0,
    "CONFIRMED_ADD": 0.8,
    "ENTERED_TOP10": 0.5,
    "CONFIRMED_EXIT": -1.0,
    "CONFIRMED_REDUCE": -0.8,
    "DROPPED_FROM_TOP10": -0.4,
}


def build_stock_consensus(
    events: Iterable[dict[str, Any]],
    stocks: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    stock_map = {str(stock.get("code")): stock for stock in stocks if stock.get("code")}
    event_list = [event for event in events if event.get("event_type") in _EVENT_STRENGTH]
    if not event_list:
        return []
    latest_period = max(str(event.get("report_period") or "") for event in event_list)
    grouped: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in event_list:
        code = str(event.get("stock_code") or "")
        if event.get("report_period") == latest_period and code in stock_map:
            grouped[code].append(event)

    rows: list[dict[str, Any]] = []
    for code, stock_events in grouped.items():
        manager_contributions: dict[str, float] = {}
        verified_manager_ids: set[str] = set()
        for event in stock_events:
            manager_ids = [str(value).strip() for value in (event.get("manager_ids") or []) if str(value).strip()]
            if not manager_ids and event.get("manager_id"):
                manager_ids = [str(event["manager_id"]).strip()]
            contribution = (
                float(event.get("screen_score") or 0)
                * float(event.get("confidence") or 0)
                * _EVENT_STRENGTH[event["event_type"]]
            )
            contribution_keys = [f"manager:{manager_id}" for manager_id in manager_ids] or ["unknown"]
            contribution /= len(contribution_keys)
            verified_manager_ids.update(manager_ids)
            for manager_key in contribution_keys:
                prior = manager_contributions.get(manager_key)
                if prior is None or abs(contribution) > abs(prior):
                    manager_contributions[manager_key] = contribution
        stock = stock_map[code]
        score = round(sum(manager_contributions.values()), 6)
        rows.append({
            "code": code,
            "name": stock.get("name") or stock_events[0].get("stock_name"),
            "industry": stock.get("industry"),
            "market_cap": stock.get("market_cap"),
            "report_period": latest_period,
            "consensus_score": score,
            "direction": "positive" if score > 0 else "negative" if score < 0 else "mixed",
            "independent_manager_count": len(verified_manager_ids),
            "unverified_manager_event_count": sum(not (event.get("manager_ids") or event.get("manager_id")) for event in stock_events),
            "fund_count": len({str(event.get("fund_code")) for event in stock_events}),
            "confirmed_event_count": sum(event["event_type"].startswith("CONFIRMED_") for event in stock_events),
            "events": sorted(stock_events, key=lambda event: (-abs(float(event.get("screen_score") or 0)), str(event.get("fund_code") or ""))),
        })
    return sorted(rows, key=lambda row: (-abs(row["consensus_score"]), row["code"]))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def write_fund_dataset(
    output: Path,
    funds: list[dict[str, Any]],
    reports_by_fund: dict[str, list[dict[str, Any]]],
    events: list[dict[str, Any]],
    consensus: list[dict[str, Any]],
    generated_at: str,
) -> dict[str, Any]:
    invalid_codes = [str(fund.get("code") or "") for fund in funds if not re.fullmatch(r"\d{6}", str(fund.get("code") or ""))]
    if invalid_codes:
        raise ValueError(f"基金代码必须为六位数字: {', '.join(invalid_codes)}")
    published_reports = [reports_by_fund.get(str(fund["code"]), []) for fund in funds]
    fund_dir = output / "funds"
    if fund_dir.exists():
        shutil.rmtree(fund_dir)
    metadata = {
        "generated_at": generated_at,
        "fund_count": len(funds),
        "report_count": sum(len(reports) for reports in published_reports),
        "parse_failure_count": sum(
            report.get("parse_status") != "parsed"
            for reports in published_reports
            for report in reports
        ),
        "event_count": len(events),
        "consensus_count": len(consensus),
        "source": "基金定期报告与东方财富公开接口",
        "scope_note": "季报通常为前十大；中报和年报为完整股票持仓。未披露不等于零持仓。",
    }
    _write_json(fund_dir / "meta.json", metadata)
    _write_json(fund_dir / "universe.json", {"meta": metadata, "funds": funds})
    _write_json(fund_dir / "events.json", {"meta": metadata, "events": events})
    _write_json(fund_dir / "consensus.json", {"meta": metadata, "rows": consensus})
    events_by_fund: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        events_by_fund[str(event.get("fund_code") or "")].append(event)
    for fund in funds:
        code = str(fund["code"])
        _write_json(fund_dir / "portfolios" / f"{code}.json", {
            "meta": metadata,
            "fund": fund,
            "reports": reports_by_fund.get(code, []),
            "events": events_by_fund.get(code, []),
        })
    return metadata


def select_research_funds(
    rank_rows: Iterable[dict[str, Any]],
    catalog: dict[str, dict[str, Any]],
    details: dict[str, dict[str, Any]],
    *,
    limit: int = 24,
    minimum_size: float = 5.0,
    max_per_manager: int = 2,
    as_of: str | None = None,
) -> list[dict[str, Any]]:
    cutoff = date.fromisoformat(as_of) if as_of else date.today()
    candidates: list[dict[str, Any]] = []
    for row in rank_rows:
        code = str(row.get("code") or "")
        name = str(row.get("name") or "")
        fund_type = str((catalog.get(code) or {}).get("fund_type") or "")
        detail = details.get(code) or {}
        inception = str(row.get("inception_date") or "")[:10]
        if (
            fund_type not in _ALLOWED_FUND_TYPES
            or any(part.lower() in name.lower() for part in _EXCLUDED_NAME_PARTS)
            or row.get("return_3y") is None
            or not inception
            or (cutoff - date.fromisoformat(inception)).days < 365 * 3
            or float(detail.get("fund_size") or 0) < minimum_size
            or not detail.get("manager_id")
        ):
            continue
        candidates.append({
            **row,
            **detail,
            "fund_type": fund_type,
            "portfolio_name": normalize_portfolio_name(name),
        })

    by_portfolio: dict[str, dict[str, Any]] = {}
    for row in candidates:
        current = by_portfolio.get(row["portfolio_name"])
        priority = (share_class_preference(str(row["name"])), -float(row["return_3y"]))
        if current is None or priority < current["_share_priority"]:
            by_portfolio[row["portfolio_name"]] = {**row, "_share_priority": priority}
    candidates = list(by_portfolio.values())
    percentiles = _percentiles([float(row["return_3y"]) for row in candidates])
    for row, return_percentile in zip(candidates, percentiles):
        row["screen_score"] = (
            return_percentile * 0.4
            + float(row.get("risk_score") or 0) * 0.2
            + float(row.get("stability_score") or 0) * 0.2
            + float(row.get("experience_score") or 0) * 0.1
            + float(row.get("manager_score") or 0) * 0.1
        )
        row["score_label"] = "研究池筛选分（非基金评级）"
        row.pop("_share_priority", None)

    candidates.sort(key=lambda row: (-row["screen_score"], -float(row["return_3y"]), row["code"]))
    selected: list[dict[str, Any]] = []
    manager_counts: defaultdict[str, int] = defaultdict(int)
    for row in candidates:
        manager_ids = [str(item.get("id")) for item in (row.get("managers") or []) if item.get("id")]
        if not manager_ids:
            manager_ids = [str(row["manager_id"])]
        if any(manager_counts[manager_id] >= max_per_manager for manager_id in manager_ids):
            continue
        selected.append(row)
        for manager_id in manager_ids:
            manager_counts[manager_id] += 1
        if len(selected) >= limit:
            break
    return selected
