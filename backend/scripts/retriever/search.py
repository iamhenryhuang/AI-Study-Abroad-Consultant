from __future__ import annotations
import json
import re
from typing import Any
import sys
from pathlib import Path

# 修復 Windows 控制台 UTF-8 輸出
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from db.connection import get_connection
from embedder.vectorize import embed_texts
from retriever.reranker import rerank


# ── 內部工具 ────────────────────────────────────────────────────────────────

def _build_filter_clauses(school_id: str | None) -> tuple[str, str, list]:
    """
    依 school_id 建立 WHERE 與 AND 子句及對應參數列表。

    Returns:
        (where_clause, and_where_clause, params)
    """
    if school_id:
        return "WHERE dc.school_id = %s", "AND dc.school_id = %s", [school_id]
    return "", "", []


def _execute_hybrid_search(
    conn,
    query_vector: list[float],
    query: str,
    where_clause: str,
    and_where_clause: str,
    filter_params: list,
    initial_k: int,
) -> list[dict]:
    """
    執行 RRF 混合搜尋 SQL，回傳候選文件列表。

    參數順序：
      1-2. query_vector（vector_matches 的分數與排名）
      3.   filter_params（vector_matches 的過濾）
      4-6. query（keyword_matches 的分數、排名與 WHERE）
      7.   filter_params（keyword_matches 的過濾）
    """
    sql = f"""
        WITH vector_matches AS (
            SELECT
                id,
                1 - (embedding <=> %s::vector) AS vector_score,
                ROW_NUMBER() OVER (ORDER BY embedding <=> %s::vector) AS rank
            FROM document_chunks dc
            {where_clause}
            LIMIT {initial_k}
        ),
        keyword_matches AS (
            SELECT
                id,
                ts_rank_cd(fts_vector, websearch_to_tsquery('simple', %s)) AS fts_score,
                ROW_NUMBER() OVER (
                    ORDER BY ts_rank_cd(fts_vector, websearch_to_tsquery('simple', %s)) DESC
                ) AS rank
            FROM document_chunks dc
            WHERE fts_vector @@ websearch_to_tsquery('simple', %s)
            {and_where_clause}
            LIMIT {initial_k}
        )
        SELECT
            dc.chunk_text,
            dc.source_url,
            dc.passed_types,
            dc.school_id,
            u.name AS university_name,
            COALESCE(vm.vector_score, 0) AS vector_score,
            COALESCE(km.fts_score, 0)   AS fts_score,
            (COALESCE(1.0 / (60 + vm.rank), 0) + COALESCE(1.0 / (60 + km.rank), 0)) AS rrf_score
        FROM document_chunks dc
        JOIN universities u ON dc.university_id = u.id
        LEFT JOIN vector_matches vm ON dc.id = vm.id
        LEFT JOIN keyword_matches km ON dc.id = km.id
        WHERE vm.id IS NOT NULL OR km.id IS NOT NULL
        ORDER BY rrf_score DESC
        LIMIT {initial_k};
    """

    full_params = (
        [query_vector, query_vector]
        + filter_params
        + [query, query, query]
        + filter_params
    )

    with conn.cursor() as cur:
        cur.execute(sql, full_params)
        colnames = [desc[0] for desc in cur.description]
        rows = cur.fetchall()

    return [dict(zip(colnames, row)) for row in rows]

#---------------------新增的----------------------------------------------------------

"""
search_alternative.py

LLM 版備案學校推薦。

輸入：
  - profile        : {"gpa": float|None, "toefl": float|None, "gre": float|None, ...}
  - admission_stats: {school_id: {"median_gpa": ..., "median_gre": ..., "median_toefl": ..., ...}}
  - admission_exp  : {school_key: [{"result": ..., "gpa": ..., "toefl": ..., "gre": ..., ...}, ...]}
  - top_k          : 最多回傳幾所學校（預設 3）

輸出：list[str]  ← school_id 列表，依推薦優先度排序
"""

