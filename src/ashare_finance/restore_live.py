from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
import time
import tarfile
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path, PurePosixPath
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


def derive_fund_event_shards(data_root: Path) -> int:
    fund_dir = data_root / "funds"
    fund_meta = json.loads((fund_dir / "meta.json").read_text(encoding="utf-8"))
    fund_events = json.loads((fund_dir / "events.json").read_text(encoding="utf-8")).get("events") or []
    stock_dir = fund_dir / "stocks"
    if stock_dir.exists():
        shutil.rmtree(stock_dir)
    events_by_stock: dict[str, list[dict]] = {}
    for event in fund_events:
        if not isinstance(event, dict) or event.get("event_type") in {"CENSORED", "UNCHANGED"}:
            continue
        code = str(event.get("stock_code") or "")
        if len(code) == 6 and code.isdigit():
            events_by_stock.setdefault(code, []).append(event)
    for code, events in events_by_stock.items():
        destination = stock_dir / code[:2] / f"{code}.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps({"meta": fund_meta, "events": events}, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
    return len(events_by_stock)


def restore_snapshot_archive(archive: Path, output: Path, expected_sha256: str) -> dict[str, int]:
    digest = hashlib.sha256()
    with archive.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest().lower() != expected_sha256.lower():
        raise ValueError("数据快照SHA-256校验失败")

    staging = output.parent / f".{output.name}-snapshot-restore"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        with tarfile.open(archive, "r:gz") as bundle:
            members = bundle.getmembers()
            for member in members:
                path = PurePosixPath(member.name.replace("\\", "/"))
                if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != "data":
                    raise ValueError(f"数据快照包含不安全路径: {member.name}")
                if not (member.isfile() or member.isdir()):
                    raise ValueError(f"数据快照包含不安全文件类型: {member.name}")
            bundle.extractall(staging, members=members)

        restored = staging / "data"
        for required in ("meta.json", "stocks.json", "funds/meta.json", "funds/events.json"):
            if not (restored / required).is_file():
                raise ValueError(f"数据快照缺少文件: {required}")
        if (output / "funds" / "meta.json").exists():
            shutil.copytree(output / "funds", restored / "funds", dirs_exist_ok=True)
        fund_stock_shards = derive_fund_event_shards(restored)
        if output.exists():
            shutil.rmtree(output)
        restored.replace(output)
        return {"fund_stock_shards": fund_stock_shards}
    finally:
        if staging.exists():
            shutil.rmtree(staging)


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

    if preserve_funds:
        shutil.copytree(output / "funds", staging / "funds", dirs_exist_ok=True)
    derived_shards = derive_fund_event_shards(staging)
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
    parser.add_argument("--url")
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--sha256")
    parser.add_argument("--output", type=Path, default=Path("public/data"))
    parser.add_argument("--workers", type=int, default=24)
    args = parser.parse_args()
    if args.archive:
        if not args.sha256:
            parser.error("--archive requires --sha256")
        result = restore_snapshot_archive(args.archive, args.output, args.sha256)
    else:
        if not args.url:
            parser.error("--url or --archive is required")
        result = restore_live_data(args.url, args.output, workers=args.workers)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
