from __future__ import annotations

import ast
import json
import random
import re
import time
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable


Progress = Callable[[str], None]


def _number(value: Any) -> float | None:
    text = str(value or "").strip().replace("%", "").replace(",", "")
    if not text or text in {"--", "---", "null", "None"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_fund_catalog(payload: str) -> list[dict[str, str]]:
    match = re.search(r"var\s+r\s*=\s*(\[.*\])\s*;?\s*$", payload.lstrip("\ufeff"), re.S)
    if not match:
        raise ValueError("基金目录格式异常")
    raw = json.loads(match.group(1))
    return [
        {
            "code": str(item[0]).strip(),
            "search_key": str(item[1]).strip(),
            "name": str(item[2]).strip(),
            "fund_type": str(item[3]).strip(),
            "pinyin": str(item[4]).strip(),
        }
        for item in raw
        if isinstance(item, list) and len(item) >= 5
    ]


def parse_rank_payload(payload: str) -> tuple[list[dict[str, Any]], int, int, int]:
    match = re.search(r"datas\s*:\s*(\[.*?\])\s*,\s*allRecords", payload, re.S)
    if not match:
        raise ValueError("基金排名格式异常")
    raw_rows = ast.literal_eval(match.group(1))
    rows: list[dict[str, Any]] = []
    for line in raw_rows:
        values = str(line).split(",")
        values += [""] * max(0, 21 - len(values))
        rows.append({
            "code": values[0],
            "name": values[1],
            "search_key": values[2],
            "nav_date": values[3] or None,
            "unit_nav": _number(values[4]),
            "daily_growth": _number(values[6]),
            "return_1y": _number(values[11]),
            "return_2y": _number(values[12]),
            "return_3y": _number(values[13]),
            "return_ytd": _number(values[14]),
            "return_since_inception": _number(values[15]),
            "inception_date": values[16] or None,
        })
    def meta(name: str, default: int) -> int:
        found = re.search(rf"\b{name}\s*:\s*(\d+)", payload)
        return int(found.group(1)) if found else default
    return rows, meta("allRecords", len(rows)), meta("allPages", 1), meta("pageIndex", 1)


def _json_variable(payload: str, name: str) -> Any:
    match = re.search(rf"\bvar\s+{re.escape(name)}\s*=\s*", payload)
    if not match:
        return None
    try:
        value, _ = json.JSONDecoder().raw_decode(payload[match.end():])
        return value
    except json.JSONDecodeError:
        return None


def _string_variable(payload: str, name: str) -> str | None:
    match = re.search(rf'\bvar\s+{re.escape(name)}\s*=\s*"((?:\\.|[^"\\])*)"', payload)
    if not match:
        return None
    return match.group(1).replace(r'\"', '"').replace(r"\\", "\\")


def _score_by_category(payload: dict[str, Any] | None, category: str) -> float | None:
    if not payload:
        return None
    categories = payload.get("categories") or []
    values = payload.get("data") or []
    try:
        return float(values[categories.index(category)])
    except (ValueError, IndexError, TypeError):
        return None


def _latest_series_value(payload: dict[str, Any] | None, name: str | None = None) -> float | None:
    if not payload:
        return None
    series = payload.get("series") or []
    if name is None:
        values = series
    else:
        values = [item for item in series if item.get("name") == name]
    if not values:
        return None
    data = values[0].get("data") if name is not None else values
    if name is None:
        last = data[-1] if data else None
        raw = last.get("y") if isinstance(last, dict) else last
    else:
        raw = data[-1] if data else None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def parse_fund_details(payload: str) -> dict[str, Any]:
    managers = _json_variable(payload, "Data_currentFundManager") or []
    manager = managers[0] if managers else {}
    manager_power = manager.get("power") or {}
    evaluation = _json_variable(payload, "Data_performanceEvaluation") or {}
    scale = _json_variable(payload, "Data_fluctuationScale") or {}
    allocation = _json_variable(payload, "Data_assetAllocation") or {}
    categories = allocation.get("categories") or scale.get("categories") or []
    return {
        "code": _string_variable(payload, "fS_code"),
        "name": _string_variable(payload, "fS_name"),
        "manager_id": manager.get("id"),
        "manager_name": manager.get("name"),
        "manager_work_time": manager.get("workTime"),
        "managers": [
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "work_time": item.get("workTime"),
                "manager_score": _number((item.get("power") or {}).get("avr")),
                "experience_score": _score_by_category(item.get("power") or {}, "经验值"),
            }
            for item in managers
            if item.get("id") and item.get("name")
        ],
        "manager_score": _number(manager_power.get("avr")),
        "experience_score": _score_by_category(manager_power, "经验值"),
        "risk_score": _score_by_category(evaluation, "抗风险"),
        "stability_score": _score_by_category(evaluation, "稳定性"),
        "fund_size": _latest_series_value(scale),
        "stock_allocation": _latest_series_value(allocation, "股票占净比"),
        "detail_date": categories[-1] if categories else None,
    }


_PERIODIC_REPORT_TITLE = re.compile(r"20\d{2}年(?:第[13]季度报告|中期报告|半年度报告|年度报告)$")


def parse_notice_list(payload: str) -> list[dict[str, Any]]:
    document = json.loads(payload)
    source_rows = document.get("Data") or []
    rows: list[dict[str, Any]] = []
    for item in source_rows:
        title = str(item.get("TITLE") or "").strip()
        art_code = str(item.get("ID") or "").strip()
        if not art_code or not _PERIODIC_REPORT_TITLE.search(title):
            continue
        rows.append({
            "art_code": art_code,
            "title": title,
            "publish_date": str(item.get("PUBLISHDATE") or "")[:10],
            "pdf_url": f"https://pdf.dfcfw.com/pdf/H2_{art_code}_1.pdf",
        })
    return rows


