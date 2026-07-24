"""
流程：
  1. Decomposer       — LLM 拆解子問題 + 偵測學校
  1.5 ProfessorFetcher — 若 query 含教授意圖，呼叫 SerpAPI 即時抓取教授資料
  2. Searcher         — 每個子問題依自己的 school_id 向資料庫檢索
  3. Planner          — 判斷目前收集的資料是否夠（最多 2 輪）
                        若不夠，產生英文補充子問題繼續搜尋
  4. Finalizer        — 彙整所有文件，呼叫 Gemini 生成最終答案
"""

from __future__ import annotations

import json
import re
import sys
import threading
from pathlib import Path
from typing import Annotated, Callable, Optional, TypedDict
import operator
import time


class AgentCancelledError(Exception):
    """由取消事件觸發，用來快速中止 agent 執行。"""

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from dotenv import load_dotenv
load_dotenv()

from langgraph.graph import StateGraph, END
from langgraph.types import Send

from retriever.search import search_core
from generator.gemini import get_gemini_client, generate_answer, generate_answer_stream, chunk_compress
from professor_fetcher.fetch_for_agent import run_professor_fetch
from retriever.analyzer import analyze_and_evaluate

# ─── 學校別名對照表 ───────────────────────────────────────────────────────────

_SCHOOL_ALIASES: dict[str, list[str]] = {
    # ── 資料庫現有學校（school_data/ 來源） ──
    "cmu":      ["cmu", "carnegie mellon", "卡內基梅隆"],
    "mit":      ["mit", "massachusetts institute", "麻省理工"],
    "stanford": ["stanford", "史丹佛", "斯坦福"],
    "caltech":  ["caltech", "california institute", "加州理工"],
    "gatech":   ["georgia tech", "gatech", "喬治亞理工"],
    "ucla":     ["ucla", "uc los angeles", "加州洛杉磯"],
    "ucsd":     ["ucsd", "uc san diego", "加州聖地牙哥"],
    "uci":      ["uci", "uc irvine", "加州爾灣"],
    "umass":    ["umass", "amherst", "麻州大學"],
    "purdue":   ["purdue", "purdure", "普渡"],
    "washu":    ["washu", "wustl", "washington university", "聖路易斯華盛頓"],
    "utoronto": ["utoronto", "toronto", "多倫多"],
}

MAX_ROUNDS      = 2   # Planner 最多重試幾輪
TOP_K_PER_QUERY = 10   # 每個子問題檢索幾筆

# Thread-local execution context prevents concurrent API requests from sharing callbacks.
_agent_context = threading.local()


def _check_cancel() -> None:
    """若取消事件已設定，立即拋出 AgentCancelledError。"""
    cancel_event = getattr(_agent_context, "cancel_event", None)
    if cancel_event is not None and cancel_event.is_set():
        raise AgentCancelledError("Agent 已被取消")


# ─── State ───────────────────────────────────────────────────────────────────

class AgentState(TypedDict):
    original_query:    str
    sub_queries:       list[str]
    extra_queries:     Annotated[list[str], operator.add]
    pending_queries:   list[str]
    collected_docs:    Annotated[list[dict], operator.add]   # search 結果
    extension_docs:    Annotated[list[dict], operator.add]   # extension 結果（教授 / 選校推薦）
    searched_queries:  Annotated[list[str], operator.add]
    is_sufficient:     bool
    round_count:       int
    final_answer:      str
    professor_query:   dict | None   # Decomposer 偵測到的教授查詢 {name, school, school_id}
    professor_fetched: bool
    school_tend:       bool


# ─── 工具函式 ─────────────────────────────────────────────────────────────────

def _emit(event: dict) -> None:
    """安全地呼叫目前的 on_event callback（若有設定）。"""
    on_event = getattr(_agent_context, "on_event", None)
    if on_event is not None:
        try:
            on_event(event)
        except Exception:
            pass


def _detect_school_ids(text: str) -> list[str]:
    """從文字中偵測提到的學校 ID 列表。"""
    text_lower = text.lower()
    return [
        school_id
        for school_id, aliases in _SCHOOL_ALIASES.items()
        if any(alias in text_lower for alias in aliases)
    ]


def _deduplicate_docs(docs: list[dict]) -> list[dict]:
    """以 chunk_text 前 200 字去重複文件。"""
    seen: set[str] = set()
    result: list[dict] = []
    for doc in docs:
        key = doc.get("chunk_text", "")[:200]
        if key not in seen:
            seen.add(key)
            result.append(doc)
    return result


