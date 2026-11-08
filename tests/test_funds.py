import unittest
import json
import tempfile
from pathlib import Path

from ashare_finance.funds import build_holding_events, build_stock_consensus, normalize_portfolio_name, parse_fund_report, select_research_funds, write_fund_dataset


class FundSelectionTests(unittest.TestCase):
    def test_select_research_funds_deduplicates_share_classes_and_limits_manager_copies(self):
        rank_rows = [
            {"code": "000001", "name": "优选成长混合A", "return_1y": 30, "return_3y": 100, "inception_date": "2018-01-01"},
            {"code": "000002", "name": "优选成长混合C", "return_1y": 29, "return_3y": 99, "inception_date": "2019-01-01"},
            {"code": "000003", "name": "科技指数A", "return_1y": 80, "return_3y": 200, "inception_date": "2018-01-01"},
            {"code": "000004", "name": "稳健价值混合A", "return_1y": 20, "return_3y": 80, "inception_date": "2017-01-01"},
            {"code": "000005", "name": "周期精选股票A", "return_1y": 25, "return_3y": 70, "inception_date": "2016-01-01"},
            {"code": "000006", "name": "小规模成长混合A", "return_1y": 60, "return_3y": 150, "inception_date": "2017-01-01"},
        ]
        catalog = {
            "000001": {"fund_type": "混合型-偏股"},
            "000002": {"fund_type": "混合型-偏股"},
            "000003": {"fund_type": "指数型-股票"},
            "000004": {"fund_type": "混合型-灵活"},
            "000005": {"fund_type": "股票型"},
            "000006": {"fund_type": "混合型-偏股"},
        }
        details = {
            "000001": {"manager_id": "m1", "manager_name": "甲", "manager_score": 80, "experience_score": 90, "risk_score": 70, "stability_score": 60, "fund_size": 20},
            "000002": {"manager_id": "m1", "manager_name": "甲", "manager_score": 80, "experience_score": 90, "risk_score": 70, "stability_score": 60, "fund_size": 18},
            "000003": {"manager_id": "m2", "manager_name": "乙", "manager_score": 90, "experience_score": 90, "risk_score": 90, "stability_score": 90, "fund_size": 50},
            "000004": {"manager_id": "m1", "manager_name": "甲", "manager_score": 75, "experience_score": 80, "risk_score": 75, "stability_score": 75, "fund_size": 12},
            "000005": {"manager_id": "m1", "manager_name": "甲", "manager_score": 70, "experience_score": 70, "risk_score": 80, "stability_score": 80, "fund_size": 10},
            "000006": {"manager_id": "m3", "manager_name": "丙", "manager_score": 95, "experience_score": 95, "risk_score": 95, "stability_score": 95, "fund_size": 3},
        }

        selected = select_research_funds(
            rank_rows,
            catalog,
            details,
            limit=10,
            minimum_size=5,
            max_per_manager=2,
            as_of="2026-11-08",
        )

        self.assertEqual(normalize_portfolio_name("优选成长混合C"), "优选成长混合")
        self.assertEqual([row["code"] for row in selected], ["000001", "000004"])
        self.assertEqual(selected[0]["portfolio_name"], "优选成长混合")
        self.assertGreater(selected[0]["screen_score"], 0)
        self.assertEqual(selected[0]["score_label"], "研究池筛选分（非基金评级）")


