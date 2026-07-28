# Task 3 Report — generator 推薦專屬格式

## Summary
Successfully implemented recommendation answer format for the generator module. All three files modified, import check passed, and changes committed.

## Changes Made

### 1. `backend/scripts/generator/prompts.py`
- **Added constant**: `_RECOMMENDATION_INSTRUCTION` (lines after line 73)
  - Defines the recommendation-specific formatting rules: three Markdown headers (`### 衝刺`, `### 適中`, `### 保底`)
  - Specifies requirements for listing schools with score comparisons and real case examples
  - Includes disclaimer about non-official data usage
  - Prohibits recommending schools outside reference materials

- **Modified function**: `_build_prompt()`
  - Added parameter: `recommendation: bool = False`
  - Conditional injection: `extra = _RECOMMENDATION_INSTRUCTION if recommendation else ""`
  - Updated return statement to include `{extra}` after `_SYSTEM_PROMPT`

### 2. `backend/scripts/generator/answer.py`
- **Modified function**: `generate_answer_stream()`
  - Added parameter: `recommendation: bool = False`
  - Updated `_build_prompt()` call to pass: `recommendation=recommendation`

- **Modified function**: `generate_answer()`
  - Added parameter: `recommendation: bool = False`
  - Updated `_build_prompt()` call to pass: `recommendation=recommendation`

### 3. `backend/scripts/retriever/agent/nodes/answer.py`
- **Modified function**: `finalizer_node()`
  - Added extraction: `recommendation = state.get("wants_recommendation", False)` (before streaming section)
  - Updated `generate_answer_stream()` call to pass: `recommendation=recommendation`
  - Updated `generate_answer()` call to pass: `recommendation=recommendation`

## Step 4: Import Check
**Command**: `python -c "import sys; sys.path.insert(0,'backend/scripts'); from retriever.agent import run_agent; from generator.answer import generate_answer_stream; print('OK')"`

**Output**: `OK`

**Status**: ✓ All imports successful. No syntax errors or import failures detected.

## Step 5: Commit
**Commit Hash**: `7eaaf3aeb33be84a2a746afa1d4cd1d1e5aa2b00`

**Message**: `feat: add recommendation answer format to generator`

**Files Changed**: 3
- `backend/scripts/generator/prompts.py` (+8, -0)
- `backend/scripts/generator/answer.py` (+2, -2)
- `backend/scripts/retriever/agent/nodes/answer.py` (+9, -6)

## Verification Notes
- All edits were exact text replacements verified against original file content
- No new files created; only modifications to the three specified files
- Import check confirms no syntax errors or module import issues
- Commit successful on feat-school-recommendation branch
- All parameter passing follows the spec: boolean flag flows from finalizer_node → answer.py functions → prompts.py _build_prompt()

## No Concerns
All steps completed successfully without issues.