def _call_llm(prompt: str, model_name: str = "gemini-2.5-flash") -> str:
    """呼叫 Gemini，回傳純文字。每次呼叫前先檢查是否已取消。"""
    _check_cancel()
    client = get_gemini_client()
    response = client.models.generate_content(model=model_name, contents=prompt)
    return (response.text or "").strip()


# ─── Node 1：Decomposer ───────────────────────────────────────────────────────

# 學校 ID → 問題中常見的顯示名稱（取第一個 alias）
_SCHOOL_DISPLAY_NAME: dict[str, str] = {
    k: aliases[0] for k, aliases in _SCHOOL_ALIASES.items()
}


def _make_school_query(original_query: str, target_school_id: str, all_school_ids: list[str]) -> str:
    """
    從原始問題製作「只提到 target_school」的子問題。

    1. 把其他學校的 alias 從問題中移除（含連接詞清理）
    2. 若目標學校名稱完全不在結果中，在開頭補上
    """
    query = original_query
    for sid in all_school_ids:
        if sid == target_school_id:
            continue
        for alias in _SCHOOL_ALIASES[sid]:
            query = re.compile(re.escape(alias), re.IGNORECASE).sub("", query)

    # \b 確保 and/or 只匹配完整單詞，不會誤切 "georgia" 裡的 "or"
    query = re.sub(r"\b(and|or)\b", " ", query, flags=re.IGNORECASE)
    query = re.sub(r"[,，、]|(跟|和|與|&|以及)", " ", query, flags=re.IGNORECASE)
    query = re.sub(r"\s{2,}", " ", query).strip().strip(",，、 ")

    target_aliases = _SCHOOL_ALIASES[target_school_id]
    if not any(a in query.lower() for a in target_aliases):
        display = _SCHOOL_DISPLAY_NAME[target_school_id].title()
        query = f"{display} {query}"

    return query.strip()

# def test__():
#     query = "Compare the CS Master's admission requirements for UCLA, umass."
#     q2 = "Compare the CS Master's admission requirements for UCSD, Stanford. my gpa is 3.2/4 give me some suggestion to choose school."
#     q3 = "What are the differences between UCSD and Stanford CS MS requirements?"
#     q4 = "What GRE/TOEFL requirements do Stanford and UMass CS MS have?"
#
#     q5 = "Compare UCLA and UMass CS MS requirements base on my GPA is 3.2/4."
#     q6 = "Compare these programs considering my background: GPA 3.2/4, TOEFL 100."
#
#     q7 = "give me some suggestion about cs master base on my GRE is 350."
#
#     initial_state: AgentState = {
#         "original_query":  q7,
#         "sub_queries":     [],
#         "extra_queries":   [],
#         "pending_queries": [],
#         "collected_docs":  [],
#         "searched_queries": [],
#         "is_sufficient":   False,
#         "round_count":     0,
#         "final_answer":    "",
#     }
#     decomposer_node_1(initial_state)
    
def decomposer_node(state: AgentState) -> dict:
    """
    規則式拆解（無 LLM）：
    - 偵測到 2+ 所學校 → 拆成 N 個子問題
    - 否則 → 使用原始問題
    """
    query = state["original_query"]
    print(f"\n[Decomposer] 原始問題：{query}")

    school_ids = _detect_school_ids(query)

    if len(school_ids) >= 2:
        sub_queries = [_make_school_query(query, sid, school_ids) for sid in school_ids]
        print(f"[Decomposer] 偵測到 {len(school_ids)} 所學校，拆解為 {len(sub_queries)} 個子問題：")
    else:
        sub_queries = [query]
        label = f"（學校：{school_ids[0]}）" if school_ids else "（無特定學校）"
        print(f"[Decomposer] 單一問題 {label}：")

    for i, q in enumerate(sub_queries, 1):
        print(f"  Q{i}: {q}")

    return {
        "sub_queries":      sub_queries,
        "pending_queries":  sub_queries,
        "extra_queries":    [],
        "collected_docs":   [],
        "searched_queries": [],
        "is_sufficient":    False,
        "round_count":      0,
        "final_answer":     "",
    }

