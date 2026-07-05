"""
流程：
  1. Decomposer       — 兩次獨立 LLM 呼叫：
                          (a) 意圖判斷：偵測學校 + 教授查詢意圖 + 是否需要查 SQL（決定路由）
                          (b) 子問題拆解：只在需要查 SQL 時才呼叫，省下純教授查詢的一次 LLM 呼叫
  1.5 ProfessorFetcher — 若 query 含教授意圖，呼叫 SerpAPI 即時抓取教授資料
  2. SQLSearcher      — 每個子問題透過 text-to-SQL 查詢結構化申請要求資料
  3. Verifier         — 判斷彙整後的資料是否文不對題/足以回答問題
  4. Refiner          — 資料不足時重新改寫查詢，回頭再跑一次 search/extension_function（最多 1 輪）
  5. Finalizer        — 呼叫 OpenAI 生成最終答案，或誠實告知資料不足
  6. Critic           — 只在真的生成過答案時執行：檢查答案是否有內容在參考資料中找不到根據（幻覺），
                          發現問題就在答案後面附上警告，不重新生成
"""

from __future__ import annotations

import json
import re
import sys
import threading
from pathlib import Path
from typing import Callable, Optional, TypedDict
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

from retriever.sql_search import sql_search, get_known_school_ids
from generator.openai_client import call_llm, generate_answer, generate_answer_stream, format_context_for_prompt
from professor_fetcher.fetch_for_agent import run_professor_fetch

# ─── 學校別名對照表 ───────────────────────────────────────────────────────────

_SCHOOL_ALIASES: dict[str, list[str]] = {
    "cmu":          ["cmu", "carnegie mellon", "卡內基梅隆"],
    "mit":          ["mit", "massachusetts institute", "麻省理工"],
    "stanford":     ["stanford", "史丹佛", "斯坦福"],
    "caltech":      ["caltech", "california institute", "加州理工"],
    "gatech":       ["georgia tech", "gatech", "喬治亞理工"],
    "ucla":         ["ucla", "uc los angeles", "加州洛杉磯"],
    "ucsd":         ["ucsd", "uc san diego", "加州聖地牙哥"],
    "umass":        ["umass", "amherst", "麻州大學"],
    "berkeley":     ["berkeley", "uc berkeley", "柏克萊"],
    "washington":   ["university of washington", "uw", "華盛頓大學"],
    "uiuc":         ["uiuc", "illinois", "urbana-champaign", "伊利諾"],
    "cornell":      ["cornell", "康乃爾"],
    "princeton":    ["princeton", "普林斯頓"],
    "columbia":     ["columbia", "哥倫比亞"],
    "upenn":        ["upenn", "university of pennsylvania", "penn", "賓州大學", "賓夕法尼亞"],
    "umich":        ["umich", "michigan", "密西根"],
    "utaustin":     ["ut austin", "university of texas at austin", "texas austin", "德州大學奧斯汀"],
    "ucsb":         ["ucsb", "santa barbara", "聖塔芭芭拉"],
    "uci":          ["uci", "uc irvine", "irvine", "爾灣"],
    "purdue":       ["purdue", "普渡"],
    "wisc":         ["wisconsin", "madison", "威斯康辛"],
    "umd":          ["umd", "maryland", "college park", "馬里蘭"],
    "nyu":          ["nyu", "new york university", "紐約大學"],
    "ucsc":         ["ucsc", "santa cruz", "聖塔克魯茲"],
    "rice":         ["rice", "萊斯大學"],
    "northeastern": ["northeastern", "東北大學"],
    "ucdavis":      ["ucdavis", "uc davis", "戴維斯"],
    "osu":          ["ohio state", "osu", "俄亥俄州立"],
    "duke":         ["duke", "杜克"],
    "rutgers":      ["rutgers", "羅格斯"],
}

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
    collected_docs:    list[dict]    # SQL 查詢結果（每輪由 searcher_node 整份覆寫，重試時不與前一輪疊加）
    extension_docs:    list[dict]    # 教授資料（每輪由 extension_function_node 整份覆寫，重試時不與前一輪疊加）
    final_answer:      str
    professor_query:   dict | None   # Decomposer 偵測到的教授查詢 {name, school, school_id}
    needs_sql_search:  bool         # Decomposer 判斷是否需要查 program_requirements
    mentioned_school_ids: list[str]   # Decomposer 從已知清單中偵測到的 school_id（不代表資料庫已收錄）
    mentioned_school_names: list[str] # Decomposer 偵測到的學校名稱原文（不限於已知清單）
    verified_docs:     list[dict]    # Verifier 去重後的資料（search + extension 合併）
    is_sufficient:     bool          # Verifier 判斷資料是否足以回答問題
    insufficiency_reason: str        # 資料不足或文不對題時的簡短原因
    retry_count:       int           # 已重試次數（Refiner 重新查詢的次數，上限 1 輪）
    generated_answer:  bool          # finalizer 是否真的呼叫 LLM 生成過答案（決定是否需要跑 Critic）


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
    """以 school_id + chunk_text/欄位內容去重複文件。"""
    seen: set[str] = set()
    result: list[dict] = []
    for doc in docs:
        key = doc.get("chunk_text") or json.dumps(doc, sort_keys=True, default=str)
        key = f"{doc.get('school_id', '')}:{key[:200]}"
        if key not in seen:
            seen.add(key)
            result.append(doc)
    return result


