import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from ashare_finance.pipeline import (
    build_company_documents,
    build_dividend_rankings,
    build_performance_rankings,
    dividend_ranking_end_year,
    dedupe_financials,
    filter_recent_companies,
    merge_stock_quotes,
    normalize_dividend,
    normalize_financial,
    normalize_stock,
    ranking_end_year,
    require_market_caps,
    write_dataset,
    year_windows,
)


class NormalizationTests(unittest.TestCase):
    def test_normalize_stock_derives_exchange_and_caps(self):
        row = {"f12": "600519", "f14": "贵州茅台", "f13": 1, "f2": 1500.5, "f3": 1.2, "f20": 1_800_000_000_000, "f21": 1_800_000_000_000, "f100": "白酒"}
        self.assertEqual(normalize_stock(row), {
            "code": "600519", "secucode": "600519.SH", "name": "贵州茅台",
            "exchange": "SH", "industry": "白酒", "price": 1500.5,
            "change_pct": 1.2, "market_cap": 1_800_000_000_000,
            "float_market_cap": 1_800_000_000_000,
        })

    def test_normalize_stock_accepts_stable_org_directory_rows(self):
        row = {
            "SECURITY_CODE": "000001", "SECUCODE": "000001.SZ", "SECURITY_NAME_ABBR": "平安银行",
            "SECURITY_TYPE": "深交所主板A股", "EM2016": "金融-银行-股份制与城商行",
            "TRADE_MARKET": "深圳证券交易所", "LISTING_DATE": "1991-04-03 00:00:00",
        }
        item = normalize_stock(row)
        self.assertEqual(item["code"], "000001")
        self.assertEqual(item["secucode"], "000001.SZ")
        self.assertEqual(item["name"], "平安银行")
        self.assertEqual(item["industry"], "金融-银行-股份制与城商行")
        self.assertEqual(item["listing_date"], "1991-04-03")

    def test_normalize_financial_keeps_key_income_balance_and_cashflow_fields(self):
        row = {
            "SECURITY_CODE": "600519", "REPORT_DATE": "2025-12-31 00:00:00",
            "REPORT_DATE_NAME": "2025年报", "REPORT_TYPE": "年报", "NOTICE_DATE": "2026-03-31 00:00:00",
            "TOTALOPERATEREVE": 180_000_000_000, "PARENTNETPROFIT": 90_000_000_000,
            "KCFJCXSYJLR": 88_000_000_000, "TOTALOPERATEREVETZ": 10.2,
            "PARENTNETPROFITTZ": 12.3, "EPSJB": 71.1, "BPS": 230.0,
            "ROEJQ": 32.1, "XSMLL": 91.2, "XSJLL": 50.0, "ZCFZL": 15.1,
            "TOTAL_ASSETS_PK": 300_000_000_000, "LIABILITY": 45_000_000_000,
            "TOTAL_EQUITY_PK": 255_000_000_000, "NETCASH_OPERATE_PK": 95_000_000_000,
            "NETCASH_INVEST_PK": -20_000_000_000, "NETCASH_FINANCE_PK": -70_000_000_000,
        }
        item = normalize_financial(row)
        self.assertEqual(item["period"], "2025-12-31")
        self.assertEqual(item["revenue"], 180_000_000_000)
        self.assertEqual(item["net_profit"], 90_000_000_000)
        self.assertEqual(item["assets"], 300_000_000_000)
        self.assertEqual(item["operating_cash_flow"], 95_000_000_000)
        self.assertEqual(item["roe"], 32.1)

    def test_normalize_dividend_converts_dates_and_plan(self):
        row = {
            "SECURITY_CODE": "600519", "REPORT_DATE": "2025-12-31 00:00:00",
            "ASSIGN_PROGRESS": "实施分配", "IMPL_PLAN_PROFILE": "10派300元(含税)",
            "PRETAX_BONUS_RMB": 300, "BONUS_RATIO": 0, "IT_RATIO": 0,
            "EQUITY_RECORD_DATE": "2026-06-20 00:00:00", "EX_DIVIDEND_DATE": "2026-06-21 00:00:00",
        }
        item = normalize_dividend(row)
        self.assertEqual(item["period"], "2025-12-31")
        self.assertEqual(item["cash_per_10"], 300)
        self.assertEqual(item["ex_date"], "2026-06-21")


