from __future__ import annotations

import hashlib
import json
import math
import shutil
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .fund_pipeline import _corporate_actions, _expected_latest_report_period
from .funds import (
    build_holding_events,
    build_stock_consensus,
    normalize_portfolio_name,
    parse_fund_report,
    report_identity,
    share_class_preference,
)


_ALLOWED_TYPES = {"股票型", "混合型-偏股", "混合型-灵活"}
_EXCLUDED_PARTS = ("指数", "ETF", "联接", "量化", "FOF", "QDII", "港股", "沪港深")


def build_size_universe(
    rows: Iterable[dict[str, Any]],
    catalog: dict[str, dict[str, Any]],
    *,
    target_count: int,
) -> list[dict[str, Any]]:
    if target_count <= 0:
        raise ValueError("基金目标数量必须大于0")

    by_code: dict[str, dict[str, Any]] = {}
    for row in rows:
        code = str(row.get("code") or "")
        name = str(row.get("name") or "")
        fund_type = str((catalog.get(code) or {}).get("fund_type") or "")
        try:
            end_nav = float(row.get("end_nav") or 0)
        except (TypeError, ValueError):
            end_nav = 0
        if (
            len(code) != 6
            or not code.isdigit()
            or fund_type not in _ALLOWED_TYPES
            or any(part.lower() in name.lower() for part in _EXCLUDED_PARTS)
            or not math.isfinite(end_nav)
            or end_nav <= 0
        ):
            continue
        current = by_code.get(code)
        if current is None or end_nav > float(current["end_nav"]):
            by_code[code] = {**row, "code": code, "name": name, "fund_type": fund_type, "end_nav": end_nav}

    grouped: dict[str, dict[str, Any]] = {}
    for row in by_code.values():
        portfolio_name = normalize_portfolio_name(row["name"])
        current = grouped.get(portfolio_name)
        priority = (share_class_preference(row["name"]), row["code"])
        if current is None:
            grouped[portfolio_name] = {
                "representative": row,
                "priority": priority,
                "total_end_nav": float(row["end_nav"]),
                "share_class_codes": [row["code"]],
            }
            continue
        current["total_end_nav"] += float(row["end_nav"])
        current["share_class_codes"].append(row["code"])
        if priority < current["priority"]:
            current["representative"] = row
            current["priority"] = priority

    funds: list[dict[str, Any]] = []
    for portfolio_name, group in grouped.items():
        representative = group["representative"]
        share_codes = sorted(group["share_class_codes"])
        funds.append({
            **representative,
            "portfolio_name": portfolio_name,
            "fund_size": round(float(group["total_end_nav"]) / 1e8, 6),
            "share_class_count": len(share_codes),
            "share_class_codes": share_codes,
            "selection_basis": "组合各份额规模合计",
            "screen_score": 100.0,
            "score_label": "规模研究池等权分（非基金评级）",
        })
    funds.sort(key=lambda fund: (-float(fund["fund_size"]), str(fund["code"])))
    if len(funds) < target_count:
        raise RuntimeError(f"基金规模候选不足 {len(funds)}/{target_count}")
    return funds[:target_count]


