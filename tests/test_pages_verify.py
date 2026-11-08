import unittest

from ashare_finance.verify_pages import FUND_IDENTITY_FIELDS, metadata_matches, verify_deployment


class PagesVerificationTests(unittest.TestCase):
    def setUp(self):
        self.expected = {
            "generated_at": "2026-11-08T00:00:00Z",
            "company_count": 5927,
            "financial_row_count": 120000,
            "dividend_row_count": 22000,
        }

    def test_metadata_matches_build_identity_and_counts(self):
        self.assertTrue(metadata_matches(self.expected, dict(self.expected)))
        stale = {**self.expected, "generated_at": "2026-11-07T00:00:00Z"}
        self.assertFalse(metadata_matches(self.expected, stale))
        incomplete = dict(self.expected)
        incomplete.pop("dividend_row_count")
        self.assertFalse(metadata_matches(self.expected, incomplete))

    def test_verify_deployment_retries_until_pages_serves_the_new_build(self):
        responses = [
            {**self.expected, "generated_at": "2026-11-07T00:00:00Z"},
            dict(self.expected),
        ]
        sleeps = []

        actual = verify_deployment(
            self.expected,
            fetch=lambda: responses.pop(0),
            attempts=2,
            delay=0.1,
            sleep=sleeps.append,
        )

        self.assertEqual(actual, self.expected)
        self.assertEqual(sleeps, [0.1])

    def test_verify_deployment_fails_after_all_attempts(self):
        stale = {**self.expected, "generated_at": "2026-11-07T00:00:00Z"}
        with self.assertRaisesRegex(RuntimeError, "线上元数据未更新"):
            verify_deployment(
                self.expected,
                fetch=lambda: stale,
                attempts=2,
                delay=0,
                sleep=lambda _: None,
            )

    def test_fund_metadata_uses_its_own_build_identity_fields(self):
        expected = {
            "generated_at": "2026-11-08T00:00:00Z",
            "fund_count": 24,
            "report_count": 192,
            "event_count": 1200,
        }
        self.assertTrue(metadata_matches(expected, dict(expected), FUND_IDENTITY_FIELDS))
        self.assertFalse(metadata_matches(expected, {**expected, "report_count": 191}, FUND_IDENTITY_FIELDS))


if __name__ == "__main__":
    unittest.main()
