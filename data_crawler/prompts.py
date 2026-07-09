"""LLM prompt 模板。

分類清單（與使用者定案的 6+2）：
  保留：admissions / deadlines / program / tuition / scholarship / faq
  丟棄：faculty / other
多標籤：一頁可同時屬於多個保留類型。
"""
import json

from .state import KEEP_TYPES, DROP_TYPES

_TYPE_DESCRIPTIONS = """
- admissions: 研究所申請方式、申請要求（GPA/GRE/TOEFL/IELTS）、申請流程、資格條件、備審清單
- deadlines: 申請截止日期、各學期申請時程（若頁面主要在講日期時程，才標此類；一般申請頁順帶提到日期可同時標 admissions+deadlines）
- program: 學位學程介紹、課程規劃、學分/學位要求、學程比較（MS/MEng/PhD）
- tuition: 學費、雜費、就學成本
- scholarship: 獎學金、fellowship、助學金、financial aid、TA/RA 資助
- faq: 常見問答（FAQ 型式的頁面或段落）
- faculty: 教授、研究人員、人員名錄（此類會被丟棄，不做後續抽取）
- other: 與研究所申請無關（新聞、活動、校園生活、大學部、無實質內容的導覽頁等）
""".strip()


def classification_prompt(url: str, title: str, url_path_bonuses: dict, text_excerpt: str) -> str:
    hint_lines = ", ".join(f"{k}={v:+.1f}" for k, v in url_path_bonuses.items() if v)
    hint_part = f"URL 路徑規則加分參考（僅供參考，最終判斷以內文為準）：{hint_lines}\n" if hint_lines else ""
    return f"""你是升學資訊爬蟲的頁面分類器。判斷以下網頁內容是否含「北美研究所申請」相關資訊，並做多標籤分類。

類型定義：
{_TYPE_DESCRIPTIONS}

URL: {url}
標題: {title}
{hint_part}
頁面內容（節錄）:
---
{text_excerpt}
---

回傳 JSON（不要多餘文字）：
{{
  "is_relevant": true/false,          // 是否含研究所申請相關資訊（faculty/other 視為 false）
  "types": [                          // 多標籤，只列出真的符合的類型；不相關時列 ["other"] 或 ["faculty"]
    {{"type": "<{"/".join(KEEP_TYPES + DROP_TYPES)}>", "confidence": 0.0-1.0}}
  ],
  "reason": "一句話說明判斷依據"
}}"""


def identify_programs_prompt(url: str, title: str, text_excerpt: str, known_programs: list[str]) -> str:
    known = json.dumps(known_programs, ensure_ascii=False) if known_programs else "[]"
    return f"""判斷以下頁面內容對應到哪個（或哪些）研究所學位 program。

規則：
- program_code 格式為「<領域縮寫> <學位>」，例如 "CS MS"、"CS PhD"、"ECE MS"、"DS MS"
- 該校已知的 program_code 清單：{known}（若頁面明顯屬於既有代碼請沿用，不要另創同義代碼）
- 若頁面是全校/全院通用的申請資訊（不特定 program），回傳 school_wide=true 且 programs 留空
- 若完全判斷不出，programs 留空、school_wide=false

URL: {url}
標題: {title}
頁面內容（節錄）:
---
{text_excerpt}
---

回傳 JSON：
{{
  "school_wide": true/false,
  "programs": [
    {{"program_code": "CS MS", "degree_type": "MS", "program_name": "Master of Science in Computer Science", "department": "Computer Science"}}
  ]
}}"""


