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

import hashlib
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
from retriever.hybrid_search import hybrid_search_with_fallback
from retriever.applicant_search import applicant_search
from generator.openai_client import (
    call_llm, generate_answer, generate_answer_stream,
    format_context_for_prompt, clean_answer_text,
)
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
    fulltext_docs:     list[dict]    # 全文檢索結果（SQL 不足時才由 fulltext_search_node 補上）
    fulltext_done:     bool          # 是否已對本輪子問題做過全文檢索 fallback（避免重複做）
    extension_docs:    list[dict]    # 教授資料（每輪由 extension_function_node 整份覆寫，重試時不與前一輪疊加）
    experience_docs:   list[dict]    # 申請經驗回報（needs_experience 時由 experience_search_node 整份覆寫）
    final_answer:      str
    professor_query:   dict | None   # Decomposer 偵測到的教授查詢 {name, school, school_id}
    needs_sql_search:  bool         # Decomposer 判斷是否需要查 programs 申請要求資料
    needs_experience:  bool         # Decomposer 判斷是否問「錄取背景/機會/案例」，需查 applicant_reports
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
    """以 school_id + chunk_text/完整欄位內容去重複文件。

    對結構化 SQL doc 用完整內容的 hash 當 key（不截斷），避免只有尾端欄位
    （如 application_url）不同的兩筆 doc 被前綴截斷誤判為重複。
    """
    seen: set[str] = set()
    result: list[dict] = []
    for doc in docs:
        content = doc.get("chunk_text") or json.dumps(doc, sort_keys=True, default=str)
        key = f"{doc.get('school_id', '')}:{hashlib.md5(content.encode('utf-8')).hexdigest()}"
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
資料庫（programs 及其子表）只有 GPA、TOEFL/IELTS/GRE、申請截止日期、學費、獎助等「申請要求」欄位，
不包含教授的研究領域、發表論文、經歷等資訊。

- 若使用者問題只在詢問教授本人相關資訊（如研究領域、論文、背景等），且完全沒有詢問任何申請要求，
  needs_sql_search 設為 false
- 若使用者問題除了教授資訊外，還有詢問任何申請要求（GPA/TOEFL/IELTS/GRE/截止日期等），
  needs_sql_search 設為 true
- 若問題完全沒有教授查詢意圖，needs_sql_search 一律為 true

====================
【任務四：是否需要查詢申請經驗回報（needs_experience）】
====================
系統另有一批「申請經驗回報」資料（GradCafe / 一畝三分地論壇的個別錄取/被拒案例，含
申請者 GPA、結果、背景等），屬非官方、有樣本偏誤的經驗談，只適合回答「實際錄取者背景
長怎樣」「某分數/背景有沒有機會上」「往年錄取案例」這類問題。

- 若使用者問題在詢問「錄取機會 / 錄取者背景 / 案例 / 我這條件上不上得了 / 往年錄取情況」，
  needs_experience 設為 true
- 若使用者問題只問官方申請要求（GPA 門檻幾分、截止日、學費等固定規定），
  needs_experience 設為 false
- 純教授查詢時 needs_experience 一律為 false

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
  "needs_sql_search": true or false,
  "needs_experience": true or false
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
        needs_experience = bool(parsed.get("needs_experience", False))
    except Exception as e:
        print(f"[Decomposer] 意圖判斷失敗（{e}），使用原始問題作為 fallback")
        school_ids             = _detect_school_ids(query)
        mentioned_school_names = []
        professor_query         = None
        needs_sql_search        = True
        needs_experience        = False

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
    print(f"[Decomposer] needs_sql_search = {needs_sql_search}  needs_experience = {needs_experience}")

    return {
        "sub_queries":            sub_queries,
        "collected_docs":         [],
        "fulltext_docs":          [],
        "fulltext_done":          False,
        "extension_docs":         [],
        "experience_docs":        [],
        "final_answer":           "",
        "professor_query":        professor_query,
        "needs_sql_search":       needs_sql_search,
        "needs_experience":       needs_experience,
        "mentioned_school_ids":   school_ids,
        "mentioned_school_names": mentioned_school_names,
    }


def route_to_retrieval(state: AgentState):
    """
    決定要跑哪些檢索節點（decompose 完成後、以及 refine 重新產生查詢後皆共用此路由）：
    各檢索支線互相獨立，依旗標並行觸發：
    - needs_sql_search → search（text-to-SQL 官方申請要求）
    - professor_query   → extension_function（SerpAPI 教授資料）
    - needs_experience  → experience_search（applicant_reports 錄取經驗）
    至少會走一個節點；若三者皆無（理論上不會），退回只走 search 保底。
    """
    targets = []
    if state.get("needs_sql_search", True):
        targets.append(Send("search", state))
    if state.get("professor_query") is not None:
        targets.append(Send("extension_function", state))
    if state.get("needs_experience", False):
        targets.append(Send("experience_search", state))

    return targets or [Send("search", state)]


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


