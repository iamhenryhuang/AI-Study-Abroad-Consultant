# 上傳成績 → 推薦學校（聊天整合）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 使用者在聊天輸入 GPA/IELTS/TOEFL/GRE，agent 偵測推薦意圖+分數，依各校錄取中位數分成衝刺/適中/保底三檔推薦，並用真實錄取案例佐證，經 generator 專屬格式輸出。

**Architecture:** 比照 `needs_experience` 的意圖旗標模式：Decomposer 偵測 `wants_recommendation` + 提取 `profile` → `route_to_retrieval` 新增分支 → 新節點 `recommend_node`（呼叫新模組 `recommend.py`）→ docs 併入 verified_docs（verifier 短路放行）→ generator 用推薦專屬 prompt 輸出。純後端，走既有 /api/chat。

**Tech Stack:** Python、既有 LangGraph agent、`crawler/data/1point3_distribution.json`、`applicant_reports` 表、OpenAI generator、unittest。

## Global Constraints

- 資料源 `crawler/data/1point3_distribution.json`：`{"university_admissions_data": {"<Name>": {"median_gpa","median_gre","median_toefl","gpa"(直方圖),"school_id"}}}`。只有 GPA/GRE/TOEFL，**無 IELTS**；部分校缺 `median_*`。
- IELTS→TOEFL 換算（ETS concordance 代表值）：9→119, 8.5→116, 8→112, 7.5→105, 7→98, 6.5→86, 6→69；區間外回 None。
- 分級：衝刺/適中/保底，各檔取至多 3 所。
- `recommend.py` 的分級邏輯（`recommend`/`classify_tier`/換算/正規化）不碰 DB，distribution 可注入 → 純函式好測；DB 案例查詢獨立成可 mock 的函式。
- 後端測試：unittest，`python -m unittest discover tests -p "test_x.py" -v`；測試檔開頭 `sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend" / "scripts"))`。
- recommend_node 內任何例外只記 log、回空 recommend_docs，不影響其他檢索支線。
- 免責：generator 推薦格式須含「基於歷史數據、僅供參考、非錄取保證」。
- 環境：Windows。Bash 找不到 git 時用 PowerShell。DB 連線用 `db.connection.get_connection()`（案例查詢設 `read_only=True`）。

## File Structure

| 檔案 | 責任 |
|------|------|
| `backend/scripts/retriever/recommend.py`（新）| 分級邏輯 + 案例查詢 |
| `tests/test_recommend.py`（新）| 換算/分級/推薦 單元測試 |
| `backend/scripts/retriever/agent/state.py`（改）| wants_recommendation/profile/recommend_docs |
| `backend/scripts/retriever/agent/prompts.py`（改）| 意圖偵測+分數提取；推薦答案格式 |
| `backend/scripts/retriever/agent/nodes/decompose.py`（改）| 解析旗標/profile + 路由分支 |
| `backend/scripts/retriever/agent/nodes/retrieval.py`（改）| recommend_node |
| `backend/scripts/retriever/agent/nodes/verification.py`（改）| 併入 recommend_docs + 短路 |
| `backend/scripts/retriever/agent/nodes/answer.py`（改）| finalizer 傳推薦旗標 |
| `backend/scripts/retriever/agent/nodes/__init__.py` + `graph.py`（改）| 註冊 recommend 節點 |
| `backend/scripts/generator/answer.py` + `generator/prompts.py`（改）| 推薦格式參數 |

---

### Task 1: recommend.py 推薦核心模組

**Files:**
- Create: `backend/scripts/retriever/recommend.py`
- Test: `tests/test_recommend.py`