def decomposer_node_1(state: AgentState) -> dict:
    query = state["original_query"]
    print(f"\n{'='*60}")
    print(f"[Decomposer] 原始問題：{query}")
    print(f"{'='*60}")
    prompt = f"""
你是一個 Query Decomposer，負責將使用者問題轉換為結構化資訊。

你需要完成三個任務，並嚴格遵守規則輸出 JSON。
[嚴格遵守] decompose的問題最多9個，只有一間學校的話，一間最多5個，兩間學校的話，一間最多四個，若有三間以上，單一學校最多三個問題

====================
【任務一：學校辨識 + 子問題拆解】
====================
1. 從使用者問題中辨識出所有「明確提及」的學校（必須出現在已知學校清單中）
2. 若偵測到多所學校：
   - 為每一所學校產生一個對應的子問題
3. 若只偵測到一所學校：
   - 產生 1 個子問題
4. 若沒有偵測到任何學校：
   - school_ids = []
   - sub_queries = [將原問題改寫為較清楚的單一問題]
5.若問題太過於廣泛，如只詢問了application requirement、申請資格等等這種廣泛問題，請在子問題種搜尋細項，
    請將廣泛問題的細項分開詢問，細項詢問甚麼由你判斷，參考範例如下。
    問題:application requirement 子問題拆成:min gpa/min english score/min gre/等等，類似這樣的子問題找答案

【子問題格式要求】
- 所有子問題必須用英文撰寫，無論使用者原始問題是何種語言
- 優先使用：「School + requirement content?」形式
- 保持語意完整，不要遺漏條件
- 不要自行新增未提及的資訊
- 若有分數相關問題如GRE/GPA/English test，請都要詢問 是否需要、最低分多少
- 推薦信問題則詢問最少封數、推薦信方向
- 申請資格問題，則詢問背景需求、特殊資格規定等等
====================
【任務二：是否有推薦學校意圖（school_tend）】
====================
請判斷使用者是否「希望你推薦學校」

設為 true 的條件（需同時滿足）：
1. 問題包含「選校 / 推薦 / 哪些學校適合 / 落點分析」等意圖
2. 且提供至少一項背景資訊：
   - GPA
   - GRE / GMAT
   - 英文成績（TOEFL / IELTS / DET）

否則一律為 false

====================
【任務三：教授查詢偵測（professor_query）】
====================
判斷問題是否在詢問「某位具體教授」的相關資訊。

- 若問題中有「明確的教授姓名」，提取其姓名與學校，professor_query 為物件
- 姓名必須完整保留原始寫法，包括連字號（-）與大小寫，例如 "Fei-Fei Li" 不得改寫為 "Feifei Li"
- 否則 professor_query 為 null

====================
【使用者問題】
{query}

【已知學校清單】（school_ids 只能從此清單選取；professor_query.school_id 若學校不在清單內，填寫學校英文全名即可）
{list(_SCHOOL_ALIASES.keys())}

====================
【輸出格式（嚴格遵守）】
====================
只輸出 JSON，禁止輸出任何說明文字

{{
  "school_ids": ["school_id_1", ...],
  "sub_queries": ["sub-query in English 1", "sub-query in English 2", ...],
  "school_tend": true or false,
  "professor_query": {{"name": "教授全名（英文）", "school": "學校名稱（英文）", "school_id": "學校ID"}} or null
}}
"""

    # ── LLM 呼叫 ──────────────────────────────────────────────────
    raw = _call_llm(prompt)

    # ── 解析結果 ──────────────────────────────────────────────────
    try:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            raise ValueError("找不到 JSON")
        parsed      = json.loads(match.group())
        school_ids  = [
            str(s).strip()
            for s in parsed.get("school_ids", [])
            if str(s).strip() in _SCHOOL_ALIASES
        ]
        sub_queries = [str(q).strip() for q in parsed.get("sub_queries", []) if str(q).strip()]

        if not sub_queries:
            raise ValueError("sub_queries 為空")

        # ── 教授查詢解析 ──────────────────────────────────────────
        # 可進行解偶
        pq_raw    = parsed.get("professor_query")
        professor_query = None
        if isinstance(pq_raw, dict):
            pq_name = pq_raw.get("name", "").strip()
            pq_school = pq_raw.get("school", "").strip()
            pq_sid  = pq_raw.get("school_id", "").strip()
            if pq_name:
                if pq_sid not in _SCHOOL_ALIASES:
                    detected = _detect_school_ids(pq_school)
                    pq_sid = detected[0] if detected else (pq_school or pq_sid)
                professor_query = {"name": pq_name, "school": pq_school, "school_id": pq_sid}
                
        # 意圖解析
        intent = parsed.get("school_tend") 
            

    except Exception as e:
        print(f"[Decomposer] LLM 解析失敗（{e}），使用原始問題作為 fallback")
        school_ids      = _detect_school_ids(query)
        sub_queries     = [query]
        professor_query = None

    # ── 日誌 ──────────────────────────────────────────────────────
    if len(school_ids) >= 2:
        print(f"[Decomposer] 偵測到 {len(school_ids)} 所學校，拆解為 {len(sub_queries)} 個子問題：")
    else:
        label = f"（學校：{school_ids[0]}）" if school_ids else "（無特定學校）"
        print(f"[Decomposer] 單一問題 {label}：")
    for i, q in enumerate(sub_queries, 1):
        print(f"  Q{i}: {q}")

    # ── Intent 摘要 ───────────────────────────────────────────────
    print(f"[Decomposer] ── Intent 摘要 ──────────────────────────────")
    print(f"  school_tend     = {intent}   "
          f"（{'需要選校推薦' if intent else '不需要選校推薦'}）")
    if professor_query:
        print(f"  professor_query = {professor_query['name']} @ {professor_query['school']} "
              f"[{professor_query.get('school_id', '?')}]")
    else:
        print(f"  professor_query = None   （無教授查詢意圖）")
    print(f"[Decomposer] ───────────────────────────────────────────────")

    return {
        "sub_queries":      sub_queries,
        "pending_queries":  sub_queries,
        "extra_queries":    [],
        "collected_docs":   [],
        "searched_queries": [],
        "is_sufficient":    False,
        "round_count":      0,
        "final_answer":     "",
        "professor_query":  professor_query,
        "school_tend":      intent,
    }

