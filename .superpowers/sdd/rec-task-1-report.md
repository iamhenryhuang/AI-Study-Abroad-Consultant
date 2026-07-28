# Fix Report: Test Coverage for fetch_nearby_cases

## What Was Added

Added three fake database helper classes to tests/test_recommend.py:
- _FakeCursor: Mock cursor object with execute(), fetchall(), and context manager support
- _FakeConn: Mock connection object with cursor factory and read_only/closed tracking

Added TestFetchNearbyCases test class with three test methods:
1. test_returns_rows_and_sets_readonly: Verifies row conversion to dict format, read_only flag set, connection closed, SQL contains ABS(gpa when gpa provided
2. test_no_gpa_uses_fallback_branch: Verifies fallback SQL branch when gpa is None, connection closed
3. test_no_connection_returns_empty: Verifies empty list returned when get_connection returns None

## Test Command and Results

python -m unittest discover tests -p test_recommend.py -v

Result: Ran 13 tests in 0.007s - OK

## Commit

- Hash: 262dce9
- Message: test: cover fetch_nearby_cases DB query in recommendation module

## Status

✓ All 13 tests pass (10 existing + 3 new)
✓ fetch_nearby_cases DB query now has test coverage
✓ Tests verify both gpa-based and fallback SQL branches
✓ Connection mock confirms read_only flag set and cleanup
