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

            def _request(self, url, params):
                page = int(params["pn"])
                self.pages_seen.append(page)
                size = 100 if page < 3 else 50
                return {"data": {"total": 250, "diff": [{"f12": f"{page:02d}{i:04d}"} for i in range(size)]}}

        client = QuoteClient()
        rows = client.fetch_stocks()
        self.assertEqual(len(rows), 250)
        self.assertEqual(client.pages_seen, [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