def _call_llm(prompt: str) -> str:
    """呼叫 OpenAI，回傳純文字。每次呼叫前先檢查是否已取消。"""
    _check_cancel()
    return call_llm(prompt)


def _parse_json_object(raw: str) -> dict:
    """從 LLM 回覆中擷取第一個 JSON 物件並解析，失敗時拋出例外。"""
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError("找不到 JSON")
    return json.loads(match.group())


def _parse_professor_query(pq_raw: object) -> dict | None:
    """
    將 LLM 回傳的 professor_query 物件正規化為 {name, school, school_id}。
    school_id 不在已知別名表時，嘗試從 school 名稱重新偵測；仍找不到就沿用原字串。
    decomposer_node 與 refiner_node 共用此邏輯，避免各自維護一份。
    """
    if not isinstance(pq_raw, dict):
        return None
    pq_name = (pq_raw.get("name") or "").strip()
    if not pq_name:
        return None
    pq_school = (pq_raw.get("school") or "").strip()
    pq_sid    = (pq_raw.get("school_id") or "").strip()
    if pq_sid not in _SCHOOL_ALIASES:
        detected = _detect_school_ids(pq_school)
        pq_sid = detected[0] if detected else (pq_school or pq_sid)
    return {"name": pq_name, "school": pq_school, "school_id": pq_sid}


# ─── Node 1：Decomposer ───────────────────────────────────────────────────────

