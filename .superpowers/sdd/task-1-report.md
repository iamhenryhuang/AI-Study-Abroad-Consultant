# Task 1 Implementation Report — contextualize_query

## Summary

Successfully implemented query contextualization module for multi-turn chat feature following TDD cycle.

## Files Created

1. **`backend/scripts/retriever/contextualize.py`**
   - Main implementation with `contextualize_query(query: str, history: list[dict]) -> str`
   - Helper function `_build_prompt()` to construct LLM prompt from history
   - Handles empty history (no LLM call), blank rewrites, and LLM failures gracefully

2. **`tests/test_contextualize.py`**
   - 4 test cases covering all requirements
   - Tests: no-history path, successful rewrite, blank rewrite fallback, LLM failure handling

## Test Execution

**Command:** `python -m unittest discover tests -p "test_contextualize.py" -v`

**Output:**
```
test_blank_rewrite_falls_back_to_original (test_contextualize.TestContextualizeQuery.test_blank_rewrite_falls_back_to_original) ... ok
test_llm_failure_degrades_to_original_query (test_contextualize.TestContextualizeQuery.test_llm_failure_degrades_to_original_query) ... ok
test_no_history_returns_query_unchanged_without_llm (test_contextualize.TestContextualizeQuery.test_no_history_returns_query_unchanged_without_llm) ... ok
test_with_history_returns_rewritten_query (test_contextualize.TestContextualizeQuery.test_with_history_returns_rewritten_query) ... ok

----------------------------------------------------------------------
Ran 4 tests in 0.008s

OK
```

## Commit

**Hash:** `6e7ab59`  
**Message:** `feat: add query contextualization for multi-turn chat`

## Concerns

None. All 4 tests pass. Implementation follows the exact specification from the brief, including:
- ✓ No LLM call when history is empty
- ✓ Proper handling of blank/whitespace-only rewrites
- ✓ Graceful degradation on LLM exceptions
- ✓ Correct import structure with `from generator.client import call_llm`
- ✓ Chinese prompts and comments preserved as specified
