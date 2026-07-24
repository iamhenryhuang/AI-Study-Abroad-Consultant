# 經驗資料背景補爬 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** agent 查某校申請經驗發現資料 <5 筆時，仍用現有資料回答並標記「資料較少」，同時在背景（daemon thread、非阻塞）補爬該校 GradCafe 資料 upsert 進 DB。

**Architecture:** 新增 `retriever/experience_crawl.py`（背景爬蟲 + 兩層去重），`experience_search_node` 偵測不足時觸發它並在 state 標記 `experience_sparse`，`finalizer_node` 依此在答案前加註。跨目錄的 `crawl_query`/`_clean_gradcafe`/`_INSERT_SQL` 以 importlib 從檔案路徑**延遲載入**，避開 `db` namespace 撞名。Spec：`docs/superpowers/specs/2026-07-24-experience-autocrawl-design.md`。

**Tech Stack:** Python threading、importlib、psycopg（可寫連線）、既有 `crawler/gradcafe.py` 與 `db/load_applicant_reports.py`、unittest。

## Global Constraints

- `SPARSE_THRESHOLD = 5`；補爬 `max_pages=5`；去重窗口 7 天。
- 背景爬蟲用獨立、**可寫**的 `get_connection()`（不設 `read_only`）；`_recently_crawled` 的查詢用唯讀。
- 跨目錄函式以 importlib 從檔案路徑載入，**不得**把 repo 根加進 `sys.path`（會使 `db` 與 `backend/scripts/db` 撞名）。importlib 載入採**延遲**（首次要用時才載），使模組 import 輕量。
- 背景爬蟲任何失敗只記 log，**絕不 raise 回 agent、絕不影響已回的答案**。
- `crawler/gradcafe.py`、`db/load_applicant_reports.py` 只重用、不修改。
- 後端測試：unittest，`python -m unittest discover tests -p "test_x.py" -v`；測試檔開頭 `sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend" / "scripts"))`。
- 環境：Windows。Bash 找不到 git 時用 PowerShell。

## File Structure

| 檔案 | 責任 |
|------|------|
| `backend/scripts/retriever/experience_crawl.py`（新）| 背景補爬：enqueue+去重+爬+清洗+upsert |
| `tests/test_experience_crawl.py`（新）| enqueue 去重 / recently_crawled 單元測試 |
| `backend/scripts/retriever/agent/state.py`（改）| 加 `experience_sparse` 欄位 + 初始值 |
| `backend/scripts/retriever/agent/nodes/retrieval.py`（改）| experience_search_node 偵測不足→觸發+回傳 sparse |
| `backend/scripts/retriever/agent/nodes/answer.py`（改）| sparse 時答案前加註 |

---

### Task 1: experience_crawl.py 背景補爬模組

**Files:**
- Create: `backend/scripts/retriever/experience_crawl.py`
- Test: `tests/test_experience_crawl.py`

**Interfaces:**
- Consumes: `db.connection.get_connection()`；`retriever.agent.state._SCHOOL_ALIASES`；延遲載入的 `crawler/gradcafe.py:crawl_query`、`db/load_applicant_reports.py:_clean_gradcafe,_INSERT_SQL`
- Produces: `maybe_enqueue_crawl(school_id: str) -> None`（非阻塞、永不 raise；Task 2 呼叫）、`SPARSE_THRESHOLD = 5`

- [ ] **Step 1: 寫失敗測試 `tests/test_experience_crawl.py`**