def _build_intent_prompt(query: str) -> str:
    """任務一：學校辨識 + 教授查詢偵測 + 判斷是否需要查 SQL（決定路由）。"""
    return f"""你是一個 Query Intent 分析助理，負責從使用者問題辨識學校、教授查詢意圖，並判斷是否需要查詢結構化資料庫。

====================
【任務一：學校辨識】
====================
1. 從使用者問題中辨識出所有「明確提及」的學校（必須出現在已知學校清單中），存入 school_ids
2. 額外辨識使用者問題中「明確提及」的所有學校名稱原文（不限於已知清單，包含清單外的學校），
   以英文全名或使用者原本使用的名稱存入 mentioned_school_names（例如使用者問 University of Toronto，
   即使它不在已知清單內，也要存入 "University of Toronto"）
3. 若沒有偵測到任何學校，school_ids 與 mentioned_school_names 皆為 []

====================
【任務二：教授查詢偵測（professor_query）】
====================
判斷問題是否在詢問「某位具體教授」的相關資訊。

- 若問題中有「明確的教授姓名」，提取其姓名與學校，professor_query 為物件
- 姓名必須完整保留原始寫法，包括連字號（-）與大小寫，例如 "Fei-Fei Li" 不得改寫為 "Feifei Li"
- 否則 professor_query 為 null

====================
【任務三：是否需要查詢結構化申請要求資料庫（needs_sql_search）】
====================
資料庫（program_requirements）只有 GPA、TOEFL/IELTS/GRE、申請截止日期等「申請要求」欄位，
不包含教授的研究領域、發表論文、經歷等資訊。

- 若使用者問題只在詢問教授本人相關資訊（如研究領域、論文、背景等），且完全沒有詢問任何申請要求，
  needs_sql_search 設為 false
- 若使用者問題除了教授資訊外，還有詢問任何申請要求（GPA/TOEFL/IELTS/GRE/截止日期等），
  needs_sql_search 設為 true
- 若問題完全沒有教授查詢意圖，needs_sql_search 一律為 true

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
  "mentioned_school_names": ["School Name 1", ...],
  "professor_query": {{"name": "教授全名（英文）", "school": "學校名稱（英文）", "school_id": "學校ID"}} or null,
  "needs_sql_search": true or false
}}
"""


def _build_subquery_prompt(query: str, school_ids: list[str]) -> str:
    """任務二：依偵測到的學校拆解子問題，只在需要查 SQL 時才呼叫。"""
    return f"""你是一個 Query Decomposer，負責將使用者問題拆解為適合 text-to-SQL 檢索的英文子問題。

[嚴格遵守] 子問題最多 9 個；只有一間學校時最多 5 個；兩間學校時每間最多 4 個；三間以上時每間最多 3 個。

【已辨識出的學校】
{school_ids or "無特定學校"}

【規則】
1. 若有多所學校：為每一所學校產生一個對應的子問題
2. 若只有一所學校：產生 1 個子問題
3. 若沒有偵測到任何學校：sub_queries = [將原問題改寫為較清楚的單一問題]
4. 若問題太過於廣泛，如只詢問了 application requirement、申請資格等等這種廣泛問題，請在子問題種搜尋細項，
   請將廣泛問題的細項分開詢問，細項詢問甚麼由你判斷，參考範例如下。
   問題:application requirement 子問題拆成:min gpa/min english score/min gre/等等，類似這樣的子問題找答案

【子問題格式要求】
- 所有子問題必須用英文撰寫，無論使用者原始問題是何種語言
- 優先使用：「School + requirement content?」形式
- 保持語意完整，不要遺漏條件
- 不要自行新增未提及的資訊
- 若有分數相關問題如 GRE/GPA/English test，請都要詢問 是否需要、最低分多少

【使用者問題】
{query}

【輸出格式（嚴格遵守）】
只輸出 JSON，禁止輸出任何說明文字：

{{"sub_queries": ["sub-query in English 1", "sub-query in English 2", ...]}}
"""


