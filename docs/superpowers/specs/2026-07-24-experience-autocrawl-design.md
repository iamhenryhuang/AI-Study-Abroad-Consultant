# 經驗資料背景補爬 設計

日期：2026-07-24
狀態：已核准（設計於對話中呈現並核准，決策採建議值）

## 目標

當 agent 查詢某校的申請經驗（`applicant_reports`）發現資料太少時：
1. **仍用現有資料回答**，並在答案標記「此校資料較少」；
2. **在背景（非阻塞）排一個爬蟲任務**補該校的 GradCafe 資料，下次查詢就更完整。

使用者的即時回應**不等待**爬蟲。

## 背景與現況

- `experience_search_node`（`backend/scripts/retriever/agent/nodes/retrieval.py`）查 `applicant_reports`（唯讀），撈不到就撈不到，不觸發任何爬蟲。
- GradCafe 爬蟲 `crawler/gradcafe.py` 是離線手動 pipeline：`crawl_query(query, max_pages)` 已參數化；`main()` dump JSON。
- `db/load_applicant_reports.py` 有 `_clean_gradcafe(rows) -> list[dict]`（清洗）與 `_INSERT_SQL`（`ON CONFLICT DO UPDATE` upsert，重跑安全）。
- `backend/scripts/db/connection.py` 的 `get_connection()` 預設可寫；agent 檢索會另外設 `conn.read_only = True`。
- `_SCHOOL_ALIASES`（`retriever/agent/state.py`）：`school_id -> [別名...]`，例如 `"cmu": ["cmu","carnegie mellon","卡內基梅隆"]`。
- **import 限制**：`crawler/` 與 repo 根 `db/` 皆非正常套件，且 repo 根 `db/` 會與 `backend/scripts/db/` 撞名。故重用其函式須以 **importlib 從檔案路徑載入**（比照 `run.py` 載 `load_applicant_reports` 的作法），不可用 `sys.path` 加 repo 根（會造成 `db` namespace 衝突）。

## 決策（採對話中的建議值）

| 決策 | 值 |
|------|----|
| 「不足」門檻 | 少於 5 筆（`SPARSE_THRESHOLD = 5`）|
| 補爬頁數 | 該校 5 頁（`max_pages=5`）|
| 去重窗口 | 7 天內爬過就不重爬 |
| 任務機制 | 做法 A：in-process daemon thread（不引入 Redis/Celery/DB job 表）|
| 即時 vs 背景 | 背景，回答不等爬蟲 |
| 寫入連線 | 另開 `get_connection()`（**不設** `read_only`），與 agent 唯讀連線隔離 |
| 失敗處理 | 背景爬蟲失敗只記 log，絕不影響已回的答案 |

## 架構與資料流

```
experience_search_node 查 applicant_reports
  ├─ 結果 ≥ 5 或無鎖定學校 → 照常（experience_sparse=False）
  └─ 結果 < 5 且有 school_id
       ├─ state["experience_sparse"] = True     → 答案加註「資料較少」
       └─ maybe_enqueue_crawl(school_id)         → 背景 thread（非阻塞）
              ↓（背景）
           crawl_query(該校查詢字串, 5 頁)  →  _clean_gradcafe  →  upsert applicant_reports（可寫連線）
```

## 元件設計

### 1. 新模組 `backend/scripts/retriever/experience_crawl.py`

以 importlib 從檔案路徑載入跨目錄函式，避開 path 衝突：

```python
import importlib.util, threading
from datetime import datetime, timedelta
from pathlib import Path
from db.connection import get_connection

_ROOT = Path(__file__).resolve().parents[3]   # repo 根

def _load_module(name, relpath):
    spec = importlib.util.spec_from_file_location(name, _ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
# 於模組載入時各載一次
# _gradcafe = _load_module("gradcafe_crawler", "crawler/gradcafe.py")
# _lar      = _load_module("load_applicant_reports", "db/load_applicant_reports.py")
```

介面：
- `maybe_enqueue_crawl(school_id: str) -> None`：兩層去重（in-flight set + `_recently_crawled`）後開 daemon thread。永不 raise。
- `_recently_crawled(school_id, days=7) -> bool`：唯讀查 `MAX(created_at) FROM applicant_reports WHERE school_id=%s`，7 天內為 True。
- `_school_to_gradcafe_query(school_id) -> str`：用 `_SCHOOL_ALIASES` 取一個適合 GradCafe 搜尋的字串（取最長別名，通常是全名）。
- `_crawl_and_load(school_id)`（thread target）：`crawl_query` → `_clean_gradcafe` → 用可寫連線逐筆 `_INSERT_SQL` upsert → commit；`try/except` 記 log；`finally` 移出 in-flight。

常數 `SPARSE_THRESHOLD = 5`。

### 2. 改 `experience_search_node`（`retrieval.py`）

搜尋後：偵測 school_id、判斷 `sparse = school_id and len(exp_docs) < SPARSE_THRESHOLD`；若 sparse 呼叫 `maybe_enqueue_crawl(school_id)`；回傳新增 `"experience_sparse": sparse`。

### 3. State 加欄位（`agent/state.py`）

`experience_sparse: bool`，初始值 False（於 `run_agent` 初始 state 與 decomposer 回傳補上，比照現有 bool 欄位）。

### 4. 答案標記（generator）

當 `experience_sparse=True`，在送生成前把一句提示併入 context 或 system 指示：「此校經驗回報較少，以下為現有資料；系統已在背景補充更多，稍後再問可能更完整。」採最小改動：在 finalizer/answer 節點依 flag 附加一段說明文字給 generator。

## 錯誤處理

- 背景爬蟲任何例外 → 記 log，不影響已串流的答案。
- `maybe_enqueue_crawl` 內所有判斷（DB 查詢、thread 建立）以 try/except 包住，永不 raise 回 agent。
- GradCafe 回非 200 / Inertia 版本變動 → `crawl_query` 既有處理回空，upsert 0 筆，安全。

## 三個必須守住的約束

1. **寫入連線隔離**：背景爬蟲用獨立、可寫的 `get_connection()`，**不得**沿用 agent 的唯讀連線。
2. **importlib 載入**：不加 repo 根到 `sys.path`（避免 `db` 撞名）；用檔案路徑 importlib 載 `crawler/gradcafe.py` 與 `db/load_applicant_reports.py`。
3. **失敗隔離**：補爬失敗絕不影響使用者已收到的答案。

## 測試

`tests/test_experience_crawl.py`（unittest）：
- `_recently_crawled`：mock 連線，回 7 天內 → True、超過 / None → False。
- `maybe_enqueue_crawl`：mock `_recently_crawled` 與 thread 建立——最近爬過 → 不開 thread；in-flight 已有 → 不開；否則開一次。
- 不實際打 GradCafe / 不實際寫 DB（全 mock）。

## 不改動範圍

- `crawler/gradcafe.py`、`db/load_applicant_reports.py`（只重用、不改）。
- agent 圖結構、hybrid search、其他檢索節點、DB schema。

## 檔案清單

| 檔案 | 動作 |
|------|------|
| `backend/scripts/retriever/experience_crawl.py` | 新增 |
| `backend/scripts/retriever/agent/nodes/retrieval.py` | 改（experience_search_node 尾端偵測+觸發）|
| `backend/scripts/retriever/agent/state.py` | 改（加 `experience_sparse` 欄位與初始值）|
| generator 答案節點（`retriever/agent/nodes/answer.py` 或 generator prompt）| 改（sparse 時加註）|
| `tests/test_experience_crawl.py` | 新增 |