def parse_notice_content(payload: str) -> dict[str, str]:
    document = json.loads(payload)
    data = document.get("data") if document.get("success") == 1 else None
    title = str((data or {}).get("notice_title") or "").strip()
    content = str((data or {}).get("notice_content") or "").strip()
    if not title or not content:
        raise ValueError("基金公告正文无效")
    return {
        "title": title,
        "content": content,
        "pdf_url": str(data.get("attach_url") or ""),
    }


class FundClient:
    CATALOG_URL = "https://fund.eastmoney.com/js/fundcode_search.js"
    RANK_URL = "https://fund.eastmoney.com/data/rankhandler.aspx"
    DETAILS_URL = "https://fund.eastmoney.com/pingzhongdata/{code}.js"
    NOTICES_URL = "https://api.fund.eastmoney.com/f10/JJGG"
    CONTENT_URL = "https://np-cnotice-fund.eastmoney.com/api/content/ann"

    def __init__(
        self,
        min_interval: float = 0.30,
        timeout: float = 35,
        retries: int = 4,
        progress: Progress | None = None,
        cache_dir: Path | None = None,
    ) -> None:
        self.min_interval = min_interval
        self.timeout = timeout
        self.retries = retries
        self.progress = progress or (lambda _: None)
        self.cache_dir = cache_dir
        self._last_request = 0.0

    def _get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        referer: str | None = None,
    ) -> str:
        query = urllib.parse.urlencode(params or {})
        request = urllib.request.Request(
            f"{url}?{query}" if query else url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 AShareFinance/0.1",
                "Referer": referer or "https://fund.eastmoney.com/",
                "Accept": "application/json,text/plain,*/*",
            },
        )
        for attempt in range(1, self.retries + 1):
            wait = self.min_interval - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    body = response.read().decode("utf-8-sig")
                self._last_request = time.monotonic()
                return body
            except Exception:
                if attempt >= self.retries:
                    raise
                time.sleep(min(8.0, 2 ** (attempt - 1) + random.random()))
        raise RuntimeError("unreachable")

    def fetch_catalog(self) -> list[dict[str, str]]:
        return parse_fund_catalog(self._get(self.CATALOG_URL))

    def _rank_params(self, fund_type: str, page: int, page_size: int) -> dict[str, Any]:
        end = date.today()
        start = end - timedelta(days=366)
        return {
            "op": "ph", "dt": "kf", "ft": fund_type, "rs": "", "gs": "0",
            "sc": "zzf", "st": "desc", "sd": start.isoformat(), "ed": end.isoformat(),
            "qdii": "", "tabSubtype": ",,,,,", "pi": page, "pn": page_size,
            "dx": "1", "v": str(time.time()),
        }

    def fetch_rankings(self, fund_types: tuple[str, ...] = ("gp", "hh"), page_size: int = 500) -> list[dict[str, Any]]:
        combined: dict[str, dict[str, Any]] = {}
        for fund_type in fund_types:
            first = parse_rank_payload(self._get(
                self.RANK_URL,
                self._rank_params(fund_type, 1, page_size),
                "https://fund.eastmoney.com/data/fundranking.html",
            ))
            rows, total, pages, _ = first
            self.progress(f"基金排名 {fund_type}: 1/{pages} · {len(rows)}/{total}")
            category_rows = list(rows)
            for page in range(2, pages + 1):
                current, current_total, current_pages, _ = parse_rank_payload(self._get(
                    self.RANK_URL,
                    self._rank_params(fund_type, page, page_size),
                    "https://fund.eastmoney.com/data/fundranking.html",
                ))
                if current_total != total or current_pages != pages:
                    raise RuntimeError(f"基金排名分页元数据变化: {fund_type}")
                category_rows.extend(current)
            category_map = {row["code"]: row for row in category_rows if row.get("code")}
            if len(category_map) != total:
                raise RuntimeError(f"基金排名覆盖不完整 {fund_type}: {len(category_map)}/{total}")
            combined.update(category_map)
        return [combined[code] for code in sorted(combined)]

    def fetch_details(self, code: str) -> dict[str, Any]:
        return parse_fund_details(self._get(
            self.DETAILS_URL.format(code=code),
            referer=f"https://fund.eastmoney.com/{code}.html",
        ))

    def fetch_notices(self, code: str) -> list[dict[str, Any]]:
        return parse_notice_list(self._get(
            self.NOTICES_URL,
            {"fundcode": code, "pageIndex": 1, "pageSize": 100, "type": 3},
            f"https://fundf10.eastmoney.com/jjgg_{code}_3.html",
        ))

    def fetch_notice_content(self, art_code: str) -> dict[str, str]:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", art_code):
            raise ValueError("基金公告编号无效")
        cache_path = self.cache_dir / f"{art_code}.json" if self.cache_dir else None
        if cache_path and cache_path.exists():
            try:
                return parse_notice_content(cache_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                cache_path.unlink(missing_ok=True)
        payload = self._get(
            self.CONTENT_URL,
            {"client_source": "web_fund", "show_all": 1, "art_code": art_code},
            "https://fund.eastmoney.com/",
        )
        document = parse_notice_content(payload)
        if cache_path:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = cache_path.with_suffix(".tmp")
            temporary.write_text(payload, encoding="utf-8")
            temporary.replace(cache_path)
        return document
