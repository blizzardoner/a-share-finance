import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from ashare_finance.cli import run_pipeline
from ashare_finance.fund_pipeline import run_fund_pipeline


class FakeFundClient:
    def fetch_catalog(self):
        return [{"code": "000001", "name": "示例成长混合A", "fund_type": "混合型-偏股", "search_key": "SL", "pinyin": "SHILI"}]

    def fetch_rankings(self):
        return [{
            "code": "000001", "name": "示例成长混合A", "return_1y": 30,
            "return_2y": 60, "return_3y": 100, "inception_date": "2018-01-01",
        }]

    def fetch_details(self, code):
        return {
            "code": code, "name": "示例成长混合A", "manager_id": "m1", "manager_name": "甲",
            "manager_work_time": "8年", "manager_score": 80, "experience_score": 90,
            "risk_score": 70, "stability_score": 60, "fund_size": 20,
            "stock_allocation": 85, "detail_date": "2026-09-30",
        }

    def fetch_notices(self, code):
        return [
            {"art_code": "Q3", "title": "示例成长混合型证券投资基金2026年第3季度报告", "publish_date": "2026-10-25", "pdf_url": "https://example.test/q3.pdf"},
            {"art_code": "H1", "title": "示例成长混合型证券投资基金2026年中期报告", "publish_date": "2026-08-31", "pdf_url": "https://example.test/h1.pdf"},
        ]

    def fetch_notice_content(self, art_code):
        if art_code == "Q3":
            return {
                "title": "示例成长混合型证券投资基金2026年第3季度报告",
                "pdf_url": "https://example.test/q3.pdf",
                "content": """
                4.1 基金经理（或基金经理小组）简介
                姓名 职务 任本基金的基金经理期限
                甲 基金经理 2020年1月1日 -
                4.2 管理人对报告期内本基金运作遵规守信情况的说明
                5.3.1 报告期末按公允价值占基金资产净值比例大小排序的前十名股票投资明细
                1 600001 示例公司 150 1,500.00 6.00
                5.4 报告期末按债券品种分类的债券投资组合
                """,
            }
        return {
            "title": "示例成长混合型证券投资基金2026年中期报告",
            "pdf_url": "https://example.test/h1.pdf",
            "content": """
            7.3 期末按公允价值占基金资产净值比例大小排序的所有股票投资明细
            1 600001 示例公司 100 1,000.00 5.00
            7.4 报告期内股票投资组合的重大变动
            """,
        }


class ShareClassFundClient(FakeFundClient):
    def fetch_catalog(self):
        return [
            {"code": "000001", "name": "示例成长混合A", "fund_type": "混合型-偏股", "search_key": "SLA", "pinyin": "SHILIA"},
            {"code": "000002", "name": "示例成长混合C", "fund_type": "混合型-偏股", "search_key": "SLC", "pinyin": "SHILIC"},
        ]

    def fetch_rankings(self):
        return [
            {"code": "000001", "name": "示例成长混合A", "return_1y": 30, "return_3y": 100, "inception_date": "2018-01-01"},
            {"code": "000002", "name": "示例成长混合C", "return_1y": 31, "return_3y": 110, "inception_date": "2018-01-01"},
        ]

    def fetch_details(self, code):
        detail = super().fetch_details(code)
        return {**detail, "code": code, "name": "示例成长混合A" if code == "000001" else "示例成长混合C"}


class OlderBrokenReportClient(FakeFundClient):
    def fetch_notices(self, code):
        return [
            *super().fetch_notices(code),
            {"art_code": "FY", "title": "示例成长混合型证券投资基金2025年年度报告", "publish_date": "2026-03-30", "pdf_url": "https://example.test/fy.pdf"},
        ]

    def fetch_notice_content(self, art_code):
        if art_code == "FY":
            return {"title": "示例成长混合型证券投资基金2025年年度报告", "pdf_url": "https://example.test/fy.pdf", "content": "股票表解析失败"}
        return super().fetch_notice_content(art_code)


class MissingOlderReportClient(OlderBrokenReportClient):
    def fetch_notice_content(self, art_code):
        if art_code == "FY":
            raise OSError("temporary download failure")
        return super().fetch_notice_content(art_code)


class CountingCandidateClient(FakeFundClient):
    def __init__(self):
        self.detail_calls = []

    def fetch_catalog(self):
        return [
            {"code": f"00000{i}", "name": f"示例成长{i}混合A", "fund_type": "混合型-偏股"}
            for i in range(1, 5)
        ]

    def fetch_rankings(self):
        return [
            {"code": f"00000{i}", "name": f"示例成长{i}混合A", "return_1y": 30 - i, "return_3y": 110 - i, "inception_date": "2018-01-01"}
            for i in range(1, 5)
        ]

    def fetch_details(self, code):
        self.detail_calls.append(code)
        detail = super().fetch_details(code)
        return {**detail, "code": code, "manager_id": f"m{code}"}


