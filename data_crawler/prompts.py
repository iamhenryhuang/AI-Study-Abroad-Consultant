"""LLM prompt templates.

所有實際送給 LLM 的 prompt 使用英文，因為學校官網原文主要為英文。
每個 prompt 上方保留中文維護註解，說明用途、重要規則與預期輸出，方便後續修改與排查。
"""
import json

from .state import KEEP_TYPES, DROP_TYPES


# 中文維護說明：頁面分類的 6 個保留類型與 2 個排除類型。
# program 僅保留與 CS/CSE 申請直接相關的正式學程頁，不保留一般課程或研究介紹。
_TYPE_DESCRIPTIONS = """
- admissions: Graduate application procedures, requirements (GPA/GRE/TOEFL/IELTS), eligibility, and required documents.
- deadlines: Application opening/closing dates and decision-release timelines. Use this label when dates are a primary topic; it may coexist with admissions.
- program: Official CS/CSE master's program information directly relevant to applying or distinctions among MS/MEng/MCS options. Exclude ordinary curricula, credit rules, and research descriptions.
- tuition: Tuition, mandatory fees, and cost of attendance.
- scholarship: Scholarships, fellowships, financial aid, and admission-related TA/RA funding.
- faq: Frequently asked questions about graduate admission.
- faculty: Faculty, researcher, or personnel directory content. This category is discarded.
- other: Content unrelated to CS/CSE graduate admission, including news, events, campus life, undergraduate content, and empty navigation pages.
""".strip()


# 中文原意／維護說明：判斷頁面是否含研究所申請資訊並做多標籤分類。
# 保留 admissions/deadlines/program/tuition/scholarship/faq；faculty/other 為不相關。
# 後續限縮：只收官方 CS/CSE，以碩士為主；但全校共用且適用 CS 的規則仍保留。
# URL 分數只能參考，正文證據才是最後依據。輸出必須含布林 is_relevant、類型信心與理由。
def classification_prompt(url: str, title: str, url_path_bonuses: dict, text_excerpt: str) -> str:
    hint_lines = ", ".join(f"{k}={v:+.1f}" for k, v in url_path_bonuses.items() if v)
    hint_part = (f"URL-path heuristic scores (reference only; page content is authoritative): "
                 f"{hint_lines}\n") if hint_lines else ""
    return f"""You are a page classifier for an international Computer Science master's admissions crawler. Determine whether the page contains information applicable to an international applicant seeking an MS, MSc, MEng, MCS, or equivalent master's degree in Computer Science/CSE, and assign all applicable labels.

Target scope:
- Only official Computer Science / Computer Science and Engineering programs.
- Exclude standalone Data Science, AI, Machine Learning, Statistics, Information Science, ECE, and EECS programs unless the term is merely a specialization inside an official CS program.

Degree priority:
- Primary: MS, MSc, MEng, MCS, and other professional master's programs in CS/CSE.
- Secondary: PhD. Keep PhD content only when the page also covers master's admission or contains university-wide rules that apply to master's applicants.
- PhD-only research areas, faculty, laboratories, qualifying exams, dissertations, and doctoral training rules are outside the primary scope.

Semantic reasoning requirements:
- Treat keywords as clues, never as decisive rules. Infer the page's actual purpose from the title, headings, surrounding sentences, intended audience, and the relationship among concepts.
- A page is not relevant merely because it contains words such as admission, graduate, CS, funding, or requirements.
- A page may still be relevant when it uses synonymous language instead of expected keywords, if its meaning clearly provides CS/CSE application information.
- Distinguish admission requirements from similarly worded TA eligibility, continuing-student policies, course requirements, and special pathways by meaning and applicant scope.

Non-CS graduate programs are irrelevant. However, university-wide graduate admission, English proficiency, tuition, fees, or funding rules should be retained when they apply to CS/CSE applicants.

Label definitions:
{_TYPE_DESCRIPTIONS}

URL: {url}
Title: {title}
{hint_part}Page excerpt:
---
{text_excerpt}
---

Return JSON only:
{{
  "is_relevant": false,
  "types": [
    {{"type": "other", "confidence": 0.95}}
  ],
  "reason": "Example shape only; replace every value with the actual decision and evidence."
}}

Output requirements:
- is_relevant must be a JSON boolean based on this page, not copied from the example.
- type must be one of: {", ".join(KEEP_TYPES + DROP_TYPES)}.
- confidence must be a JSON number from 0.0 to 1.0 based on actual evidence; never copy 0.0 or 0.95 as a default.
- Set is_relevant=false when the page is only faculty or other content.
- Include only labels genuinely supported by the page. Do not classify a page as program merely because navigation lists program names."""


