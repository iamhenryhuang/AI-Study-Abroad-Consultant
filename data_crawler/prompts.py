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
    return f"""你是升學資訊爬蟲的頁面分類器。判斷以下網頁內容是否含「北美 CS、資料科學或 AI 相關研究所申請」資訊，並做多標籤分類。

目標學程範圍：
- Computer Science、Computer Engineering、Information Science、Software Engineering
- Data Science、Data Analytics、Statistics/Data Science 等以資料科學為核心的學程
- Artificial Intelligence、Machine Learning、Robotics、Computer Vision、NLP 等以 AI/ML 為核心的學程
- EECS、ECE 等跨領域學程中，與計算、軟體、資料或 AI 直接相關的研究所方向

非上述方向的商學、法律、醫學、純生物、純化學、純人文藝術等研究所，不視為目標內容；但全校共用且適用目標學程的招生、語言、學費或獎助資訊仍應保留。

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


def url_filter_prompt(school_id: str, roots: list[str], candidates: list[dict]) -> str:
    """只根據 URL 周邊資訊做寬鬆初篩；不確定時必須保留。"""
    return f"""你是北美 CS、資料科學與 AI 相關研究所升學資料爬蟲的 URL 篩選器。

你的職責是判斷每個候選 URL 是否值得進入下一階段，讓爬蟲開啟頁面、尋找子連結，並在之後下載完整內容。這只是爬取前的寬鬆初篩，不是最終內容分類。

目標學程包含：
- Computer Science、Computer Engineering、Information Science、Software Engineering
- Data Science、Data Analytics，以及以資料科學為核心的統計／運算學程
- Artificial Intelligence、Machine Learning、Robotics、Computer Vision、NLP
- EECS、ECE 等學程中與計算、軟體、資料、AI/ML 直接相關的研究所方向

應保留可能包含以下資訊的 URL，且內容應適用上述目標學程：
- 研究所招生、申請流程、資格、要求、截止日期與國際學生規定
- 碩士、博士或其他研究所學位、課程、學分與學位要求
- TOEFL、IELTS、Duolingo、GRE、GPA、推薦信、SOP、履歷、作品集等要求
- 學費、費用、獎學金、fellowship、financial aid、TA、RA、assistantship、funding
- FAQ、表單、政策、目錄、導覽或可能繼續連到上述資料的入口頁
- 教授、faculty、研究人員、實驗室、研究方向及人員名錄。這些資料未來會寫入教授資訊 table，必須保留

判斷規則：
1. 採寬鬆、保守的保留策略；只有「明確與上述需求無關」才可 drop。
2. URL 用途不明、資訊不足、anchor text 模糊或仍有合理可能包含目標資料時，一律 keep。
3. 不可只因 URL 含 faculty、people、research、lab、staff、directory 就 drop；教授與研究人員資訊必須 keep。
4. 不可只因 URL 含 news、event、blog、undergraduate 等字詞就自動 drop，必須綜合完整 URL、anchor text 與來源頁面。只有明確無關的一般新聞、活動、行銷、登入、法務或純大學部內容才可 drop。
5. 導覽頁或索引頁只要可能通往目標資訊就 keep。
6. 商學、法律、醫學、純生物、純化學、純人文藝術等其他研究所，若與 CS、資料科學或 AI/ML 沒有直接關係，可以 drop。
7. 全校／研究所共用的 admissions、語言要求、學費、獎助或申請政策，只要可能適用目標學程就 keep。
8. 每個輸入 URL 必須恰好回傳一筆決策，URL 必須原樣複製。
9. 每筆都要給具體的一句話理由。只有高度確定明確無關時才回傳 drop。

學校 ID：{school_id}
Root URLs：
{json.dumps(roots, ensure_ascii=False, indent=2)}

候選 URL（包含來源頁、anchor text 與下一層深度）：
{json.dumps(candidates, ensure_ascii=False, indent=2)}

只回傳合法 JSON，不要 Markdown 或其他文字：
{{
  "decisions": [
    {{
      "url": "<原樣複製候選 URL>",
      "decision": "keep 或 drop",
      "reason": "具體的一句話理由",
      "confidence": 0.0
    }}
  ]
}}"""


def identify_programs_prompt(url: str, title: str, text_excerpt: str, known_programs: list[str]) -> str:
    known = json.dumps(known_programs, ensure_ascii=False) if known_programs else "[]"
    return f"""判斷以下頁面內容對應到哪個（或哪些）研究所學位 program。

規則：
- 只識別 CS、資料科學、AI/ML 及直接相關跨領域學程；其他不相關領域不要列入 programs
- program_code 格式為「<領域縮寫> <學位>」，例如 "CS MS"、"CS PhD"、"DS MS"、"AI MS"、"ML PhD"、"ECE MS"
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
    {{"program_code": "DS MS", "degree_type": "MS", "program_name": "Master of Science in Data Science", "department": "Data Science"}}
  ]
}}"""


_EXTRACTION_SCHEMA = """
{
  "programs": [
    {
      "program_code": "CS MS",
      "fields": {
        "toefl_min":         {"value": <int|null>, "source_excerpt": "<舊版相容欄位；若原文未說考試種類才使用>"},
        "toefl_ibt_min":     {"value": <int|null>, "source_excerpt": "<TOEFL iBT 分數優先填此欄>"},
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
6. 原文明確寫 TOEFL iBT 時，分數必須填入 toefl_ibt_min；只有只寫 TOEFL、無法確認考試種類時才填 toefl_min。
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