def after_decompose(state: AgentState):
    """
    Decomposer 完成後的路由：
    - 一般問題（search_tend=False, 無 professor_query）：只派 search，走原始 RAG 流程
    - 有教授查詢 / 備案推薦意圖：依需求決定是否加派 extension_function
      - school_tend=True：只派 extension_function（備案推薦，不查 DB）
      - professor_query 存在：search + extension_function 並行
    """
    school_tend     = state.get("school_tend", False)
    professor_query = state.get("professor_query")

    need_extension = school_tend or (professor_query is not None)

    if school_tend:
        # 純備案推薦：不查 DB
        return [Send("extension_function", state)]
    elif need_extension:
        # 教授查詢：兩路並行
        return [Send("search", state), Send("extension_function", state)]
    else:
        # 一般問題：只走 search → plan → finalize
        return [Send("search", state)]
# def _test():
#     q = "Provide detailed information about English test(including min score) and GRE requirements for applying to Caltech’s CS master’s program."
#     q2 = "Caltech english min score requrement"
#     q3 = "Caltech cs master apply deadline"
#     q4 = "Caltech cs master Eligibility and min gpa"
#     q5 = "Caltech cs master min gpa"
#     _search_one_query(q,q)

# ─── Node 1.5：Extension Function ────────────────────────────────────────────

def extension_function_node(state: AgentState) -> dict:
    """
    擴充功能節點（與 search 並行）：
    - 若 professor_query 存在 → SerpAPI 抓取教授資料
    - 若 school_tend=True     → 進行選校推薦分析
    結果寫入 extension_docs，不影響 search 的 collected_docs。
    """
    _emit({"type": "thinking", "step": "extension_function"})

    extension_docs: list[dict] = []
    professor_fetched = False

    # ── 教授查詢 ──────────────────────────────────────────────────────────────
    professor_query = state.get("professor_query")
    if professor_query is not None:
        print(f"[Extension] 抓取教授資料：{professor_query.get('name')}")
        docs = run_professor_fetch(professor_query, state["original_query"])
        if docs:
            _emit({
                "type": "tool_result",
                "tool": "fetch_professor",
                "preview": f"找到 {len(docs)} 筆教授相關資料",
            })
            professor_fetched = True
            extension_docs.extend(docs)
        else:
            print("[Extension] 未抓到教授資料")
    
    # ── 選校推薦 ──────────────────────────────────────────────────────────────
    if state.get("school_tend") is True:
        print("[Extension] 進行選校推薦分析...")
        result = analyze_and_evaluate(state["original_query"])
        if result and result.get("collected_docs"):
            alt_docs = result["collected_docs"]
            _emit({
                "type": "tool_result",
                "tool": "school_recommend",
                "preview": f"找到 {len(alt_docs)} 筆推薦學校相關 chunk",
            })
            extension_docs.extend(alt_docs)
        else:
            print("[Extension] 選校推薦無結果")
    
    print(f"[Extension] 共取得 {len(extension_docs)} 筆擴充資料")
    return {
        "extension_docs":    extension_docs,
        "professor_fetched": professor_fetched,
    }