class FundReportParsingTests(unittest.TestCase):
    def test_parse_quarter_report_marks_top10_scope_and_extracts_holdings(self):
        content = """
        长安裕隆灵活配置混合型证券投资基金2026年第3季度报告
        5.3 期末按公允价值占基金资产净值比例大小排序的股票投资明细
        5.3.1 报告期末按公允价值占基金资产净值比例大小排序的前十名股票投资明细
        序号 股票代码 股票名称 数量(股) 公允价值(元) 占基金资产净值比例（％）
        1 688825 长鑫科技 200,000 10,958,000.00 7.23
        2 603259 药明康德 56,000 9,371,040.00 6.19
        5.4 报告期末按债券品种分类的债券投资组合
        """

        report = parse_fund_report(
            fund_code="005744",
            title="长安裕隆灵活配置混合型证券投资基金2026年第3季度报告",
            content=content,
            announcement_date="2026-10-27",
            source_url="https://example.test/q3.pdf",
        )

        self.assertEqual(report["report_period"], "2026-09-30")
        self.assertEqual(report["report_type"], "Q3")
        self.assertEqual(report["scope"], "TOP10")
        self.assertEqual(report["disclosed_nav_pct"], 13.42)
        self.assertEqual(report["holdings"][0], {
            "rank": 1,
            "stock_code": "688825",
            "stock_name": "长鑫科技",
            "shares": 200000.0,
            "market_value": 10958000.0,
            "nav_ratio": 7.23,
        })
        self.assertEqual(report["source_url"], "https://example.test/q3.pdf")
        self.assertEqual(report["parser_version"], "fund-report-v1")
        self.assertEqual(report["parse_status"], "parsed")
        self.assertEqual(len(report["source_hash"]), 64)

    def test_parse_interim_report_marks_full_scope(self):
        content = """
        7.3 期末按公允价值占基金资产净值比例大小排序的所有股票投资明细
        1 600519 贵州茅台 10,000 15,000,000.00 4.20
        2 000001 平安银行 20,000 200,000.00 0.06
        7.4 报告期内股票投资组合的重大变动
        """

        report = parse_fund_report(
            fund_code="000001",
            title="示例基金2026年中期报告",
            content=content,
            announcement_date="2026-08-31T00:00:00",
            source_url="https://example.test/h1.pdf",
        )

        self.assertEqual((report["report_period"], report["report_type"], report["scope"]), ("2026-06-30", "H1", "FULL"))
        self.assertEqual(len(report["holdings"]), 2)

    def test_parse_report_rejects_missing_or_empty_stock_section(self):
        with self.assertRaisesRegex(ValueError, "未找到股票披露范围"):
            parse_fund_report(
                fund_code="000001",
                title="示例基金2026年第3季度报告",
                content="只有债券投资明细",
                announcement_date="2026-10-25",
                source_url="https://example.test/invalid.pdf",
            )

    def test_full_report_rejects_partial_holding_table_parse(self):
        content = """
        7.3 期末按公允价值占基金资产净值比例大小排序的所有股票投资明细
        1 600001 正常公司 100 1,000.00 5.00
        2 600002 漂移公司 无法解析的数值 4.00
        7.4 报告期内股票投资组合的重大变动
        """

        with self.assertRaisesRegex(ValueError, "部分解析"):
            parse_fund_report(
                fund_code="000001", title="示例基金2026年中期报告", content=content,
                announcement_date="2026-08-31", source_url="https://example.test/h1.pdf",
            )

    def test_parse_report_uses_actual_table_after_table_of_contents_marker(self):
        content = """
        7.3 期末按公允价值占基金资产净值比例大小排序的所有股票投资明细 ...... 38
        7.4 报告期内股票投资组合的重大变动 ...... 39
        §7 投资组合报告
        7.3 期末按公允价值占基金资产净值比例大小排序的所有股票投资明细
        1 300502 新易盛 1,001,401 389,544,989.00 9.86
        7.4 报告期内股票投资组合的重大变动
        """

        report = parse_fund_report(
            fund_code="162201", title="宏利成长混合2026年中期报告", content=content,
            announcement_date="2026-08-31", source_url="https://example.test/h1.pdf",
        )

        self.assertEqual(report["holdings"][0]["shares"], 1001401.0)

    def test_parse_report_splits_adjacent_share_and_market_value_columns(self):
        content = """
        5.3.1 报告期末按公允价值占基金资产净值比例大小排序的前十名股票投资明细
        1 300308 中际旭创 721,500583,289,460.00 8.87
        5.4 报告期末按债券品种分类的债券投资组合
        """

        report = parse_fund_report(
            fund_code="001194", title="景顺长城稳健回报混合2026年第3季度报告", content=content,
            announcement_date="2026-10-27", source_url="https://example.test/q3.pdf",
        )

        self.assertEqual(report["holdings"][0]["shares"], 721500.0)
        self.assertEqual(report["holdings"][0]["market_value"], 583289460.0)

    def test_parse_report_accepts_split_scope_marker_and_stock_name_with_space(self):
        content = """
        5.3.1 报告期末按公允价值占基金资产净值比例大小排序的前十名股票投资明 细
        8 002602 ST 华通 8,752,300181,260,133.00 2.99
        5.4 报告期末按债券品种分类的债券投资组合
        """

        report = parse_fund_report(
            fund_code="001194", title="景顺长城稳健回报混合2025年第3季度报告", content=content,
            announcement_date="2025-10-27", source_url="https://example.test/q3.pdf",
        )

        self.assertEqual(report["scope"], "TOP10")
        self.assertEqual(report["holdings"][0]["stock_name"], "ST 华通")
        self.assertEqual(report["holdings"][0]["shares"], 8752300.0)

    def test_parse_report_confirms_known_manager_only_from_report_manager_table(self):
        content = """
        4.1 基金经理（或基金经理小组）简介
        姓名 职务 任本基金的基金经理期限
        旧经理 基金经理 2025年4月10日 -
        共同经理 基金经理 2026年1月1日 -
        4.2 管理人对报告期内本基金运作遵规守信情况的说明
        5.3.1 报告期末按公允价值占基金资产净值比例大小排序的前十名股票投资明细
        1 600001 示例公司 100 1,000.00 5.00
        5.4 报告期末按债券品种分类的债券投资组合
        """

        report = parse_fund_report(
            fund_code="000001", title="示例基金2026年第3季度报告", content=content,
            announcement_date="2026-10-25", source_url="https://example.test/q3.pdf",
            known_manager_id="old", known_manager_name="旧经理",
            known_managers=[
                {"id": "old", "name": "旧经理"},
                {"id": "co", "name": "共同经理"},
                {"id": "future", "name": "未出现经理"},
            ],
        )

        self.assertEqual(report["manager_id"], "old")
        self.assertEqual(report["manager_name"], "旧经理")
        self.assertEqual(report["manager_ids"], ["old", "co"])
        self.assertEqual(report["manager_names"], ["旧经理", "共同经理"])


