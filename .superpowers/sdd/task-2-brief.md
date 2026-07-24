# Task 2 Brief — 後端 /api/chat 接線（history + 改寫）

多輪聊天功能的第二個 task：把 Task 1 的 `contextualize_query` 接進 `/api/chat`，並讓請求接受對話歷史。只改一個檔案：`backend/api.py`。

## Global Constraints
- Task 1 已完成並提供：`retriever.contextualize.contextualize_query(query: str, history: list[dict]) -> str`。
- 環境：Windows。用 Bash 工具跑 git 若找不到，改用 PowerShell 工具。Python 用 miniconda。
- 只改 `backend/api.py`，不要動其他檔案。

## Files
- Modify: `backend/api.py`

## Interface（後續前端依賴）
- `/api/chat` 接受 `{query, max_steps, history:[{role,content}...]}`。
- `ChatRequest` 有 `history: list[ChatMessage]` 欄位（上限 20）。

## Step 1: 加 import
在 `backend/api.py` 第 22 行

```python
from retriever.agent import run_agent
```

下方新增一行：

```python
from retriever.contextualize import contextualize_query
```

## Step 2: 擴充請求模型
把原本的

```python
class ChatRequest(BaseModel):
    query: str
    max_steps: int = 5
```

改成

```python
class ChatMessage(BaseModel):
    role: str        # "user" | "assistant"
    content: str


class ChatRequest(BaseModel):
    query: str
    max_steps: int = 5
    history: list[ChatMessage] = Field(default_factory=list, max_length=20)
```

## Step 3: 在 run_agent 前插入改寫
在 `chat` 端點的 `run_in_thread` 內，把

```python
    def run_in_thread() -> None:
        try:
            result = run_agent(
                query=request.query,
                max_steps=request.max_steps,
                verbose=False,
                on_event=on_event,
                cancel_event=cancel_event,
            )
```

改成

```python
    def run_in_thread() -> None:
        try:
            standalone = contextualize_query(
                request.query,
                [m.model_dump() for m in request.history],
            )
            result = run_agent(
                query=standalone,
                max_steps=request.max_steps,
                verbose=False,
                on_event=on_event,
                cancel_event=cancel_event,
            )
```

## Step 4: 驗證 app 可載入、ChatRequest 接受 history
Run（專案根目錄）:
```bash
python -c "import sys; sys.path.insert(0, 'backend'); from api import ChatRequest; req = ChatRequest(query='q', max_steps=5, history=[{'role':'user','content':'x'},{'role':'assistant','content':'y'}]); print('history len', len(req.history))"
```
Expected: 印出 `history len 2`（無 ImportError、模型接受 history 欄位）。

注意：此指令會 import 整個 api（含 langgraph 等），第一次可能較慢。若出現與本次改動無關的既有 import 錯誤，請在報告中說明，不要嘗試修別的檔案。

## Step 5（手動、本 task 不做）
plan 的手動 curl 端到端驗證需要啟動 DB + OpenAI 服務，交由人類最後執行，你不需要做這步。

## Step 6: Commit
```bash
git add backend/api.py
git commit -m "feat: wire multi-turn history into /api/chat via contextualization"
```
（Bash git 不可用時改用 PowerShell。）