# ─── Node 2：Searcher ─────────────────────────────────────────────────────────

def _search_one_query(q: str, original_query: str) -> tuple[list[dict], str | None]:
    """
    對單一子問題執行混合搜尋，自動偵測 school_id。

    Returns:
        (results, school_id)
    """
    school_ids = _detect_school_ids(q) or _detect_school_ids(original_query)
    school_id = school_ids[0] if school_ids else None

    _emit({
        "type": "tool_call",
        "tool": "search_school" if school_id else "search_general",
        "args": {"query": q, **({"school_id": school_id} if school_id else {})},
    })

    results = search_core(query=q, top_k=TOP_K_PER_QUERY, use_rerank=True, school_id=school_id)
    
    """
        Returns: result結構
        list of dict，每筆包含 chunk_text、source_url、passed_types、
        school_id、university_name、vector_score、rerank_score。
    """
    # 插入compress 邏輯
    
    # for v in results:
    #     k = v.get("chunk_text")
    #     print("chunck長度:  ",len(k))
    #     print(v.get("passed_types"))
    #     print(v.get("source_url"))
    #     print("內容",k)
        
    """
        測試compress函數
    """
    #results = chunk_compress(results,q)
    
    #for v in results:
    #    k = v.get("chunk_text")
    #    print("Chunk ID",v.get("id"))
    #    print("問題是:  ",v.get("query"))
    #    print("chunck長度:  ",len(k))
    #    print("內容",k)

    _emit({
        "type": "tool_result",
        "tool": "search_school" if school_id else "search_general",
        "preview": f"找到 {len(results)} 筆相關文獻",
    })

    return results, school_id


def searcher_node(state: AgentState) -> dict:
    """
    對 pending_queries 中的每個子問題執行向量 + 關鍵字混合檢索。
    每次搜尋都發送 thinking / tool_call / tool_result 事件。
    """
    pending        = state.get("pending_queries", [])
    already_searched = state.get("searched_queries", [])
    original_query = state["original_query"]
    round_num      = state.get("round_count", 0) + 1

    print(f"\n[Searcher] 第 {round_num} 輪搜尋，共 {len(pending)} 個問題")
    _emit({"type": "thinking", "step": round_num})

    new_docs: list[dict] = []
    newly_searched: list[str] = []

    for q in pending:
        _check_cancel()
        if q in already_searched:
            print(f"  ⏭ 略過（已搜尋）：{q}")
            continue

        results, school_id = _search_one_query(q, original_query)
        print(f"     搜尋：{q}  學校過濾：{school_id or '（全域）'}  取得 {len(results)} 筆")
        
        for item in results:
            item["query"] = q

        new_docs.extend(results)
        newly_searched.append(q)
    
    new_docs = chunk_compress(new_docs) or new_docs

    print(f"\n[Searcher] 本輪新增 {len(new_docs)} 筆文件")

    return {
        "collected_docs":   new_docs,
        "searched_queries": newly_searched,
        "round_count":      round_num,
        "pending_queries":  [],
    }


