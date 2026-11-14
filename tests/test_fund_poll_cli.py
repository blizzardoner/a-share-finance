import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from ashare_finance.fund_poll import build_parser, next_shard_index


class FundPollCliTests(unittest.TestCase):
    def test_defaults_track_one_thousand_funds_in_one_hundred_fund_batches(self):
        args = build_parser().parse_args([])

        self.assertEqual(args.target, 1000)
        self.assertEqual(args.batch_size, 100)
        self.assertEqual(args.data_root, "public/data")
        self.assertIsNone(args.shard)

    def test_next_shard_continues_from_deployed_cursor_instead_of_skipping_with_clock(self):
        with tempfile.TemporaryDirectory() as tmp:
            meta = Path(tmp) / "meta.json"
            meta.write_text(json.dumps({"shard_index": 3}), encoding="utf-8")

            shard = next_shard_index(meta, datetime(2026, 1, 1, tzinfo=timezone.utc), shard_count=10)

        self.assertEqual(shard, 4)


if __name__ == "__main__":
    unittest.main()
