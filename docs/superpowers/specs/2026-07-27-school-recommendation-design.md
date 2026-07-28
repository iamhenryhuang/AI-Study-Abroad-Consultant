# 上傳成績 → 推薦學校 設計（聊天整合）

日期：2026-07-27
狀態：已核准（設計於對話中呈現並核准）

## 目標

使用者在 AI 諮詢聊天輸入自己的成績（GPA / IELTS / TOEFL / GRE），系統推薦適合的學校，並依「你的分數 vs 各校錄取中位數」分成**衝刺 / 適中 / 保底**三檔，每檔附推薦理由與真實錄取案例佐證。

## 決策摘要（來自 brainstorming）

- 形式：**整合進聊天**（Decomposer 偵測推薦意圖 + 提取分數，比照 `needs_experience` 模式），非獨立頁。
- 推薦法：**方案 D 混合** — 數據算分級（可信），LLM 寫理由（好讀）。
- 分級：**衝刺 / 適中 / 保底** 三檔，各挑 2-3 所。
- 數據源：`1point3_distribution.json` 中位數（分級）+ `applicant_reports` 真實案例（LLM 理由佐證）。
- IELTS：資料源無 IELTS，採 **IELTS→TOEFL 換算**後與 TOEFL 中位數比對。

## 背景與現況

- 推薦邏輯曾存在（`analyzer.py` 的 `evaluate_attainability`、`alternative_recommendation.py` 的 `search_alternative`），大改為 text-to-SQL 時移除；**`backup/AI-Study-Abroad-Consultant-1.0.0/backend/scripts/retriever/` 下的舊碼為可參考範本**。
- 資料源健在：
  - `crawler/data/1point3_distribution.json`：`{"university_admissions_data": {"<School Name>": {"median_gpa","median_gre","median_toefl","gpa"(直方圖),"school_id"}}}`。
    - **限制 1**：只有 GPA/GRE/**TOEFL**，無 IELTS。
    - **限制 2**：部分校缺 `median_*`（僅有直方圖，如 Northeastern）。
  - `applicant_reports` 表：個別錄取案例，含 `gpa`、`decision`、`school_id`。
- agent 現有意圖旗標模式（`needs_experience` / `professor_query`）：Decomposer 偵測 → state 旗標 → `route_to_retrieval` 分支 → 專屬節點 → docs → generator。
- 聊天答案已支援 markdown render（PR #46），可輸出表格。

## 架構與資料流

```
使用者聊天：「我 GPA 3.5 IELTS 7 GRE 320 推薦學校」
  ↓
Decomposer 意圖判斷（新增任務）
  ├─ wants_recommendation = True
  └─ profile = {gpa:3.5, ielts:7, gre:320, toefl:None}
  ↓ route_to_retrieval 新增分支：wants_recommendation → recommend_node
recommend_node（新）
  ① 讀 distribution.json；profile 正規化（IELTS→TOEFL、GPA 制式）
  ② 對每校比對中位數 → classify_tier → 衝刺/適中/保底
  ③ 各檔挑 2-3 所；從 applicant_reports 撈分數相近的真實錄取案例
  ④ 組 docs（標 type='recommendation'）寫入 state
  ↓
Finalizer / generator（推薦專屬 prompt）
  → 三檔分級 + 每校「你的分數 vs 中位數」對比 + 案例佐證 + 免責聲明
  ↓ 前端聊天 markdown render
```

## 後端改動

### ① 意圖偵測 + 分數提取（`generator`/`agent` prompts）
`_build_intent_prompt` 加任務：判斷 `wants_recommendation`（使用者是否提供分數並求推薦）、提取 `profile`（gpa/ielts/toefl/gre，數字或 null）。輸出 JSON 增 `wants_recommendation`、`profile` 兩欄。

### ② State（`agent/state.py`）
`AgentState` 加 `wants_recommendation: bool`、`profile: dict`、`recommend_docs: list[dict]`；`create_initial_state` 補初始值（False / {} / []）。

### ③ 新模組 `backend/scripts/retriever/recommend.py`（參考 backup 舊碼）
- `_load_distribution() -> dict`：讀 `crawler/data/1point3_distribution.json`，整理成 `{school_id: {median_gpa, median_gre, median_toefl, name}}`；缺 `median_*` 者由直方圖估算中位數，估不出則該維度為 None。
- `_ielts_to_toefl(ielts: float) -> int | None`：標準換算（e.g. 7.0→~94-101 取代表值）。
- `_normalize_profile(profile: dict) -> dict`：GPA 用既有 `_parse_gpa` 邏輯；無 toefl 但有 ielts 時換算補上。
- `classify_tier(profile, medians) -> str | None`：以達成度（分數 ≥ 中位為達標）判定；整體偏低→衝刺、接近→適中、偏高→保底；可比維度不足→None。
- `recommend(profile, per_tier=3) -> list[dict]`：對所有校分級，各檔取 2-3 所；每校附「你 vs 中位數」對比與 applicant_reports 相近案例。

### ④ 新節點 `recommend_node`（`agent/nodes/retrieval.py`）+ 路由
`route_to_retrieval` 加：`if state.get("wants_recommendation"): targets.append(Send("recommend", state))`。節點呼叫 `recommend(profile)`，組 `recommend_docs`（標 `type='recommendation'`）。

### ⑤ generator 推薦格式
`wants_recommendation` 時，`generate_answer_stream` 帶旗標 → 專屬 prompt：分三檔（衝刺/適中/保底）列學校，每校「你的分數 vs 該校中位數」對比 + 真實案例佐證；結尾免責「基於歷史數據、僅供參考、非錄取保證」。

## 錯誤處理

- profile 無任何有效分數 → 不硬推，請使用者補分數。
- `distribution.json` 讀取失敗 → 誠實告知推薦暫不可用（不 crash）。
- 分數對不到任何校 / 可比維度不足 → 告知資料涵蓋有限。
- `recommend_node` 內例外 → 記 log、回空 recommend_docs，不影響其他檢索支線。

## 測試

`tests/test_recommend.py`（unittest，全 mock，不打 LLM/DB）：
- `_ielts_to_toefl`：代表分數換算正確、超界回 None。
- `classify_tier`：分數低/接近/高於中位 → 衝刺/適中/保底；維度不足 → None。
- `recommend`：mock distribution，驗證分級、各檔數量、案例組裝結構。

## 不改動範圍
- `crawler/data/1point3_distribution.json`、`applicant_reports` schema（只讀）。
- 既有檢索節點、hybrid search、聊天前端（推薦走既有聊天 UI + markdown render）。

## 檔案清單

| 檔案 | 動作 |
|------|------|
| `backend/scripts/retriever/recommend.py` | 新增（分級 + 案例組裝）|
| `tests/test_recommend.py` | 新增 |
| `backend/scripts/retriever/agent/state.py` | 改（wants_recommendation / profile / recommend_docs）|
| `backend/scripts/retriever/agent/prompts.py` | 改（意圖偵測 + 分數提取）|
| `backend/scripts/retriever/agent/nodes/decompose.py` | 改（解析 wants_recommendation/profile + 路由分支）|
| `backend/scripts/retriever/agent/nodes/retrieval.py` | 改（recommend_node）|
| `backend/scripts/retriever/agent/nodes/answer.py` + generator prompts | 改（推薦專屬格式）|
