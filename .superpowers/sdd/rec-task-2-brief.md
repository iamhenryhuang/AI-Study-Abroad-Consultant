# Task 2 Brief — agent 接線（推薦意圖 + 節點 + 路由 + verifier + graph）

把 Task 1 的 `recommend.py` 接進 agent：Decomposer 偵測推薦意圖+分數 → 路由到新節點 `recommend_node` → docs 併入 verified_docs（verifier 短路放行）。比照現有 `needs_experience` 模式。

## Global Constraints
- Task 1 已提供 `retriever.recommend`：`recommend(profile, distribution=None, per_tier=3) -> dict`、`fetch_nearby_cases(school_id, gpa, limit=3) -> list[dict]`。
- 只改下列 7 個檔案，不新增檔案、不動別的。
- 環境：Windows。Bash git 不可用時用 PowerShell。
- 精確 find/replace：替換前先確認原文完全吻合。

## Files（皆為 Modify）
- `backend/scripts/retriever/agent/state.py`
- `backend/scripts/retriever/agent/prompts.py`
- `backend/scripts/retriever/agent/nodes/decompose.py`
- `backend/scripts/retriever/agent/nodes/retrieval.py`
- `backend/scripts/retriever/agent/nodes/verification.py`
- `backend/scripts/retriever/agent/nodes/__init__.py`
- `backend/scripts/retriever/agent/graph.py`

## Step 1: state.py 加欄位
`AgentState` 於 `generated_answer: bool` 那行下方新增三行：
```python
    wants_recommendation: bool       # 使用者提供分數並要求推薦學校
    profile:           dict          # 提取的成績 {gpa, ielts, toefl, gre}
    recommend_docs:    list[dict]    # recommend_node 產出的推薦資料
```
`create_initial_state` 回傳 dict 於 `"generated_answer": False,` 下方新增：
```python
        "wants_recommendation": False,
        "profile": {},
        "recommend_docs": [],
```

## Step 2: prompts.py 意圖偵測加任務
在 `_build_intent_prompt` 的「任務四」區塊之後、`【使用者問題】` 之前，插入：
```
====================
【任務五：是否為「上傳成績求推薦學校」（wants_recommendation）】
====================
若使用者提供了自己的成績（GPA / IELTS / TOEFL / GRE 任一）並希望「推薦學校 / 我適合哪些學校 / 幫我選校」，
wants_recommendation 設為 true，並把分數提取到 profile（數字，沒有的填 null）。否則 wants_recommendation 為 false、profile 全 null。
```
並把輸出格式 JSON（原本結尾是 needs_experience 那個物件）改成：
```
{{
  "school_ids": ["school_id_1", ...],
  "mentioned_school_names": ["School Name 1", ...],
  "professor_query": {{"name": "教授全名（英文）", "school": "學校名稱（英文）", "school_id": "學校ID"}} or null,
  "needs_sql_search": true or false,
  "needs_experience": true or false,
  "wants_recommendation": true or false,
  "profile": {{"gpa": number or null, "ielts": number or null, "toefl": number or null, "gre": number or null}}
}}
```

## Step 3: decompose.py 解析 + 路由
`decomposer_node` 的 try 區塊，`needs_experience = bool(parsed.get("needs_experience", False))` 那行下方新增：
```python
        wants_recommendation = bool(parsed.get("wants_recommendation", False))
        profile = parsed.get("profile") or {}
```
except fallback 區塊，`needs_experience = False` 那行下方新增：
```python
        wants_recommendation = False
        profile = {}
```
return dict，於 `"needs_experience": needs_experience,` 下方新增：
```python
        "wants_recommendation": wants_recommendation,
        "profile":              profile,
        "recommend_docs":       [],
```
`route_to_retrieval`，在 needs_experience 分支之後新增：
```python
    if state.get("wants_recommendation", False):
        targets.append(Send("recommend", state))
```

## Step 4: retrieval.py 加 recommend_node
import 區（現有 `from retriever.sql_search import sql_search` 附近）新增：
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
（注意：`_emit` 與 `AgentState` 在此檔已有 import；若無，從 `..state` 補 import。）

## Step 5: verification.py 併入 + 短路
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
並在經驗短路那段（`if state.get("needs_experience", False) and experience_docs:` ... return 那段）之後新增：
```python
    if state.get("wants_recommendation", False) and recommend_docs:
        print(f"[Verifier] wants_recommendation 且有 {len(recommend_docs)} 筆推薦，直接放行")
        return {"verified_docs": all_docs, "is_sufficient": True, "insufficiency_reason": ""}
```

## Step 6: 註冊節點
`nodes/__init__.py`：`from .retrieval import (...)` 清單加入 `recommend_node`，並加進 `__all__`。

`graph.py`：`from .nodes import (...)` 清單加 `recommend_node`；`_build_graph` 內 `builder.add_node("experience_search", experience_search_node)` 下方新增：
```python
    builder.add_node("recommend", recommend_node)
```
`builder.add_edge("experience_search", "verify")` 下方新增：
```python
    builder.add_edge("recommend", "verify")
```

## Step 7: 驗證 import + 建圖
Run（專案根）:
```bash
python -c "import sys; sys.path.insert(0,'backend/scripts'); from retriever.agent import run_agent; from retriever.recommend import recommend; print('OK', list(recommend({'gpa':3.5,'toefl':100,'gre':315}).keys()))"
```
Expected: 印出 `OK ['衝刺', '適中', '保底']`

## Step 8: 回歸測試
Run: `python -m unittest discover tests -p "test_recommend.py" -v`
Expected: PASS（13 tests OK）

## Step 9: Commit
```bash
git add backend/scripts/retriever/agent/state.py backend/scripts/retriever/agent/prompts.py backend/scripts/retriever/agent/nodes/decompose.py backend/scripts/retriever/agent/nodes/retrieval.py backend/scripts/retriever/agent/nodes/verification.py backend/scripts/retriever/agent/nodes/__init__.py backend/scripts/retriever/agent/graph.py
git commit -m "feat: wire school recommendation node into agent graph"
```
（Bash git 不可用時用 PowerShell。）