def decomposer_node(state: AgentState) -> dict:
    query = state["original_query"]
    print(f"\n{'='*60}")
    print(f"[Decomposer] 原始問題：{query}")
    print(f"{'='*60}")

    # 任務一+二+三：學校/教授意圖辨識，並決定是否需要查 SQL（決定後續路由）
    try:
        parsed = _parse_json_object(_call_llm(_build_intent_prompt(query)))
        school_ids = [
            str(s).strip()
            for s in parsed.get("school_ids", [])
            if str(s).strip() in _SCHOOL_ALIASES
        ]
        mentioned_school_names = [
            str(s).strip() for s in parsed.get("mentioned_school_names", []) if str(s).strip()
        ]
        professor_query  = _parse_professor_query(parsed.get("professor_query"))
        needs_sql_search = bool(parsed.get("needs_sql_search", True))
    except Exception as e:
        print(f"[Decomposer] 意圖判斷失敗（{e}），使用原始問題作為 fallback")
        school_ids             = _detect_school_ids(query)
        mentioned_school_names = []
        professor_query         = None
        needs_sql_search        = True

    # 任務四：只在需要查 SQL 時才拆解子問題（純教授查詢可省下這次 LLM 呼叫）
    if needs_sql_search:
        try:
            parsed_sq = _parse_json_object(_call_llm(_build_subquery_prompt(query, school_ids)))
            sub_queries = [str(q).strip() for q in parsed_sq.get("sub_queries", []) if str(q).strip()]
            if not sub_queries:
                raise ValueError("sub_queries 為空")
        except Exception as e:
            print(f"[Decomposer] 子問題拆解失敗（{e}），使用原始問題作為 fallback")
            sub_queries = [query]
    else:
        sub_queries = []

    if len(school_ids) >= 2:
        print(f"[Decomposer] 偵測到 {len(school_ids)} 所學校，拆解為 {len(sub_queries)} 個子問題：")
    else:
        label = f"（學校：{school_ids[0]}）" if school_ids else "（無特定學校）"
        print(f"[Decomposer] 單一問題 {label}：")
    for i, q in enumerate(sub_queries, 1):
        print(f"  Q{i}: {q}")

    if professor_query:
        print(f"[Decomposer] professor_query = {professor_query['name']} @ {professor_query['school']} "
              f"[{professor_query.get('school_id', '?')}]")
    else:
        print("[Decomposer] professor_query = None（無教授查詢意圖）")
    print(f"[Decomposer] needs_sql_search = {needs_sql_search}")

    return {
        "sub_queries":            sub_queries,
        "collected_docs":         [],
        "final_answer":           "",
        "professor_query":        professor_query,
        "needs_sql_search":       needs_sql_search,
        "mentioned_school_ids":   school_ids,
        "mentioned_school_names": mentioned_school_names,
    }


def route_to_retrieval(state: AgentState):
    """
    決定要跑哪些檢索節點（decompose 完成後、以及 refine 重新產生查詢後皆共用此路由）：
    - 有教授查詢 + 需要 SQL → search + extension_function 並行
    - 只查教授、不需要 SQL → 只走 extension_function
    - 一般問題（無教授查詢）→ 只走 search
    """
    professor_query  = state.get("professor_query")
    needs_sql_search = state.get("needs_sql_search", True)

    if professor_query is not None:
        if needs_sql_search:
            return [Send("search", state), Send("extension_function", state)]
        return [Send("extension_function", state)]
    return [Send("search", state)]


# ─── Node 1.5：Extension Function（教授查詢） ─────────────────────────────────

def extension_function_node(state: AgentState) -> dict:
    """擴充功能節點（與 search 並行）：抓取教授資料，寫入 extension_docs。"""
    _emit({"type": "thinking", "step": "extension_function"})

    extension_docs: list[dict] = []

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
            extension_docs.extend(docs)
        else:
            print("[Extension] 未抓到教授資料")

    print(f"[Extension] 共取得 {len(extension_docs)} 筆擴充資料")
    return {
        "extension_docs":    extension_docs,
    }


# ─── Node 2：SQL Searcher ─────────────────────────────────────────────────────

def _search_one_query(q: str, original_query: str) -> list[dict]:
    """對單一子問題執行 text-to-SQL 檢索。"""
    school_ids = _detect_school_ids(q) or _detect_school_ids(original_query)
    school_id = school_ids[0] if school_ids else None

    _emit({
        "type": "tool_call",
        "tool": "sql_search",
        "args": {"query": q, **({"school_id": school_id} if school_id else {})},
    })

    results, sql = sql_search(q)

    for item in results:
        item["query"] = q

    _emit({
        "type": "tool_result",
        "tool": "sql_search",
        "preview": f"找到 {len(results)} 筆相關資料" + (f"（SQL: {sql}）" if sql else ""),
    })

    return results


