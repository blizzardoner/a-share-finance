from __future__ import annotations

import json
import random
import time
import urllib.parse
import urllib.request
from typing import Any, Callable


Progress = Callable[[str], None]


def _quote_market_id(code: str) -> int:
    if code.startswith(("4", "8", "92")):
        return 0
    return 1 if code.startswith(("5", "6", "9")) else 0


def _quote_rows(payload: dict[str, Any], label: str) -> list[dict[str, Any]]:
    data = payload.get("data")
    rows = data.get("diff") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError(f"东方财富{label}返回无效")
    return rows


class EastmoneyClient:
    STOCK_URL = "https://82.push2.eastmoney.com/api/qt/clist/get"
    STOCK_URLS = tuple(f"https://{host}.push2.eastmoney.com/api/qt/clist/get" for host in (82, 28, 33, 80))
    SELECTED_STOCK_URL = "https://push2.eastmoney.com/api/qt/ulist.np/get"
    SECURITIES_URL = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
    DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    ORG_COLUMNS = (
        "SECUCODE,SECURITY_CODE,SECURITY_NAME_ABBR,STR_CODEA,SECURITY_TYPE,"
        "EM2016,TRADE_MARKET,LISTING_DATE,EXPAND_NAME_PINYIN"
    )

    def __init__(self, min_interval: float = 0.12, timeout: float = 35, retries: int = 4, progress: Progress | None = None) -> None:
        self.min_interval = min_interval
        self.timeout = timeout
        self.retries = retries
        self.progress = progress or (lambda _: None)
        self._last_request = 0.0

    def _request(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        query = urllib.parse.urlencode(params)
        request = urllib.request.Request(
            f"{url}?{query}",
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 AShareFinance/0.1",
                "Referer": "https://data.eastmoney.com/",
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
                return json.loads(body)
            except Exception:
                if attempt >= self.retries:
                    raise
                time.sleep(min(8.0, (2 ** (attempt - 1)) + random.random()))
        raise RuntimeError("unreachable")

    def fetch_pages(self, url: str, params: dict[str, Any], label: str = "dataset") -> list[dict[str, Any]]:
        base = dict(params)
        base["pageNumber"] = 1
        first = self._request(url, base)
        if not first.get("success"):
            raise RuntimeError(first.get("message") or f"Eastmoney {label} request failed")
        result = first.get("result") or {}
        rows = list(result.get("data") or [])
        pages = int(result.get("pages") or 1)
        self.progress(f"{label}: 1/{pages} · {len(rows)} rows")
        for page in range(2, pages + 1):
            current = dict(base)
            current["pageNumber"] = page
            payload = self._request(url, current)
            if not payload.get("success"):
                raise RuntimeError(payload.get("message") or f"Eastmoney {label} page {page} failed")
            rows.extend((payload.get("result") or {}).get("data") or [])
            if page == pages or page % 10 == 0:
                self.progress(f"{label}: {page}/{pages} · {len(rows)} rows")
        return rows

    def fetch_stocks(self) -> list[dict[str, Any]]:
        params = {
            "pn": 1,
            "pz": 100,
            "po": 1,
            "np": 1,
            "fltt": 2,
            "invt": 2,
            "fid": "f12",
            "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
            "fields": "f2,f3,f9,f12,f13,f14,f20,f21,f100",
        }
        first = self._request(self.STOCK_URLS[0], params)
        data = first.get("data") or {}
        rows = list(_quote_rows(first, "行情目录"))
        raw_total = data.get("total")
        if not isinstance(raw_total, int) or isinstance(raw_total, bool) or raw_total < len(rows):
            raise RuntimeError("东方财富行情目录总数无效")
        total = raw_total
        effective_page_size = max(1, len(rows))
        pages = max(1, (total + effective_page_size - 1) // effective_page_size)
        self.progress(f"A股目录: 1/{pages} · {len(rows)}/{total}")
        for page in range(2, pages + 1):
            current = dict(params)
            current["pn"] = page
            if self.min_interval > 0:
                time.sleep(max(0.0, 0.8 - self.min_interval))
            payload = self._request(self.STOCK_URLS[(page - 1) % len(self.STOCK_URLS)], current)
            rows.extend(_quote_rows(payload, f"行情目录第{page}页"))
            self.progress(f"A股目录: {page}/{pages} · {len(rows)}/{total}")
        unique_codes = {str(row.get("f12") or "") for row in rows if row.get("f12")}
        if len(unique_codes) < total:
            raise RuntimeError(f"行情目录不完整: 预期 {total}，实际 {len(unique_codes)}")
        return rows

    def fetch_selected_stock_quotes(self, codes: set[str]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        secids = [f"{_quote_market_id(code)}.{code}" for code in sorted(codes)]
        for start in range(0, len(secids), 100):
            payload = self._request(self.SELECTED_STOCK_URL, {
                "secids": ",".join(secids[start:start + 100]),
                "fields": "f2,f3,f9,f12,f13,f14,f20,f21,f100",
                "fltt": 2,
                "invt": 2,
            })
            rows.extend(_quote_rows(payload, "指定股票行情"))
        found = {str(row.get("f12") or "") for row in rows if row.get("f12")}
        missing = sorted(codes - found)
        if missing:
            raise RuntimeError(f"行情缺少 {', '.join(missing)}")
        return rows

    def fetch_org_directory(self, codes: set[str] | None = None) -> list[dict[str, Any]]:
        common = {
            "reportName": "RPT_F10_BASIC_ORGINFO",
            "columns": self.ORG_COLUMNS,
            "pageSize": 500,
            "sortTypes": 1,
            "sortColumns": "SECURITY_CODE",
            "source": "HSF10",
            "client": "PC",
        }
        if not codes:
            return self.fetch_pages(self.SECURITIES_URL, common, "A股公司目录")
        rows: list[dict[str, Any]] = []
        for code in sorted(codes):
            params = {**common, "filter": f'(SECURITY_CODE="{code}")'}
            rows.extend(self.fetch_pages(self.SECURITIES_URL, params, f"公司目录 {code}"))
        return rows
