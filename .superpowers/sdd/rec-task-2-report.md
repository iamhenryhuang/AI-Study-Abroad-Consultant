# Task 2 Implementation Report — School Recommendation Wire-In

## Summary
Successfully implemented Task 2: wired the school recommendation module (`recommend.py`) into the LangGraph agent with intent detection, dedicated graph node, routing, and verifier bypass.

## Changes Per File

### 1. `backend/scripts/retriever/agent/state.py`
**Step 1 edits:**
- Added three new fields to `AgentState` TypedDict (after `generated_answer`):
  - `wants_recommendation: bool` — User provided scores and wants school recommendations
  - `profile: dict` — Extracted scores {gpa, ielts, toefl, gre}
  - `recommend_docs: list[dict]` — Recommendation node output

- Added three new entries to `create_initial_state()` return dict:
  - `"wants_recommendation": False`
  - `"profile": {}`
  - `"recommend_docs": []`

### 2. `backend/scripts/retriever/agent/prompts.py`
**Step 2 edits:**
- Inserted "Task 5" section in `_build_intent_prompt()` (between Task 4 and user query):
  - Detects "wants_recommendation" intent when user provides scores (GPA/IELTS/TOEFL/GRE) and asks for school recommendations
  - Extracts numeric scores to profile object

- Updated JSON output format to include:
  - `"wants_recommendation": true or false`
  - `"profile": {"gpa": number or null, ...}`

### 3. `backend/scripts/retriever/agent/nodes/decompose.py`
**Step 3 edits:**
- In try block: added parsing of `wants_recommendation` and `profile` from LLM response
- In except fallback: set `wants_recommendation = False` and `profile = {}`
- In return dict: added three new keys for routing and state propagation
- In `route_to_retrieval()`: added routing branch to send to "recommend" node when `wants_recommendation=True`

### 4. `backend/scripts/retriever/agent/nodes/retrieval.py`
**Step 4 edits:**
- Added import: `from retriever.recommend import recommend, fetch_nearby_cases`
- Added new `recommend_node()` function (185 lines total):
  - Calls `recommend(profile)` to get tiered recommendations (衝刺/適中/保底)
  - For each school, fetches nearby applicant cases via `fetch_nearby_cases()`
  - Formats docs with real-world examples and score comparisons
  - Returns dict with `recommend_docs` key

### 5. `backend/scripts/retriever/agent/nodes/verification.py`
**Step 5 edits:**
- Added `recommend_docs = state.get("recommend_docs", [])`
- Updated `all_docs` deduplication to include `recommend_docs`
- Added wants_recommendation short-circuit after needs_experience check:
  - If `wants_recommendation=True` and recommend_docs exist, bypass LLM verification and return immediately with `is_sufficient=True`
  - Follows same pattern as experience_docs bypass

### 6. `backend/scripts/retriever/agent/nodes/__init__.py`
**Step 6 edits:**
- Added `recommend_node` to import from `.retrieval`
- Added `"recommend_node"` to `__all__` list (alphabetical position)

### 7. `backend/scripts/retriever/agent/graph.py`
**Step 6 edits:**
- Added `recommend_node` to import from `.nodes`
- Added `builder.add_node("recommend", recommend_node)` to graph construction
- Added `builder.add_edge("recommend", "verify")` to route recommend output to verifier

## Verification Results

### Step 7: Import & Build Check
```
cd "C:\Users\timwu\source\AI-Study-Abroad-Consultant"
python -c "import sys; sys.path.insert(0,'backend/scripts'); from retriever.agent import run_agent; from retriever.recommend import recommend; print('OK', list(recommend({'gpa':3.5,'toefl':100,'gre':315}).keys()))"
```
**Result:** ✓ PASS
- Output: `OK ['衝刺', '適中', '保底']`
- All imports resolved successfully
- recommend() function callable and returns expected tier keys

### Step 8: Regression Tests
```
python -m unittest discover tests -p "test_recommend.py" -v
```
**Result:** ✓ PASS (13/13 tests)
- test_classify_tier (4 tests)
- test_fetch_nearby_cases (3 tests)
- test_ielts_to_toefl (2 tests)
- test_normalize_profile (2 tests)
- test_recommend (2 tests)
- All passed in 0.007s

### Step 9: Commit
```
git commit -m "feat: wire school recommendation node into agent graph"
```
**Result:** ✓ Committed
- Hash: `4f2682d`
- Branch: `feat-school-recommendation`
- 7 files changed, 67 insertions(+), 3 deletions(-)

## Architecture Integration
The recommendation node integrates into the agent graph as follows:

1. **Decomposer** detects `wants_recommendation=True` if user provides scores + asks for recommendations
2. **Router** sends to parallel nodes: search, extension_function, experience_search, AND **recommend**
3. **Recommend Node** (new):
   - Calls `recommend(profile)` for tiered school suggestions
   - Fetches real applicant cases for each recommendation
   - Returns structured docs with comparison data
4. **Verifier** combines all doc types and short-circuits if `wants_recommendation=True` with docs present
5. **Finalizer** generates answer from all combined docs (SQL + extension + experience + **recommendation**)

## Notes
- All edits are precise replacements matching existing code structure
- No new files created; only modified 7 existing files as specified
- Follows existing patterns for nodes (decompose, route, verify, short-circuit)
- English/Chinese mixed prompts preserved as in existing codebase
- Recommendation docs integrate seamlessly with existing doc deduplication pipeline

---

## Code-Review Fixes — Task 2 Revision

### Status
✓ COMPLETE

### Commit Hash
`61a0034`

### Changes Applied

**Fix 1: Exception Safety in `recommend_node`** (`backend/scripts/retriever/agent/nodes/retrieval.py`)
- Widened try/except to wrap entire function body (lines 188–216)
- Now protects both `recommend(profile)` call and doc-building loop (`fetch_nearby_cases`, dict access)
- Node guaranteed never to raise; returns empty recommend_docs on error

**Fix 2: Routing Docstring Update** (`backend/scripts/retriever/agent/nodes/decompose.py`)
- Added `wants_recommendation → recommend（依成績分級推薦學校）` flag to docstring
- Changed "若三者皆無" → "若皆無" (accurate now that 4 branches exist)

### Verification
- Agent graph builds: ✓ (prints `OK`)
- Recommend tests: ✓ 13/13 pass in 0.003s