class CorporateActionFundClient(FakeFundClient):
    def fetch_notice_content(self, art_code):
        document = super().fetch_notice_content(art_code)
        if art_code == "Q3":
            document = {**document, "content": document["content"].replace("示例公司 150", "示例公司 200")}
        return document


class StaleReportClient(FakeFundClient):
    def fetch_notices(self, code):
        return [
            {"art_code": "H1", "title": "示例成长混合型证券投资基金2026年中期报告", "publish_date": "2026-08-31", "pdf_url": "https://example.test/h1.pdf"},
            {"art_code": "Q1", "title": "示例成长混合型证券投资基金2026年第1季度报告", "publish_date": "2026-04-22", "pdf_url": "https://example.test/q1.pdf"},
        ]

    def fetch_notice_content(self, art_code):
        if art_code == "Q1":
            document = super().fetch_notice_content("Q3")
            return {**document, "title": "示例成长混合型证券投资基金2026年第1季度报告"}
        return super().fetch_notice_content(art_code)


class BackfillFundClient(CountingCandidateClient):
    def fetch_catalog(self):
        return super().fetch_catalog()[:3]

    def fetch_rankings(self):
        return super().fetch_rankings()[:3]

    def fetch_notices(self, code):
        return StaleReportClient().fetch_notices(code) if code in {"000001", "000002"} else FakeFundClient().fetch_notices(code)

    def fetch_notice_content(self, art_code):
        if art_code == "Q1":
            return StaleReportClient().fetch_notice_content(art_code)
        return FakeFundClient().fetch_notice_content(art_code)