# ─── Node 3：Planner ──────────────────────────────────────────────────────────
# query = 原始問題 | docs = 找到的資料  | searched = 已經搜尋的子問題 
def _build_planner_prompt(query: str, docs: list[dict], ext_docs: list[dict], searched: list[str]) -> str:
    """組合 Planner 用的 prompt。"""
    def _primary_type(doc: dict) -> str:
        pts = doc.get("passed_types") or []
        return max(pts, key=lambda x: x.get("score", 0))["type"] if pts else "?"

    # 顯示全部 docs（不再限制 10 筆），每筆截 1500 字防止 token 過大
    context_preview = "".join(
        f"\n[{i+1}] {doc.get('school_id', '?')}/{_primary_type(doc)}: "
        f"{doc.get('chunk_text', '')[:1500]}\n"
        for i, doc in enumerate(docs)
    )

    # extension_docs（教授資料 / 選校推薦）一併提供給 Planner 判斷
    ext_preview = ""
    if ext_docs:
        ext_preview = f"\n\n【擴充資料（教授 / 選校推薦）】（共 {len(ext_docs)} 筆）"
        ext_preview += "".join(
            f"\n[E{i+1}] {doc.get('school_id', '?')}: "
            f"{doc.get('chunk_text', '')[:1500]}\n"
            for i, doc in enumerate(ext_docs)
        )

    searched_str = "\n".join(f"- {q}" for q in searched)

    return f"""你是的任務是評估資料充足性。請根據以下資訊判斷目前的資料是否足夠回答使用者問題。

【使用者原始問題】
{query}

【已搜尋的子問題】
{searched_str}

【目前收集到的資料摘要】（共 {len(docs)} 筆）
{context_preview}{ext_preview}

【評估任務】
請判斷：目前的資料是否已足夠回答使用者的原始問題？
若問題涉及教授相關資訊，請一併考量「擴充資料」是否已滿足需求。

輸出格式（JSON）：
{{
  "is_sufficient": true 或 false,
  "reason": "簡短說明為何足夠/不夠（1-2句）",
  "extra_queries": ["English follow-up query 1", ..., "English follow-up query 5"],// 若 is_sufficient=false 才填，最多 5 個
}}

規則（請嚴格遵守）：
1. 預設為 sufficient=true。只要有找到相關資料，無論是否完整，都應標記 true。
2. 只有在某所學校或某個面向完全沒有任何文件時，才標記 false 並補問。
3. 不要因為「想找更多資訊」就標記 false，資料不完美是正常的。
4. 補充問題只針對完全空白的學校或面向，不得重複已搜尋的問題。
5. 補充問題（extra_queries）必須用英文撰寫，以提升向量檢索效果。
6. 只輸出 JSON，不要有其他文字。
7. 除非問題真的太短，否則盡量拆解問題。
8. 拆解問題時，盡量用 學校 + 需求問題 的形式。
9. 問題請用英文，作答時也用英文，最後輸出轉為中文即可
10.若出現需要找english proficiency(TOFEL、TOEFL iBT、IELTS、Duolingo English Test(DET))或GRE成績需求時，皆在子問題中包含最低接受成績與是否需要提供
11.盡量減少問題過於發散，只問相關的問題與目前資料不足的問題
12.若問題太過於廣泛，如只詢問了application requirement、申請資格等等這種廣泛問題，請在子問題種搜尋細項，範例如下。
    問題:application requirement 子問題拆成:min gpa/min english score/min gre/類似這樣
你的判斷：(判斷用中文回答) """


def _parse_planner_response(raw: str) -> tuple[bool, str, list[str]]:
    """
    解析 Planner 的 JSON 回應。

    Returns:
        (is_sufficient, reason, extra_queries)
    """
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError("無法找到 JSON")
    parsed = json.loads(match.group())

    is_sufficient = bool(parsed.get("is_sufficient", True))
    reason        = parsed.get("reason", "")
    extra_queries = [str(q).strip() for q in parsed.get("extra_queries", []) if str(q).strip()]
    return is_sufficient, reason, extra_queries[:3]


