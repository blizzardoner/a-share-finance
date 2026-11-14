import unittest
import json
import tempfile
from pathlib import Path

from ashare_finance.fund_client import FundClient, parse_fund_catalog, parse_fund_details, parse_notice_content, parse_notice_list, parse_size_rank_payload


class FundDetailParsingTests(unittest.TestCase):
    def test_parse_size_rank_payload_keeps_scale_and_pagination(self):
        payload = '''{
          "Success": true,
          "TotalCount": 2,
          "Datas": [
            {"FCODE": "000001", "SHORTNAME": "甲基金A", "ENDNAV": "1234567890.12", "SYL_3N": "45.67"},
            {"FCODE": "000002", "SHORTNAME": "乙基金", "ENDNAV": "--"}
          ]
        }'''

        rows, total = parse_size_rank_payload(payload)

        self.assertEqual(total, 2)
        self.assertEqual(rows, [
            {"code": "000001", "name": "甲基金A", "end_nav": 1234567890.12, "return_3y": 45.67},
            {"code": "000002", "name": "乙基金", "end_nav": None, "return_3y": None},
        ])

    def test_parse_fund_details_extracts_manager_risk_stability_and_latest_size(self):
        payload = '''
        var fS_name = "财通成长优选混合A";
        var fS_code = "001480";
        var Data_currentFundManager = [
          {"id":"30304626","name":"金梓才","workTime":"11年又357天","power":{"avr":"76.99","categories":["经验值","收益率","抗风险","稳定性","择时能力"],"data":[95.5,97.2,50.8,50.1,67.1]}},
          {"id":"m2","name":"乙经理","workTime":"6年","power":{"avr":"70","categories":["经验值"],"data":[80]}}
        ];
        var Data_fluctuationScale = {"categories":["2026-06-30","2026-09-30"],"series":[{"y":63.08,"mom":"188.21%"},{"y":56.83,"mom":"-9.91%"}]};
        var Data_performanceEvaluation = {"avr":"75.75","categories":["选证能力","收益率","抗风险","稳定性","择时能力"],"data":[95.0,90.0,50.0,50.0,70.0]};
        var Data_assetAllocation = {"series":[{"name":"股票占净比","data":[84.25,84.35]},{"name":"净资产","type":"line","data":[208.4864,186.7323]}],"categories":["2026-06-30","2026-09-30"]};
        '''

        detail = parse_fund_details(payload)

        self.assertEqual(detail["code"], "001480")
        self.assertEqual(detail["name"], "财通成长优选混合A")
        self.assertEqual(detail["manager_id"], "30304626")
        self.assertEqual(detail["manager_name"], "金梓才")
        self.assertEqual([(row["id"], row["name"]) for row in detail["managers"]], [("30304626", "金梓才"), ("m2", "乙经理")])
        self.assertEqual(detail["manager_work_time"], "11年又357天")
        self.assertEqual(detail["manager_score"], 76.99)
        self.assertEqual(detail["experience_score"], 95.5)
        self.assertEqual(detail["risk_score"], 50.0)
        self.assertEqual(detail["stability_score"], 50.0)
        self.assertEqual(detail["fund_size"], 56.83)
        self.assertEqual(detail["stock_allocation"], 84.35)
        self.assertEqual(detail["detail_date"], "2026-09-30")

    def test_parse_fund_details_treats_provider_no_data_scores_as_missing(self):
        payload = '''
        var fS_name = "缺评分基金";var fS_code = "001715";
        var Data_currentFundManager = [{"id":"m1","name":"甲","power":{"avr":"暂无数据","categories":["经验值"],"data":["暂无数据"]}}];
        var Data_fluctuationScale = {"categories":["2026-09-30"],"series":[{"y":10}]};
        var Data_performanceEvaluation = {"avr":"暂无数据","categories":["抗风险","稳定性"],"data":["暂无数据","暂无数据"]};
        '''

        detail = parse_fund_details(payload)

        self.assertIsNone(detail["manager_score"])
        self.assertIsNone(detail["experience_score"])
        self.assertIsNone(detail["risk_score"])


