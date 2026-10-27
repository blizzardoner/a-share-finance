import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from ashare_finance.pipeline import (
    build_company_documents,
    dedupe_financials,
    filter_recent_companies,
    normalize_dividend,
    normalize_financial,
    normalize_stock,
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
            result = write_dataset(Path(tmp), documents, "2026-10-22T00:00:00Z")
            shard = Path(tmp) / "companies" / "60" / "600519.json"
            self.assertTrue(shard.exists())
            self.assertNotIn("\n", shard.read_text(encoding="utf-8"))
            index = json.loads((Path(tmp) / "stocks.json").read_text(encoding="utf-8"))
            self.assertEqual(index["stocks"][0]["code"], "600519")
            self.assertEqual(result["company_count"], 1)


if __name__ == "__main__":
    unittest.main()