def planner_node(state: AgentState) -> dict:
    """
    判斷目前收集的資料是否足夠回答原始問題。
    - 足夠 → is_sufficient = True
    - 不夠且輪次 < MAX_ROUNDS → 產生 1-2 個補充子問題
    - 達到最大輪次 → 強制標記 is_sufficient = True
    """
    query       = state["original_query"]
    docs        = _deduplicate_docs(state.get("collected_docs", []))
    ext_docs    = _deduplicate_docs(state.get("extension_docs", []))
    round_count = state.get("round_count", 0)
    searched    = state.get("searched_queries", [])

    print(f"\n[Planner] ══ 第 {round_count} 輪 ══════════════════════════════════════")
    print(f"[Planner] 去重後文件數：{len(docs)}  擴充文件數：{len(ext_docs)}  已搜尋問題數：{len(searched)}")
    if searched:
        print("[Planner] 已搜尋過的問題：")
        for q in searched:
            print(f"  ✓ {q}")

    if round_count >= MAX_ROUNDS:
        print(f"[Planner] 已達最大輪次（{MAX_ROUNDS}），強制結束")
        return {"is_sufficient": True, "pending_queries": []}

    if not docs and not ext_docs:
        print("[Planner] 無文件，結束搜尋")
        return {"is_sufficient": True, "pending_queries": []}

    _emit({"type": "llm_call", "purpose": "planner", "round": round_count})
    raw = _call_llm(_build_planner_prompt(query, docs, ext_docs, searched))

    try:
        is_sufficient, reason, extra_queries = _parse_planner_response(raw)
    except Exception as e:
        print(f"[Planner] JSON 解析失敗：{e}，預設為 sufficient=true")
        return {"is_sufficient": True, "pending_queries": []}

    print(f"[Planner] 是否充足：{is_sufficient}  理由：{reason}")
    if not is_sufficient and extra_queries:
        print(f"[Planner] 補充問題：")
        for q in extra_queries:
            print(f"  → {q}")

    return {
        "is_sufficient":  is_sufficient,
        "extra_queries":  extra_queries if not is_sufficient else [],
        "pending_queries": extra_queries if not is_sufficient else [],
    }


# ─── Node 4：Finalizer ────────────────────────────────────────────────────────

def finalizer_node(state: AgentState) -> dict:
    """
    彙整所有收集到的文件（search 的 collected_docs + extension 的 extension_docs），
    呼叫 Gemini 生成最終答案，並發送 answer 事件。
    """
    query = state["original_query"]

    search_docs    = state.get("collected_docs", [])
    extension_docs = state.get("extension_docs", [])

    # extension_docs 放在前面（教授資料 / 選校推薦優先作為上下文）
    all_docs = _deduplicate_docs(extension_docs + search_docs)

    print(
        f"\n[Finalizer] 搜尋文件：{len(search_docs)} 筆  "
        f"擴充文件：{len(extension_docs)} 筆  "
        f"合併去重後：{len(all_docs)} 筆"
    )

    if not all_docs:
        answer = "很抱歉，未能從資料庫中找到相關資訊。建議您直接前往各校官方網站查詢。"
        _emit({"type": "answer", "text": answer})
        return {"final_answer": answer}

    # 以 rerank_score（或 rrf_score）降冪排序，讓最相關的文件排在前面
    docs_sorted = sorted(
        all_docs,
        key=lambda d: d.get("rerank_score", d.get("rrf_score", 0)),
        reverse=True,
    )

    _check_cancel()
    _emit({"type": "llm_call", "purpose": "finalizer"})

    full_text = ""
    try:
        for chunk in generate_answer_stream(query, docs_sorted):
            _check_cancel()
            full_text += chunk
            if chunk:
                _emit({"type": "answer_chunk", "text": chunk})
    except Exception as e:
        print(f"[Finalizer] 串流失敗，回退到非串流: {e}")
        full_text = generate_answer(query, docs_sorted) or ""

    if full_text:
        # 清理格式（統一在全文上處理，避免跨 chunk 的問題）
        import re as _re
        clean = full_text.replace("**", "")
        clean = _re.sub(
            r"<span[^>]*>\s*(\[[^\]]+\]\([^\)]+\))\s*</span>",
            r"\1", clean, flags=_re.IGNORECASE,
        )
        clean = _re.sub(r"</?span[^>]*>", "", clean, flags=_re.IGNORECASE)
        clean = clean.strip()
        _emit({"type": "answer", "text": clean})
        return {"final_answer": clean}
    else:
        msg = "Gemini 生成失敗"
        print(f"[Finalizer] {msg}")
        _emit({"type": "error", "message": msg})
        return {"final_answer": None}


# ─── 路由 & 圖建構 ────────────────────────────────────────────────────────────

def should_continue(state: AgentState) -> str:
    """Planner 結束後的路由：繼續搜尋 or 直接 Finalize。"""
    return "finalize" if state.get("is_sufficient", True) else "search"


def after_professor_fetch(state: AgentState) -> str:
    """
    professor_fetch 後的路由：
    - 已抓到教授資料（professor_fetched=True）→ 直接 finalize，跳過向量搜尋
    - 無教授意圖 → 走一般向量搜尋流程
    """
    return "finalize" if state.get("professor_fetched", False) else "search"


