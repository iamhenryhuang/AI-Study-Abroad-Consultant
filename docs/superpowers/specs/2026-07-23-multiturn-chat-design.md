# 多輪 AI 諮詢聊天頁 設計

日期：2026-07-23
狀態：已核准

## 目標

在既有前端加一個**全螢幕聊天頁**，讓使用者用自然語言向後端 agent 提問，串流顯示答案與可摺疊的思考步驟，並支援**多輪對話**（agent 能理解「那它的截止日呢」這類承接前文的問題）。

## 背景與現況

- 後端 `POST /api/chat`（`backend/api.py`）已存在：接收 `{query, max_steps}`，以 SSE（`text/event-stream`）串流回傳 agent 事件。
- SSE 傳輸格式：標準 `data: {json}\n\n`，收到 `answer` 或 `error` 事件即結束串流。
- SSE 事件類型：`thinking` / `tool_call` / `tool_result` / `answer_chunk` / `answer` / `error`。
- CORS 已允許 `http://localhost:5173`、`http://127.0.0.1:5173`（前端 dev port）。
- `/api/chat` 目前**無狀態**：每次只處理單一 query，不帶對話歷史。
- 前端為 Vite + React 19，hash 路由（`#/upload`、`#/search`），API 集中在 `frontend/src/api.js`（普通 fetch）。
- Agent 入口 `run_agent(query, max_steps, verbose, on_event, cancel_event)`。判斷型任務用 `call_llm`（`DEFAULT_MODEL`＝gpt-4o-mini，`temperature=0`）。

## 決策摘要（來自 brainstorming）

- 介面形式：**全螢幕聊天頁**（新路由，非浮動 widget、非現有頁內嵌）。
- 顯示程度：**串流答案 + 可摺疊的思考步驟**（非只顯示答案、非完整 debug 全事件）。
- 多輪對話：**要**（agent 需理解承接前文的問題）。
- 持久化：**不存**，對話僅活在 React state，重整頁即消失（無 localStorage、無 DB）。
- 多輪實作方式：**方案 A — 查詢改寫（condense question）**。後端在 `run_agent` 前加一個獨立前置步驟，用歷史把跟隨問題改寫成獨立問題，再交給**完全不改**的 agent 處理。

## 架構與資料流

```
前端 ChatPage (#/chat)
  │ POST /api/chat { query, history:[{role,content}...], max_steps }
  ▼
後端 /api/chat
  │ ① contextualize_query(query, history) → standalone_query
  │    （history 為空時原樣回傳，不呼叫 LLM）
  │ ② run_agent(standalone_query, on_event=...)   ← 不改
  ▼
  SSE：data: {"type":"thinking"...}\n\n / tool_call / tool_result
       data: {"type":"answer_chunk","text":"..."}\n\n
       data: {"type":"answer","text":"完整答案"}\n\n
  ▼
前端逐事件更新：思考步驟進摺疊區、答案逐字浮現
```

## 後端改動

### a. `ChatRequest` 加 `history`（`backend/api.py`）

```python
class ChatMessage(BaseModel):
    role: str        # "user" | "assistant"
    content: str

class ChatRequest(BaseModel):
    query: str
    max_steps: int = 5
    history: list[ChatMessage] = Field(default_factory=list, max_length=20)
```

`max_length=20` 防止前端送過長歷史造成濫用/成本失控。

### b. 新檔 `backend/scripts/retriever/contextualize.py`

單一職責：把承接前文的跟隨問題改寫成獨立問題。

```python
def contextualize_query(query: str, history: list[dict]) -> str:
    """有歷史時用 mini 把跟隨問題改寫成獨立問題；無歷史直接回傳原問題。

    history: [{"role": "user"|"assistant", "content": str}, ...]
    LLM 呼叫失敗時降級回原始 query（不讓改寫失敗擋住整個對話）。
    """
```

- `history` 為空 → 直接 `return query`，不呼叫 LLM（無成本）。
- 用既有 `call_llm`（`DEFAULT_MODEL`＝mini、`temperature=0`）。
- Prompt 要點：給定對話歷史與跟隨問題，改寫成不需上下文即可理解的獨立問題；若本身已是獨立問題則原樣回傳；只輸出改寫後的問題，不要多餘文字。
- `try/except` 包住 LLM 呼叫，失敗回傳原 `query`。

### c. `/api/chat` 在 `run_agent` 前插入改寫

