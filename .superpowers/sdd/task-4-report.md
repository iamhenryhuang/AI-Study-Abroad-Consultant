# Task 4 Report — 前端聊天頁實裝完成

## 完成項目

### 1. 建立 ChatPage.jsx
- **檔案路徑**: `frontend/src/ChatPage.jsx`
- **內容**: 完整的聊天頁面元件，包括：
  - 訊息渲染（使用者和助手分開顯示）
  - 思考過程展示面板（StepsPanel）
  - 使用者輸入和發送邏輯
  - SSE 串流處理（answer_chunk、answer、error、tool_call、tool_result、thinking）
  - 停止按鈕（AbortController）
  - 自動滾動至最新訊息

### 2. 改 App.jsx（3 處精確編輯）
- **2a 加 import**: 在第 6 行後新增 `import ChatPage from './ChatPage.jsx'`
- **2b 路由判斷**: 第 183 行改為：
  ```jsx
  const hash = window.location.hash
  const route = hash === '#/search' ? 'search' : hash === '#/chat' ? 'chat' : 'upload'
  ```
- **2c return 內容**: 第 187 行更新，新增 `#/chat` 導覽連結和聊天頁面渲染邏輯

### 3. 追加 styles.css
- **位置**: 檔尾（footer 後、@media 前）
- **內容**: 25 行 CSS，涵蓋：
  - `.chat-page` — 主容器（flex 佈局、高度計算）
  - `.chat-scroll` — 訊息區域
  - `.chat-empty` — 初始空狀態
  - `.msg`、`.msg.user`、`.msg.assistant` — 訊息氣泡樣式
  - `.steps`、`.steps-toggle` — 思考過程面板
  - `.chat-input` — 輸入框區域
  - `.send-btn`、`.stop-btn` — 按鈕樣式

## 編譯驗證

```bash
cd frontend
npm install
npm run build
```

**最終輸出**:
```
✓ 1745 modules transformed.
✓ built in 4.69s

dist/index.html                   0.50 kB │ gzip:  0.35 kB
dist/assets/index-xQaK1VOq.css    9.40 kB │ gzip:  2.93 kB
dist/assets/index-Bl-c8t-Y.js   211.40 kB │ gzip: 67.59 kB
```

**結果**: ✓ 無語法/import 錯誤，編譯成功。

## Git 提交

```bash
git add frontend/src/ChatPage.jsx frontend/src/App.jsx frontend/src/styles.css
git commit -m "feat: add full-screen multi-turn chat page"
```

- **提交雜湊**: `0054832`
- **分支**: `feat-chat-page`
- **變更**: 3 個檔案，165 行新增，2 行刪除

## 技術檢查清單

- ✓ ChatPage.jsx 正確 import chatApi.js
- ✓ App.jsx 路由邏輯完整（upload / search / chat 三路由）
- ✓ 導覽列新增 "AI 諮詢" 連結，active 狀態正確
- ✓ CSS 變數使用（藍色 #2563eb、紅色 #ef4444、灰色等）與既有設計一致
- ✓ 尚未進行瀏覽器實機測試（留予人類驗證多輪對話、串流動畫、停止鈕）

## 無已知問題

---

# Fix: ChatPage Unmount Cleanup (Code Review Finding)

## 問題
當使用者在串流進行中離開聊天頁時，ChatPage 卸載但在途中的 fetch 請求從未被中止，導致持續串流並在已卸載元件上呼叫 `setMessages`，浪費後端和網路資源。

## 修正內容
**檔案**: `frontend/src/ChatPage.jsx`  
**變更**: 在第 54-56 行的自動滾動 effect 之後，新增一個卸載清理 effect：

```jsx
// 卸載時中止仍在進行的串流，避免對已卸載元件 setState、浪費後端資源
useEffect(() => () => abortRef.current?.abort(), [])
```

此 effect：
- 在元件卸載時執行清理函數
- 呼叫 `abortRef.current?.abort()` 中止任何進行中的 AbortController
- 防止串流繼續向已卸載元件 setState

## 編譯驗證
```bash
cd frontend
npm run build
```

**最終輸出**:
```
✓ built in 4.56s
```

**結果**: ✓ 編譯成功，無錯誤。

## 提交
```
commit 347598c
fix: abort in-flight chat stream on ChatPage unmount
```

---

# Fix: Cap Outgoing Chat History to Backend Limit (Code Review Finding)

## 問題
後端 `/api/chat` 將 `history` 上限設定為 20 則訊息 (`max_length=20`)。但 `ChatPage.jsx` 的 `send()` 每次都送出整個訊息歷史而不進行修剪，導致在約 11 次交換（22 則訊息）後所有請求都失敗並回傳 HTTP 422，多輪對話因此無聲地中斷。

## 修正內容
**檔案**: `frontend/src/ChatPage.jsx`  
**行號**: 第 74-75 行  
**變更**:
```jsx
// 變前：
const history = messages.map((m) => ({ role: m.role, content: m.content }))

// 變後：
// 後端 history 上限 20，只送最近 20 則（保留近期上下文即可）
const history = messages.slice(-20).map((m) => ({ role: m.role, content: m.content }))
```

## 編譯驗證
```bash
cd frontend
npm run build
```

**最終輸出**:
```
✓ built in 4.67s
```

**結果**: ✓ 編譯成功，無錯誤。

## 提交
```
commit 0f6544c
fix: cap outgoing chat history to backend's 20-message limit
```