_EXTRACTION_SCHEMA = """
{
  "programs": [
    {
      "program_code": "CS MS",
      "fields": {
        "toefl_min":         {"value": <int|null>, "source_excerpt": "<原文片段>"},
        "toefl_ibt_min":     {"value": <int|null>, "source_excerpt": ""},
        "ielts_min":         {"value": <float|null>, "source_excerpt": ""},
        "duolingo_min":      {"value": <int|null>, "source_excerpt": ""},
        "language_waiver":   {"value": <string|null>, "source_excerpt": ""},
        "gre_required":      {"value": <"required"|"optional"|"not_accepted"|null>, "source_excerpt": ""},
        "gre_quant_min":     {"value": <int|null>, "source_excerpt": ""},
        "gre_verbal_min":    {"value": <int|null>, "source_excerpt": ""},
        "gre_awa_min":       {"value": <float|null>, "source_excerpt": ""},
        "gpa_min":           {"value": <float|null>, "source_excerpt": ""},
        "gpa_scale":         {"value": <string|null>, "source_excerpt": ""},
        "gpa_note":          {"value": <string|null>, "source_excerpt": ""},
        "transcript_copies": {"value": <int|null>, "source_excerpt": ""},
        "transcript_format": {"value": <string|null>, "source_excerpt": ""},
        "rec_letter_count":  {"value": <int|null>, "source_excerpt": ""},
        "sop_word_limit":    {"value": <int|null>, "source_excerpt": ""},
        "sop_prompt":        {"value": <string|null>, "source_excerpt": ""},
        "cv_required":       {"value": <bool|null>, "source_excerpt": ""},
        "writing_sample_required": {"value": <bool|null>, "source_excerpt": ""},
        "application_fee_usd":    {"value": <int|null>, "source_excerpt": ""},
        "fee_waiver_available":   {"value": <bool|null>, "source_excerpt": ""},
        "fee_waiver_criteria":    {"value": <string|null>, "source_excerpt": ""},
        "tuition_per_year_usd":   {"value": <int|null>, "source_excerpt": ""},
        "tuition_note":           {"value": <string|null>, "source_excerpt": ""},
        "application_url":        {"value": <string|null>, "source_excerpt": ""},
        "application_system":     {"value": <string|null>, "source_excerpt": ""}
      }
    }
  ],
  "deadlines": [
    {"program_code": "CS MS", "deadline_type": "<early|regular|international|rolling>",
     "deadline_date": "YYYY-MM-DD or null", "semester": "fall_2026",
     "note": "", "source_excerpt": "<原文片段>"}
  ],
  "scholarships": [
    {"program_code": "CS MS", "name": "", "amount_usd": <int|null>,
     "coverage": "<full_tuition|partial|stipend_only|null>", "eligibility": "",
     "auto_consider": <bool|null>, "source_excerpt": "<原文片段>"}
  ],
  "app_materials": [
    {"program_code": "CS MS", "material_type": "<additional_essay|portfolio|video|writing_sample|other>",
     "requirement": "", "word_limit": <int|null>, "note": "", "source_excerpt": "<原文片段>"}
  ]
}
""".strip()


def extraction_prompt(url: str, program_codes: list[str], markdown: str,
                      feedback: str | None = None) -> str:
    feedback_part = ""
    if feedback:
        feedback_part = f"""
上一次抽取有以下驗證問題，請修正（找不到明確原文依據的欄位請設為 null）：
{feedback}
"""
    return f"""從以下頁面內容抽取研究所申請的結構化資料。

嚴格規則：
1. 只抽取原文明確寫出的資訊，嚴禁推測或補充自己的知識。
2. 每個非 null 欄位都必須附 source_excerpt：從原文「逐字」複製的短句（10-40 字），必須能在原文找到。
3. 數字、日期、金額務必與原文一致；原文沒寫的欄位 value 一律 null、source_excerpt 留空字串。
4. 只填這些 program：{json.dumps(program_codes, ensure_ascii=False)}。deadlines/scholarships/app_materials 若適用全部 program，逐一列出。
5. 金額換算：只在原文明確標 USD 時填 *_usd 欄位；學期制學費若原文只有每學期金額，填 tuition_note 不要自己乘二。
{feedback_part}
URL: {url}
頁面內容（markdown）:
---
{markdown}
---

回傳 JSON，schema 如下（省略沒資料的陣列即可，programs 內只需列出有 value 的欄位）：
{_EXTRACTION_SCHEMA}"""


def sufficiency_prompt(school_id: str, coverage: dict, candidate_urls: list[str]) -> str:
    return f"""你在評估「{school_id}」這間學校的研究所申請資料是否已足夠寫入資料庫。

目前抽取到的欄位覆蓋狀況（program → 已取得欄位 / 缺少的重要欄位）：
{json.dumps(coverage, ensure_ascii=False, indent=2)}

重要欄位優先序：申請截止日 > 語言門檻（TOEFL/IELTS）> GRE 要求 > 申請費 > 學費 > 推薦信數量。

尚未爬過、可以補爬的候選 URL（最多列 40 個）：
{json.dumps(candidate_urls[:40], ensure_ascii=False, indent=2)}

回傳 JSON：
{{
  "sufficient": true/false,          // 重要欄位大致齊全（截止日 + 語言門檻 + GRE 有值）就算足夠
  "missing_summary": "缺什麼的一句話總結",
  "seed_urls": ["<從候選清單中挑出最可能補到缺漏欄位的 URL，最多 5 個；sufficient=true 時留空>"]
}}"""
