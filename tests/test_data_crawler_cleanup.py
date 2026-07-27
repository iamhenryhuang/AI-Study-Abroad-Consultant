import unittest

from data_crawler.cleanup import _school_files, _SCHOOL_ID


class DataCrawlerCleanupTest(unittest.TestCase):
    def test_school_id_rejects_path_traversal(self):
        self.assertIsNone(_SCHOOL_ID.fullmatch("../../gatech"))
        self.assertIsNotNone(_SCHOOL_ID.fullmatch("WashU"))
        self.assertIsNotNone(_SCHOOL_ID.fullmatch("school-test_1"))

    def test_school_cleanup_targets_are_exact(self):
        targets = [path.name for path in _school_files("gatech")]

        self.assertIn("gatech_result.json", targets)
        self.assertIn("gatech_url_filter_review.json", targets)
        self.assertIn("gatech_events.jsonl", targets)
        self.assertTrue(all("gatech" in name for name in targets))


if __name__ == "__main__":
    unittest.main()