def shard_index_for(when: datetime, shard_count: int) -> int:
    if shard_count <= 0:
        raise ValueError("分片数量必须大于0")
    return int(when.timestamp() // (4 * 60 * 60)) % shard_count


def select_shard(
    funds: list[dict[str, Any]],
    *,
    batch_size: int,
    shard_index: int,
) -> list[dict[str, Any]]:
    if batch_size <= 0:
        raise ValueError("每批基金数量必须大于0")
    shard_count = max(1, math.ceil(len(funds) / batch_size))
    if not 0 <= shard_index < shard_count:
        raise ValueError(f"基金分片超出范围: {shard_index}/{shard_count}")
    start = shard_index * batch_size
    return funds[start:start + batch_size]


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temporary.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON对象格式异常: {path}")
    return payload


def ensure_empty_fund_state(
    data_root: Path,
    generated_at: str,
    *,
    target_count: int = 1000,
    batch_size: int = 100,
) -> dict[str, Any]:
    fund_dir = data_root / "funds"
    meta_path = fund_dir / "meta.json"
    if meta_path.exists():
        return _read_json(meta_path)
    shard_count = math.ceil(target_count / batch_size)
    metadata = {
        "generated_at": generated_at,
        "fund_count": 0,
        "tracked_count": 0,
        "report_count": 0,
        "parse_failure_count": 0,
        "event_count": 0,
        "published_event_count": 0,
        "consensus_count": 0,
        "report_period": None,
        "shard_index": -1,
        "shard_count": shard_count,
        "batch_size": batch_size,
        "cycle_hours": shard_count * 4,
        "updated_fund_count": 0,
        "failed_fund_count": 0,
        "failures": [],
        "source": "等待首次基金规模轮询",
        "scope_note": "基金池将在分片轮询后逐步填充。",
    }
    _write_json(meta_path, metadata)
    _write_json(fund_dir / "universe.json", {"meta": metadata, "funds": []})
    _write_json(fund_dir / "events.json", {"meta": metadata, "events": []})
    _write_json(fund_dir / "consensus.json", {"meta": metadata, "rows": []})
    return metadata


def _fetch_reports(
    client: Any,
    fund: dict[str, Any],
    *,
    report_limit: int,
    expected_latest_period: str,
) -> list[dict[str, Any]]:
    notices = sorted(client.fetch_notices(fund["code"]), key=lambda row: row["publish_date"], reverse=True)[:report_limit]
    reports: list[dict[str, Any]] = []
    for notice in notices:
        document: dict[str, Any] | None = None
        try:
            document = client.fetch_notice_content(notice["art_code"])
            reports.append(parse_fund_report(
                fund_code=fund["code"],
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
                "fund_code": fund["code"],
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
    reports.sort(key=lambda row: row["report_period"])
    parsed = [report for report in reports if report.get("parse_status") == "parsed"]
    if len(parsed) < 2:
        raise RuntimeError("可解析定期报告少于2份")
    if any(report.get("parse_status") != "parsed" for report in reports[-2:]):
        raise RuntimeError("最新两期定期报告存在解析失败")
    if reports[-1]["report_period"] < expected_latest_period:
        raise RuntimeError(f"最新报告期陈旧 {reports[-1]['report_period']}，应至少为 {expected_latest_period}")
    return reports


def _load_corporate_actions(data_root: Path, reports: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    codes = {
        str(holding.get("stock_code") or "")
        for report in reports
        for holding in report.get("holdings", [])
        if str(holding.get("stock_code") or "").isdigit()
    }
    dividends: list[dict[str, Any]] = []
    for code in codes:
        path = data_root / "companies" / code[:2] / f"{code}.json"
        if path.exists():
            dividends.extend(_read_json(path).get("dividends") or [])
    return _corporate_actions(dividends)


def _compact_consensus(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for row in rows:
        events = row.get("events") or []
        manager_names = sorted({str(event.get("manager_name")) for event in events if event.get("manager_name")})
        compact.append({**{key: value for key, value in row.items() if key != "events"}, "manager_names": manager_names[:5]})
    return compact


def run_fund_rotation(
    *,
    data_root: Path,
    client: Any,
    target_count: int = 1000,
    batch_size: int = 100,
    shard_index: int,
    refresh_universe: bool,
    report_limit: int = 4,
    as_of: date | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    if target_count <= 0 or batch_size <= 0:
        raise ValueError("基金轮询参数无效")
    fund_dir = data_root / "funds"
    universe_path = fund_dir / "universe.json"
    previous_funds: dict[str, dict[str, Any]] = {}
    if universe_path.exists():
        previous_funds = {
            str(fund["code"]): fund
            for fund in (_read_json(universe_path).get("funds") or [])
            if fund.get("code")
        }
    if refresh_universe or not universe_path.exists():
        catalog_rows = client.fetch_catalog()
        catalog = {row["code"]: row for row in catalog_rows if row.get("code")}
        funds = build_size_universe(client.fetch_size_rankings(), catalog, target_count=target_count)
        funds = [
            {
                **fund,
                **{
                    key: previous_funds[str(fund["code"])][key]
                    for key in ("tracking_status", "last_attempted_at", "last_update_error")
                    if key in previous_funds.get(str(fund["code"]), {})
                },
            }
            for fund in funds
        ]
    else:
        funds = list(_read_json(universe_path).get("funds") or [])
        if len(funds) != target_count:
            raise RuntimeError(f"已发布基金池数量异常 {len(funds)}/{target_count}")

    shard_count = math.ceil(len(funds) / batch_size)
    shard = select_shard(funds, batch_size=batch_size, shard_index=shard_index)
    portfolio_dir = fund_dir / "portfolios"
    portfolio_dir.mkdir(parents=True, exist_ok=True)
    allowed_codes = {str(fund["code"]) for fund in funds}
    for path in portfolio_dir.glob("*.json"):
        if path.stem not in allowed_codes:
            path.unlink()

    today = as_of or date.today()
    expected_latest_period = _expected_latest_report_period(today)
    timestamp = generated_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    updated = 0
    failures: list[dict[str, str]] = []
    for offset, base_fund in enumerate(shard, start=1):
        code = str(base_fund["code"])
        try:
            detail = client.fetch_details(code)
            fund = {**base_fund, **detail, "code": code, "fund_size": base_fund["fund_size"]}
            reports = _fetch_reports(
                client,
                fund,
                report_limit=report_limit,
                expected_latest_period=expected_latest_period,
            )
            parsed = [report for report in reports if report.get("parse_status") == "parsed"]
            events = build_holding_events(fund, parsed, _load_corporate_actions(data_root, parsed))
            _write_json(portfolio_dir / f"{code}.json", {
                "updated_at": timestamp,
                "fund": fund,
                "reports": reports,
                "events": events,
            })
            updated += 1
        except Exception as error:
            failures.append({"code": code, "error": str(error)})
        if offset % 20 == 0 or offset == len(shard):
            print(f"基金轮询分片 {shard_index + 1}/{shard_count}: {offset}/{len(shard)}")

    if shard and updated == 0:
        raise RuntimeError(f"基金分片无一更新成功: {len(failures)}/{len(shard)}")

    failure_by_code = {failure["code"]: failure["error"] for failure in failures}
    attempted_codes = {str(fund["code"]) for fund in shard}

    stocks_payload = _read_json(data_root / "stocks.json")
    stocks = list(stocks_payload.get("stocks") or [])
    portfolios: dict[str, dict[str, Any]] = {}
    all_events: list[dict[str, Any]] = []
    report_count = 0
    parse_failure_count = 0
    for fund in funds:
        code = str(fund["code"])
        path = portfolio_dir / f"{code}.json"
        if not path.exists():
            continue
        try:
            portfolio = _read_json(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        portfolios[code] = portfolio
        reports = list(portfolio.get("reports") or [])
        report_count += len(reports)
        parse_failure_count += sum(report.get("parse_status") != "parsed" for report in reports)
        all_events.extend({**event, "screen_score": fund["screen_score"]} for event in (portfolio.get("events") or []))

    actionable = [event for event in all_events if event.get("event_type") not in {"CENSORED", "UNCHANGED"}]
    consensus = _compact_consensus(build_stock_consensus(all_events, stocks))
    metadata = {
        "generated_at": timestamp,
        "fund_count": len(funds),
        "tracked_count": len(portfolios),
        "report_count": report_count,
        "parse_failure_count": parse_failure_count,
        "event_count": len(all_events),
        "published_event_count": len(actionable),
        "consensus_count": len(consensus),
        "report_period": max((str(event.get("report_period") or "") for event in all_events), default="") or None,
        "shard_index": shard_index,
        "shard_count": shard_count,
        "batch_size": batch_size,
        "cycle_hours": shard_count * 4,
        "updated_fund_count": updated,
        "failed_fund_count": len(failures),
        "failures": failures,
        "source": "基金定期报告与东方财富公开接口",
        "scope_note": "季报通常为前十大；中报和年报为完整股票持仓。未披露不等于零持仓。",
    }
    published_funds: list[dict[str, Any]] = []
    for fund in funds:
        code = str(fund["code"])
        portfolio = portfolios.get(code)
        detail = dict((portfolio or {}).get("fund") or {})
        failure_error = failure_by_code.get(code)
        if failure_error is not None:
            tracking_status = "update_failed"
        elif code not in attempted_codes and fund.get("tracking_status") == "update_failed":
            tracking_status = "update_failed"
        elif portfolio:
            tracking_status = "tracked"
        else:
            tracking_status = "pending"
        published_funds.append({
            **fund,
            **detail,
            "code": code,
            "fund_size": fund["fund_size"],
            "tracking_status": tracking_status,
            "last_updated_at": (portfolio or {}).get("updated_at"),
            "last_attempted_at": timestamp if code in attempted_codes else fund.get("last_attempted_at"),
            "last_update_error": failure_error if failure_error is not None else (None if code in attempted_codes else fund.get("last_update_error")),
        })

    _write_json(fund_dir / "meta.json", metadata)
    _write_json(fund_dir / "universe.json", {"meta": metadata, "funds": published_funds})
    _write_json(fund_dir / "events.json", {"meta": metadata, "events": actionable})
    _write_json(fund_dir / "consensus.json", {"meta": metadata, "rows": consensus})
    stock_dir = fund_dir / "stocks"
    if stock_dir.exists():
        shutil.rmtree(stock_dir)
    events_by_stock: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in actionable:
        code = str(event.get("stock_code") or "")
        if len(code) == 6 and code.isdigit():
            events_by_stock[code].append(event)
    for code, stock_events in events_by_stock.items():
        _write_json(stock_dir / code[:2] / f"{code}.json", {"meta": metadata, "events": stock_events})
    return metadata