class HoldingEventTests(unittest.TestCase):
    def test_full_to_top10_classifies_confirmed_new_without_inventing_exits(self):
        fund = {"code": "F1", "name": "示例基金", "manager_id": "m1", "manager_name": "甲", "screen_score": 88}
        reports = [
            {
                "report_period": "2026-06-30", "scope": "FULL", "announcement_date": "2026-08-31",
                "source_url": "https://example.test/h1.pdf",
                "holdings": [
                    {"rank": 1, "stock_code": "600001", "stock_name": "旧持仓", "shares": 100, "market_value": 1000, "nav_ratio": 5},
                    {"rank": 2, "stock_code": "600002", "stock_name": "跌出前十", "shares": 80, "market_value": 800, "nav_ratio": 4},
                    {"rank": 20, "stock_code": "600004", "stock_name": "范围外未知", "shares": 10, "market_value": 100, "nav_ratio": 0.5},
                ],
            },
            {
                "report_period": "2026-09-30", "scope": "TOP10", "announcement_date": "2026-10-25",
                "source_url": "https://example.test/q3.pdf",
                "holdings": [
                    {"rank": 1, "stock_code": "600001", "stock_name": "旧持仓", "shares": 190, "market_value": 2200, "nav_ratio": 6},
                    {"rank": 2, "stock_code": "600003", "stock_name": "确认新建", "shares": 50, "market_value": 700, "nav_ratio": 3},
                ],
            },
        ]
        actions = {"600001": [{"ex_date": "2026-07-15", "bonus_per_10": 0, "transfer_per_10": 10}]}

        events = build_holding_events(fund, reports, actions)
        by_code = {row["stock_code"]: row for row in events}

        self.assertEqual(by_code["600001"]["event_type"], "CONFIRMED_REDUCE")
        self.assertEqual(by_code["600001"]["previous_shares_adjusted"], 200)
        self.assertEqual(by_code["600003"]["event_type"], "CONFIRMED_NEW")
        self.assertEqual(by_code["600002"]["event_type"], "DROPPED_FROM_TOP10")
        self.assertEqual(by_code["600004"]["event_type"], "CENSORED")
        self.assertTrue(all(row["report_period"] == "2026-09-30" for row in events))
        self.assertTrue(all(row["announcement_date"] == "2026-10-25" for row in events))

    def test_event_uses_report_period_manager_instead_of_current_profile_manager(self):
        fund = {
            "code": "F1", "name": "示例基金", "manager_id": "new", "manager_name": "新经理",
            "screen_score": 80,
        }
        reports = [
            {
                "report_period": "2026-06-30", "scope": "FULL", "source_url": "https://example.test/h1.pdf",
                "holdings": [{"rank": 1, "stock_code": "600001", "stock_name": "甲公司", "shares": 100, "nav_ratio": 5}],
            },
            {
                "report_period": "2026-09-30", "scope": "TOP10", "source_url": "https://example.test/q3.pdf",
                "announcement_date": "2026-10-25", "manager_id": "old", "manager_name": "旧经理",
                "holdings": [{"rank": 1, "stock_code": "600001", "stock_name": "甲公司", "shares": 120, "nav_ratio": 6}],
            },
        ]

        event = build_holding_events(fund, reports)[0]

        self.assertEqual(event["manager_id"], "old")
        self.assertEqual(event["manager_name"], "旧经理")

    def test_top10_to_full_confirms_exit_but_not_new_for_previously_censored_stock(self):
        reports = [
            {"report_period": "2026-03-31", "scope": "TOP10", "announcement_date": "2026-04-20", "source_url": "q1", "holdings": [
                {"rank": 1, "stock_code": "600001", "stock_name": "退出股", "shares": 100, "market_value": 1000, "nav_ratio": 5},
            ]},
            {"report_period": "2026-06-30", "scope": "FULL", "announcement_date": "2026-08-31", "source_url": "h1", "holdings": [
                {"rank": 20, "stock_code": "600002", "stock_name": "此前未知", "shares": 50, "market_value": 500, "nav_ratio": 1},
            ]},
        ]

        events = build_holding_events({"code": "F1", "name": "示例基金"}, reports)
        by_code = {row["stock_code"]: row["event_type"] for row in events}

        self.assertEqual(by_code, {"600001": "CONFIRMED_EXIT", "600002": "CENSORED"})