# ── 與目標校的 school_id 對照：experience key → stats school_id ──────────────
# experience 資料的 key 是大寫縮寫，需要對應到 distribution 的 school_id
_EXP_KEY_TO_SCHOOL_ID: dict[str, str] = {
    "CMU":          "cmu",
    "UCLA":         "ucla",       # 若 stats 有 UCLA 可加
    "UCSD":         "ucsd",
    "UCI":          "ucirving",
    "UIUC":         "uiuc",
    "COLUMBIA":     "columbia",
    "Columbia":     "columbia",
    "NYU":          "nyu",
    "CORNELL":      "cornell",
    "BROWN":        "brown",
    "DUKE":         "duke",
    "duke":         "duke",
    "YALE":         "yale",
    "USC":          "usc",
    "usc":          "usc",
    "JHU":          "johnsh",
    "NORTHWESTERN": "johnsh",     # 合併於同一筆統計
    "BU":           "boston",
    "Boston University": "boston",
    "WUSTL":        "washingtonstouis",
    "UCHICAGO":     "uchicago",
    "University of Illinois, Urbana-Champaign": "uiuc",
    "Georgia Institute of Technology": "gatech",
    "Georgia Tech": "gatech",
    "gatech":       "gatech",
    "GATECH":       "gatech",
}

# 接受的錄取結果關鍵字（排除 Reject / Withdraw）
_ADMIT_KEYWORDS = ("AD", "Offer", "admit", "錄取")
_REJECT_KEYWORDS = ("Rej", "拒", "Withdraw", "WL")


def _parse_score(raw: Any) -> float | None:
    """
    從各種格式解析出第一個數字分數。
    支援：3.85、"3.85"、"3.98||3.83"（取第一個）、None
    """
    if raw is None:
        return None
    s = str(raw).strip()
    # 取第一段（以 || 或空格分隔）
    first = re.split(r"\|\||\s+", s)[0]
    try:
        return float(first)
    except ValueError:
        return None


def _is_admitted(result: str | None) -> bool:
    """判斷這筆經驗是否為錄取。"""
    if not result:
        return False
    r = result.upper()
    return any(kw.upper() in r for kw in _ADMIT_KEYWORDS) and not any(
        kw.upper() in r for kw in _REJECT_KEYWORDS
    )


def _summarize_exp(school_id: str, exps: list[dict], max_items: int = 6) -> str:
    """
    從 admission_exp 中整理該校的申請經驗摘要，
    優先挑有分數的、且結果明確（AD / Rej）的紀錄。
    回傳給 LLM 閱讀的純文字。
    """
    lines: list[str] = []
    for exp in exps[:max_items * 3]:  # 多取幾筆再篩
        result = exp.get("result", "")
        gpa    = _parse_score(exp.get("gpa"))
        toefl  = _parse_score(exp.get("toefl"))
        gre    = _parse_score(exp.get("gre"))
        major  = exp.get("major", "?")
        bg     = exp.get("undergrad_school", "")

        # 至少要有一個分數才有參考價值
        if gpa is None and toefl is None and gre is None:
            continue

        score_parts = []
        if gpa:   score_parts.append(f"GPA {gpa:.2f}")
        if toefl: score_parts.append(f"TOEFL {toefl:.0f}")
        if gre:   score_parts.append(f"GRE {gre:.0f}")

        admitted = "✅錄取" if _is_admitted(result) else "❌拒絕/其他"
        lines.append(
            f"  - {admitted} | {major} | {', '.join(score_parts)} | 背景:{bg}"
        )
        if len(lines) >= max_items:
            break

    if not lines:
        return "  （無足夠經驗資料）"
    return "\n".join(lines)


