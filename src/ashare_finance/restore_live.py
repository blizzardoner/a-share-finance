from __future__ import annotations

import argparse
import json
import random
import shutil
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable


Fetch = Callable[[str], bytes]
_RANKING_KINDS = ("dividend", "performance", "peg")
_RANKING_YEARS = (1, 3, 5, 10)
_FUND_CORE = ("funds/meta.json", "funds/universe.json", "funds/events.json", "funds/consensus.json")


def build_live_data_paths(
    stock_codes: list[str],
    fund_codes: list[str],
    *,
    include_funds: bool = True,
) -> list[str]:
    paths = {"meta.json", "stocks.json"}
    paths.update(f"companies/{code[:2]}/{code}.json" for code in stock_codes if len(code) == 6 and code.isdigit())
    paths.update(f"rankings/{kind}-{years}.json" for kind in _RANKING_KINDS for years in _RANKING_YEARS)
    if include_funds:
        paths.update(_FUND_CORE)
        paths.update(f"funds/portfolios/{code}.json" for code in fund_codes if len(code) == 6 and code.isdigit())
    return sorted(paths)


def _url_fetcher(base_url: str, *, retries: int = 4, timeout: float = 35) -> Fetch:
    base = base_url.rstrip("/")

    def fetch(path: str) -> bytes:
        request = urllib.request.Request(
            f"{base}/{path}",
            headers={"User-Agent": "AShareFinance/0.1 live-data-recovery", "Accept": "application/json"},
        )
        for attempt in range(1, retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    return response.read()
            except Exception:
                if attempt >= retries:
                    raise
                time.sleep(min(5.0, 2 ** (attempt - 1) + random.random()))
        raise RuntimeError("unreachable")

    return fetch


def _json_bytes(payload: bytes, path: str) -> dict:
    value = json.loads(payload.decode("utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"线上数据格式异常: {path}")
    return value


def restore_live_data(
    base_url: str,
    output: Path,
    *,
    fetch: Fetch | None = None,
    workers: int = 24,
) -> dict[str, int]:
    fetcher = fetch or _url_fetcher(base_url)
    preserve_funds = (output / "funds" / "meta.json").exists()
    initial = {
        "meta.json": fetcher("meta.json"),
        "stocks.json": fetcher("stocks.json"),
    }
    stocks = _json_bytes(initial["stocks.json"], "stocks.json").get("stocks") or []
    stock_codes = [str(row.get("code") or "") for row in stocks if isinstance(row, dict)]

    fund_codes: list[str] = []
    if not preserve_funds:
        for path in _FUND_CORE:
            initial[path] = fetcher(path)
        funds = _json_bytes(initial["funds/universe.json"], "funds/universe.json").get("funds") or []
        fund_codes = [str(row.get("code") or "") for row in funds if isinstance(row, dict)]

    paths = build_live_data_paths(stock_codes, fund_codes, include_funds=not preserve_funds)
    payloads = dict(initial)
    pending = [path for path in paths if path not in payloads]
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {executor.submit(fetcher, path): path for path in pending}
        for future in as_completed(futures):
            path = futures[future]
            payloads[path] = future.result()

    staging = output.parent / f".{output.name}-live-restore"
    if staging.exists():
        shutil.rmtree(staging)
    for path in paths:
        destination = staging / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payloads[path])

    derived_shards = 0
    if not preserve_funds:
        fund_meta = _json_bytes(payloads["funds/meta.json"], "funds/meta.json")
        fund_events = _json_bytes(payloads["funds/events.json"], "funds/events.json").get("events") or []
        events_by_stock: dict[str, list[dict]] = {}
        for event in fund_events:
            if not isinstance(event, dict) or event.get("event_type") in {"CENSORED", "UNCHANGED"}:
                continue
            code = str(event.get("stock_code") or "")
            if len(code) == 6 and code.isdigit():
                events_by_stock.setdefault(code, []).append(event)
        for code, events in events_by_stock.items():
            destination = staging / "funds" / "stocks" / code[:2] / f"{code}.json"
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                json.dumps({"meta": fund_meta, "events": events}, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            derived_shards += 1

    if preserve_funds:
        shutil.copytree(output / "funds", staging / "funds", dirs_exist_ok=True)
    if output.exists():
        shutil.rmtree(output)
    staging.replace(output)
    return {
        "company_count": len([code for code in stock_codes if len(code) == 6 and code.isdigit()]),
        "fund_count": len(fund_codes),
        "file_count": len(paths) + derived_shards,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Restore the last deployed static data snapshot")
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", type=Path, default=Path("public/data"))
    parser.add_argument("--workers", type=int, default=24)
    args = parser.parse_args()
    result = restore_live_data(args.url, args.output, workers=args.workers)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
