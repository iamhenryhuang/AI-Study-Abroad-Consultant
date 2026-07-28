"""Prompt builders used by the agent nodes."""

from __future__ import annotations

from .state import _SCHOOL_ALIASES

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
【任務二點五：教授名單查詢偵測（professor_list_query）】
====================
判斷問題是否在詢問「某校有哪些教授」這類名單型問題（沒有指名特定教授姓名，
而是要列出多位教授，例如「Stanford CS 系有哪些教授」「Purdue 有做電腦視覺的教授嗎」）。

- 若問題在問某校教授名單/推薦，且該校在已知學校清單內，professor_list_query 設為
  {{"school_id": "學校ID"}}
- 若問題已經有明確教授姓名（professor_query 不為 null），professor_list_query 一律為 null
  （兩者互斥：指名查詳情 vs 列名單，不會同時成立）
- 若學校不在已知清單內或未提及學校，professor_list_query 為 null

====================
【任務三：是否需要查詢結構化申請要求資料庫（needs_sql_search）】
====================
資料庫（programs 及其子表）只有 GPA、TOEFL/IELTS/GRE、申請截止日期、學費、獎助等「申請要求」欄位，
不包含教授的研究領域、發表論文、經歷等資訊。

- 若使用者問題只在詢問教授本人相關資訊（如研究領域、論文、背景等），且完全沒有詢問任何申請要求，
  needs_sql_search 設為 false
- 若使用者問題除了教授資訊外，還有詢問任何申請要求（GPA/TOEFL/IELTS/GRE/截止日期等），
  needs_sql_search 設為 true
- 若問題只在詢問教授名單（professor_list_query 不為 null）且完全沒有申請要求問題，
  needs_sql_search 設為 false
- 若問題完全沒有教授查詢意圖，needs_sql_search 一律為 true

====================
【任務四：是否需要查詢申請經驗回報（needs_experience）】
====================
系統另有「申請經驗回報」資料，包含本站使用者分享，以及 GradCafe / 一畝三分地的個別
錄取/被拒案例。這些資料屬非官方、有樣本偏誤的經驗談，只適合回答「實際申請背景長怎樣」
「某分數/背景有沒有機會上」「往年案例」「本站有哪些分享」這類問題。

- 若使用者問題在詢問「錄取機會 / 錄取者背景 / 案例 / 我這條件上不上得了 / 往年錄取情況」，
  needs_experience 設為 true
- 若使用者問題只問官方申請要求（GPA 門檻幾分、截止日、學費等固定規定），
  needs_experience 設為 false
- 純教授查詢時 needs_experience 一律為 false

====================
【任務五：是否為「上傳成績求推薦學校」（wants_recommendation）】
====================
若使用者提供了自己的成績（GPA / IELTS / TOEFL / GRE 任一）並希望「推薦學校 / 我適合哪些學校 / 幫我選校」，
wants_recommendation 設為 true，並把分數提取到 profile（數字，沒有的填 null）。否則 wants_recommendation 為 false、profile 全 null。

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
  "professor_list_query": {{"school_id": "學校ID"}} or null,
  "needs_sql_search": true or false,
  "needs_experience": true or false,
  "wants_recommendation": true or false,
  "profile": {{"gpa": number or null, "ielts": number or null, "toefl": number or null, "gre": number or null}}
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
  而參考資料中有標示為「申請經驗回報（非官方，個別案例）」的資料且觸及了該校該科系的
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

