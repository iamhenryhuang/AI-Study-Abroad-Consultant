# Task 3 Brief — generator 推薦專屬格式

推薦功能最後一個 task：讓 generator 在推薦題輸出「衝刺/適中/保底」三檔專屬格式。改 3 個檔案。

## Global Constraints
- 只改下列 3 檔，不新增檔案。
- 環境：Windows。Bash git 不可用時用 PowerShell。
- 精確 find/replace：替換前確認原文吻合。

## Files（皆 Modify）
- `backend/scripts/generator/prompts.py`
- `backend/scripts/generator/answer.py`
- `backend/scripts/retriever/agent/nodes/answer.py`

## Step 1: prompts.py 加推薦格式指示
在 `generator/prompts.py`，`_SYSTEM_PROMPT = """..."""` 定義結束之後、`def _build_prompt` 之前，新增常數：
```python
_RECOMMENDATION_INSTRUCTION = """
【選校推薦格式（本題為成績推薦）】
參考資料中標為 [衝刺]/[適中]/[保底] 的是依你的分數對照各校錄取中位數分出的三檔。請：
- 用三個 Markdown 標題分段：`### 衝刺`、`### 適中`、`### 保底`。
- 每檔下用 `-` 列出學校，附「你的分數 vs 該校中位數」對比，以及相近的真實錄取案例（標明為非官方個別案例）。
- 結尾加一句免責：本推薦基於歷史數據與個別回報，僅供參考，非錄取保證。
- 不得推薦參考資料以外的學校，也不得編造中位數或案例。
"""
```
把 `_build_prompt` 改成接受 `recommendation` 旗標並附加指示。原本：
```python
def _build_prompt(query: str, context_docs: list[dict]) -> str:
    context_text = format_context_for_prompt(context_docs)
    return f"""{_SYSTEM_PROMPT}

--- 參考資料（共 {len(context_docs)} 筆） ---
{context_text}

--- 使用者問題 ---
{query}

--- 你的回答 ---
（請嚴格遵守以上規則，若資料不足請直接說不知道並引導查官網）
"""
```
改成：
```python
def _build_prompt(query: str, context_docs: list[dict], recommendation: bool = False) -> str:
    context_text = format_context_for_prompt(context_docs)
    extra = _RECOMMENDATION_INSTRUCTION if recommendation else ""
    return f"""{_SYSTEM_PROMPT}
{extra}
--- 參考資料（共 {len(context_docs)} 筆） ---
{context_text}

--- 使用者問題 ---
{query}

--- 你的回答 ---
（請嚴格遵守以上規則，若資料不足請直接說不知道並引導查官網）
"""
```

## Step 2: answer.py 傳旗標
`backend/scripts/generator/answer.py`，兩個函式各加 `recommendation` 參數並傳給 `_build_prompt`。

`generate_answer_stream` 的簽名與 `_build_prompt` 呼叫：
```python
def generate_answer_stream(query: str, context_docs: list[dict], model_name: str = ANSWER_MODEL, recommendation: bool = False):
    """串流版本：逐 chunk yield 原始文字。若 API 失敗則 raise Exception。"""
    client = get_openai_client()
    prompt = _build_prompt(query, context_docs, recommendation=recommendation)
```
`generate_answer` 的簽名與 `_build_prompt` 呼叫：
```python
def generate_answer(query: str, context_docs: list[dict], model_name: str = ANSWER_MODEL, recommendation: bool = False) -> str | None:
    """根據檢索到的結構化資料生成回答。"""
    client = get_openai_client()
    prompt = _build_prompt(query, context_docs, recommendation=recommendation)
```
（其餘函式主體不變。）

## Step 3: finalizer 傳入旗標
`backend/scripts/retriever/agent/nodes/answer.py` 的 `finalizer_node`，把串流生成那段：
```python
    full_text = ""
    try:
        for chunk in generate_answer_stream(query, all_docs):
            _check_cancel()
            full_text += chunk
            if chunk:
                _emit({"type": "answer_chunk", "text": chunk})
    except Exception as e:
        print(f"[Finalizer] 串流失敗，回退到非串流: {e}")
        full_text = generate_answer(query, all_docs) or ""
```
改成（加 recommendation 旗標）：
```python
    recommendation = state.get("wants_recommendation", False)
    full_text = ""
    try:
        for chunk in generate_answer_stream(query, all_docs, recommendation=recommendation):
            _check_cancel()
            full_text += chunk
            if chunk:
                _emit({"type": "answer_chunk", "text": chunk})
    except Exception as e:
        print(f"[Finalizer] 串流失敗，回退到非串流: {e}")
        full_text = generate_answer(query, all_docs, recommendation=recommendation) or ""
```
（若 finalizer_node 內 `state` 變數名不同，用實際的 state 變數。這段前面已有 `query = state["original_query"]` 與 `all_docs = state.get("verified_docs", [])`。）

## Step 4: 驗證 import
Run: `python -c "import sys; sys.path.insert(0,'backend/scripts'); from retriever.agent import run_agent; from generator.answer import generate_answer_stream; print('OK')"`
Expected: 印出 `OK`

## Step 5: Commit
```bash
git add backend/scripts/generator/prompts.py backend/scripts/generator/answer.py backend/scripts/retriever/agent/nodes/answer.py
git commit -m "feat: add recommendation answer format to generator"
```
（Bash git 不可用時用 PowerShell。）
