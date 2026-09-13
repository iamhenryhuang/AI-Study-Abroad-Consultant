# Copilot Instructions — Study Abroad RAG

以 LangGraph 打造的結構化資料問答系統，協助北美 CS 碩士申請諮詢。與 `CLAUDE.md`（若存在）內容保持同步，作為 Claude Code 與 GitHub Copilot 共用的專案上下文。

## 系統概覽

各校申請要求（GPA、TOEFL/IELTS、GRE、截止日期、學費、獎助）由 `data_crawler` 爬蟲抽取後存入 PostgreSQL 結構化欄位；頁面全文切段存入 `document_chunks` 供全文檢索。使用者提問時，後端 Agent 先用 text-to-SQL 查結構化欄位，查不到再 fallback 到全文檢索，最後整理成答案。

## Agent 流程（LangGraph StateGraph）

`decompose`（意圖判斷/拆解）→ 依旗標並行觸發 `search`（text-to-SQL）/ `extension_function`（教授查詢，SerpAPI 或 professors 表）/ `experience_search`（applicant_reports 錄取經驗）→ `verify`（資料是否足夠）→ 不足時 `fulltext`（全文檢索）→ 仍不足時 `refine`（改寫查詢，最多 1 輪重跑）→ `finalize`（生成答案）→ `critic`（幻覺複查）→ 最終答案。

分層檢索 fallback 觸發條件是「Verifier 判定資料不足」，不是「SQL 回傳 0 筆」。

## 專案結構

```
backend/                 # FastAPI、檢索邏輯、LangGraph Agent
  api.py                   # API 入口（SSE 串流，POST /api/chat）
  scripts/
    db/                      # DB 連線與操作
    retriever/               # sql_search + fulltext_search + hybrid_search + applicant_search + agent/
    generator/               # OpenAI 答案生成（分級模型）
    professor_fetcher/       # SerpAPI 教授資料抓取
frontend/                # React/Vite：申請經驗上傳與依學校查詢
data_crawler/            # LangGraph 爬蟲（正式資料源）
crawler/                 # 舊版 Playwright 爬蟲設定（root_url/黑名單，data_crawler 沿用）
db/                      # schema、migrations、測試資料、載入腳本
```

## 常用指令

```bash
# 環境
python -m venv .venv && source .venv/Scripts/activate
pip install -r requirements.txt

# 資料庫初始化（從專案根目錄執行）
python backend/scripts/run.py init-full     # 一鍵建表 + 灌入資料（含社群回報）
python backend/scripts/run.py verify-db     # 確認資料已寫入

# 啟動服務
uvicorn backend.api:app --reload --host 0.0.0.0 --port 8000
cd frontend && npm run dev                  # http://localhost:5173

# CLI 測試問答
python backend/scripts/run.py agent "問題"   # 完整跑一次 LangGraph Agent（正式問答用這個）
python backend/scripts/run.py search "問題"  # 只測 text-to-SQL
```

## 關鍵規則（務必遵守）

- **DB port**：本機開發用 `5434`（docker-compose 對外埠），不是 PostgreSQL 預設的 `5432`；`DATABASE_URL` 要對應正確，否則會 connection timeout。
- **Windows + Docker Desktop**：連線用 `127.0.0.1` 而非 `localhost`，避免 IPv6 解析 fallback 造成延遲。
- **模型分級**：判斷/結構化任務（decomposer/verifier/critic/text-to-SQL）用 `gpt-4.1`；最終答案生成用 `gpt-4o`（`OPENAI_MODEL` / `OPENAI_ANSWER_MODEL` 可覆寫）。
- **新增學校時**：記得同步更新 `backend/scripts/retriever/agent/state.py` 的 `_SCHOOL_ALIASES`，否則 Decomposer 無法辨識學校縮寫/別名。
- **`professor_query` 與 `professor_list_query` 互斥**：問題已指名教授姓名時一律走指名查詢，不會同時觸發名單查詢。
- **錄取經驗類問題**：必須用 `run.py agent`（完整 LangGraph 流程）才會查 `applicant_reports`；`run.py rag` 不含經驗回報/教授/全文檢索。
- **不亂編答案**：檢索結果先經 Verifier 判斷是否文不對題，生成後再經 Critic 複查有無幻覺，有疑慮就誠實告知或附警告；申請經驗類回答需標註「非官方經驗談」。

## 資料源

三個資料源：`data_crawler/` 爬蟲寫入的正式 `programs` 家族；`db/data/schools_data.json` 測試假資料（目前 5 校）；`db/data/professors.json` 手動整理的教授名單種子資料（目前 Georgia Tech / Purdue / Stanford 3 校）。

## 研究/實驗背景（`eval/`）

本專案有一組論文向的對照實驗，結論會反過來限制架構決策，改動 Verifier/Critic 相關程式碼前應留意：

- **實驗問題**：面對「資料庫沒有官方資料的學校」，結構化 agent（本專案架構）是否比自主 ReAct 更誠實、更少幻覺？差距是靠整套結構化流程 + 生成前 Verifier，還是靠生成後 Critic？
- **三方對照**：`agent`（Verifier✅ + Critic✅，本專案現有系統）、`react`（純 ReAct，無護欄）、`react_critic`（ReAct + 只補 Critic，用於消融）。三者共用同一組檢索工具、同款 LLM，只差護欄設計。
- **題庫**：94 題，A 類 34 題（5 所已收錄學校的結構化欄位，可自動判分）+ E 類 60 題（20 所未收錄學校，考誠實拒答，官方資料確定不存在故任何具體分數都判為幻覺）。
- **核心結論**：A 類兩系統都 1.000 答對（有資料時一樣準）；E 類 agent 誠實拒答率 1.000／幻覺率 0.000，純 ReAct 幻覺率 0.550，**補上 Critic 幻覺率仍是 0.550 幾乎沒改善**（Critic 只會加警告，不會移除虛構內容）。→ **防幻覺要靠生成前的來源控制與拒答決策（Verifier），不是生成後的警告標註（Critic）**。
- **對開發的含意**：`extension_function` 的 ReAct baseline 與 `verify`/`critic` 節點是這組實驗的核心受測對象；修改其行為時，若條件允許應重跑 `eval/run_eval.py` 驗證是否影響誠實拒答率/幻覺率，而不能只看程式碼是否能跑。
- 詳細方法論、逐題案例、侷限說明見 [`../eval/EXPERIMENT_REPORT.md`](../eval/EXPERIMENT_REPORT.md)；一頁摘要見 [`../eval/EXPERIMENT_SUMMARY.md`](../eval/EXPERIMENT_SUMMARY.md)；跑實驗的程式見 `eval/run_eval.py`、`eval/generate_questions.py`。

## 文件連結

- [`../README.md`](../README.md) — 完整系統概覽、架構圖、快速開始
- [`../backend/README.md`](../backend/README.md) — 分層檢索、品質控管節點、事件串流、教授查詢實作細節
- [`../data_crawler/README.md`](../data_crawler/README.md) — 爬蟲 pipeline 用法與 graph 結構
- [`../db/README.md`](../db/README.md) — 資料庫 schema 細節
- [`../eval/EXPERIMENT_REPORT.md`](../eval/EXPERIMENT_REPORT.md) — 結構化 Agent vs. ReAct 誠實性/幻覺對照實驗完整報告
- [`../eval/EXPERIMENT_SUMMARY.md`](../eval/EXPERIMENT_SUMMARY.md) — 同實驗一頁摘要