class DatasetTests(unittest.TestCase):
    def test_ranking_end_year_waits_for_annual_report_deadline(self):
        self.assertEqual(ranking_end_year(date(2026, 4, 30)), 2024)
        self.assertEqual(ranking_end_year(date(2026, 5, 1)), 2025)

    def test_dividend_ranking_waits_for_implementation_season_to_finish(self):
        self.assertEqual(dividend_ranking_end_year(date(2026, 8, 31)), 2024)
        self.assertEqual(dividend_ranking_end_year(date(2026, 9, 1)), 2025)

    def test_merge_stock_quotes_preserves_directory_identity_and_adds_market_values(self):
        stocks = [{
            "code": "600519", "name": "贵州茅台", "industry": "白酒",
            "listing_date": "2001-08-27", "price": None, "market_cap": None,
        }]
        quotes = [{
            "code": "600519", "name": "贵州茅台", "industry": "饮料制造",
            "price": 1500.5, "market_cap": 1_800_000_000_000, "change_pct": 1.2,
        }]

        merged = merge_stock_quotes(stocks, quotes)

        self.assertEqual(merged[0]["listing_date"], "2001-08-27")
        self.assertEqual(merged[0]["industry"], "白酒")
        self.assertEqual(merged[0]["price"], 1500.5)
        self.assertEqual(merged[0]["market_cap"], 1_800_000_000_000)

    def test_merge_stock_quotes_excludes_directory_rows_outside_current_quote_universe(self):
        stocks = [
            {"code": "000001", "name": "平安银行", "listing_date": "1991-04-03"},
            {"code": "000003", "name": "PT金田A", "listing_date": "1991-07-03"},
        ]
        quotes = [{"code": "000001", "price": 10.5, "market_cap": 200_000_000_000}]

        merged = merge_stock_quotes(stocks, quotes)

        self.assertEqual([stock["code"] for stock in merged], ["000001"])

    def test_merge_stock_quotes_rejects_current_quotes_missing_directory_identity(self):
        stocks = [{"code": "000001", "name": "平安银行"}]
        quotes = [{"code": "600519", "price": 1500, "market_cap": 1_800_000_000_000}]

        with self.assertRaisesRegex(RuntimeError, "行情代码缺少公司目录 600519"):
            merge_stock_quotes(stocks, quotes)

        with self.assertRaisesRegex(RuntimeError, "当前行情目录为空"):
            merge_stock_quotes(stocks, [])

        with self.assertRaisesRegex(RuntimeError, "公司目录为空"):
            merge_stock_quotes([], [])

    def test_require_market_caps_rejects_current_stocks_missing_usable_quotes(self):
        invalid_values = (None, 0, -1, float("nan"), float("inf"))
        for value in invalid_values:
            with self.subTest(value=value):
                stocks = [
                    {"code": "000001", "market_cap": 100},
                    {"code": "600519", "market_cap": value},
                ]
                with self.assertRaisesRegex(RuntimeError, "市值缺失 600519"):
                    require_market_caps(stocks)

    def test_dividend_rankings_average_implemented_cash_over_full_year_window(self):
        stocks = [
            {
                "code": "000001", "name": "稳定分红", "listing_date": "2000-01-01",
                "market_cap": 1_000,
            },
            {
                "code": "000002", "name": "低分红", "listing_date": "2000-01-01",
                "market_cap": 1_000,
            },
            {
                "code": "000003", "name": "新股", "listing_date": "2025-06-01",
                "market_cap": 1_000,
            },
        ]
        dividends = [
            {"code": "000001", "period": "2025-12-31", "progress": "实施分配", "cash_per_10": 2, "total_shares": 100, "ex_date": "2026-06-01", "plan_date": "2026-04-01"},
            # The same implemented plan must not be counted twice.
            {"code": "000001", "period": "2025-12-31", "progress": "实施分配", "cash_per_10": 2, "total_shares": 100, "ex_date": "2026-06-01", "plan_date": "2026-04-01"},
            # A superseded version for the same corporate action must not be added.
            {"code": "000001", "period": "2025-12-31", "progress": "实施分配", "cash_per_10": 1.5, "total_shares": 100, "ex_date": "2026-06-01", "plan_date": "2026-03-01"},
            {"code": "000001", "period": "2024-06-30", "progress": "实施分配", "cash_per_10": 1, "total_shares": 100, "ex_date": "2024-09-01"},
            {"code": "000001", "period": "2023-12-31", "progress": "董事会预案", "cash_per_10": 9, "total_shares": 100},
            {"code": "000002", "period": "2025-12-31", "progress": "实施分配", "cash_per_10": 1, "total_shares": 100, "ex_date": "2026-06-02"},
            {"code": "000003", "period": "2025-12-31", "progress": "实施分配", "cash_per_10": 20, "total_shares": 100, "ex_date": "2026-06-03"},
        ]

        rankings = build_dividend_rankings(stocks, dividends, end_year=2025, periods=(1, 3))

        self.assertEqual([row["code"] for row in rankings[1]], ["000001", "000002"])
        self.assertAlmostEqual(rankings[1][0]["yield"], 2.0)
        self.assertAlmostEqual(rankings[3][0]["yield"], 1.0)
        self.assertEqual(rankings[3][0]["dividend_years"], 2)
        self.assertNotIn("000003", {row["code"] for row in rankings[1]})

    def test_dividend_rankings_dedupe_undated_revisions_by_fiscal_period(self):
        stocks = [{"code": "000001", "listing_date": "2000-01-01", "market_cap": 1_000}]
        dividends = [
            {"code": "000001", "period": "2025-12-31", "progress": "实施分配", "cash_per_10": 1, "total_shares": 100, "plan_date": "2026-03-01"},
            {"code": "000001", "period": "2025-12-31", "progress": "实施分配", "cash_per_10": 2, "total_shares": 100, "plan_date": "2026-04-01"},
        ]

        rankings = build_dividend_rankings(stocks, dividends, end_year=2025, periods=(1,))

        self.assertAlmostEqual(rankings[1][0]["yield"], 2.0)

    def test_performance_rankings_combine_growth_profitability_and_consistency(self):
        stocks = [
            {"code": "000001", "name": "高质量", "listing_date": "2000-01-01"},
            {"code": "000002", "name": "普通公司", "listing_date": "2000-01-01"},
            {"code": "000003", "name": "亏损公司", "listing_date": "2000-01-01"},
        ]
        annuals = []
        for year, revenue, profit, roe in [(2022, 100, 10, 10), (2023, 120, 13, 12), (2024, 150, 17, 15), (2025, 180, 22, 18)]:
            annuals.append({"code": "000001", "period": f"{year}-12-31", "revenue": revenue, "net_profit": profit, "roe": roe})
        annuals.append({"code": "000001", "period": "2025-09-30", "revenue": 9_999, "net_profit": 9_999, "roe": 99})
        for year, revenue, profit, roe in [(2022, 100, 10, 8), (2023, 103, 10, 8), (2024, 106, 10, 8), (2025, 109, 10, 8)]:
            annuals.append({"code": "000002", "period": f"{year}-12-31", "revenue": revenue, "net_profit": profit, "roe": roe})
        for year, revenue, profit, roe in [(2022, 100, -5, -2), (2023, 120, -3, -1), (2024, 140, 1, 1), (2025, 160, 2, 2)]:
            annuals.append({"code": "000003", "period": f"{year}-12-31", "revenue": revenue, "net_profit": profit, "roe": roe})

        rankings = build_performance_rankings(stocks, annuals, end_year=2025, periods=(1, 3))

        self.assertEqual([row["code"] for row in rankings[3]], ["000001", "000002"])
        leader = rankings[3][0]
        self.assertGreater(leader["score"], rankings[3][1]["score"])
        self.assertAlmostEqual(leader["revenue_cagr"], (1.8 ** (1 / 3) - 1) * 100)
        self.assertAlmostEqual(leader["profit_cagr"], (2.2 ** (1 / 3) - 1) * 100)
        self.assertEqual(leader["profitable_years"], 3)
        self.assertNotIn("000003", {row["code"] for row in rankings[3]})

    def test_performance_rankings_reject_missing_intermediate_profit(self):
        stocks = [{"code": "000001", "listing_date": "2000-01-01"}]
        annuals = [
            {"code": "000001", "period": "2022-12-31", "revenue": 100, "net_profit": 10, "roe": 10},
            {"code": "000001", "period": "2023-12-31", "revenue": 110, "net_profit": None, "roe": 11},
            {"code": "000001", "period": "2024-12-31", "revenue": 120, "net_profit": 12, "roe": 12},
            {"code": "000001", "period": "2025-12-31", "revenue": 130, "net_profit": 13, "roe": 13},
        ]

        rankings = build_performance_rankings(stocks, annuals, end_year=2025, periods=(3,))

        self.assertEqual(rankings[3], [])

    def test_year_windows_split_global_fetch_into_bounded_partitions(self):
        self.assertEqual(year_windows("2024-06-01", date(2026, 10, 27)), [
            ("2024-06-01", "2024-12-31"),
            ("2025-01-01", "2025-12-31"),
            ("2026-01-01", "2026-10-27"),
        ])

    def test_dedupe_financials_keeps_latest_notice_for_same_period(self):
        rows = [
            {"code": "600519", "period": "2025-12-31", "notice_date": "2026-03-01", "revenue": 1},
            {"code": "600519", "period": "2025-12-31", "notice_date": "2026-03-31", "revenue": 2},
        ]
        self.assertEqual(dedupe_financials(rows)[0]["revenue"], 2)

    def test_filter_recent_companies_excludes_historical_delisted_rows(self):
        stocks = [
            {"code": "000001", "name": "平安银行"},
            {"code": "000003", "name": "PT金田A"},
        ]
        financials = [
            {"code": "000001", "period": "2026-06-30"},
            {"code": "000003", "period": "2002-12-31"},
        ]
        current = filter_recent_companies(stocks, financials, "2025-09-30")
        self.assertEqual(current, [{"code": "000001", "name": "平安银行"}])

    def test_build_documents_filters_non_current_stocks_and_sorts_periods(self):
        stocks = [normalize_stock({"f12": "600519", "f14": "贵州茅台", "f13": 1})]
        financials = [
            normalize_financial({"SECURITY_CODE": "600519", "REPORT_DATE": "2024-12-31"}),
            normalize_financial({"SECURITY_CODE": "000001", "REPORT_DATE": "2025-12-31"}),
            normalize_financial({"SECURITY_CODE": "600519", "REPORT_DATE": "2025-12-31"}),
        ]
        documents = build_company_documents(stocks, financials, [])
        self.assertEqual(list(documents), ["600519"])
        self.assertEqual([x["period"] for x in documents["600519"]["financials"]], ["2025-12-31", "2024-12-31"])

    def test_write_dataset_uses_two_digit_shards_and_minified_json(self):
        documents = {"600519": {"stock": {"code": "600519", "name": "贵州茅台"}, "financials": [], "dividends": []}}
        with tempfile.TemporaryDirectory() as tmp:
            rankings = {
                "dividend": {1: [{"code": "600519", "yield": 2.5, "dividend_years": 1}]},
                "performance": {1: [{"code": "600519", "score": 88.0}]},
            }
            result = write_dataset(
                Path(tmp), documents, "2026-10-22T00:00:00Z",
                rankings=rankings, ranking_end_year={"dividend": 2024, "performance": 2025},
            )
            shard = Path(tmp) / "companies" / "60" / "600519.json"
            self.assertTrue(shard.exists())
            self.assertNotIn("\n", shard.read_text(encoding="utf-8"))
            index = json.loads((Path(tmp) / "stocks.json").read_text(encoding="utf-8"))
            self.assertEqual(index["stocks"][0]["code"], "600519")
            self.assertEqual(result["company_count"], 1)
            dividend = json.loads((Path(tmp) / "rankings" / "dividend-1.json").read_text(encoding="utf-8"))
            performance = json.loads((Path(tmp) / "rankings" / "performance-1.json").read_text(encoding="utf-8"))
            self.assertEqual(dividend["meta"]["end_year"], 2024)
            self.assertEqual(performance["meta"]["end_year"], 2025)
            self.assertEqual(dividend["rows"][0]["yield"], 2.5)
            self.assertEqual(performance["rows"][0]["score"], 88.0)


if __name__ == "__main__":
    unittest.main()
