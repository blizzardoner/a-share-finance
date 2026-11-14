import unittest
from datetime import datetime, timedelta, timezone

from ashare_finance.fund_rotation import build_size_universe, select_shard, shard_index_for


class FundSizeUniverseTests(unittest.TestCase):
    def test_build_size_universe_sums_share_classes_and_orders_by_total_scale(self):
        catalog = {
            "000001": {"fund_type": "混合型-偏股"},
            "000002": {"fund_type": "混合型-偏股"},
            "000003": {"fund_type": "股票型"},
            "000004": {"fund_type": "指数型-股票"},
            "000005": {"fund_type": "混合型-灵活"},
        }
        rows = [
            {"code": "000001", "name": "甲基金A", "end_nav": 40e8},
            {"code": "000002", "name": "甲基金C", "end_nav": 10e8},
            {"code": "000003", "name": "乙基金", "end_nav": 30e8},
            {"code": "000004", "name": "指数基金A", "end_nav": 100e8},
            {"code": "000005", "name": "丙基金A", "end_nav": 20e8},
        ]

        funds = build_size_universe(rows, catalog, target_count=2)

        self.assertEqual([fund["code"] for fund in funds], ["000001", "000003"])
        self.assertEqual([fund["fund_size"] for fund in funds], [50.0, 30.0])
        self.assertEqual(funds[0]["share_class_count"], 2)
        self.assertEqual(funds[0]["selection_basis"], "组合各份额规模合计")
        self.assertEqual(funds[0]["screen_score"], 100.0)
        self.assertEqual(funds[0]["score_label"], "规模研究池等权分（非基金评级）")


class FundShardRotationTests(unittest.TestCase):
    def test_four_hour_shard_rotates_and_wraps_ten_batches(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        first = shard_index_for(start, shard_count=10)

        self.assertEqual(shard_index_for(start + timedelta(hours=4), 10), (first + 1) % 10)
        self.assertEqual(shard_index_for(start + timedelta(hours=40), 10), first)

    def test_select_shard_returns_one_hundred_funds_from_thousand(self):
        funds = [{"code": f"{index:06d}"} for index in range(1000)]

        shard = select_shard(funds, batch_size=100, shard_index=9)

        self.assertEqual(len(shard), 100)
        self.assertEqual(shard[0]["code"], "000900")
        self.assertEqual(shard[-1]["code"], "000999")


if __name__ == "__main__":
    unittest.main()