def _build_graph():
    """
    流程：
      decompose
        ├─ Send → search ──────────────────────────┐
        │                                          ▼
        └─ Send → extension_function ──────────► plan ─(not sufficient)→ search
                                                   └─(sufficient)──────────────┐
                                                                                ▼
                                                                            finalize
    兩條並行路徑（search / extension_function）fan-in 到 plan，
    Planner 能同時看到 collected_docs 與 extension_docs 再決定是否充足。
    finalize 彙整兩者後呼叫 Gemini 生成回答。
    """
    builder = StateGraph(AgentState)

    builder.add_node("decompose",          decomposer_node_1)
    builder.add_node("extension_function", extension_function_node)
    builder.add_node("search",             searcher_node)
    builder.add_node("plan",               planner_node)
    builder.add_node("finalize",           finalizer_node)

    builder.set_entry_point("decompose")

    # decompose → 平行派出 search + extension_function
    builder.add_conditional_edges("decompose", after_decompose)

    # extension_function 路徑：完成後匯入 plan（fan-in），讓 Planner 能看到擴充資料
    builder.add_edge("extension_function", "plan")

    # search 路徑：search → plan → (loop or finalize)
    builder.add_edge("search", "plan")
    builder.add_conditional_edges(
        "plan",
        should_continue,
        {"search": "search", "finalize": "finalize"},
    )

    builder.add_edge("finalize", END)

    return builder.compile()


_graph = None


def _get_graph():
    global _graph
    if _graph is None:
        _graph = _build_graph()
    return _graph


# ─── 公開 API ─────────────────────────────────────────────────────────────────

def run_agent(
    query: str,
    max_steps: int = 10,
    verbose: bool = True,
    on_event: Optional[Callable[[dict], None]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> str | None:
    """
    執行 Agentic RAG 流程。

    Args:
        query:     使用者問題
        max_steps: LangGraph 最大步驟數（安全閥）
        verbose:   是否顯示詳細日誌
        on_event:  SSE 事件 callback，格式：{"type": "thinking"|"tool_call"|..., ...}

    Returns:
        最終回答字串，或 None
    """
    _agent_context.on_event = on_event
    _agent_context.cancel_event = cancel_event

    try:
        if verbose:
            print(f"\n{'='*60}")
            print(f"   Agentic RAG 啟動")
            print(f"   Agentic RAG 啟動時間 : {time.localtime()}")
            print(f"   問題：{query}")
            print(f"   最大搜尋輪次：{MAX_ROUNDS}")
            print(f"{'='*60}")

        initial_state: AgentState = {
            "original_query":    query,
            "sub_queries":       [],
            "extra_queries":     [],
            "pending_queries":   [],
            "collected_docs":    [],
            "extension_docs":    [],
            "searched_queries":  [],
            "is_sufficient":     False,
            "round_count":       0,
            "final_answer":      "",
            "professor_query":   None,
            "professor_fetched": False,
            "school_tend":       False,
        }

        result_state = _get_graph().invoke(initial_state, {"recursion_limit": max_steps * 4})

        if verbose:
            total_docs = len(_deduplicate_docs(result_state.get("collected_docs", [])))
            rounds     = result_state.get("round_count", 0)
            print(f"\n{'='*60}")
            print(f"完成！共搜尋 {rounds} 輪，彙整 {total_docs} 筆去重文件")
            print(f"{'='*60}")
        print(f"   Agentic RAG 結束時間 : {time.localtime()}")
        return result_state.get("final_answer") or None

    except AgentCancelledError:
        print("[Agent] 收到取消信號，提前結束。")
        return None

    finally:
        _agent_context.on_event = None
        _agent_context.cancel_event = None


# ─── CLI 入口 ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    #test__()
    
    import argparse
    import io

    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    parser = argparse.ArgumentParser(description="Agentic RAG（LangGraph）")
    parser.add_argument("query",       nargs="?", help="使用者問題")
    parser.add_argument("--max-steps", type=int, default=20, help="最大 LangGraph 步驟數")
    args = parser.parse_args()

    q = args.query or input("請輸入問題：").strip()
    if not q:
        print("未輸入問題。")
        sys.exit(1)

    answer = run_agent(q, max_steps=args.max_steps, verbose=True)
    if answer:
        print("\n" + "=" * 30 + " 最終回答 " + "=" * 30)
        print(answer)
        print("=" * 70 + "\n")
    else:
        print("生成回答失敗。")
        sys.exit(1)
    
