from __future__ import annotations
import json
import re
from typing import Any

# ── 與目標校的 school_id 對照：experience key → stats school_id ──────────────
# experience 資料的 key 是大寫縮寫，需要對應到 distribution 的 school_id
_EXP_KEY_TO_SCHOOL_ID: dict[str, str] = {
    "CMU":          "cmu",
    "UCLA":         "ucla",
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
    "NORTHWESTERN": "johnsh",
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
_ADMIT_KEYWORDS  = ("AD", "Offer", "admit", "錄取")
_REJECT_KEYWORDS = ("Rej", "拒", "Withdraw", "WL")


def _parse_score(raw: Any) -> float | None:
    """
    從各種格式解析出第一個數字分數。
    支援：3.85、"3.85"、"3.98||3.83"（取第一個）、None
    """
    if raw is None:
        return None
    s = str(raw).strip()
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
    for exp in exps[:max_items * 3]:
        result = exp.get("result", "")
        gpa    = _parse_score(exp.get("gpa"))
        toefl  = _parse_score(exp.get("toefl"))
        gre    = _parse_score(exp.get("gre"))
        major  = exp.get("major", "?")
        bg     = exp.get("undergrad_school", "")

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

    # ── 1. 建立統計摘要文字 ───────────────────────────────────────────────────
    stats_lines: list[str] = []
    valid_school_ids: set[str] = set()

    for school_id, info in admission_stats.items():
        mgpa   = info.get("median_gpa")
        mgre   = info.get("median_gre")
        mtoefl = info.get("median_toefl")
        if not mgpa:
            continue

        valid_school_ids.add(school_id)
        parts = [f"GPA中位={mgpa}"]
        if mgre:   parts.append(f"GRE中位={mgre}")
        if mtoefl: parts.append(f"TOEFL中位={mtoefl}")
        stats_lines.append(f"  - school_id={school_id}: {', '.join(parts)}")

    stats_text = "\n".join(stats_lines) if stats_lines else "（無統計資料）"

    # ── 2. 建立申請經驗摘要文字 ──────────────────────────────────────────────
    exp_sections: list[str] = []

    for exp_key, exps in admission_exp.items():
        school_id = _EXP_KEY_TO_SCHOOL_ID.get(exp_key)
        if school_id not in valid_school_ids:
            continue

        summary = _summarize_exp(school_id, exps)
        if "無足夠" not in summary:
            exp_sections.append(f"[{school_id}]\n{summary}")

    exp_text = "\n\n".join(exp_sections) if exp_sections else "（無申請經驗資料）"

    # ── 3. 呼叫 LLM ──────────────────────────────────────────────────────────
    prompt = _build_prompt(profile, stats_text, exp_text, top_k)

    if _llm_call_fn is None:
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

    # 過濾不在 valid_school_ids 的結果，保留順序，附帶推薦理由
    result: list[dict] = []
    seen: set[str] = set()
    for rec in recs:
        sid    = rec.get("school_id", "")
        reason = rec.get("reason", "")
        if sid in valid_school_ids and sid not in seen:
            seen.add(sid)
            result.append({"school_id": sid, "reason": reason})
            print(f"  推薦 {sid}：{reason}")

    result = result[:top_k]
    print(f"[search_alternative] 最終推薦：{[r['school_id'] for r in result]}")
    return result
