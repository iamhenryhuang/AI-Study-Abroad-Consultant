"""Query decomposition and retrieval routing nodes."""

from __future__ import annotations

from langgraph.types import Send

from ..prompts import _build_intent_prompt, _build_subquery_prompt
from ..state import AgentState, _SCHOOL_ALIASES, _detect_school_ids
from .common import _call_llm, _parse_json_object, _parse_professor_query

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

