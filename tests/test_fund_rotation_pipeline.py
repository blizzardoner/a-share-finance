import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from ashare_finance.fund_rotation import ensure_empty_fund_state, run_fund_rotation


class RotationClient:
    def __init__(self):
        self.detail_calls = []

    def fetch_catalog(self):
        return [
            {"code": f"00000{i}", "name": f"规模基金{i}A", "fund_type": "混合型-偏股"}
            for i in range(1, 5)
        ]

    def fetch_size_rankings(self):
        return [
            {"code": f"00000{i}", "name": f"规模基金{i}A", "end_nav": float((5 - i) * 1e8)}
            for i in range(1, 5)
        ]

    def fetch_details(self, code):
        self.detail_calls.append(code)
        index = int(code[-1])
        return {
            "code": code,
            "name": f"规模基金{index}A",
            "manager_id": f"m{index}",
            "manager_name": f"经理{index}",
            "managers": [{"id": f"m{index}", "name": f"经理{index}"}],
            "fund_size": float(5 - index),
            "risk_score": 50,
            "stability_score": 50,
            "manager_score": 50,
            "experience_score": 50,
        }

    def fetch_notices(self, code):
        return [
            {"art_code": f"{code}Q3", "title": f"规模基金{code}2026年第3季度报告", "publish_date": "2026-10-25", "pdf_url": "https://pdf.dfcfw.com/q3.pdf"},
            {"art_code": f"{code}H1", "title": f"规模基金{code}2026年中期报告", "publish_date": "2026-08-31", "pdf_url": "https://pdf.dfcfw.com/h1.pdf"},
        ]

    def fetch_notice_content(self, art_code):
        is_q3 = art_code.endswith("Q3")
        scope = "前十名" if is_q3 else "所有"
        shares = 150 if is_q3 else 100
        report = "第3季度报告" if is_q3 else "中期报告"
        return {
            "title": f"规模基金{art_code[:6]}2026年{report}",
            "pdf_url": "https://pdf.dfcfw.com/report.pdf",
            "content": f"""
            5.3.1 报告期末按公允价值占基金资产净值比例大小排序的{scope}股票投资明细
            1 600001 示例公司 {shares} 1,500.00 6.00
            5.4 报告期末按债券品种分类的债券投资组合
            """,
        }