```python
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend" / "scripts"))

from retriever import experience_crawl as ec


class _FakeCursor:
    def __init__(self, row):
        self._row = row
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, *a, **k): pass
    def fetchone(self): return self._row


class _FakeConn:
    def __init__(self, row):
        self._row = row
        self.read_only = False
        self.closed = False
    def cursor(self): return _FakeCursor(self._row)
    def close(self): self.closed = True


class TestRecentlyCrawled(unittest.TestCase):
    def test_recent_returns_true(self):
        conn = _FakeConn([datetime.now() - timedelta(days=1)])
        with patch.object(ec, "get_connection", return_value=conn):
            self.assertTrue(ec._recently_crawled("cmu", days=7))

    def test_old_returns_false(self):
        conn = _FakeConn([datetime.now() - timedelta(days=30)])
        with patch.object(ec, "get_connection", return_value=conn):
            self.assertFalse(ec._recently_crawled("cmu", days=7))

    def test_never_crawled_returns_false(self):
        conn = _FakeConn([None])
        with patch.object(ec, "get_connection", return_value=conn):
            self.assertFalse(ec._recently_crawled("cmu", days=7))


class TestMaybeEnqueueCrawl(unittest.TestCase):
    def setUp(self):
        ec._in_flight.clear()

    def tearDown(self):
        ec._in_flight.clear()

    def test_empty_school_id_does_nothing(self):
        with patch.object(ec.threading, "Thread") as mock_thread:
            ec.maybe_enqueue_crawl("")
        mock_thread.assert_not_called()

    def test_recently_crawled_skips(self):
        with patch.object(ec, "_recently_crawled", return_value=True), \
             patch.object(ec.threading, "Thread") as mock_thread:
            ec.maybe_enqueue_crawl("cmu")
        mock_thread.assert_not_called()

    def test_in_flight_skips(self):
        ec._in_flight.add("cmu")
        with patch.object(ec, "_recently_crawled", return_value=False), \
             patch.object(ec.threading, "Thread") as mock_thread:
            ec.maybe_enqueue_crawl("cmu")
        mock_thread.assert_not_called()

    def test_fresh_starts_thread_once(self):
        started = MagicMock()
        with patch.object(ec, "_recently_crawled", return_value=False), \
             patch.object(ec.threading, "Thread", return_value=started) as mock_thread:
            ec.maybe_enqueue_crawl("cmu")
        mock_thread.assert_called_once()
        started.start.assert_called_once()
        self.assertIn("cmu", ec._in_flight)


class TestSchoolToQuery(unittest.TestCase):
    def test_picks_longest_english_alias(self):
        # cmu 別名含 "cmu" 與 "carnegie mellon"，應取較長的英文全名
        self.assertEqual(ec._school_to_gradcafe_query("cmu"), "carnegie mellon")

    def test_unknown_school_falls_back_to_id(self):
        self.assertEqual(ec._school_to_gradcafe_query("nonexistent"), "nonexistent")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 確認測試失敗**

Run: `python -m unittest discover tests -p "test_experience_crawl.py" -v`
Expected: FAIL/ERROR `ModuleNotFoundError: No module named 'retriever.experience_crawl'`

- [ ] **Step 3: 寫實作 `backend/scripts/retriever/experience_crawl.py`**

```python
"""申請經驗背景補爬：agent 發現某校 applicant_reports 資料太少時，
在背景（daemon thread）爬 GradCafe 補該校資料 upsert 進 DB，不阻塞使用者回應。

跨目錄的 crawl_query / _clean_gradcafe / _INSERT_SQL 以 importlib 從檔案路徑
延遲載入，避開 repo 根 db/ 與 backend/scripts/db/ 的 namespace 撞名。
"""
from __future__ import annotations

import importlib.util
import threading
from datetime import datetime, timedelta
from pathlib import Path

from db.connection import get_connection
from retriever.agent.state import _SCHOOL_ALIASES

SPARSE_THRESHOLD = 5          # 少於此筆數視為「資料不足」
_CRAWL_MAX_PAGES = 5
_DEDUP_DAYS = 7

_ROOT = Path(__file__).resolve().parents[3]   # repo 根
_in_flight: set[str] = set()
_lock = threading.Lock()
_deps = None                  # (crawl_query, _clean_gradcafe, _INSERT_SQL)