# 中文原意／維護說明：只根據 URL、anchor、來源頁做開頁前初篩，不讀正文。
# 原版採保守 keep；後續依需求限縮為「必須有具體 CS 碩士申請欄位證據」才 keep。
# 每個候選 URL 必須原樣回傳且恰好一筆；confidence 是實際數字，不是 schema 預設值。
def url_filter_prompt(school_id: str, roots: list[str], candidates: list[dict]) -> str:
    return f"""You are a strict URL filter for a North American Computer Science/CSE graduate-admissions crawler. You are not collecting general academic content.

Target scope:
- Official Computer Science / Computer Science and Engineering programs only.
- Standalone DS, AI, ML, Statistics, Information Science, ECE, and EECS programs are out of scope.
- Prioritize MS, MSc, MEng, MCS, and other master's admission pages.
- Drop PhD-only URLs unless they also contain university-wide admission, language, fee, or funding rules applicable to master's applicants.

Keep URLs likely to contain concrete CS/CSE application data such as:
- admission procedures, eligibility, requirements, deadlines, and international-applicant rules;
- TOEFL, IELTS, Duolingo, GRE, GPA, recommendation letters, statements of purpose, CV/resume, or portfolios;
- tuition, application fees, fee waivers, scholarships, fellowships, and admission-related funding;
- FAQs, forms, or policies explicitly about graduate applications.

Decision rules:
1. A keep decision must identify a concrete application field the page is likely to provide. Topical relation to computing alone is insufficient.
2. Drop individual courses, course descriptions, catalogs, subject/department browse pages, curricula, credit rules, and general degree requirements.
3. Drop faculty, people, research, lab, staff, directory, research-center, and faculty-roster pages.
4. Drop news, events, blogs, undergraduate pages, majors, minors, honors, academic advising, login, and legal pages.
5. Drop generic landing/index pages without explicit evidence such as admissions, application, deadline, tuition, or funding.
6. Keep university-wide graduate admission, language, tuition, funding, and application policies when they may apply to CS/CSE.
7. If the URL and anchor text are ambiguous and contain no application evidence, drop them. Do not keep a URL merely because it might be related.
8. Return exactly one decision for every input URL and copy each URL exactly.
9. The reason for keep must name a likely master's application field. A reason such as "related to CS/AI/graduate study" is not sufficient.
10. confidence must be a real number from 0.0 to 1.0 representing confidence in the selected decision. Do not copy a placeholder value. Use high confidence (typically >=0.8) for URLs that are clearly application-related or clearly irrelevant.
11. Do not decide from a single path token or anchor keyword. Interpret the full URL, anchor phrase, source-page context, likely audience, and navigation relationship together. Keywords are evidence, not exact-match rules.
12. Synonyms and paraphrases may indicate a valid application page even without words such as admission or requirements; keep only when the combined semantics identify a concrete application purpose.

Confidence examples:
- /graduate-admissions -> keep, confidence 0.95, when the anchor indicates master's application requirements.
- /research-labs -> drop, confidence 0.98, because research-lab content provides no application fields.

School ID: {school_id}
Root URLs:
{json.dumps(roots, ensure_ascii=False, indent=2)}

Candidate URLs, including source page, anchor text, and depth:
{json.dumps(candidates, ensure_ascii=False, indent=2)}

Return valid JSON only:
{{
  "decisions": [
    {{
      "url": "<copy the candidate URL exactly>",
      "decision": "keep or drop",
      "reason": "Replace this with one concrete sentence tied to an application field.",
      "confidence": 0.85
    }}
  ]
}}"""


