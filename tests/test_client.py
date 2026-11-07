import unittest

from ashare_finance.client import EastmoneyClient


class FakeClient(EastmoneyClient):
    def __init__(self):
        super().__init__(min_interval=0)
        self.pages_seen = []

    def _request(self, url, params):
        page = int(params["pageNumber"])
        self.pages_seen.append(page)
        data = [{"id": 1}, {"id": 2}] if page == 1 else [{"id": 3}]
        return {"success": True, "result": {"pages": 2, "count": 3, "data": data}}


class ClientTests(unittest.TestCase):
    def test_fetch_pages_collects_every_page_once(self):
        client = FakeClient()
        rows = client.fetch_pages("https://example.test", {"pageSize": 2})
        self.assertEqual(rows, [{"id": 1}, {"id": 2}, {"id": 3}])
        self.assertEqual(client.pages_seen, [1, 2])

    def test_fetch_stocks_honors_the_api_effective_page_size(self):
        class QuoteClient(EastmoneyClient):
            def __init__(self):
                super().__init__(min_interval=0)
                self.pages_seen = []
                self.filters_seen = []

            def _request(self, url, params):
                page = int(params["pn"])
                self.pages_seen.append(page)
                self.filters_seen.append(params["fs"])
                size = 100 if page < 3 else 50
                return {"data": {"total": 250, "diff": [{"f12": f"{page:02d}{i:04d}"} for i in range(size)]}}

        client = QuoteClient()
        rows = client.fetch_stocks()
        self.assertEqual(len(rows), 250)
        self.assertEqual(client.pages_seen, [1, 2, 3])
        self.assertTrue(all("m:0+t:81+s:2048" in value for value in client.filters_seen))

    def test_fetch_selected_stock_quotes_batches_codes_in_one_request(self):
        class SelectedQuoteClient(EastmoneyClient):
            def __init__(self):
                super().__init__(min_interval=0)
                self.secids_seen = []

            def _request(self, url, params):
                self.secids_seen.append(params["secids"])
                return {"data": {"diff": [{"f12": code[-6:]} for code in params["secids"].split(",")]}}

        client = SelectedQuoteClient()
        rows = client.fetch_selected_stock_quotes({"600519", "000001", "300750", "920001"})

        self.assertEqual(client.secids_seen, ["0.000001,0.300750,1.600519,0.920001"])
        self.assertEqual([row["f12"] for row in rows], ["000001", "300750", "600519", "920001"])

    def test_fetch_selected_stock_quotes_rejects_partial_api_results(self):
        class PartialQuoteClient(EastmoneyClient):
            def __init__(self):
                super().__init__(min_interval=0)

            def _request(self, url, params):
                return {"data": {"diff": [{"f12": "000001"}]}}

        with self.assertRaisesRegex(RuntimeError, "行情缺少 600519"):
            PartialQuoteClient().fetch_selected_stock_quotes({"000001", "600519"})

    def test_fetch_stocks_rejects_duplicate_pages_that_leave_incomplete_coverage(self):
        class DuplicatePageClient(EastmoneyClient):
            def __init__(self):
                super().__init__(min_interval=0)

            def _request(self, url, params):
                return {"data": {"total": 2, "diff": [{"f12": "000001"}]}}

        with self.assertRaisesRegex(RuntimeError, "行情目录不完整"):
            DuplicatePageClient().fetch_stocks()

    def test_fetch_stocks_requires_the_api_total_count(self):
        class MissingTotalClient(EastmoneyClient):
            def __init__(self):
                super().__init__(min_interval=0)

            def _request(self, url, params):
                return {"data": {"diff": [{"f12": "000001"}]}}

        with self.assertRaisesRegex(RuntimeError, "行情目录总数无效"):
            MissingTotalClient().fetch_stocks()


if __name__ == "__main__":
    unittest.main()