class FundRankingClientTests(unittest.TestCase):
    def test_mobile_fund_api_uses_a_mobile_browser_user_agent(self):
        self.assertIn("Android", FundClient.USER_AGENT)
        self.assertIn("Mobile", FundClient.USER_AGENT)

    @staticmethod
    def _rank_payload(code: str, page: int, total: int = 2, pages: int = 2) -> str:
        values = [code, f"基金{code}", "PINYIN", "2026-11-06", "1", "1", "0", "1", "2", "3", "4", "10", "20", "30", "5", "100", "2020-01-01", "", "40", "1.5", "0.15"]
        return f'var rankData = {{datas:["{",".join(values)}"],allRecords:{total},allPages:{pages},pageIndex:{page}}};'

    def test_fetch_rankings_collects_every_declared_page_once(self):
        class FakeClient(FundClient):
            def _get(self, _url, params=None, referer=None):
                page = int(params["pi"])
                return FundRankingClientTests._rank_payload(f"00000{page}", page)

        rows = FakeClient(min_interval=0).fetch_rankings(fund_types=("gp",))

        self.assertEqual([row["code"] for row in rows], ["000001", "000002"])
        self.assertEqual(rows[0]["return_3y"], 30.0)

    def test_fetch_rankings_rejects_duplicate_pages_with_incomplete_coverage(self):
        class DuplicateClient(FundClient):
            def _get(self, _url, params=None, referer=None):
                return FundRankingClientTests._rank_payload("000001", int(params["pi"]))

        with self.assertRaisesRegex(RuntimeError, "基金排名覆盖不完整"):
            DuplicateClient(min_interval=0).fetch_rankings(fund_types=("gp",))

    def test_fetch_size_rankings_collects_all_pages_for_each_requested_type(self):
        class FakeClient(FundClient):
            def __init__(self):
                super().__init__(min_interval=0)
                self.calls = []
                self.devices = []

            def _get(self, _url, params=None, referer=None):
                fund_type = str(params["FundType"])
                page = int(params["pageIndex"])
                self.calls.append((fund_type, page, int(params["pageSize"])))
                self.devices.append(str(params["deviceid"]))
                code = f"{fund_type}{page:04d}"
                return json.dumps({
                    "Success": True,
                    "TotalCount": 2,
                    "Datas": [{"FCODE": code, "SHORTNAME": f"基金{code}", "ENDNAV": str(page * 1e8)}],
                })

        client = FakeClient()
        rows = client.fetch_size_rankings(fund_types=("25", "27"), page_size=1)

        self.assertEqual(client.calls, [("25", 1, 1), ("25", 2, 1), ("27", 1, 1), ("27", 2, 1)])
        self.assertEqual(len(set(client.devices)), 4)
        self.assertEqual([row["code"] for row in rows], ["250001", "250002", "270001", "270002"])

    def test_fetch_size_rankings_retries_provider_busy_payload(self):
        class BusyOnceClient(FundClient):
            def __init__(self):
                super().__init__(min_interval=0, retries=2)
                self.calls = 0

            def _get(self, _url, params=None, referer=None):
                self.calls += 1
                if self.calls == 1:
                    return json.dumps({"Success": False, "ErrMsg": "网络繁忙，请稍后重试！"})
                return json.dumps({
                    "Success": True,
                    "TotalCount": 1,
                    "Datas": [{"FCODE": "000001", "SHORTNAME": "甲基金A", "ENDNAV": "100000000"}],
                })

        client = BusyOnceClient()
        rows = client.fetch_size_rankings(fund_types=("25",), page_size=30)

        self.assertEqual(client.calls, 2)
        self.assertEqual(rows[0]["code"], "000001")

    def test_fetch_size_rankings_rejects_repeated_pages_without_progress(self):
        class RepeatingPageClient(FundClient):
            def __init__(self):
                super().__init__(min_interval=0, retries=1)
                self.calls = 0

            def _get(self, _url, params=None, referer=None):
                self.calls += 1
                if self.calls > 2:
                    raise RuntimeError("test request cap exceeded")
                return json.dumps({
                    "Success": True,
                    "TotalCount": 2,
                    "Datas": [{"FCODE": "250001", "SHORTNAME": "重复基金", "ENDNAV": "100000000"}],
                })

        client = RepeatingPageClient()
        with self.assertRaisesRegex(RuntimeError, "基金规模排名分页未前进"):
            client.fetch_size_rankings(fund_types=("25",), page_size=1)
        self.assertEqual(client.calls, 2)

    def test_fetch_size_rankings_extends_when_total_grows_mid_scan(self):
        class GrowingTotalClient(FundClient):
            def __init__(self):
                super().__init__(min_interval=0)
                self.pages = []

            def _get(self, _url, params=None, referer=None):
                page = int(params["pageIndex"])
                self.pages.append(page)
                return json.dumps({
                    "Success": True,
                    "TotalCount": 2 if page == 1 else 3,
                    "Datas": [{"FCODE": f"00000{page}", "SHORTNAME": f"基金{page}", "ENDNAV": "100000000"}],
                })

        client = GrowingTotalClient()
        rows = client.fetch_size_rankings(fund_types=("25",), page_size=1)

        self.assertEqual(client.pages, [1, 2, 3])
        self.assertEqual([row["code"] for row in rows], ["000001", "000002", "000003"])

    def test_fetch_size_rankings_bounds_continuously_growing_totals(self):
        class EverGrowingClient(FundClient):
            def __init__(self):
                super().__init__(min_interval=0)
                self.pages = []

            def _get(self, _url, params=None, referer=None):
                page = int(params["pageIndex"])
                self.pages.append(page)
                if page > 20:
                    raise RuntimeError("test request cap exceeded")
                return json.dumps({
                    "Success": True,
                    "TotalCount": page + 1,
                    "Datas": [{"FCODE": f"{page:06d}", "SHORTNAME": f"基金{page}", "ENDNAV": "100000000"}],
                })

        client = EverGrowingClient()
        with self.assertRaisesRegex(RuntimeError, "覆盖不完整"):
            client.fetch_size_rankings(fund_types=("25",), page_size=1)
        self.assertEqual(len(client.pages), 12)

    def test_parse_fund_catalog_keeps_code_name_and_type(self):
        rows = parse_fund_catalog('var r = [["000001","HXCZHH","华夏成长混合","混合型-灵活","HUAXIACHENGZHANG"]];')
        self.assertEqual(rows, [{
            "code": "000001", "search_key": "HXCZHH", "name": "华夏成长混合",
            "fund_type": "混合型-灵活", "pinyin": "HUAXIACHENGZHANG",
        }])