**Interfaces:**
- Consumes: `db.connection.get_connection()`（案例查詢）
- Produces:
  - `_ielts_to_toefl(ielts: float) -> int | None`
  - `_normalize_profile(profile: dict) -> dict`（gpa/toefl/gre；ielts 補算 toefl）
  - `classify_tier(profile: dict, medians: dict) -> str | None`（"衝刺"/"適中"/"保底"/None）
  - `recommend(profile: dict, distribution: dict | None = None, per_tier: int = 3) -> dict`（回 `{"衝刺":[...], "適中":[...], "保底":[...]}`，每項 `{school_id, name, medians, comparison}`；distribution 可注入供測試）
  - `fetch_nearby_cases(school_id: str, gpa: float | None, limit: int = 3) -> list[dict]`（查 applicant_reports 相近 GPA 錄取案例）

- [ ] **Step 1: 寫失敗測試 `tests/test_recommend.py`**

```python
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend" / "scripts"))

from retriever import recommend as rec


class TestIeltsToToefl(unittest.TestCase):
    def test_known_scores(self):
        self.assertEqual(rec._ielts_to_toefl(7.0), 98)
        self.assertEqual(rec._ielts_to_toefl(8.0), 112)

    def test_out_of_range(self):
        self.assertIsNone(rec._ielts_to_toefl(3.0))
        self.assertIsNone(rec._ielts_to_toefl(None))


class TestNormalizeProfile(unittest.TestCase):
    def test_ielts_fills_toefl_when_missing(self):
        out = rec._normalize_profile({"gpa": 3.5, "ielts": 7.0})
        self.assertEqual(out["toefl"], 98)
        self.assertEqual(out["gpa"], 3.5)

    def test_explicit_toefl_wins_over_ielts(self):
        out = rec._normalize_profile({"toefl": 105, "ielts": 7.0})
        self.assertEqual(out["toefl"], 105)


class TestClassifyTier(unittest.TestCase):
    _MED = {"median_gpa": 3.8, "median_toefl": 105, "median_gre": 328}

    def test_all_above_is_safety(self):
        p = {"gpa": 3.9, "toefl": 110, "gre": 330}
        self.assertEqual(rec.classify_tier(p, self._MED), "保底")

    def test_all_below_is_reach(self):
        p = {"gpa": 3.0, "toefl": 90, "gre": 300}
        self.assertEqual(rec.classify_tier(p, self._MED), "衝刺")

    def test_mixed_is_moderate(self):
        p = {"gpa": 3.9, "toefl": 110, "gre": 300}  # 2/3 達標 → 0.67 落在適中
        self.assertEqual(rec.classify_tier(p, self._MED), "適中")

    def test_no_comparable_dim_returns_none(self):
        self.assertIsNone(rec.classify_tier({"gpa": None}, {"median_toefl": 105}))


class TestRecommend(unittest.TestCase):
    _DIST = {
        "cmu": {"school_id": "cmu", "name": "CMU", "median_gpa": 3.85, "median_toefl": 105, "median_gre": 328},
        "nyu": {"school_id": "nyu", "name": "NYU", "median_gpa": 3.75, "median_toefl": 105, "median_gre": 325},
        "ucsd": {"school_id": "ucsd", "name": "UCSD", "median_gpa": 3.93, "median_toefl": 106, "median_gre": 327},
    }

    def test_buckets_by_tier(self):
        # 中等偏低 profile：對高標校是衝刺
        result = rec.recommend({"gpa": 3.5, "toefl": 100, "gre": 315}, distribution=self._DIST)
        self.assertIn("衝刺", result)
        self.assertIn("適中", result)
        self.assertIn("保底", result)
        all_ids = [s["school_id"] for tier in result.values() for s in tier]
        self.assertTrue(set(all_ids) <= {"cmu", "nyu", "ucsd"})

    def test_each_tier_capped(self):
        result = rec.recommend({"gpa": 4.0, "toefl": 120, "gre": 340},
                               distribution=self._DIST, per_tier=2)
        for tier in result.values():
            self.assertLessEqual(len(tier), 2)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 確認測試失敗**

Run: `python -m unittest discover tests -p "test_recommend.py" -v`
Expected: FAIL/ERROR `ModuleNotFoundError: No module named 'retriever.recommend'`

- [ ] **Step 3: 寫實作 `backend/scripts/retriever/recommend.py`**

```python
"""上傳成績 → 推薦學校：依 1point3 錄取中位數把各校分成衝刺/適中/保底，
並從 applicant_reports 撈相近分數的真實錄取案例佐證。

分級邏輯為純函式（distribution 可注入）；DB 案例查詢獨立成 fetch_nearby_cases。
參考 backup/AI-Study-Abroad-Consultant-1.0.0 的舊推薦邏輯。
"""
from __future__ import annotations

