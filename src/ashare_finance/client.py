from __future__ import annotations

import json
import random
import time
import urllib.parse
import urllib.request
from typing import Any, Callable


Progress = Callable[[str], None]


class EastmoneyClient:
    STOCK_URL = "https://82.push2.eastmoney.com/api/qt/clist/get"
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
            "pz": 1000,
            "po": 1,
            "np": 1,
            "fltt": 2,
            "invt": 2,
            "fid": "f12",
            "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
            "fields": "f2,f3,f12,f13,f14,f20,f21,f100",
        }
        first = self._request(self.STOCK_URL, params)
        data = first.get("data") or {}
        rows = list(data.get("diff") or [])
        total = int(data.get("total") or len(rows))
        pages = max(1, (total + params["pz"] - 1) // params["pz"])
        self.progress(f"A股目录: 1/{pages} · {len(rows)}/{total}")
        for page in range(2, pages + 1):
            current = dict(params)
            current["pn"] = page
            payload = self._request(self.STOCK_URL, current)
            rows.extend((payload.get("data") or {}).get("diff") or [])
            self.progress(f"A股目录: {page}/{pages} · {len(rows)}/{total}")
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
