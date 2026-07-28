"""Professor, experience, SQL, and full-text retrieval nodes."""

from __future__ import annotations

from professor_fetcher.fetch_for_agent import run_professor_fetch
from retriever.applicant_search import applicant_search
from retriever.hybrid_search import hybrid_search_with_fallback
from retriever.professor_list_search import professor_list_search
from retriever.sql_search import sql_search
from retriever.experience_crawl import SPARSE_THRESHOLD, maybe_enqueue_crawl
from retriever.recommend import recommend, fetch_nearby_cases

from ..state import AgentState, _check_cancel, _detect_school_ids, _emit

def extension_function_node(state: AgentState) -> dict:
    """擴充功能節點（與 search 並行）：抓取教授資料，寫入 extension_docs。

    處理兩種互斥的教授查詢：
    - professor_query：指名教授，走 SerpAPI/Google Scholar 抓研究細節
    - professor_list_query：只給學校，查 professors 表列出該校教授名單
    """
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

    professor_list_query = state.get("professor_list_query")
    if professor_list_query is not None:
        school_id = professor_list_query.get("school_id")
        print(f"[Extension] 查詢教授名單：{school_id}")
        _emit({
            "type": "tool_call",
            "tool": "professor_list_search",
            "args": {"school_id": school_id},
        })
        docs = professor_list_search(school_id)
        _emit({
            "type": "tool_result",
            "tool": "professor_list_search",
            "preview": f"找到 {len(docs)} 位教授",
        })
        if docs:
            extension_docs.extend(docs)
        else:
            print(f"[Extension] {school_id} 無教授名單資料")

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
    fallback_ids   = _detect_school_ids(original_query)
    fallback_id    = fallback_ids[0] if fallback_ids else None

    exp_docs: list[dict] = []
    seen_urls: set = set()
    sparse_school_ids: set = set()
    for q in sub_queries:
        _check_cancel()
        # 每個子問題各自偵測學校，避免多校問題（如「A 跟 B 哪個容易上」）被
        # 整句偵測到的第一間學校蓋掉其他子問題的學校，導致其他學校查不到資料。
        q_school_ids = _detect_school_ids(q)
        school_id = q_school_ids[0] if q_school_ids else fallback_id
        _emit({
            "type": "tool_call",
            "tool": "applicant_search",
            "args": {"query": q, **({"school_id": school_id} if school_id else {})},
        })
        docs = applicant_search(q, school_id=school_id)
        if school_id is not None and len(docs) < SPARSE_THRESHOLD:
            sparse_school_ids.add(school_id)
        for doc in docs:
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

    # 有子問題鎖定學校但該校資料不足 → 標記 sparse（答案加註）並在背景補爬該校 GradCafe
    sparse = bool(sparse_school_ids)
    for sid in sparse_school_ids:
        print(f"[Experience] {sid} 資料不足（<{SPARSE_THRESHOLD}），排背景補爬")
        maybe_enqueue_crawl(sid)

    return {"experience_docs": exp_docs, "experience_sparse": sparse}


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


def recommend_node(state: AgentState) -> dict:
    """依 profile 分級推薦學校（衝刺/適中/保底）+ 真實案例佐證，寫入 recommend_docs。"""
    _emit({"type": "thinking", "step": "recommend"})
    profile = state.get("profile") or {}
    docs: list[dict] = []
    try:
        tiers = recommend(profile)
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
    except Exception as e:
        print(f"[Recommend] 推薦失敗：{e}")
        return {"recommend_docs": []}
    _emit({"type": "tool_result", "tool": "recommend",
           "preview": f"分級推薦 {len(docs)} 所學校"})
    print(f"[Recommend] 產出 {len(docs)} 筆推薦")
    return {"recommend_docs": docs}