import json
from pathlib import Path

from db.connection import get_connection

_DIST_PATH = Path(__file__).resolve().parents[3] / "crawler" / "data" / "1point3_distribution.json"

# ETS IELTS→TOEFL concordance 代表值
_IELTS_TOEFL = {9.0: 119, 8.5: 116, 8.0: 112, 7.5: 105, 7.0: 98, 6.5: 86, 6.0: 69}


def _ielts_to_toefl(ielts) -> int | None:
    if ielts is None:
        return None
    try:
        key = round(float(ielts) * 2) / 2      # 對齊到 0.5 級距
    except (TypeError, ValueError):
        return None
    return _IELTS_TOEFL.get(key)


def _normalize_profile(profile: dict) -> dict:
    """回傳 {gpa, toefl, gre}；無 toefl 但有 ielts 時換算補上。"""
    gpa   = profile.get("gpa")
    toefl = profile.get("toefl")
    gre   = profile.get("gre")
    if toefl is None and profile.get("ielts") is not None:
        toefl = _ielts_to_toefl(profile.get("ielts"))
    return {"gpa": gpa, "toefl": toefl, "gre": gre}


def classify_tier(profile: dict, medians: dict) -> str | None:
    """依達標比例分級：普遍達標→保底、約半→適中、普遍未達→衝刺；無可比維度→None。"""
    dims = []
    for key in ("gpa", "toefl", "gre"):
        u, m = profile.get(key), medians.get(f"median_{key}")
        if u is not None and m is not None:
            dims.append((u, m))
    if not dims:
        return None
    meets = sum(1 for u, m in dims if u >= m)
    ratio = meets / len(dims)
    if ratio >= 0.7:
        return "保底"
    if ratio >= 0.35:
        return "適中"
    return "衝刺"


