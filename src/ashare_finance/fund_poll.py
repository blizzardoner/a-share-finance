from __future__ import annotations

import argparse
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path

from .fund_client import FundClient
from .fund_rotation import run_fund_rotation, shard_index_for


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="按基金规模轮询更新主动权益基金持仓")
    parser.add_argument("--data-root", default="public/data", help="已生成的静态数据目录")
    parser.add_argument("--target", type=int, default=1000, help="按组合规模排序的基金数量")
    parser.add_argument("--batch-size", type=int, default=100, help="每次轮询更新的基金数量")
    parser.add_argument("--shard", type=int, help="手动指定从0开始的分片；默认按UTC四小时窗口轮换")
    parser.add_argument("--refresh-universe", action="store_true", help="强制重新抓取并按规模生成基金池")
    parser.add_argument("--min-interval", type=float, default=0.50, help="东方财富请求最小间隔秒数")
    parser.add_argument("--report-limit", type=int, default=4, help="每只基金保留的最近定期报告数量")
    return parser


def _published_universe_count(path: Path) -> int | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return len(payload.get("funds") or [])
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def next_shard_index(meta_path: Path, now: datetime, shard_count: int) -> int:
    try:
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
        previous = int(payload["shard_index"])
        if 0 <= previous < shard_count:
            return (previous + 1) % shard_count
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        pass
    return shard_index_for(now, shard_count)


def main() -> None:
    args = build_parser().parse_args()
    if args.target <= 0 or args.batch_size <= 0:
        raise SystemExit("--target和--batch-size必须大于0")
    shard_count = math.ceil(args.target / args.batch_size)
    now = datetime.now(timezone.utc)
    data_root = Path(args.data_root)
    shard_index = (
        args.shard
        if args.shard is not None
        else next_shard_index(data_root / "funds" / "meta.json", now, shard_count)
    )
    if not 0 <= shard_index < shard_count:
        raise SystemExit(f"--shard必须在0到{shard_count - 1}之间")

    universe_path = data_root / "funds" / "universe.json"
    refresh_universe = (
        args.refresh_universe
        or shard_index == 0
        or _published_universe_count(universe_path) != args.target
    )
    cache_dir = Path(os.environ.get("ASHARE_FUND_CACHE_DIR", ".cache/fund-reports"))
    client = FundClient(min_interval=args.min_interval, progress=print, cache_dir=cache_dir)
    result = run_fund_rotation(
        data_root=data_root,
        client=client,
        target_count=args.target,
        batch_size=args.batch_size,
        shard_index=shard_index,
        refresh_universe=refresh_universe,
        report_limit=args.report_limit,
        generated_at=now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