def _build_prompt(
    profile: dict,
    stats_text: str,
    exp_text: str,
    top_k: int,
) -> str:
    profile_text = json.dumps(
        {k: v for k, v in profile.items() if v is not None},
        ensure_ascii=False,
    ) if profile else "（未提供分數）"

    return f"""你是一位專業留學申請顧問，請根據學生背景與各校資料，推薦最適合的 {top_k} 所備案學校。

【學生背景】
{profile_text}

【各校錄取中位數統計】
{stats_text}

【各校真實申請經驗（含分數與錄取結果）】
{exp_text}

【推薦原則】
1. 優先推薦「學生分數接近或略高於該校中位數」的學校（勝算較高）。
2. 參考真實申請經驗：若有與學生背景相近的申請者拿到錄取，該校優先推薦。
3. 避免推薦明顯超出學生能力範圍（中位數高出 0.3 GPA 以上）的學校。
4. 回傳的學校必須是統計資料中存在的（使用 school_id）。
5. 若學生分數明顯偏高，可推薦稍有挑戰性但仍合理的學校。

輸出格式（JSON）：
{{
  "recommendations": [
    {{
      "school_id": "xxx",
      "reason": "1句說明推薦原因，含與學生分數的對比"
    }},
    ...
  ]
}}

只輸出 JSON，不要有其他文字。"""


def search_alternative(
    profile: dict,
    admission_stats: dict,
    admission_exp: dict,
    top_k: int = 3,
    _llm_call_fn=None,
) -> list[str]:
    """
    LLM 版備案學校推薦。

    Args:
        profile        : 學生背景，e.g. {"gpa": 3.6, "toefl": 100, "gre": 318}
        admission_stats: {school_id: {"median_gpa": ..., "median_gre": ..., ...}}
        admission_exp  : {exp_key: [{"result": ..., "gpa": ..., ...}]}
        top_k          : 最多推薦幾所學校
        _llm_call_fn   : 可注入自訂 LLM 呼叫函式（預設使用 Gemini）

    Returns:
        推薦的 school_id 列表，依優先度排序
    """
    print("[search_alternative] 開始 LLM 備案推薦")
    print(f"  學生背景：{profile}")

    # ── 1. 建立統計摘要文字 ──────────────────────────────────────────────────
    stats_lines: list[str] = []
    valid_school_ids: set[str] = set()

    for school_id, info in admission_stats.items():
        mgpa  = info.get("median_gpa")
        mgre  = info.get("median_gre")
        mtoefl = info.get("median_toefl")
        if not mgpa:
            continue  # 無中位數資料則略過

        valid_school_ids.add(school_id)
        parts = [f"GPA中位={mgpa}"]
        if mgre:   parts.append(f"GRE中位={mgre}")
        if mtoefl: parts.append(f"TOEFL中位={mtoefl}")
        stats_lines.append(f"  - school_id={school_id}: {', '.join(parts)}")

    stats_text = "\n".join(stats_lines) if stats_lines else "（無統計資料）"

    print(f"[search_alternative] stats_text : {stats_text}")

    # ── 2. 建立申請經驗摘要文字 ──────────────────────────────────────────────
    exp_sections: list[str] = []

    for exp_key, exps in admission_exp.items():
        # 對照到 school_id
        print(f"[search_alternative] exp_key: {exp_key}, exps: {exps}")
        school_id = _EXP_KEY_TO_SCHOOL_ID.get(exp_key)
        print(f"[search_alternative] stats_ext school_id: {school_id}")

        if school_id not in valid_school_ids:
            continue  # 只整理有統計資料的學校

        summary = _summarize_exp(school_id, exps)
        if "無足夠" not in summary:
            exp_sections.append(f"[{school_id}]\n{summary}")

    exp_text = "\n\n".join(exp_sections) if exp_sections else "（無申請經驗資料）"

    print(f"[search_alternative] exp_text : {exp_text}")

    # ── 3. 呼叫 LLM ──────────────────────────────────────────────────────────
    prompt = _build_prompt(profile, stats_text, exp_text, top_k)

    if _llm_call_fn is None:
        # 預設使用與 agent.py 相同的 Gemini helper
        try:
            from generator.gemini import get_gemini_client
            client = get_gemini_client()
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
            )
            raw = (response.text or "").strip()
        except Exception as e:
            print(f"[search_alternative] Gemini 呼叫失敗：{e}")
            return []
    else:
        raw = _llm_call_fn(prompt)
    
    print(f"[search_alternative] 呼叫llm : {raw}")

    # ── 4. 解析 JSON ──────────────────────────────────────────────────────────
    try:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            raise ValueError("找不到 JSON")
        parsed = json.loads(match.group())
        recs   = parsed.get("recommendations", [])
    except Exception as e:
        print(f"[search_alternative] JSON 解析失敗：{e}")
        print(f"  LLM 原始輸出：{raw[:300]}")
        return []

    # 過濾不在 valid_school_ids 的結果，保留順序
    result: list[str] = []
    for rec in recs:
        sid    = rec.get("school_id", "")
        reason = rec.get("reason", "")
        if sid in valid_school_ids and sid not in result:
            result.append(sid)
            print(f"  ✅ 推薦 {sid}：{reason}")

    result = result[:top_k]
    print(f"[search_alternative] 最終推薦：{result}")
    return result