def searcher_node(state: AgentState) -> dict:
    """對所有子問題執行 text-to-SQL 檢索，一輪到位（無重試迴圈）。"""
    sub_queries     = state.get("sub_queries", [])
    original_query = state["original_query"]

    print(f"\n[Searcher] 共 {len(sub_queries)} 個子問題")
    _emit({"type": "thinking", "step": 1})

    new_docs: list[dict] = []

    for q in sub_queries:
        _check_cancel()
        results = _search_one_query(q, original_query)
        print(f"     查詢：{q}  取得 {len(results)} 筆")
        new_docs.extend(results)

    print(f"\n[Searcher] 共取得 {len(new_docs)} 筆資料")

    return {"collected_docs": new_docs}


# ─── Node 3：Verifier ─────────────────────────────────────────────────────────

def _build_verify_prompt(query: str, context_text: str) -> str:
    return f"""你是一個檢索結果品質檢查員，負責判斷「檢索到的參考資料」是否真的能回答使用者的問題。

【判斷重點】
1. 資料是否與問題「文不對題」：例如問的是 A 學校，資料卻是 B 學校；問的是某位教授，資料卻是同名的另一個人。
2. 資料是否「足夠完整」：若問題涉及多個面向（如多所學校比較、多個欄位），資料是否涵蓋了大部分核心面向。
3. 允許「合理推論」：不需要資料逐字明講問題的關鍵詞才算足夠。只要資料內容能讓一般人合理推論出答案，
   就算 sufficient=true。例如問「研究領域」，資料即使沒有寫「研究領域：xxx」這種明講句子，
   只要列出了該教授近期論文的標題/摘要，就足以合理歸納出研究方向，應判定為足夠。
4. 允許「部分足夠」：只要資料裡有能實際回答問題核心的部分，即使不是每個細節都涵蓋，也算 sufficient=true，
   缺的部分由後續生成階段自然告知使用者「部分資料不足」即可，不需要在這裡整批判定為不足。
5. 只有在資料明顯「文不對題」或「完全無關」時，才判定 sufficient=false。判定為不足前，先自問：
   「如果我是使用者，看到這些資料，能不能合理猜出答案？」若答案是能，就算 sufficient=true。

【使用者問題】
{query}

【檢索到的參考資料】
{context_text}

【輸出格式（嚴格遵守）】
只輸出 JSON，禁止輸出任何說明文字：

{{"sufficient": true or false, "reason": "若 sufficient=false，用一句話說明資料為何文不對題或無關；否則留空字串"}}
"""


def verifier_node(state: AgentState) -> dict:
    """
    在生成答案前，檢查 search + extension_function 收集到的資料是否真的與問題相關、足以回答。
    只做好壞判斷，不重試、不重新查詢——文不對題時讓 finalizer 誠實告知使用者。
    """
    query = state["original_query"]

    search_docs    = state.get("collected_docs", [])
    extension_docs = state.get("extension_docs", [])

    # extension_docs 放在前面（教授資料優先作為上下文）
    all_docs = _deduplicate_docs(extension_docs + search_docs)

    if not all_docs:
        return {"verified_docs": [], "is_sufficient": False, "insufficiency_reason": ""}

    _check_cancel()
    context_text = format_context_for_prompt(all_docs)

    try:
        parsed = _parse_json_object(_call_llm(_build_verify_prompt(query, context_text)))
        is_sufficient = bool(parsed.get("sufficient", True))
        reason        = str(parsed.get("reason", "")).strip()
    except Exception as e:
        print(f"[Verifier] 判斷失敗（{e}），預設視為足夠，交由生成階段自行把關")
        is_sufficient = True
        reason        = ""

    print(f"[Verifier] is_sufficient={is_sufficient}" + (f"  reason={reason}" if reason else ""))

    return {
        "verified_docs":        all_docs,
        "is_sufficient":        is_sufficient,
        "insufficiency_reason": reason,
    }