def _load_module(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, _ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _get_deps():
    """延遲載入跨目錄函式，首次呼叫時才載，避免模組 import 時的重依賴與副作用。"""
    global _deps
    if _deps is None:
        gradcafe = _load_module("gradcafe_crawler", "crawler/gradcafe.py")
        lar = _load_module("load_applicant_reports", "db/load_applicant_reports.py")
        _deps = (gradcafe.crawl_query, lar._clean_gradcafe, lar._INSERT_SQL)
    return _deps


def _school_to_gradcafe_query(school_id: str) -> str:
    """把 school_id 轉成適合 GradCafe（英文站）搜尋的字串：取最長的英文別名。"""
    aliases = _SCHOOL_ALIASES.get(school_id, [])
    english = [a for a in aliases if a.isascii()]
    return max(english, key=len) if english else school_id


def _recently_crawled(school_id: str, days: int = _DEDUP_DAYS) -> bool:
    """該校最近 days 天內是否已有補爬紀錄（以 applicant_reports.created_at 最大值判斷）。"""
    conn = get_connection()
    if not conn:
        return False
    try:
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute(
                "SELECT MAX(created_at) FROM applicant_reports WHERE school_id = %s",
                (school_id,),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    last = row[0] if row else None
    if last is None:
        return False
    return (datetime.now() - last) < timedelta(days=days)


def _crawl_and_load(school_id: str) -> None:
    """背景 thread 主體：爬該校 GradCafe → 清洗 → upsert。失敗只記 log。"""
    try:
        crawl_query, clean_gradcafe, insert_sql = _get_deps()
        query = _school_to_gradcafe_query(school_id)
        entries = crawl_query(query, max_pages=_CRAWL_MAX_PAGES)
        records = clean_gradcafe(entries)
        if not records:
            print(f"[ExpCrawl] {school_id} 補爬無新資料")
            return
        conn = get_connection()
        if not conn:
            print("[ExpCrawl] 無法取得資料庫連線")
            return
        try:
            with conn.cursor() as cur:
                for rec in records:
                    cur.execute(insert_sql, rec)
            conn.commit()
            print(f"[ExpCrawl] {school_id} 補爬完成，upsert {len(records)} 筆")
        finally:
            conn.close()
    except Exception as e:
        print(f"[ExpCrawl] {school_id} 補爬失敗：{e}")
    finally:
        with _lock:
            _in_flight.discard(school_id)


def maybe_enqueue_crawl(school_id: str) -> None:
    """若該校未在進行中、且近 7 天未爬過，開一個背景 daemon thread 補爬。永不 raise。"""
    if not school_id:
        return
    try:
        with _lock:
            if school_id in _in_flight:
                return
        if _recently_crawled(school_id):
            return
        with _lock:
            if school_id in _in_flight:      # DB 查詢期間可能已被別的請求加入
                return
            _in_flight.add(school_id)
        threading.Thread(target=_crawl_and_load, args=(school_id,), daemon=True).start()
    except Exception as e:
        print(f"[ExpCrawl] enqueue 失敗：{e}")
```

- [ ] **Step 4: 確認測試通過**

Run: `python -m unittest discover tests -p "test_experience_crawl.py" -v`
Expected: PASS（9 tests OK）

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/retriever/experience_crawl.py tests/test_experience_crawl.py
git commit -m "feat: add background GradCafe auto-crawl for sparse experience data"
```

---

### Task 2: 接進 agent（偵測不足 + 觸發 + 答案加註）

**Files:**
- Modify: `backend/scripts/retriever/agent/state.py`
- Modify: `backend/scripts/retriever/agent/nodes/retrieval.py`
- Modify: `backend/scripts/retriever/agent/nodes/answer.py`

**Interfaces:**
- Consumes: `retriever.experience_crawl.maybe_enqueue_crawl`、`SPARSE_THRESHOLD`（Task 1）
- Produces: state 新增 `experience_sparse: bool`；experience 查詢資料 <5 且有鎖定學校時，背景補爬 + 答案加註

- [ ] **Step 1: state.py 加欄位**

在 `backend/scripts/retriever/agent/state.py` 的 `AgentState` TypedDict，於 `generated_answer: bool` 那行下方新增：

```python
    experience_sparse: bool          # 經驗查詢資料不足（<SPARSE_THRESHOLD）時為 True，答案會加註並觸發背景補爬
```

並在 `create_initial_state` 的回傳 dict，於 `"generated_answer": False,` 下方新增：

```python
        "experience_sparse": False,
```

- [ ] **Step 2: retrieval.py 偵測不足並觸發**

在 `backend/scripts/retriever/agent/nodes/retrieval.py`，於 import 區（現有 `from retriever.sql_search import sql_search` 附近）新增：

```python
from retriever.experience_crawl import SPARSE_THRESHOLD, maybe_enqueue_crawl
```

把 `experience_search_node` 結尾

```python
    print(f"[Experience] 共取得 {len(exp_docs)} 筆申請經驗回報")
    return {"experience_docs": exp_docs}
```

改成

```python
    print(f"[Experience] 共取得 {len(exp_docs)} 筆申請經驗回報")

    # 資料不足且有鎖定學校 → 標記 sparse（答案加註）並在背景補爬該校 GradCafe
    sparse = school_id is not None and len(exp_docs) < SPARSE_THRESHOLD
    if sparse:
        print(f"[Experience] {school_id} 資料不足（{len(exp_docs)}<{SPARSE_THRESHOLD}），排背景補爬")
        maybe_enqueue_crawl(school_id)

    return {"experience_docs": exp_docs, "experience_sparse": sparse}
```

（`school_id` 在該函式開頭已有：`school_id = school_ids[0] if school_ids else None`。）

- [ ] **Step 3: answer.py 答案加註**

在 `backend/scripts/retriever/agent/nodes/answer.py` 的 `finalizer_node`，把串流生成那段

```python
    _check_cancel()
    _emit({"type": "llm_call", "purpose": "finalizer"})

    full_text = ""
    try:
        for chunk in generate_answer_stream(query, all_docs):
            _check_cancel()
            full_text += chunk
            if chunk:
                _emit({"type": "answer_chunk", "text": chunk})
```

改成

```python
    _check_cancel()
    _emit({"type": "llm_call", "purpose": "finalizer"})

    # 經驗資料不足：在答案前加註，並先當作第一個 chunk 送出（前端立即看到）
    sparse_note = ""
    if state.get("experience_sparse"):
        sparse_note = ("（提醒：此校的申請經驗回報目前較少，以下為現有資料；"
                       "系統已在背景補充更多，稍後再問可能更完整。）\n\n")
        _emit({"type": "answer_chunk", "text": sparse_note})

    full_text = sparse_note
    try:
        for chunk in generate_answer_stream(query, all_docs):
            _check_cancel()
            full_text += chunk
            if chunk:
                _emit({"type": "answer_chunk", "text": chunk})
```

- [ ] **Step 4: 驗證 agent 可正常 import**

Run（專案根目錄）:
```bash
python -c "import sys; sys.path.insert(0, 'backend/scripts'); from retriever.agent import run_agent; from retriever.experience_crawl import maybe_enqueue_crawl, SPARSE_THRESHOLD; print('OK', SPARSE_THRESHOLD)"
```
Expected: 印出 `OK 5`（無 ImportError；延遲載入不會在此觸發爬蟲依賴）

- [ ] **Step 5: 跑全部後端測試確認無回歸**

Run: `python -m unittest discover tests -p "test_experience_crawl.py" -v` 然後 `python -m unittest discover tests -p "test_contextualize.py" -v`
Expected: 兩者皆 PASS

- [ ] **Step 6: Commit**

```bash
git add backend/scripts/retriever/agent/state.py backend/scripts/retriever/agent/nodes/retrieval.py backend/scripts/retriever/agent/nodes/answer.py
git commit -m "feat: flag sparse experience data and trigger background crawl in agent"
```

- [ ] **Step 7: 手動端到端驗證（人類，需 DB + OpenAI + 真 SerpAPI 無關）**

啟動 DB + 後端 + 前端，於 `#/chat` 問一個「資料庫收錄少的學校」的錄取經驗問題（例如某冷門校「XXX 錄取的人 GPA 大概多少」）。預期：答案開頭出現「此校的申請經驗回報目前較少…」提示；後端 log 出現 `[ExpCrawl] <school> ... 排背景補爬` 與稍後的 `補爬完成`；再問同校時提示消失、資料變多。此步由人類操作瀏覽器，不在自動化範圍。
```
