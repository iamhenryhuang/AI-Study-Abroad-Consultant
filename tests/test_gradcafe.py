import unittest

from crawler.gradcafe import should_run_gradcafe_for_school


class FakeCursor:
    def __init__(self, results):
        self.results = list(results)
        self._result = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, params=None):
        self._result = self.results.pop(0)

    def fetchone(self):
        if isinstance(self._result, list):
            if self._result:
                return self._result[0]
            return None
        return self._result

    def fetchall(self):
        if isinstance(self._result, list):
            return self._result
        return [self._result]


class FakeConn:
    def __init__(self, results):
        self.results = list(results)

    def cursor(self):
        return FakeCursor(self.results)

    def close(self):
        pass


class GradCafeSchoolCheckTests(unittest.TestCase):
    def test_skip_when_school_already_has_sufficient_data(self):
        conn = FakeConn([
            [(1, "CMU")],
            [(3,)],
            [(1,)],
        ])

        should_run, reason = should_run_gradcafe_for_school("CMU", conn=conn)

        self.assertFalse(should_run)
        self.assertIn("足夠", reason)

    def test_run_when_school_has_no_useful_data(self):
        conn = FakeConn([
            [],
        ])

        should_run, reason = should_run_gradcafe_for_school("MIT", conn=conn)

        self.assertTrue(should_run)
        self.assertIn("不足", reason)


if __name__ == "__main__":
    unittest.main()
