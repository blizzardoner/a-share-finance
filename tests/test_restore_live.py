import json
import tempfile
import unittest
from pathlib import Path

from ashare_finance.restore_live import build_live_data_paths, restore_live_data


class RestoreLiveDataTests(unittest.TestCase):
    def test_build_paths_covers_market_rankings_companies_and_fund_portfolios(self):
        paths = build_live_data_paths(["000001", "600519"], ["001194"])

        self.assertIn("meta.json", paths)
        self.assertIn("stocks.json", paths)
        self.assertIn("companies/00/000001.json", paths)
        self.assertIn("companies/60/600519.json", paths)
        self.assertIn("rankings/peg-10.json", paths)
        self.assertIn("funds/portfolios/001194.json", paths)

    def test_restore_downloads_complete_snapshot_and_preserves_newer_fund_state(self):
        live_payloads = {
            "meta.json": {"generated_at": "live-market"},
            "stocks.json": {"stocks": [{"code": "000001"}]},
            "funds/meta.json": {"generated_at": "live-funds"},
            "funds/universe.json": {"funds": [{"code": "001194"}]},
            "funds/events.json": {"events": []},
            "funds/consensus.json": {"rows": []},
        }

        def fetch(path):
            payload = live_payloads.get(path, {"path": path})
            return json.dumps(payload, ensure_ascii=False).encode("utf-8")

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "data"
            (output / "funds").mkdir(parents=True)
            (output / "funds" / "meta.json").write_text('{"generated_at":"cached-funds"}', encoding="utf-8")
            (output / "funds" / "universe.json").write_text('{"funds":[]}', encoding="utf-8")

            result = restore_live_data("https://example.test/data", output, fetch=fetch, workers=2)

            self.assertEqual(result["company_count"], 1)
            self.assertEqual(json.loads((output / "meta.json").read_text(encoding="utf-8"))["generated_at"], "live-market")
            self.assertTrue((output / "companies" / "00" / "000001.json").exists())
            self.assertEqual(json.loads((output / "funds" / "meta.json").read_text(encoding="utf-8"))["generated_at"], "cached-funds")

    def test_restore_derives_company_fund_event_shards_for_the_new_frontend(self):
        live_payloads = {
            "meta.json": {"generated_at": "live-market"},
            "stocks.json": {"stocks": [{"code": "600519"}]},
            "funds/meta.json": {"generated_at": "live-funds"},
            "funds/universe.json": {"funds": [{"code": "001194"}]},
            "funds/events.json": {
                "events": [
                    {"stock_code": "600519", "event_type": "CONFIRMED_ADD"},
                    {"stock_code": "600519", "event_type": "CENSORED"},
                ]
            },
            "funds/consensus.json": {"rows": []},
        }

        def fetch(path):
            return json.dumps(live_payloads.get(path, {"path": path}), ensure_ascii=False).encode("utf-8")

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "data"
            restore_live_data("https://example.test/data", output, fetch=fetch, workers=2)

            shard = json.loads((output / "funds" / "stocks" / "60" / "600519.json").read_text(encoding="utf-8"))
            self.assertEqual([event["event_type"] for event in shard["events"]], ["CONFIRMED_ADD"])
