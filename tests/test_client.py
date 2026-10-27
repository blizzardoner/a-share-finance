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


if __name__ == "__main__":
    unittest.main()