# 舊版相容 helper；現行 Node 6 使用 deterministic 單一目標判斷。
# 若外部仍呼叫此 prompt，也只允許 INTERNATIONAL_CS_MASTERS。
def identify_programs_prompt(url: str, title: str, text_excerpt: str,
                             known_programs: list[str]) -> str:
    known = json.dumps(known_programs, ensure_ascii=False) if known_programs else "[]"
    return f"""Identify the official graduate degree program or programs directly represented by this page.

Rules:
- Recognize only admission information applicable to an international CS/CSE master's applicant.
- The only allowed program_code is "INTERNATIONAL_CS_MASTERS"; do not create synonyms.
- Exclude standalone DS, AI, ML, Statistics, Information Science, ECE, PhD-only,
  undergraduate, BS/MS, current-student, and TA-employment pages.
- Known program codes for this school: {known}.
- University-wide and CS/CSE department-wide graduate requirements may map to the
  same target when they apply to master's applicants.
- program_name must be an official name explicitly shown in the page title, H1, H2, or H3. A form dropdown, Programs A-Z list, navigation item, or incidental body-text mention is not evidence.
- Never turn a research area such as Data Science, AI, or Machine Learning into an independent degree name unless the official heading explicitly presents it as a CS degree.
- If the official CS page explicitly states that no terminal master's is offered,
  return programs=[] and school_wide=false.
- A department name plus a degree mentioned only in body text is insufficient. The official program itself must be the subject of a title or heading.
- A general admissions FAQ/checklist may support the single target without
  creating a separate program.
- Use semantic identity, not keyword overlap: determine what official degree the page is actually about from the complete title/heading meaning and institutional context.
- Similar words do not imply the same program. For example, a research area, course track, form option, or specialization is not an independent degree unless the official heading presents it as one.

URL: {url}
Title: {title}
Page excerpt:
---
{text_excerpt}
---

Return JSON only:
{{
  "school_wide": false,
  "programs": [
    {{
    "program_code": "INTERNATIONAL_CS_MASTERS",
      "degree_type": "MS",
      "program_name": "Master of Science in Computer Science",
      "department": "Computer Science"
    }}
  ]
}}"""


# 中文維護說明：結構化抽取的 JSON schema。
# source_excerpt 必須逐字來自英文原文；TOEFL 新舊制與三種 deadline 日期分欄保存。
_EXTRACTION_SCHEMA = """
{
  "programs": [
    {
      "program_code": "INTERNATIONAL_CS_MASTERS",
      "fields": {
        "toefl_min": {"value": <int|null>, "source_excerpt": "<use only when the page says TOEFL but the format/scale is unclear>"},
        "toefl_ibt_min": {"value": <int|null>, "source_excerpt": "<legacy TOEFL iBT overall requirement on the 0-120 scale>"},
        "toefl_ibt_new_scale_min": {"value": <float|null>, "source_excerpt": "<updated TOEFL iBT overall requirement on the 1-6 scale in 0.5 increments>"},
        "toefl_section_requirements": {"value": <string|null>, "source_excerpt": "<all stated TOEFL section/subscore requirements as one string>"},
        "ielts_min": {"value": <float|null>, "source_excerpt": ""},
        "duolingo_min": {"value": <int|null>, "source_excerpt": ""},
        "language_waiver": {"value": <string|null>, "source_excerpt": ""},
        "english_test_notes": {"value": <string|null>, "source_excerpt": "<special English-test rules, validity, accepted formats, exceptions, conditional requirements, or applicant-specific notes>"},
        "gre_required": {"value": <"required"|"optional"|"not_accepted"|null>, "source_excerpt": ""},
        "gre_quant_min": {"value": <int|null>, "source_excerpt": ""},
        "gre_verbal_min": {"value": <int|null>, "source_excerpt": ""},
        "gre_awa_min": {"value": <float|null>, "source_excerpt": ""},
        "gpa_min": {"value": <float|null>, "source_excerpt": ""},
        "gpa_scale": {"value": <string|null>, "source_excerpt": ""},
        "gpa_note": {"value": <string|null>, "source_excerpt": ""},
        "transcript_copies": {"value": <int|null>, "source_excerpt": ""},
        "transcript_format": {"value": <string|null>, "source_excerpt": ""},
        "rec_letter_count": {"value": <int|null>, "source_excerpt": ""},
        "sop_word_limit": {"value": <int|null>, "source_excerpt": ""},
        "sop_prompt": {"value": <string|null>, "source_excerpt": ""},
        "cv_required": {"value": <bool|null>, "source_excerpt": ""},
        "writing_sample_required": {"value": <bool|null>, "source_excerpt": ""},
        "application_fee_usd": {"value": <int|null>, "source_excerpt": ""},
        "fee_waiver_available": {"value": <bool|null>, "source_excerpt": ""},
        "fee_waiver_criteria": {"value": <string|null>, "source_excerpt": ""},
        "tuition_per_year_usd": {"value": <int|null>, "source_excerpt": ""},
        "tuition_note": {"value": <string|null>, "source_excerpt": ""},
        "application_url": {"value": <string|null>, "source_excerpt": ""},
        "application_system": {"value": <string|null>, "source_excerpt": ""}
      }
    }
  ],
  "deadlines": [
    {
      "program_code": "INTERNATIONAL_CS_MASTERS",
      "deadline_type": "<early|regular|international|rolling>",
      "application_open_date": "YYYY-MM-DD or null",
      "application_close_date": "YYYY-MM-DD or null",
      "decision_release_date": "YYYY-MM-DD or null",
      "semester": "fall_2026",
      "note": "",
      "source_excerpt": "<verbatim source text>"
    }
  ],
  "scholarships": [
    {
      "program_code": "INTERNATIONAL_CS_MASTERS", "name": "", "amount_usd": <int|null>,
      "coverage": "<full_tuition|partial|stipend_only|null>", "eligibility": "",
      "auto_consider": <bool|null>, "source_excerpt": "<verbatim source text>"
    }
  ],
  "app_materials": [
    {
      "program_code": "INTERNATIONAL_CS_MASTERS",
      "material_type": "<additional_essay|portfolio|video|writing_sample|other>",
      "requirement": "", "word_limit": <int|null>, "note": "",
      "source_excerpt": "<verbatim source text>"
    }
  ],
  "evidence_paragraphs": [
    {
      "program_code": "INTERNATIONAL_CS_MASTERS",
      "category": "<deadline|english|gpa|gre|fee|materials|other>",
      "field_name": "<related structured field or null>",
      "evidence_kind": "<ambiguous|conditional|context_note>",
      "evidence_text": "<one readable paragraph preserving the requirement, applicant scope, conditions, and timing>",
      "source_excerpt": "<verbatim or faithfully compressed supporting source text>"
    }
  ]
}
""".strip()