class FundPipelineTests(unittest.TestCase):
    def test_pipeline_builds_static_fund_dataset_from_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            metadata = run_fund_pipeline(
                output=Path(tmp),
                stocks=[{"code": "600001", "name": "示例公司", "industry": "机械", "market_cap": 100e8}],
                dividends=[],
                client=FakeFundClient(),
                target_count=1,
                candidate_limit=1,
                report_limit=2,

                as_of=date(2026, 11, 8),
                generated_at="2026-11-08T00:00:00Z",
            )

            self.assertEqual(metadata["fund_count"], 1)
            self.assertEqual(metadata["report_count"], 2)
            self.assertEqual(metadata["event_count"], 1)
            events = json.loads((Path(tmp) / "funds" / "events.json").read_text(encoding="utf-8"))["events"]
            self.assertEqual(events[0]["event_type"], "CONFIRMED_ADD")
            self.assertEqual(events[0]["manager_id"], "m1")
            self.assertEqual(events[0]["manager_attribution"], "report_confirmed")
            consensus = json.loads((Path(tmp) / "funds" / "consensus.json").read_text(encoding="utf-8"))["rows"]
            self.assertEqual(consensus[0]["code"], "600001")

    def test_pipeline_chooses_a_share_class_as_portfolio_representative(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_fund_pipeline(
                output=Path(tmp), stocks=[{"code": "600001", "name": "示例公司"}], dividends=[],
                client=ShareClassFundClient(), target_count=1, candidate_limit=2, report_limit=2,
                as_of=date(2026, 11, 8), generated_at="2026-11-08T00:00:00Z",
            )
            funds = json.loads((Path(tmp) / "funds" / "universe.json").read_text(encoding="utf-8"))["funds"]

        self.assertEqual([fund["code"] for fund in funds], ["000001"])

    def test_pipeline_preserves_older_parse_failure_without_dropping_latest_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            metadata = run_fund_pipeline(
                output=Path(tmp), stocks=[{"code": "600001", "name": "示例公司"}], dividends=[],
                client=OlderBrokenReportClient(), target_count=1, candidate_limit=1, report_limit=3,
                as_of=date(2026, 11, 8), generated_at="2026-11-08T00:00:00Z",
            )
            portfolio = json.loads((Path(tmp) / "funds" / "portfolios" / "000001.json").read_text(encoding="utf-8"))

        self.assertEqual(metadata["fund_count"], 1)
        self.assertEqual(metadata["report_count"], 3)
        self.assertEqual([report["parse_status"] for report in portfolio["reports"]], ["parse_failed", "parsed", "parsed"])
        self.assertEqual(portfolio["events"][0]["event_type"], "CONFIRMED_ADD")

    def test_pipeline_records_missing_report_without_reusing_previous_document(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_fund_pipeline(
                output=Path(tmp), stocks=[{"code": "600001", "name": "示例公司"}], dividends=[],
                client=MissingOlderReportClient(), target_count=1, candidate_limit=1, report_limit=3,
                as_of=date(2026, 11, 8), generated_at="2026-11-08T00:00:00Z",
            )
            reports = json.loads((Path(tmp) / "funds" / "portfolios" / "000001.json").read_text(encoding="utf-8"))["reports"]

        self.assertEqual(reports[0]["title"], "示例成长混合型证券投资基金2025年年度报告")
        self.assertEqual(reports[0]["parse_status"], "report_missing")
        self.assertIsNone(reports[0]["source_hash"])

    def test_pipeline_stops_fetching_candidate_details_after_research_pool_is_full(self):
        client = CountingCandidateClient()
        with tempfile.TemporaryDirectory() as tmp:
            run_fund_pipeline(
                output=Path(tmp), stocks=[{"code": "600001", "name": "示例公司"}], dividends=[],
                client=client, target_count=1, candidate_limit=4, report_limit=2,
                as_of=date(2026, 11, 8), generated_at="2026-11-08T00:00:00Z",
            )

        self.assertEqual(client.detail_calls, ["000001", "000002"])

    def test_pipeline_deduplicates_revised_corporate_action_records(self):
        duplicate_action = {
            "code": "600001", "period": "2026-06-30", "ex_date": "2026-07-15",
            "plan_date": "2026-06-01", "bonus_per_10": 0, "transfer_per_10": 10,
        }
        with tempfile.TemporaryDirectory() as tmp:
            run_fund_pipeline(
                output=Path(tmp), stocks=[{"code": "600001", "name": "示例公司"}],
                dividends=[duplicate_action, {**duplicate_action, "plan_date": "2026-06-10"}],
                client=CorporateActionFundClient(), target_count=1, candidate_limit=1, report_limit=2,
                as_of=date(2026, 11, 8), generated_at="2026-11-08T00:00:00Z",
            )
            event = json.loads((Path(tmp) / "funds" / "events.json").read_text(encoding="utf-8"))["events"][0]

        self.assertEqual(event["share_factor"], 2)
        self.assertEqual(event["event_type"], "UNCHANGED")

    def test_pipeline_rejects_stale_report_set_missing_expected_latest_quarter(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, "覆盖不足"):
                run_fund_pipeline(
                    output=Path(tmp), stocks=[{"code": "600001", "name": "示例公司"}], dividends=[],
                    client=StaleReportClient(), target_count=1, candidate_limit=1, report_limit=2,
                    as_of=date(2026, 11, 8), generated_at="2026-11-08T00:00:00Z",
                )

    def test_pipeline_backfills_invalid_selected_fund_from_next_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            metadata = run_fund_pipeline(
                output=Path(tmp), stocks=[{"code": "600001", "name": "示例公司"}], dividends=[],
                client=BackfillFundClient(), target_count=1, candidate_limit=3, report_limit=2,
                as_of=date(2026, 11, 8), generated_at="2026-11-08T00:00:00Z",
            )
            funds = json.loads((Path(tmp) / "funds" / "universe.json").read_text(encoding="utf-8"))["funds"]

        self.assertEqual(metadata["fund_count"], 1)
        self.assertEqual([fund["code"] for fund in funds], ["000003"])

    @patch("ashare_finance.fund_pipeline.FundClient")
    def test_default_client_uses_configured_persistent_report_cache(self, client_class):
        client_class.return_value = FakeFundClient()
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"ASHARE_FUND_CACHE_DIR": tmp}):
            run_fund_pipeline(
                output=Path(tmp) / "out", stocks=[{"code": "600001", "name": "示例公司"}], dividends=[],
                target_count=1, candidate_limit=1, report_limit=2,
                as_of=date(2026, 11, 8), generated_at="2026-11-08T00:00:00Z",
            )

        self.assertEqual(client_class.call_args.kwargs["cache_dir"], Path(tmp))

    @patch("ashare_finance.cli.run_fund_pipeline")
    @patch("ashare_finance.cli.EastmoneyClient")
    def test_a_share_pipeline_invokes_fund_pipeline_when_explicitly_enabled(self, client_class, fund_pipeline):
        client = client_class.return_value
        client.SECURITIES_URL = "securities"
        client.DATACENTER_URL = "datacenter"
        client.fetch_org_directory.return_value = [{
            "SECURITY_CODE": "000001", "SECUCODE": "000001.SZ", "SECURITY_NAME_ABBR": "示例公司",
            "SECURITY_TYPE": "深交所主板A股", "LISTING_DATE": "2000-01-01",
        }]
        client.fetch_selected_stock_quotes.return_value = [{
            "f12": "000001", "f14": "示例公司", "f13": 0, "f2": 10, "f9": 20, "f20": 100e8,
        }]
        client.fetch_pages.return_value = []

        with tempfile.TemporaryDirectory() as tmp:
            run_pipeline(Path(tmp), codes={"000001"}, min_interval=0, include_funds=True, fund_target_count=5)

        self.assertEqual(fund_pipeline.call_count, 1)
        kwargs = fund_pipeline.call_args.kwargs
        self.assertEqual(kwargs["target_count"], 5)
        self.assertEqual(kwargs["stocks"][0]["code"], "000001")


if __name__ == "__main__":
    unittest.main()