def after_verify(state: AgentState) -> str:
    """
    Verifier 完成後的路由：
    - is_sufficient=True → finalize
    - is_sufficient=False 且「曾檢索到資料但文不對題/不足」且尚未重試過 → refine（重新產生查詢再查一次，最多 1 輪）
    - 完全查無資料（verified_docs 為空，如未收錄該校），或已重試過仍不足 → finalize（誠實告知）
    """
    if state.get("is_sufficient", True):
        return "finalize"

    if state.get("verified_docs") and state.get("retry_count", 0) < 1:
        return "refine"

    return "finalize"


# ─── Node 4：Refiner ──────────────────────────────────────────────────────────

def _build_refine_prompt(query: str, reason: str, sub_queries: list[str], professor_query: dict | None) -> str:
    return f"""你是一個查詢改寫助理。先前針對使用者問題產生的檢索查詢，被判斷為「文不對題」或「資料不足」，
請根據原因重新改寫，讓下一輪檢索能命中正確的資料。

【使用者原始問題】
{query}

【資料被判定不足的原因】
{reason or "未提供具體原因"}

【先前產生的子問題】
{sub_queries}

【先前偵測到的教授查詢】
{professor_query or "無"}

【改寫規則】
1. 若原因顯示學校辨識錯誤（例如教授實際隸屬的學校與先前填的不同），請修正 professor_query 的 school / school_id。
2. 若原因顯示子問題描述不夠精確或條件遺漏，請改寫 sub_queries 使其更貼近使用者原始問題的核心。
3. 若先前沒有 professor_query（null），輸出 professor_query 為 null。
4. 子問題一樣必須用英文撰寫。
5. 只輸出 JSON，禁止輸出任何說明文字：

{{"sub_queries": ["revised sub-query 1", ...], "professor_query": {{"name": "...", "school": "...", "school_id": "..."}} or null}}
"""


def refiner_node(state: AgentState) -> dict:
    """
    Verifier 判定資料不足時，參考 insufficiency_reason 重新改寫子問題 / 教授查詢，
    交由 route_to_retrieval 重新跑一次 search / extension_function。最多執行 1 輪。
    """
    query           = state["original_query"]
    reason          = state.get("insufficiency_reason", "")
    sub_queries     = state.get("sub_queries", [])
    professor_query = state.get("professor_query")
    retry_count     = state.get("retry_count", 0)

    print(f"\n[Refiner] 資料不足（{reason or '無具體原因'}），重新產生查詢（第 {retry_count + 1} 次重試）")
    _emit({"type": "thinking", "step": "refine"})

    try:
        parsed = _parse_json_object(_call_llm(_build_refine_prompt(query, reason, sub_queries, professor_query)))

        new_sub_queries = [
            str(q).strip() for q in parsed.get("sub_queries", []) if str(q).strip()
        ] or sub_queries

        new_professor_query = _parse_professor_query(parsed.get("professor_query")) or professor_query
    except Exception as e:
        print(f"[Refiner] 解析失敗（{e}），沿用原本查詢重試")
        new_sub_queries     = sub_queries
        new_professor_query = professor_query

    for i, q in enumerate(new_sub_queries, 1):
        print(f"  Q{i}: {q}")

    return {
        "sub_queries":     new_sub_queries,
        "professor_query": new_professor_query,
        "retry_count":     retry_count + 1,
    }


# ─── Node 5：Finalizer ────────────────────────────────────────────────────────