# ── 公開 API ────────────────────────────────────────────────────────────────

def search_core(
    query: str,
    top_k: int = 5,
    use_rerank: bool = True,
    school_id: str | None = None,
) -> list[dict]:
    """
    執行向量 + 關鍵字混合檢索，回傳排序後的文件列表。

    Args:
        query:      使用者查詢字串
        top_k:      最終返回筆數
        use_rerank: 是否啟用 Cross-Encoder 重排序
        school_id:  若指定則只搜尋該學校（e.g. 'cmu'）

    Returns:
        list of dict，每筆包含 chunk_text、source_url、passed_types、
        school_id、university_name、vector_score、rerank_score。
    """
    query_vector = embed_texts([query])
    if not query_vector:
        return []

    conn = get_connection()
    if not conn:
        return []

    try:
        initial_k = top_k * 4 if use_rerank else top_k * 2
        where_clause, and_where_clause, filter_params = _build_filter_clauses(school_id)

        candidates = _execute_hybrid_search(
            conn, query_vector[0], query,
            where_clause, and_where_clause, filter_params,
            initial_k,
        )

        if not candidates:
            return []

        if use_rerank and len(candidates) > top_k:
            try:
                return rerank(query, candidates, top_n=top_k)
            except Exception as e:
                print(f"[search] 重排序失敗，改用初篩結果：{e}")

        return candidates[:top_k]

    except Exception as e:
        print(f"搜尋核心出錯: {e}")
        import traceback
        traceback.print_exc()
        return []
    finally:
        conn.close()


def run_search(
    query: str,
    top_k: int = 5,
    use_rerank: bool = True,
    school_id: str | None = None,
) -> bool:
    """執行向量搜尋並印出結果（CLI 用）。"""
    print(f"\n正在檢索：'{query}'")
    if school_id:
        print(f"   [過濾] 學校: {school_id}")
    print("   [1/2] 執行向量搜尋...")

    results = search_core(query, top_k=top_k, use_rerank=use_rerank, school_id=school_id)

    if not results:
        print("查無相關資料。")
        return True

    print(f"   [2/2] 完成，共 {len(results)} 筆結果\n")
    print("=" * 80)

    for i, res in enumerate(results, 1):
        score_str = f"向量分數: {res['vector_score']:.4f}"
        if "rerank_score" in res:
            score_str += f"  Re-rank: {res['rerank_score']:.4f}"
        types_str = ", ".join(
            f"{pt['type']}({pt['score']})" for pt in res.get("passed_types") or []
        )
        print(
            f"【結果 {i}】 {score_str}\n"
            f"  學校: {res['university_name']} ({res['school_id']})\n"
            f"  類型: {types_str or 'unknown'}\n"
            f"  來源 URL: {res.get('source_url', 'N/A')}\n"
            f"  內容摘要: {res['chunk_text'].strip()[:500]}...\n"
            + "-" * 80
        )

    return True


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="向量檢索")
    parser.add_argument("query",     nargs="?", help="查詢字串")
    parser.add_argument("--top-k",   type=int, default=5, help="回傳筆數")
    parser.add_argument("--school",  type=str, default=None, help="限定學校 e.g. cmu")
    parser.add_argument("--no-rerank", action="store_true", help="關閉重排序")
    args = parser.parse_args()

    q = args.query or input("請輸入查詢: ").strip()
    if q:
        run_search(q, top_k=args.top_k, use_rerank=not args.no_rerank, school_id=args.school)
    else:
        print("未輸入查詢。")