def _load_distribution() -> dict:
    """讀 distribution json → {school_id: {school_id, name, median_gpa, median_toefl, median_gre}}。"""
    try:
        raw = json.loads(_DIST_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[Recommend] 讀取 distribution 失敗：{e}")
        return {}
    out = {}
    for name, info in raw.get("university_admissions_data", {}).items():
        sid = info.get("school_id")
        if not sid:
            continue
        out[sid] = {
            "school_id":     sid,
            "name":          name,
            "median_gpa":    info.get("median_gpa"),
            "median_toefl":  info.get("median_toefl"),
            "median_gre":    info.get("median_gre"),
        }
    return out


def _comparison(profile: dict, med: dict) -> list[str]:
    """產生「你的分數 vs 中位數」對比字串列表。"""
    parts = []
    for key, label in (("gpa", "GPA"), ("toefl", "TOEFL"), ("gre", "GRE")):
        u, m = profile.get(key), med.get(f"median_{key}")
        if u is not None and m is not None:
            parts.append(f"{label} 你 {u} / 中位 {m}")
    return parts


def recommend(profile: dict, distribution: dict | None = None, per_tier: int = 3) -> dict:
    """把各校分級，每檔取至多 per_tier 所。distribution 可注入供測試。"""
    norm = _normalize_profile(profile)
    dist = distribution if distribution is not None else _load_distribution()
    tiers: dict[str, list] = {"衝刺": [], "適中": [], "保底": []}
    for sid, med in dist.items():
        tier = classify_tier(norm, med)
        if tier is None:
            continue
        tiers[tier].append({
            "school_id":  sid,
            "name":       med.get("name", sid.upper()),
            "medians":    med,
            "comparison": _comparison(norm, med),
        })
    for tier in tiers:
        tiers[tier] = tiers[tier][:per_tier]
    return tiers


def fetch_nearby_cases(school_id: str, gpa, limit: int = 3) -> list[dict]:
    """查 applicant_reports 中該校、GPA 相近的錄取案例（±0.2）。失敗回 []。"""
    conn = get_connection()
    if not conn:
        return []
    try:
        conn.read_only = True
        with conn.cursor() as cur:
            if gpa is not None:
                cur.execute(
                    "SELECT gpa, decision, notes FROM applicant_reports "
                    "WHERE school_id=%s AND decision IN ('accepted','offer','ad_no_fund','ad_small_fund') "
                    "AND gpa IS NOT NULL AND ABS(gpa - %s) <= 0.2 "
                    "ORDER BY ABS(gpa - %s) LIMIT %s",
                    (school_id, gpa, gpa, limit),
                )
            else:
                cur.execute(
                    "SELECT gpa, decision, notes FROM applicant_reports "
                    "WHERE school_id=%s AND decision IN ('accepted','offer','ad_no_fund','ad_small_fund') "
                    "ORDER BY id DESC LIMIT %s",
                    (school_id, limit),
                )
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    except Exception as e:
        print(f"[Recommend] 案例查詢失敗（{school_id}）：{e}")
        return []
    finally:
        conn.close()
    return rows
```

- [ ] **Step 4: 確認測試通過**

Run: `python -m unittest discover tests -p "test_recommend.py" -v`
Expected: PASS（10 tests OK）

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/retriever/recommend.py tests/test_recommend.py
git commit -m "feat: add school recommendation tiering module"
```

---

### Task 2: agent 接線（意圖偵測 + 節點 + 路由 + verifier）

**Files:**
- Modify: `backend/scripts/retriever/agent/state.py`
- Modify: `backend/scripts/retriever/agent/prompts.py`（`_build_intent_prompt`）
- Modify: `backend/scripts/retriever/agent/nodes/decompose.py`
- Modify: `backend/scripts/retriever/agent/nodes/retrieval.py`
- Modify: `backend/scripts/retriever/agent/nodes/verification.py`
- Modify: `backend/scripts/retriever/agent/nodes/__init__.py`
- Modify: `backend/scripts/retriever/agent/graph.py`

**Interfaces:**
- Consumes: `retriever.recommend.recommend`、`fetch_nearby_cases`（Task 1）
- Produces: state 有 `wants_recommendation: bool` / `profile: dict` / `recommend_docs: list[dict]`；`recommend_node` 產出 `type='recommendation'` docs 併入 verified_docs

- [ ] **Step 1: state.py 加欄位**

`AgentState` 於 `generated_answer: bool` 下方新增：
```python
    wants_recommendation: bool       # 使用者提供分數並要求推薦學校
    profile:           dict          # 提取的成績 {gpa, ielts, toefl, gre}
    recommend_docs:    list[dict]    # recommend_node 產出的推薦資料
```
`create_initial_state` 於 `"generated_answer": False,` 下方新增：
```python
        "wants_recommendation": False,
        "profile": {},
        "recommend_docs": [],
```

- [ ] **Step 2: prompts.py 意圖偵測加任務**

在 `_build_intent_prompt` 的「任務四」區塊後、`【使用者問題】` 前插入：
```python
====================
【任務五：是否為「上傳成績求推薦學校」（wants_recommendation）】
====================
若使用者提供了自己的成績（GPA / IELTS / TOEFL / GRE 任一）並希望「推薦學校 / 我適合哪些學校 / 幫我選校」，
wants_recommendation 設為 true，並把分數提取到 profile（數字，沒有的填 null）。否則 wants_recommendation 為 false、profile 全 null。
```
並把輸出格式 JSON 改為（加兩欄）：
```python
{{
  "school_ids": ["school_id_1", ...],
  "mentioned_school_names": ["School Name 1", ...],
  "professor_query": {{"name": "...", "school": "...", "school_id": "..."}} or null,
  "needs_sql_search": true or false,
  "needs_experience": true or false,
  "wants_recommendation": true or false,
  "profile": {{"gpa": number or null, "ielts": number or null, "toefl": number or null, "gre": number or null}}
}}
```

- [ ] **Step 3: decompose.py 解析 + 路由**

在 `decomposer_node` 的 try 區塊（`needs_experience = ...` 後）新增：
```python
        wants_recommendation = bool(parsed.get("wants_recommendation", False))
        profile = parsed.get("profile") or {}
```
except fallback 區塊新增：
```python
        wants_recommendation = False
        profile = {}
```
return dict 新增兩欄：
```python
        "wants_recommendation": wants_recommendation,
        "profile":              profile,
        "recommend_docs":       [],
```
`route_to_retrieval` 在 needs_experience 分支後新增：
```python
    if state.get("wants_recommendation", False):
        targets.append(Send("recommend", state))
```

- [ ] **Step 4: retrieval.py 加 recommend_node**

import 區新增：
```python
from retriever.recommend import recommend, fetch_nearby_cases
```
檔尾新增節點：
```python
def recommend_node(state: AgentState) -> dict:
    """依 profile 分級推薦學校（衝刺/適中/保底）+ 真實案例佐證，寫入 recommend_docs。"""
    _emit({"type": "thinking", "step": "recommend"})
    profile = state.get("profile") or {}
    try:
        tiers = recommend(profile)
    except Exception as e:
        print(f"[Recommend] 推薦失敗：{e}")
        return {"recommend_docs": []}

    docs: list[dict] = []
    for tier, schools in tiers.items():
        for s in schools:
            cases = fetch_nearby_cases(s["school_id"], profile.get("gpa"))
            case_txt = "；".join(
                f"GPA {c.get('gpa')} {c.get('decision')}" for c in cases
            ) or "（無相近案例）"
            docs.append({
                "type":       "recommendation",
                "school_id":  s["school_id"],
                "chunk_text": (f"[{tier}] {s['name']}\n"
                               f"分數對比：{'；'.join(s['comparison'])}\n"
                               f"相近錄取案例：{case_txt}"),
                "source_url": "",
            })
    _emit({"type": "tool_result", "tool": "recommend",
           "preview": f"分級推薦 {len(docs)} 所學校"})
    print(f"[Recommend] 產出 {len(docs)} 筆推薦")
    return {"recommend_docs": docs}
```

- [ ] **Step 5: verification.py 併入 + 短路**

`verifier_node` 內把
```python
    experience_docs = state.get("experience_docs", [])

    # 順序：教授 → 官方 SQL → 經驗回報 → 全文檢索補充。經驗資料排官方之後，避免喧賓奪主。
    all_docs = _deduplicate_docs(
        extension_docs + search_docs + experience_docs + fulltext_docs
    )
```
改成
```python
    experience_docs = state.get("experience_docs", [])
    recommend_docs  = state.get("recommend_docs", [])

    all_docs = _deduplicate_docs(
        extension_docs + search_docs + experience_docs + recommend_docs + fulltext_docs
    )
```
並在經驗短路那段後新增推薦短路：
```python
    if state.get("wants_recommendation", False) and recommend_docs:
        print(f"[Verifier] wants_recommendation 且有 {len(recommend_docs)} 筆推薦，直接放行")
        return {"verified_docs": all_docs, "is_sufficient": True, "insufficiency_reason": ""}
```

- [ ] **Step 6: 註冊節點（`nodes/__init__.py` + `graph.py`）**

`nodes/__init__.py`：從 `.retrieval` 的 import 清單加入 `recommend_node`，並加進 `__all__`。

`graph.py`：`from .nodes import (...)` 清單加 `recommend_node`；`_build_graph` 內
```python
    builder.add_node("recommend", recommend_node)
```
（放在 `add_node("experience_search", ...)` 後），並加邊：
```python
    builder.add_edge("recommend", "verify")
```
（放在 `add_edge("experience_search", "verify")` 後）。

- [ ] **Step 7: 驗證 import + agent 建圖**

Run（專案根）:
```bash
python -c "import sys; sys.path.insert(0,'backend/scripts'); from retriever.agent import run_agent; from retriever.recommend import recommend; print('OK', list(recommend({'gpa':3.5,'toefl':100,'gre':315}).keys()))"
```
Expected: 印出 `OK ['衝刺', '適中', '保底']`（agent 建圖成功、recommend 可用）

- [ ] **Step 8: 回歸測試**

Run: `python -m unittest discover tests -p "test_recommend.py" -v`
Expected: PASS（Task 1 的 10 tests 仍 OK）

- [ ] **Step 9: Commit**

```bash
git add backend/scripts/retriever/agent/state.py backend/scripts/retriever/agent/prompts.py backend/scripts/retriever/agent/nodes/decompose.py backend/scripts/retriever/agent/nodes/retrieval.py backend/scripts/retriever/agent/nodes/verification.py backend/scripts/retriever/agent/nodes/__init__.py backend/scripts/retriever/agent/graph.py
git commit -m "feat: wire school recommendation node into agent graph"
```

---

### Task 3: generator 推薦專屬格式

**Files:**
- Modify: `backend/scripts/generator/prompts.py`
- Modify: `backend/scripts/generator/answer.py`
- Modify: `backend/scripts/retriever/agent/nodes/answer.py`（finalizer 傳旗標）

**Interfaces:**
- Consumes: state `wants_recommendation`；`recommend` 類型 docs
- Produces: `generate_answer_stream(query, docs, recommendation=bool)` / `generate_answer(..., recommendation=bool)` 依旗標套用推薦格式

- [ ] **Step 1: prompts.py 加推薦格式指示**

在 `generator/prompts.py` 新增常數（`_SYSTEM_PROMPT` 之後）：
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
把 `_build_prompt` 改為接受旗標並附加指示：
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

- [ ] **Step 2: answer.py 傳旗標**

`generator/answer.py` 兩個函式加 `recommendation` 參數並傳給 `_build_prompt`：
```python
def generate_answer_stream(query, context_docs, model_name: str = ANSWER_MODEL, recommendation: bool = False):
    ...
    prompt = _build_prompt(query, context_docs, recommendation=recommendation)
    ...

def generate_answer(query, context_docs, model_name: str = ANSWER_MODEL, recommendation: bool = False):
    ...
    prompt = _build_prompt(query, context_docs, recommendation=recommendation)
    ...
```

- [ ] **Step 3: finalizer 傳入旗標（`agent/nodes/answer.py`）**

`finalizer_node` 內串流生成處：
```python
    recommendation = state.get("wants_recommendation", False)
    full_text = ""
    try:
        for chunk in generate_answer_stream(query, all_docs, recommendation=recommendation):
            ...
    except Exception as e:
        print(f"[Finalizer] 串流失敗，回退到非串流: {e}")
        full_text = generate_answer(query, all_docs, recommendation=recommendation) or ""
```

- [ ] **Step 4: 驗證 import**

Run: `python -c "import sys; sys.path.insert(0,'backend/scripts'); from retriever.agent import run_agent; from generator.answer import generate_answer_stream; print('OK')"`
Expected: 印出 `OK`

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/generator/prompts.py backend/scripts/generator/answer.py backend/scripts/retriever/agent/nodes/answer.py
git commit -m "feat: add recommendation answer format to generator"
```

- [ ] **Step 6: 手動端到端驗證（人類，需 DB + OpenAI）**

啟動 DB + 後端 + 前端，於 `#/chat` 輸入「我 GPA 3.5 IELTS 7 GRE 320，推薦適合的學校」。預期：log 出現 `[Recommend] 產出 N 筆推薦`；答案分「衝刺/適中/保底」三段、每校有分數對比 + 案例 + 免責。此步由人類操作，不在自動化範圍。