def finalizer_node(state: AgentState) -> dict:
    """
    使用 Verifier 判斷過的資料生成最終答案。
    若 Verifier 判定資料不足/文不對題，直接誠實告知並發送 answer 事件（不需要 Critic 複查）。
    若成功生成答案，交給 Critic 檢查有無幻覺後，再由 Critic 發送最終的 answer 事件。
    """
    query = state["original_query"]
    all_docs = state.get("verified_docs", [])

    print(f"\n[Finalizer] Verifier 判斷後的資料：{len(all_docs)} 筆")

    if not all_docs:
        mentioned_ids   = state.get("mentioned_school_ids", [])
        mentioned_names = state.get("mentioned_school_names", [])
        known = get_known_school_ids()

        # school_ids 已限定在已知別名清單內，該清單與 DB 收錄範圍同步，故其恆在 known 中；
        # 真正判斷「有沒有收錄」要看 mentioned_school_names（含清單外的學校原文）。
        unknown_ids   = [sid for sid in mentioned_ids if sid not in known]
        unknown_names = mentioned_names if not mentioned_ids else []

        unrecognized = unknown_ids + unknown_names

        if unrecognized:
            names = "、".join(unrecognized)
            answer = f"很抱歉，目前系統尚未收錄 {names} 的資料，暫時無法回答此問題。建議您直接前往該校官方網站查詢。"
        else:
            answer = "很抱歉，資料庫中查無與此問題相關的欄位資訊。建議您直接前往各校官方網站查詢。"

        _emit({"type": "answer", "text": answer})
        return {"final_answer": answer}

    if not state.get("is_sufficient", True):
        reason = state.get("insufficiency_reason", "").strip()
        answer = "很抱歉，目前檢索到的資料與您的問題不符" + (f"（{reason}）" if reason else "") + "，暫時無法提供可靠的回答。建議您直接前往官方網站查詢。"
        _emit({"type": "answer", "text": answer})
        return {"final_answer": answer}

    _check_cancel()
    _emit({"type": "llm_call", "purpose": "finalizer"})

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

    if full_text:
        import re as _re
        clean = full_text.replace("**", "")
        clean = _re.sub(
            r"<span[^>]*>\s*(\[[^\]]+\]\([^\)]+\))\s*</span>",
            r"\1", clean, flags=_re.IGNORECASE,
        )
        clean = _re.sub(r"</?span[^>]*>", "", clean, flags=_re.IGNORECASE)
        clean = clean.strip()
        return {"final_answer": clean, "generated_answer": True}
    else:
        msg = "OpenAI 生成失敗"
        print(f"[Finalizer] {msg}")
        _emit({"type": "error", "message": msg})
        return {"final_answer": None}


def after_finalize(state: AgentState) -> str:
    """只有真的呼叫 LLM 生成過答案，才需要跑 Critic 複查；其餘（誠實告知/生成失敗）直接結束。"""
    if state.get("generated_answer", False):
        return "critic"
    return END


# ─── Node 6：Critic ───────────────────────────────────────────────────────────

def _build_critic_prompt(answer: str, context_text: str) -> str:
    return f"""你是一個答案品質稽核員，負責檢查「已生成的回答」內容是否都能在「參考資料」中找到根據。

【判斷重點】
1. 只關注具體、可查證的陳述：數字（GPA/TOEFL/GRE/學費等）、日期、政策規定、教授研究方向等事實性內容。
2. 一般性的總結、建議語句（例如「建議您查詢官網確認」）不需要查證，不算問題。
3. 只有在你確信某個具體陳述在參考資料中找不到對應根據時，才視為有問題（has_issue=true）。
4. 若答案內容都能對應到參考資料，或只是措辭上的合理歸納整理，視為沒有問題。

【已生成的回答】
{answer}

【參考資料】
{context_text}

【輸出格式（嚴格遵守）】
只輸出 JSON，禁止輸出任何說明文字：

{{"has_issue": true or false, "issue_summary": "若 has_issue=true，用一句話簡短說明哪部分內容缺乏根據；否則留空字串"}}
"""


