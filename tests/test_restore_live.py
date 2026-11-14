import json
import hashlib
import io
import tarfile
import tempfile
import unittest
from pathlib import Path

from ashare_finance.restore_live import build_live_data_paths, restore_live_data, restore_snapshot_archive


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
            (output / "funds" / "events.json").write_text('{"events":[]}', encoding="utf-8")

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

    def test_snapshot_restore_verifies_digest_and_preserves_output_on_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "snapshot.tar.gz"
            with tarfile.open(archive, "w:gz") as bundle:
                info = tarfile.TarInfo("data/meta.json")
                payload = b'{"generated_at":"snapshot"}'
                info.size = len(payload)
                bundle.addfile(info, io.BytesIO(payload))
            output = root / "data"
            output.mkdir()
            (output / "marker.txt").write_text("keep", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "SHA-256"):
                restore_snapshot_archive(archive, output, "0" * 64)

            self.assertEqual((output / "marker.txt").read_text(encoding="utf-8"), "keep")

    def test_snapshot_restore_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "snapshot.tar.gz"
            with tarfile.open(archive, "w:gz") as bundle:
                info = tarfile.TarInfo("../escape.json")
                payload = b"{}"
                info.size = len(payload)
                bundle.addfile(info, io.BytesIO(payload))
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()

            with self.assertRaisesRegex(ValueError, "不安全"):
                restore_snapshot_archive(archive, root / "data", digest)
            self.assertFalse((root / "escape.json").exists())

    def test_snapshot_restore_rejects_windows_separator_path_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "snapshot.tar.gz"
            with tarfile.open(archive, "w:gz") as bundle:
                info = tarfile.TarInfo(r"data/..\..\escape.json")
                payload = b"{}"
                info.size = len(payload)
                bundle.addfile(info, io.BytesIO(payload))
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()

            with self.assertRaisesRegex(ValueError, "不安全"):
                restore_snapshot_archive(archive, root / "data", digest)
            self.assertFalse((root / "escape.json").exists())