class FundConsensusTests(unittest.TestCase):
    def test_consensus_counts_independent_managers_once_and_ignores_unknown_stocks(self):
        events = [
            {"fund_code": "F1", "manager_id": "m1", "manager_name": "甲", "screen_score": 90, "stock_code": "600001", "stock_name": "共识公司", "event_type": "CONFIRMED_NEW", "confidence": 1, "report_period": "2026-09-30"},
            {"fund_code": "F2", "manager_id": "m1", "manager_name": "甲", "screen_score": 80, "stock_code": "600001", "stock_name": "共识公司", "event_type": "CONFIRMED_ADD", "confidence": 0.85, "report_period": "2026-09-30"},
            {"fund_code": "F3", "manager_id": "m2", "manager_name": "乙", "screen_score": 80, "stock_code": "600001", "stock_name": "共识公司", "event_type": "CONFIRMED_ADD", "confidence": 0.85, "report_period": "2026-09-30"},
            {"fund_code": "F4", "manager_id": "m3", "manager_name": "丙", "screen_score": 70, "stock_code": "600001", "stock_name": "共识公司", "event_type": "ENTERED_TOP10", "confidence": 0.55, "report_period": "2026-09-30"},
            {"fund_code": "F5", "manager_id": "m4", "manager_name": "丁", "screen_score": 99, "stock_code": "999999", "stock_name": "非A股目录", "event_type": "CONFIRMED_NEW", "confidence": 1, "report_period": "2026-09-30"},
        ]

        rows = build_stock_consensus(events, [{"code": "600001", "name": "共识公司", "industry": "机械", "market_cap": 100e8}])

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["code"], "600001")
        self.assertEqual(rows[0]["independent_manager_count"], 3)
        self.assertEqual(rows[0]["fund_count"], 4)
        self.assertEqual(rows[0]["confirmed_event_count"], 3)
        self.assertAlmostEqual(rows[0]["consensus_score"], 163.65)

    def test_unknown_managers_collapse_to_one_unverified_contributor(self):
        events = [
            {"fund_code": "F1", "manager_id": None, "screen_score": 80, "stock_code": "600001", "stock_name": "共识公司", "event_type": "CONFIRMED_NEW", "confidence": 1, "report_period": "2026-09-30"},
            {"fund_code": "F2", "manager_id": None, "screen_score": 70, "stock_code": "600001", "stock_name": "共识公司", "event_type": "CONFIRMED_NEW", "confidence": 1, "report_period": "2026-09-30"},
        ]

        row = build_stock_consensus(events, [{"code": "600001", "name": "共识公司"}])[0]

        self.assertEqual(row["independent_manager_count"], 0)
        self.assertEqual(row["unverified_manager_event_count"], 2)
        self.assertEqual(row["consensus_score"], 80)

    def test_multi_manager_event_splits_weight_and_deduplicates_each_manager(self):
        events = [
            {"fund_code": "F1", "manager_ids": ["m1", "m2"], "manager_names": ["甲", "乙"], "screen_score": 100, "stock_code": "600001", "stock_name": "共识公司", "event_type": "CONFIRMED_NEW", "confidence": 1, "report_period": "2026-09-30"},
            {"fund_code": "F2", "manager_ids": ["m1"], "manager_names": ["甲"], "screen_score": 80, "stock_code": "600001", "stock_name": "共识公司", "event_type": "CONFIRMED_NEW", "confidence": 1, "report_period": "2026-09-30"},
        ]

        row = build_stock_consensus(events, [{"code": "600001", "name": "共识公司"}])[0]

        self.assertEqual(row["independent_manager_count"], 2)
        self.assertEqual(row["consensus_score"], 130)


