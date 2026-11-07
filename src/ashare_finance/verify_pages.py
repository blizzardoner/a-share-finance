from __future__ import annotations

import argparse
import json
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any


IDENTITY_FIELDS = (
    "generated_at",
    "company_count",
    "financial_row_count",
    "dividend_row_count",
)


def metadata_matches(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    return all(field in expected and actual.get(field) == expected[field] for field in IDENTITY_FIELDS)


def verify_deployment(
    expected: dict[str, Any],
    fetch: Callable[[], dict[str, Any]],
    attempts: int,
    delay: float,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    actual: dict[str, Any] = {}
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            actual = fetch()
            last_error = None
            if metadata_matches(expected, actual):
                return actual
        except Exception as error:  # Network/CDN failures are retried within the bounded loop.
            last_error = error
        if attempt < attempts:
            sleep(delay)
    detail = f"；最后错误: {last_error}" if last_error else f"；线上值: {actual}"
    raise RuntimeError(f"线上元数据未更新到本次构建{detail}")


def fetch_metadata(url: str) -> dict[str, Any]:
    separator = "&" if "?" in url else "?"
    request = urllib.request.Request(
        f"{url}{separator}verify={time.time_ns()}",
        headers={"Cache-Control": "no-cache", "User-Agent": "a-share-finance-pages-verifier/1"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError("线上 meta.json 不是 JSON 对象")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="核对 GitHub Pages 是否已发布本次数据构建")
    parser.add_argument("--expected", required=True, type=Path, help="本次构建的 meta.json")
    parser.add_argument("--url", required=True, help="线上 meta.json URL")
    parser.add_argument("--attempts", type=int, default=20)
    parser.add_argument("--delay", type=float, default=15)
    args = parser.parse_args()

    expected = json.loads(args.expected.read_text(encoding="utf-8"))
    actual = verify_deployment(
        expected,
        fetch=lambda: fetch_metadata(args.url),
        attempts=args.attempts,
        delay=args.delay,
    )
    print(json.dumps({"verified": True, **actual}, ensure_ascii=False))


if __name__ == "__main__":
    main()