def critic_node(state: AgentState) -> dict:
    """
    在答案生成後、回傳給使用者前，檢查是否有內容在 verified_docs 中找不到根據（幻覺）。
    只標記警告、不重新生成——發現問題就直接把警告附在答案後面，交由使用者自行確認。
    """
    answer   = state.get("final_answer", "") or ""
    all_docs = state.get("verified_docs", [])

    if not answer or not all_docs:
        _emit({"type": "answer", "text": answer})
        return {"final_answer": answer}

    _check_cancel()
    context_text = format_context_for_prompt(all_docs)

    try:
        parsed        = _parse_json_object(_call_llm(_build_critic_prompt(answer, context_text)))
        has_issue     = bool(parsed.get("has_issue", False))
        issue_summary = str(parsed.get("issue_summary", "")).strip()
    except Exception as e:
        print(f"[Critic] 判斷失敗（{e}），略過品質檢查")
        has_issue     = False
        issue_summary = ""

    if has_issue:
        print(f"[Critic] 發現潛在問題：{issue_summary}")
        final_text = answer + f"\n\n⚠️ 提醒：{issue_summary or '部分內容可能未完全對應參考資料，建議自行確認。'}"
    else:
        print("[Critic] 未發現幻覺/文不對題問題")
        final_text = answer

    _emit({"type": "answer", "text": final_text})
    return {"final_answer": final_text}


# ─── 路由 & 圖建構 ────────────────────────────────────────────────────────────

def _build_graph():
    """
    流程：
      decompose
        ├─ Send → search ──────────────────────────┐
        │                                          ▼
        └─ Send → extension_function ──────────► verify ─┬─→ finalize ─┬─→ critic → END
                                                           │             │
                                          （is_sufficient=false 且尚未重試過）（誠實告知 / 生成失敗，不需要 Critic）
                                                           ▼             └─→ END
                                                        refine
                                                           │
                                            ┌─ Send → search ┤
                                            └─ Send → extension_function
                                                （回到 verify 再判斷一次，最多 1 輪）

    verify 判斷資料是否文不對題/足夠：足夠就交給 finalize；不足夠但曾檢索到資料且還沒重試過，
    交給 refine 依 insufficiency_reason 重新改寫查詢，再跑一次 search/extension_function → verify；
    仍不足或本來就查無資料，直接交給 finalize 誠實告知，最多重試 1 輪，不會無限迴圈。
    finalize 只在真的呼叫 LLM 生成出答案時才交給 critic 複查有無幻覺；誠實告知/生成失敗則直接結束。
    """
    builder = StateGraph(AgentState)

    builder.add_node("decompose",          decomposer_node)
    builder.add_node("extension_function", extension_function_node)
    builder.add_node("search",             searcher_node)
    builder.add_node("verify",             verifier_node)
    builder.add_node("refine",             refiner_node)
    builder.add_node("finalize",           finalizer_node)
    builder.add_node("critic",             critic_node)

    builder.set_entry_point("decompose")

    builder.add_conditional_edges("decompose", route_to_retrieval)
    builder.add_conditional_edges("refine",    route_to_retrieval)

    builder.add_edge("extension_function", "verify")
    builder.add_edge("search", "verify")
    builder.add_conditional_edges("verify", after_verify)

    builder.add_conditional_edges("finalize", after_finalize)
    builder.add_edge("critic", END)

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
            print(f"{'='*60}")

        initial_state: AgentState = {
            "original_query":    query,
            "sub_queries":       [],
            "collected_docs":    [],
            "extension_docs":    [],
            "final_answer":      "",
            "professor_query":   None,
            "needs_sql_search":  True,
            "mentioned_school_ids": [],
            "mentioned_school_names": [],
            "verified_docs":      [],
            "is_sufficient":      True,
            "insufficiency_reason": "",
            "retry_count":        0,
            "generated_answer":   False,
        }

        result_state = _get_graph().invoke(initial_state, {"recursion_limit": max_steps * 4})

        if verbose:
            total_docs = len(_deduplicate_docs(result_state.get("collected_docs", [])))
            print(f"\n{'='*60}")
            print(f"完成！彙整 {total_docs} 筆去重資料")
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