# ─── Node 1.6：Experience Search（申請經驗回報） ──────────────────────────────

def experience_search_node(state: AgentState) -> dict:
    """查 applicant_reports（GradCafe / 一畝三分地錄取回報），寫入 experience_docs。

    與 search / extension_function 並行；資料為非官方經驗談，generator 會加註警語。
    """
    _emit({"type": "thinking", "step": "experience_search"})

    original_query = state["original_query"]
    sub_queries    = state.get("sub_queries", []) or [original_query]
    school_ids     = _detect_school_ids(original_query)
    school_id      = school_ids[0] if school_ids else None

    exp_docs: list[dict] = []
    seen_urls: set = set()
    for q in sub_queries:
        _check_cancel()
        _emit({
            "type": "tool_call",
            "tool": "applicant_search",
            "args": {"query": q, **({"school_id": school_id} if school_id else {})},
        })
        for doc in applicant_search(q, school_id=school_id):
            key = doc.get("source_url") or doc.get("chunk_text", "")[:80]
            if key not in seen_urls:
                seen_urls.add(key)
                exp_docs.append(doc)

    _emit({
        "type": "tool_result",
        "tool": "applicant_search",
        "preview": f"找到 {len(exp_docs)} 筆申請經驗回報",
    })
    print(f"[Experience] 共取得 {len(exp_docs)} 筆申請經驗回報")
    return {"experience_docs": exp_docs}


# ─── Node 2：SQL Searcher ─────────────────────────────────────────────────────