# 中文原意／維護說明：從原文抽取結構化申請資料；只能填原文明確寫出的值。
# 每個非 null 欄位都要附「逐字複製」的短 source_excerpt；數字、日期、金額須一致，
# 沒寫就 null。只填指定 program，禁止外部知識與自行換算。
# 後續限縮：MS 優先；CS 個別 > CSE 系級 > 全校；特殊 BS/MS、TA、獎學金入口不可
# 覆蓋一般 admission 欄位。TOEFL 新舊制分欄；deadline 分開放、截止、結果公布。
def extraction_prompt(url: str, program_codes: list[str], markdown: str,
                      feedback: str | None = None) -> str:
    feedback_part = ""
    if feedback:
        feedback_part = f"""
The previous extraction had the following validation issues. Correct them. If no explicit source evidence exists, set the field to null:
{feedback}
"""
    return f"""Extract structured admission data for an international applicant seeking a Computer Science/CSE master's degree from the page content below.

Strict rules:
1. Extract only information explicitly stated in the source. Never infer, convert, or add outside knowledge.
2. Every non-null field must include a short source_excerpt grounded in the source. Prefer a verbatim excerpt, but minor normalization or faithful compression is acceptable when the meaning, numbers, applicant scope, and conditions remain unchanged.
3. Numbers, dates, and monetary amounts must exactly match the source. If a field is not stated, use null and an empty source_excerpt.
4. Populate only this target record code: {json.dumps(program_codes, ensure_ascii=False)}. The code represents one school's international CS master's admissions record; it is not an official degree name.
5. Populate *_usd only when the source explicitly states USD. Do not annualize per-unit, quarterly, or semester tuition; preserve it in tuition_note.
6. Keep TOEFL scales separate: put a 0-120 overall requirement only in toefl_ibt_min, and a 1-6 overall requirement only in toefl_ibt_new_scale_min. Never convert between scales and never use section/speaking scores as overall scores.
7. Extract only rules applicable to MS/MSc/MEng/MCS or equivalent CS/CSE master's applicants. Never apply a PhD-only, undergraduate, BS/MS pathway, current-student, TA-employment, graduation, or degree-progress rule.
8. University-wide and CS/CSE department-wide graduate rules may be used when they apply to master's applicants. Prefer an explicit CS master's rule over a department-wide rule, and a department-wide rule over a university minimum.
9. For applicant-specific rules, extract the international-applicant rule. Do not use a domestic-only value. English-test requirements and waivers must preserve their exact international-applicant conditions.
10. Do not combine values from separate requirements in one field. A number must be associated with the same test, applicant group, degree, and event named in its source_excerpt.
11. When the page states multiple standards, extract only the standard matching the current scope. Do not use BS/MS pathways, TA eligibility, fellowships, or continuing-student rules as ordinary international CS master's admission requirements.
12. Interpret each statement semantically before mapping it to a field. A keyword match alone is insufficient: identify the subject, requirement type, applicant population, degree, time period, and whether the number is an overall score, section score, fee, date, course load, or other quantity.
13. Important ambiguous or conditional admission information must not be discarded. If a GPA, English score, GRE rule, fee, material rule, or deadline cannot safely fit a structured field, also return it in evidence_paragraphs as one readable contextual paragraph.
{feedback_part}
URL: {url}
Page content:
---
{markdown}
---

Field guidance:
- TOEFL scale identification:
  - Tests before January 21, 2026 use the legacy 0-120 TOEFL iBT overall scale; store an explicitly stated requirement in toefl_ibt_min.
  - Starting January 21, 2026, the updated overall scale is 1-6 in 0.5 increments; store an explicitly stated requirement in toefl_ibt_new_scale_min.
  - During the January 2026-January 2028 transition, a page may state both scales. Populate both only when the institution explicitly publishes both requirements.
  - If a school publishes only 85/90/100, do not derive a new-scale value. If it publishes only 4/4.5/5, do not derive a legacy value.
  - Never use a section score, TOEFL speaking score, or TA language threshold as an overall admission requirement.
- ielts_min and duolingo_min are minimum overall admission scores for their respective tests.
- toefl_section_requirements stores all explicitly stated TOEFL section or subscore rules in one readable string (for example, "Speaking 22; Writing 21"). Do not split sections into separate database fields and do not confuse them with the overall score.
- language_waiver describes all explicit exemption conditions, such as eligible countries, degree locations, or English-medium instruction. Do not reduce it to a boolean.
- english_test_notes stores special English-proficiency provisions not captured by score or waiver fields, including score validity periods, superscoring/MyBest policy, accepted test formats, home-edition policy, conditional admission, differing applicant categories, and submission rules.
- gre_required: required means mandatory; optional means optional/not required but accepted; not_accepted requires explicit non-acceptance. Do not interpret "not required" as not_accepted.
- gre_quant_min, gre_verbal_min, and gre_awa_min require an explicit minimum. Do not treat averages or recommendations as hard minima.
- gpa_min is an explicit minimum; gpa_scale is the scale; recommendations or preferred values belong in gpa_note.
- transcript_copies is the required number of copies. transcript_format may preserve the complete official/unofficial/electronic/paper/original-document requirement when a short label would lose important conditions.
- rec_letter_count is the explicitly required number of recommendation/reference letters.
- sop_word_limit and sop_prompt cover statement-of-purpose or personal-statement length and prompt requirements.
- cv_required and writing_sample_required: true only when explicitly required, false only when explicitly not required, otherwise null.
- application_fee_usd and fee_waiver_* cover the application fee and explicit waiver availability/criteria.
- tuition_per_year_usd requires an explicit annual USD amount; per-unit/quarter/semester amounts belong in tuition_note.
- application_url must be the actual graduate application portal, not an FAQ, scholarship, TA, or informational page. application_system is the named platform, such as Slate or ApplyWeb.
- Deadline fields for each program/semester:
  - opened, begins, or available from -> application_open_date;
  - deadline, due, or closes -> application_close_date;
  - decisions released, notification by, or results announced -> decision_release_date.
  - Do not confuse priority, financial-aid, document-supplement, fellowship, or TA dates with the regular application deadline. If the year is unknown, leave the date null and preserve the wording in note.
  - When a date has no explicit year, do not return an empty deadline record. Put the complete deadline wording and its surrounding admission-cycle context in evidence_paragraphs with category="deadline".
- scholarships cover named scholarships, fellowships, assistantships, and admission funding with amount, coverage, and eligibility.
- app_materials contains additional materials not represented by dedicated fields, such as portfolios, videos, additional essays, and writing samples.

Conflict and ambiguity rules:
- A program-specific CS admission requirement is stronger than a CS/CSE department-wide requirement, which is stronger than a university-wide minimum.
- Do not resolve a conflict by guessing which value is newer. Extract the value explicitly stated on this page with its scope and verbatim evidence; downstream code applies source priority.
- "Not required" means GRE is optional/not required, not not_accepted. Use not_accepted only when the institution explicitly says it does not accept or consider GRE scores.
- A recommendation such as "competitive applicants usually have 3.5" is not gpa_min; preserve it in gpa_note.
- A course load such as "12-14 units" is not tuition and must not be stored in tuition_note.
- A link to an admissions information page is not application_url unless it is the actual submission portal.

Review every field above before finishing. Do not stop after finding only a few values. Return only items explicitly supported by this page.

Return JSON only. Omit empty arrays when appropriate, and include only non-null fields inside each program:
{_EXTRACTION_SCHEMA}"""