class FundDatasetTests(unittest.TestCase):
    def test_write_fund_dataset_emits_static_index_events_consensus_and_portfolios(self):
        funds = [{"code": "000001", "name": "示例基金", "manager_name": "甲", "screen_score": 88}]
        reports = {"000001": [{"report_period": "2026-09-30", "scope": "TOP10", "holdings": []}]}
        events = [{"fund_code": "000001", "stock_code": "600001", "event_type": "CONFIRMED_NEW"}]
        consensus = [{"code": "600001", "consensus_score": 88}]

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            stale = output / "funds" / "stale.json"
            stale.parent.mkdir(parents=True)
            stale.write_text("stale", encoding="utf-8")

            metadata = write_fund_dataset(output, funds, reports, events, consensus, "2026-11-08T00:00:00Z")

            self.assertFalse(stale.exists())
            self.assertEqual(metadata["fund_count"], 1)
            self.assertEqual(metadata["event_count"], 1)
            self.assertEqual(json.loads((output / "funds" / "universe.json").read_text(encoding="utf-8"))["funds"][0]["code"], "000001")
            self.assertEqual(json.loads((output / "funds" / "events.json").read_text(encoding="utf-8"))["events"][0]["stock_code"], "600001")
            self.assertEqual(json.loads((output / "funds" / "consensus.json").read_text(encoding="utf-8"))["rows"][0]["code"], "600001")
            portfolio = json.loads((output / "funds" / "portfolios" / "000001.json").read_text(encoding="utf-8"))
            self.assertEqual(portfolio["reports"][0]["report_period"], "2026-09-30")

    def test_write_fund_dataset_rejects_non_six_digit_fund_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(ValueError, "基金代码"):
                write_fund_dataset(
                    root,
                    [{"code": "../../escape", "name": "恶意基金"}],
                    {"../../escape": []},
                    [],
                    [],
                    "2026-11-08T00:00:00Z",
                )
            self.assertFalse((root / "escape.json").exists())

    def test_write_fund_dataset_counts_reports_only_for_published_funds(self):
        funds = [{"code": "000001", "name": "入选基金"}]
        reports = {
            "000001": [{"parse_status": "parsed"}],
            "000002": [{"parse_status": "parsed"}, {"parse_status": "parse_failed"}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            metadata = write_fund_dataset(Path(tmp), funds, reports, [], [], "2026-11-08T00:00:00Z")

        self.assertEqual(metadata["report_count"], 1)
        self.assertEqual(metadata["parse_failure_count"], 0)


if __name__ == "__main__":
    unittest.main()