def _search_one_query(q: str) -> list[dict]:
    """對單一子問題執行 text-to-SQL 檢索（學校過濾由 LLM 產生的 SQL WHERE 完成）。"""
    _emit({
        "type": "tool_call",
        "tool": "sql_search",
        "args": {"query": q},
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
    sub_queries = state.get("sub_queries", [])

    print(f"\n[Searcher] 共 {len(sub_queries)} 個子問題")
    _emit({"type": "thinking", "step": 1})

    new_docs: list[dict] = []

    for q in sub_queries:
        _check_cancel()
        results = _search_one_query(q)
        print(f"     查詢：{q}  取得 {len(results)} 筆（SQL）")
        new_docs.extend(results)

    print(f"\n[Searcher] 共取得 {len(new_docs)} 筆資料")

    return {"collected_docs": new_docs}


# ─── Node 2.5：Fulltext Search（SQL 不足時的 fallback）────────────────────────

def _fulltext_one_query(q: str, original_query: str) -> list[dict]:
    """對單一子問題做 document_chunks 全文檢索。"""
    school_ids = _detect_school_ids(q) or _detect_school_ids(original_query)
    school_id = school_ids[0] if school_ids else None

    _emit({
        "type": "tool_call",
        "tool": "fulltext_search",
        "args": {"query": q, **({"school_id": school_id} if school_id else {})},
    })

    results = hybrid_search_with_fallback(q, school_id=school_id)

    for item in results:
        item["query"] = q

    _emit({
        "type": "tool_result",
        "tool": "fulltext_search",
        "preview": f"全文檢索找到 {len(results)} 筆相關段落",
    })

    return results


def fulltext_search_node(state: AgentState) -> dict:
    """
    text-to-SQL 檢索結果被 Verifier 判定不足時，才對 document_chunks 做全文檢索補充。
    這是分層 fallback 的第二層：先靠 SQL 查結構化欄位，SQL 答不出來（文不對題或缺欄位）
    才擴大到原始頁面全文，把 chunk 併入 collected_docs 後回到 verify 重新判斷。
    """
    sub_queries    = state.get("sub_queries", [])
    original_query = state["original_query"]

    print(f"\n[Fulltext] SQL 結果不足，對 {len(sub_queries)} 個子問題做全文檢索 fallback")
    _emit({"type": "thinking", "step": "fulltext"})

    ft_docs: list[dict] = []
    for q in sub_queries:
        _check_cancel()
        results = _fulltext_one_query(q, original_query)
        print(f"     查詢：{q}  取得 {len(results)} 筆（全文檢索）")
        ft_docs.extend(results)

    print(f"\n[Fulltext] 共取得 {len(ft_docs)} 筆全文檢索資料")

    return {"fulltext_docs": ft_docs, "fulltext_done": True}


# ─── Node 3：Verifier ─────────────────────────────────────────────────────────

def _build_verify_prompt(query: str, context_text: str) -> str:
    return f"""你是一個檢索結果品質檢查員，負責判斷「檢索到的參考資料」是否真的能回答使用者的問題。

【判斷步驟（務必依序執行）】
第一步：先用一句話點出「問題到底想知道哪個具體資訊點」（例如：是否提供論文/非論文選項、某課程的學分數、某獎學金金額、GPA 門檻…）。
第二步：逐筆檢查參考資料，找有沒有「任何一句話實際觸及那個資訊點」。
        ⚠️ 只是「同一所學校 / 同一個 program 的其他不相干欄位」不算數。
        例如問「有沒有論文選項」，但資料只有 program_name、degree_type、GPA、TOEFL 這類欄位，
        完全沒有一句話提到論文/thesis/課程結構——這就是「命中學校但沒命中資訊點」。
第三步：
  - 若第二步找到了觸及該資訊點的內容（哪怕只是片段、需要合理推論）→ sufficient=true。
  - 若第二步「完全沒有任何一句話觸及該資訊點」→ sufficient=false，
    reason 寫「資料只有 XXX，未觸及問題問的 YYY」，讓系統改用全文檢索補真正相關的內文。

【其他原則】
- 文不對題（問 A 校給 B 校、問某教授給同名他人）一律 sufficient=false。
- 經驗回報也算有效資料：若問題在問「錄取機會 / 錄取者背景 / 案例 / 某分數有沒有機會」，
  而參考資料中有標示為「網路申請經驗回報（非官方，個別案例）」的資料且觸及了該校該科系的
  錄取案例（含申請者 GPA、結果等），就算 sufficient=true——這類問題本來就該用經驗回報回答，
  不要因為「不是官方數據」或「只有個別案例」而判 false（回答時的非官方警語由生成階段負責）。
- 允許合理推論：資料不必逐字明講關鍵詞，能讓人合理推論出答案即可（例如問研究領域，給論文標題摘要就算足夠）。
- 允許部分足夠：只要「觸及了資訊點」，細節不全也算 true，缺的由生成階段告知使用者——但前提是真的觸及（見第二步）。
- 判 true 前最後自問：「這些資料裡，有沒有任何一句真的在回答使用者問的那件事？」若答不出具體是哪一句，就該判 false。

【使用者問題】
{query}

【檢索到的參考資料】
{context_text}

【輸出格式（嚴格遵守）】
只輸出 JSON，禁止輸出任何說明文字：

{{"sufficient": true or false, "reason": "若 sufficient=false，用一句話說明資料未觸及哪個資訊點；否則留空字串"}}
"""


def verifier_node(state: AgentState) -> dict:
    """
    在生成答案前，檢查 search + extension_function 收集到的資料是否真的與問題相關、足以回答。
    只做好壞判斷，不重試、不重新查詢——文不對題時讓 finalizer 誠實告知使用者。
    """
    query = state["original_query"]

    search_docs     = state.get("collected_docs", [])
    fulltext_docs   = state.get("fulltext_docs", [])
    extension_docs  = state.get("extension_docs", [])
    experience_docs = state.get("experience_docs", [])

    # 順序：教授 → 官方 SQL → 經驗回報 → 全文檢索補充。經驗資料排官方之後，避免喧賓奪主。
    all_docs = _deduplicate_docs(
        extension_docs + search_docs + experience_docs + fulltext_docs
    )

    if not all_docs:
        return {"verified_docs": [], "is_sufficient": False, "insufficiency_reason": ""}

    # 經驗類問題短路：needs_experience 且真的撈到經驗回報時直接放行。
    # Verifier 的「資訊點是否被觸及」檢查是為官方欄位設計的，對「無標準答案的錄取經驗題」
    # 不適用（案例本身就是答案），送審只會被誤判不足。護欄由 generator 的非官方警語負責。
    if state.get("needs_experience", False) and experience_docs:
        print(f"[Verifier] needs_experience 且有 {len(experience_docs)} 筆經驗回報，直接放行")
        return {"verified_docs": all_docs, "is_sufficient": True, "insufficiency_reason": ""}

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
    Verifier 完成後的路由（分層 fallback：SQL → 全文檢索 → 改寫重查）：
    - is_sufficient=True → finalize
    - is_sufficient=False 且「本輪還沒做過全文檢索」→ fulltext（對 document_chunks 補查後回到 verify）
    - is_sufficient=False 且「已做過全文檢索」且曾檢索到資料且尚未重試過 → refine（改寫查詢再跑一輪 SQL）
    - 完全查無資料，或已重試過仍不足 → finalize（誠實告知）
    """
    if state.get("is_sufficient", True):
        return "finalize"

    # 第一層 fallback：SQL 不足時先擴大到全文檢索（本輪只做一次）
    if not state.get("fulltext_done", False):
        return "fulltext"

    # 第二層 fallback：全文檢索仍不足，且曾檢索到資料、尚未重試過，才改寫查詢重跑
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
        "fulltext_docs":   [],      # 清掉上一輪全文檢索結果
        "fulltext_done":   False,   # 允許改寫後的新查詢再走一次全文檢索
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

        # mentioned_school_ids 與 mentioned_school_names 是兩組獨立偵測結果，可能同時存在
        # （例如同時問已知的 MIT 和清單外的 University of Toronto），故分別判斷、不互斥。
        # school_ids 已限定在已知別名清單內，該清單與 DB 收錄範圍同步，故其恆在 known 中；
        # 真正判斷「有沒有收錄」要看 mentioned_school_names（含清單外的學校原文）。
        unknown_ids   = [sid for sid in mentioned_ids if sid not in known]
        unrecognized = unknown_ids + mentioned_names

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
        clean = clean_answer_text(full_text)
        return {"final_answer": clean, "generated_answer": True}
    else:
        msg = "OpenAI 生成失敗"
        print(f"[Finalizer] {msg}")
        _emit({"type": "error", "message": msg})
        return {"final_answer": ""}


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
    流程（分層 fallback：SQL → 全文檢索 → 改寫重查）：
      decompose
        ├─ Send → search（text-to-SQL）────────────┐
        │                                          ▼
        └─ Send → extension_function ──────────► verify ─┬─→ finalize ─┬─→ critic → END
                                                     │  ▲              │
                        （不足且本輪未做過全文檢索）  │  │（誠實告知 / 生成失敗，不需要 Critic）
                                                     ▼  │              └─→ END
                                                  fulltext（document_chunks 全文檢索）
                                                     （補資料後回到 verify 再判斷一次）
                                                     │
                        （全文檢索仍不足且尚未重試）  ▼
                                                  refine → search/extension_function（最多 1 輪）

    verify 判斷資料是否文不對題/足夠：足夠就交給 finalize。
    不足時分兩層 fallback：
      1) 本輪還沒做過全文檢索 → fulltext：對 document_chunks 全文檢索補資料，回 verify 再判斷。
      2) 全文檢索仍不足且曾檢索到資料、尚未重試過 → refine：改寫查詢重跑一輪 SQL（會 reset fulltext_done）。
    仍不足或本來就查無資料，直接交給 finalize 誠實告知，最多重試 1 輪，不會無限迴圈。
    finalize 只在真的呼叫 LLM 生成出答案時才交給 critic 複查有無幻覺；誠實告知/生成失敗則直接結束。
    """
    builder = StateGraph(AgentState)

    builder.add_node("decompose",          decomposer_node)
    builder.add_node("extension_function", extension_function_node)
    builder.add_node("experience_search",  experience_search_node)
    builder.add_node("search",             searcher_node)
    builder.add_node("fulltext",           fulltext_search_node)
    builder.add_node("verify",             verifier_node)
    builder.add_node("refine",             refiner_node)
    builder.add_node("finalize",           finalizer_node)
    builder.add_node("critic",             critic_node)

    builder.set_entry_point("decompose")

    builder.add_conditional_edges("decompose", route_to_retrieval)
    builder.add_conditional_edges("refine",    route_to_retrieval)

    builder.add_edge("extension_function", "verify")
    builder.add_edge("experience_search", "verify")
    builder.add_edge("search", "verify")
    builder.add_edge("fulltext", "verify")
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
        max_steps: 安全閥。實際 recursion_limit = max_steps * 4
                   （一輪 decompose→retrieval→verify→fulltext→finalize→critic 約需數個節點步）
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
            "fulltext_docs":     [],
            "fulltext_done":     False,
            "extension_docs":    [],
            "experience_docs":   [],
            "final_answer":      "",
            "professor_query":   None,
            "needs_sql_search":  True,
            "needs_experience":  False,
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
            total_docs = len(result_state.get("verified_docs", []))
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
    parser.add_argument("--max-steps", type=int, default=20,
                        help="安全閥（實際 recursion_limit = max_steps * 4）")
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