# 中文維護說明：驗證修正 prompt。輸入目前抽取、程式驗證問題與同頁原文片段；
# 模型只回傳有原文證據的新增／修正項目，特別檢查被錯放在 app_materials 的專用欄位。
def validation_repair_prompt(url: str, program_codes: list[str], current: dict,
                             issues: list[dict], source_part: str,
                             part_index: int, part_count: int) -> str:
    return f"""Audit and repair structured admission data for an international Computer Science/CSE master's applicant using the original page text.

This is part {part_index}/{part_count} of the same page. Inspect this entire part semantically.

Tasks:
1. Correct validation issues only when this source part provides explicit evidence. Otherwise omit the disputed field.
2. Find important fields omitted from the current extraction. In particular, promote dedicated facts to their dedicated fields: a stated number of recommendation letters belongs in rec_letter_count even if it also appears in app_materials; test scores belong in their test fields; explicit dates belong in deadline fields.
3. DET means Duolingo English Test. "Internet-based test" in a TOEFL context may support TOEFL iBT. Number words such as one, two, and three may support numeric values.
4. Do not invent a year. A month and day without an explicit year must not become YYYY-MM-DD.
5. Use only this target record code: {json.dumps(program_codes, ensure_ascii=False)}.
6. Every returned value needs a concise source_excerpt grounded in this source part. Minor formatting normalization is allowed, but meaning, numbers, scope, and conditions must remain unchanged.
7. Return only new or corrected items supported by this part. Do not repeat unsupported values from the current extraction.
8. Admission fields must describe requirements for prospective applicants. Do not map rules for current/enrolled students, maintaining GPA after enrollment, qualifying or writing exams taken after admission, graduation, degree completion, or good standing into admission fields.
9. A statement explicitly limited to PhD, undergraduate, current/enrolled students, TA employment, graduation, or degree progress must not be returned. Domestic-only rules must not replace international-applicant rules.
10. When a disputed or omitted GPA, English score, GRE rule, or deadline is important but cannot be safely normalized, preserve it in evidence_paragraphs. Include enough surrounding conditions for a later RAG model to interpret it; do not guess a normalized value.

URL: {url}
Current extraction:
{json.dumps(current, ensure_ascii=False, indent=2)}

Programmatic validation issues:
{json.dumps(issues, ensure_ascii=False, indent=2)}

Original page text, part {part_index}/{part_count}:
---
{source_part}
---

Return JSON using this schema:
{_EXTRACTION_SCHEMA}"""


# 中文維護說明：資料充足度 prompt，目前自動補爬功能已停用，但保留此模板供未來恢復。
# 若重新啟用，重要欄位優先序為 deadline、語言、GRE、申請費、學費、推薦信。
def sufficiency_prompt(school_id: str, coverage: dict, candidate_urls: list[str]) -> str:
    return f"""Evaluate whether the collected Computer Science graduate-admission data for school "{school_id}" is sufficient for database storage.

Current field coverage by program:
{json.dumps(coverage, ensure_ascii=False, indent=2)}

Priority: application dates > TOEFL/IELTS requirements > GRE policy > application fee > tuition > recommendation-letter count.

Unvisited candidate URLs, at most 40:
{json.dumps(candidate_urls[:40], ensure_ascii=False, indent=2)}

Return JSON only:
{{
  "sufficient": true,
  "missing_summary": "One concise sentence describing missing information.",
  "seed_urls": ["<up to five candidate URLs likely to fill the missing fields; empty if sufficient>"]
}}"""