class FundNoticeTests(unittest.TestCase):
    def test_notice_list_keeps_periodic_reports_and_excludes_prompts(self):
        payload = '''{"Data":[
          {"ID":"AN3","TITLE":"示例基金2026年第3季度报告","PUBLISHDATE":"2026-10-27T00:00:00","ATTACHTYPE":"0"},
          {"ID":"AN2","TITLE":"示例基金2026年中期报告","PUBLISHDATE":"2026-08-31T00:00:00","ATTACHTYPE":"0"},
          {"ID":"ANX","TITLE":"示例基金2026年第3季度报告提示性公告","PUBLISHDATE":"2026-10-27T00:00:00","ATTACHTYPE":"1"}
        ],"TotalCount":3}'''

        rows = parse_notice_list(payload)

        self.assertEqual([row["art_code"] for row in rows], ["AN3", "AN2"])
        self.assertEqual(rows[0]["publish_date"], "2026-10-27")
        self.assertEqual(rows[0]["pdf_url"], "https://pdf.dfcfw.com/pdf/H2_AN3_1.pdf")

    def test_notice_content_requires_successful_nonempty_report(self):
        payload = '{"success":1,"data":{"notice_title":"示例基金2026年第3季度报告","notice_content":"报告正文","attach_url":"https://example.test/report.pdf"}}'
        self.assertEqual(parse_notice_content(payload), {
            "title": "示例基金2026年第3季度报告",
            "content": "报告正文",
            "pdf_url": "https://example.test/report.pdf",
        })

        with self.assertRaisesRegex(ValueError, "公告正文无效"):
            parse_notice_content('{"success":0,"data":null}')

    def test_notice_content_cache_survives_client_instances_without_refetching(self):
        payload = '{"success":1,"data":{"notice_title":"示例基金2026年第3季度报告","notice_content":"报告正文","attach_url":"https://example.test/q3.pdf"}}'
        with tempfile.TemporaryDirectory() as tmp:
            first = FundClient(min_interval=0, cache_dir=Path(tmp))
            calls = []
            first._get = lambda *args, **kwargs: calls.append(args) or payload
            self.assertEqual(first.fetch_notice_content("AN20261001")["content"], "报告正文")

            second = FundClient(min_interval=0, cache_dir=Path(tmp))
            second._get = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("cache miss"))
            self.assertEqual(second.fetch_notice_content("AN20261001")["content"], "报告正文")

        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