```python
standalone = contextualize_query(
    request.query, [m.model_dump() for m in request.history]
)
# run_in_thread 內以 standalone 取代 request.query 傳給 run_agent，其餘不動
```

## 前端改動

### a. 路由與導覽（`frontend/src/App.jsx`）

- 新增路由分支 `#/chat` → `<ChatPage />`（沿用現有 hash 路由與 `hashchange` 重渲染機制）。
- 導覽列加第三個連結「AI 諮詢」，`active` 狀態邏輯與現有 upload/search 一致。

### b. 新檔 `frontend/src/chatApi.js` — SSE 串流讀取

```js
export async function streamChat(payload, onEvent, signal) {
  // fetch POST /api/chat（沿用 api.js 的 API_BASE 慣例）
  // 以 response.body.getReader() 讀 ReadableStream
  // 累積 buffer，以 "\n\n" 切 SSE frame，去掉 "data: " 前綴、JSON.parse
  // 逐一 onEvent(event)；傳入 signal 供 abort
}
```

不能用瀏覽器原生 `EventSource`（僅支援 GET），故用 `fetch` + `ReadableStream` 手動解析。與 `api.js`（普通 fetch）分開，各自單純。

### c. 新檔 `frontend/src/ChatPage.jsx`

- `messages` state：`[{ role, content, steps: [], done }]`。
- 送出流程：
  1. 推入使用者訊息 `{role:"user", content}`。
  2. 推入空的 assistant 訊息 `{role:"assistant", content:"", steps:[], done:false}` 當串流目標。
  3. 呼叫 `streamChat({ query, history, max_steps })`，`history` = 送出前的既有 messages（轉成 `{role, content}`）。
  4. 依事件更新**最後一則 assistant 訊息**：
     - `thinking` / `tool_call` / `tool_result` → push 進 `steps`。
     - `answer_chunk` → 累加 `content`（逐字浮現）。
     - `answer` → 設定最終 `content`、`done=true`、摺疊 `steps`。
     - `error` → 該則顯示錯誤訊息。
- 送出中：disable 輸入框、顯示「停止」鈕（`AbortController` → abort fetch → 後端 `finally` 觸發 `cancel_event`）。

### d. `ChatMessage` 元件

- 使用者訊息：靠右泡泡。
- assistant 訊息：靠左泡泡，上方一個可摺疊的「思考過程」區塊（預設摺疊；串流中可展開看即時步驟）。

### e. `frontend/src/styles.css`

加聊天頁樣式（訊息泡泡、輸入列、思考步驟摺疊區），沿用現有設計語言（同色系、圓角、間距）。

## 錯誤處理

- 後端連不上 / 串流中斷 → 該則 assistant 訊息顯示錯誤，輸入框恢復可用。
- `contextualize` 的 LLM 呼叫失敗 → 降級用原始 query，不中斷對話。
- 送出中提供停止鈕；abort 後前端停止讀取、後端經 `finally` 通知 agent 取消。
- 空白輸入不送出。

## 測試

- **後端** `tests/test_contextualize.py`（unittest，沿用現有慣例）：
  - 無歷史 → 原樣回傳且**不呼叫** `call_llm`（mock 驗證未被呼叫）。
  - 有歷史 → 回傳被 mock 的改寫結果。
  - `call_llm` 拋例外 → 降級回原始 query。
- **前端**（選配，待使用者決定是否引入 vitest）：`chatApi` 的 SSE 解析——餵一段 `data: {...}\n\n` 字串，驗證正確切分並逐一 `onEvent`。目前前端無測試框架，若不引入則此項略過，於 plan 中標明。

## 不改動的範圍

- Agent 本體（`retriever/agent/`）、hybrid search、DB schema、既有 `/api/experiences` 端點、既有 upload/search 前端頁面，全部不動。

## 檔案清單

| 檔案 | 動作 |
|------|------|
| `backend/api.py` | 改：加 `ChatMessage` / `history` 欄位 + 呼叫 `contextualize_query` |
| `backend/scripts/retriever/contextualize.py` | 新增 |
| `tests/test_contextualize.py` | 新增 |
| `frontend/src/App.jsx` | 改：加 `#/chat` 路由與導覽連結 |
| `frontend/src/chatApi.js` | 新增 |
| `frontend/src/ChatPage.jsx` | 新增 |
| `frontend/src/styles.css` | 改：加聊天頁樣式 |