class IncrementalFundRotationTests(unittest.TestCase):
    def _write_market_context(self, root: Path):
        (root / "companies" / "60").mkdir(parents=True)
        (root / "stocks.json").write_text(json.dumps({
            "meta": {},
            "stocks": [{"code": "600001", "name": "示例公司", "industry": "机械", "market_cap": 100e8}],
        }), encoding="utf-8")
        (root / "companies" / "60" / "600001.json").write_text(json.dumps({
            "stock": {"code": "600001", "name": "示例公司"},
            "financials": [],
            "dividends": [],
        }), encoding="utf-8")

    def test_empty_bootstrap_creates_deployable_state_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            metadata = ensure_empty_fund_state(root, "2026-11-08T00:00:00Z")

            self.assertEqual(metadata["fund_count"], 0)
            self.assertEqual(metadata["tracked_count"], 0)
            self.assertEqual(json.loads((root / "funds" / "universe.json").read_text(encoding="utf-8"))["funds"], [])
            self.assertTrue((root / "funds" / "events.json").exists())
            self.assertTrue((root / "funds" / "consensus.json").exists())

    def test_two_shards_grow_coverage_without_refetching_prior_funds(self):
        client = RotationClient()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_market_context(root)

            first = run_fund_rotation(
                data_root=root, client=client, target_count=4, batch_size=2, shard_index=0,
                refresh_universe=True, as_of=date(2026, 11, 8), generated_at="2026-11-08T00:00:00Z",
            )
            second = run_fund_rotation(
                data_root=root, client=client, target_count=4, batch_size=2, shard_index=1,
                refresh_universe=False, as_of=date(2026, 11, 8), generated_at="2026-11-08T04:00:00Z",
            )
            universe = json.loads((root / "funds" / "universe.json").read_text(encoding="utf-8"))
            consensus = json.loads((root / "funds" / "consensus.json").read_text(encoding="utf-8"))

            self.assertEqual(first["fund_count"], 4)
            self.assertEqual(first["tracked_count"], 2)
            self.assertEqual(second["tracked_count"], 4)
            self.assertEqual(client.detail_calls, ["000001", "000002", "000003", "000004"])
            self.assertEqual([fund["code"] for fund in universe["funds"]], ["000001", "000002", "000003", "000004"])
            self.assertTrue(all(fund["tracking_status"] == "tracked" for fund in universe["funds"]))
            self.assertNotIn("events", consensus["rows"][0])
            self.assertTrue((root / "funds" / "stocks" / "60" / "600001.json").exists())

    def test_rotation_cycle_is_forty_hours_for_ten_batches(self):
        client = RotationClient()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_market_context(root)
            result = run_fund_rotation(
                data_root=root, client=client, target_count=4, batch_size=2, shard_index=0,
                refresh_universe=True, as_of=date(2026, 11, 8), generated_at="2026-11-08T00:00:00Z",
            )

        self.assertEqual(result["shard_count"], 2)
        self.assertEqual(result["cycle_hours"], 8)

    def test_partial_refresh_failure_preserves_old_portfolio_but_marks_it_stale(self):
        class PartiallyFailingClient(RotationClient):
            def fetch_details(self, code):
                if code == "000001":
                    raise OSError("provider unavailable")
                return super().fetch_details(code)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_market_context(root)
            run_fund_rotation(
                data_root=root, client=RotationClient(), target_count=4, batch_size=2, shard_index=0,
                refresh_universe=True, as_of=date(2026, 11, 8), generated_at="2026-11-08T00:00:00Z",
            )
            run_fund_rotation(
                data_root=root, client=PartiallyFailingClient(), target_count=4, batch_size=2, shard_index=0,
                refresh_universe=False, as_of=date(2026, 11, 8), generated_at="2026-11-08T04:00:00Z",
            )
            funds = json.loads((root / "funds" / "universe.json").read_text(encoding="utf-8"))["funds"]
            failed = next(fund for fund in funds if fund["code"] == "000001")
            refreshed = next(fund for fund in funds if fund["code"] == "000002")

            self.assertEqual(failed["tracking_status"], "update_failed")
            self.assertEqual(failed["last_updated_at"], "2026-11-08T00:00:00Z")
            self.assertEqual(failed["last_attempted_at"], "2026-11-08T04:00:00Z")
            self.assertEqual(failed["last_update_error"], "provider unavailable")
            self.assertEqual(refreshed["tracking_status"], "tracked")
            self.assertEqual(refreshed["last_updated_at"], "2026-11-08T04:00:00Z")

    def test_universe_refresh_preserves_failure_status_for_an_unattempted_shard(self):
        class PartiallyFailingClient(RotationClient):
            def fetch_details(self, code):
                if code == "000003":
                    raise OSError("provider unavailable")
                return super().fetch_details(code)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_market_context(root)
            run_fund_rotation(
                data_root=root, client=RotationClient(), target_count=4, batch_size=2, shard_index=1,
                refresh_universe=True, as_of=date(2026, 11, 8), generated_at="2026-11-08T00:00:00Z",
            )
            run_fund_rotation(
                data_root=root, client=PartiallyFailingClient(), target_count=4, batch_size=2, shard_index=1,
                refresh_universe=False, as_of=date(2026, 11, 8), generated_at="2026-11-08T04:00:00Z",
            )
            run_fund_rotation(
                data_root=root, client=RotationClient(), target_count=4, batch_size=2, shard_index=0,
                refresh_universe=True, as_of=date(2026, 11, 8), generated_at="2026-11-08T08:00:00Z",
            )
            funds = json.loads((root / "funds" / "universe.json").read_text(encoding="utf-8"))["funds"]
            stale = next(fund for fund in funds if fund["code"] == "000003")

            self.assertEqual(stale["tracking_status"], "update_failed")
            self.assertEqual(stale["last_attempted_at"], "2026-11-08T04:00:00Z")
            self.assertEqual(stale["last_update_error"], "provider unavailable")

    def test_complete_shard_failure_aborts_without_publishing_success_metadata(self):
        class FailingClient(RotationClient):
            def fetch_details(self, code):
                raise OSError("provider unavailable")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_market_context(root)
            with self.assertRaisesRegex(RuntimeError, "无一更新成功"):
                run_fund_rotation(
                    data_root=root, client=FailingClient(), target_count=4, batch_size=2, shard_index=0,
                    refresh_universe=True, as_of=date(2026, 11, 8), generated_at="2026-11-08T00:00:00Z",
                )

            self.assertFalse((root / "funds" / "meta.json").exists())


if __name__ == "__main__":
    unittest.main()
