import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend" / "scripts"))

from retriever import experience_crawl as ec


class _FakeCursor:
    def __init__(self, row):
        self._row = row
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, *a, **k): pass
    def fetchone(self): return self._row


class _FakeConn:
    def __init__(self, row):
        self._row = row
        self.read_only = False
        self.closed = False
    def cursor(self): return _FakeCursor(self._row)
    def close(self): self.closed = True


class TestRecentlyCrawled(unittest.TestCase):
    def test_recent_returns_true(self):
        conn = _FakeConn([datetime.now() - timedelta(days=1)])
        with patch.object(ec, "get_connection", return_value=conn):
            self.assertTrue(ec._recently_crawled("cmu", days=7))

    def test_old_returns_false(self):
        conn = _FakeConn([datetime.now() - timedelta(days=30)])
        with patch.object(ec, "get_connection", return_value=conn):
            self.assertFalse(ec._recently_crawled("cmu", days=7))

    def test_never_crawled_returns_false(self):
        conn = _FakeConn([None])
        with patch.object(ec, "get_connection", return_value=conn):
            self.assertFalse(ec._recently_crawled("cmu", days=7))


class TestMaybeEnqueueCrawl(unittest.TestCase):
    def setUp(self):
        ec._in_flight.clear()

    def tearDown(self):
        ec._in_flight.clear()

    def test_empty_school_id_does_nothing(self):
        with patch.object(ec.threading, "Thread") as mock_thread:
            ec.maybe_enqueue_crawl("")
        mock_thread.assert_not_called()

    def test_recently_crawled_skips(self):
        with patch.object(ec, "_recently_crawled", return_value=True), \
             patch.object(ec.threading, "Thread") as mock_thread:
            ec.maybe_enqueue_crawl("cmu")
        mock_thread.assert_not_called()

    def test_in_flight_skips(self):
        ec._in_flight.add("cmu")
        with patch.object(ec, "_recently_crawled", return_value=False), \
             patch.object(ec.threading, "Thread") as mock_thread:
            ec.maybe_enqueue_crawl("cmu")
        mock_thread.assert_not_called()

    def test_fresh_starts_thread_once(self):
        started = MagicMock()
        with patch.object(ec, "_recently_crawled", return_value=False), \
             patch.object(ec.threading, "Thread", return_value=started) as mock_thread:
            ec.maybe_enqueue_crawl("cmu")
        mock_thread.assert_called_once()
        started.start.assert_called_once()
        self.assertIn("cmu", ec._in_flight)


class TestSchoolToQuery(unittest.TestCase):
    def test_picks_longest_english_alias(self):
        # cmu 別名含 "cmu" 與 "carnegie mellon"，應取較長的英文全名
        self.assertEqual(ec._school_to_gradcafe_query("cmu"), "carnegie mellon")

    def test_unknown_school_falls_back_to_id(self):
        self.assertEqual(ec._school_to_gradcafe_query("nonexistent"), "nonexistent")


if __name__ == "__main__":
    unittest.main()
