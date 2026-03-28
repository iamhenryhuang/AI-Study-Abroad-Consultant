import json
import os
from groq import Groq
from pathlib import Path

from search import search_core, search_alternative
from generator.gemini import get_gemini_client, generate_answer

_PROJECT_ROOT    = Path(__file__).resolve().parent.parent.parent
_DATA_DIR        = _PROJECT_ROOT / "data" 
_DISTRIBUTION_PATH = _DATA_DIR / "1point3_distribution.json"
_EXPERIENCE_PATH   = _DATA_DIR / "merged_1point3_data.json"

MAX_ALT_SCHOOLS = 3
MAX_EXP_PER_SCHOOL = 5

def _load_admission_stats() -> dict:
    """載入 1point3 錄取統計，以 school_id 為 key。"""
    try:
        with open(_DISTRIBUTION_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        raw = data.get("university_admissions_data", data)
        stats = {}
        for _name, info in raw.items():
            sid = info.get("school_id")
            if sid:
                stats[sid] = info
        print(f"[Data] 載入錄取統計：{len(stats)} 所學校")
        return stats
    except FileNotFoundError:
        print(f"[Data] 找不到統計資料：{_DISTRIBUTION_PATH}")
        return {}
 
def _load_admission_experience() -> dict:
    """載入 1point3 論壇申請經驗，以 school_id 為 key。"""
    try:
        with open(_EXPERIENCE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        print(f"[Data] 載入申請經驗：{len(data)} 筆")
        return data
    except FileNotFoundError:
        print(f"[Data] 找不到經驗資料：{_EXPERIENCE_PATH}")
        return {}
    
def analyze_user_intent(query: str, current_profile: dict) -> dict:
    """
    利用 Groq 進行極速意圖分析。
    """
    print("  [Agent] 正在透過 Groq 分析使用者意圖...")
    
    api_key = os.environ.get("GROQ_API_KEY", "你的_GROQ_API_KEY")
    
    if not api_key or api_key == "你的_GROQ_API_KEY":
        print("  [Error] 找不到 GROQ_API_KEY，請設定環境變數。")
        return {"wants_alternatives": False, "contains_profile": False, "extracted_profile": {}}

    client = Groq(api_key=api_key)
    
    prompt = f"""
    分析使用者問題，並提取申請相關資訊。回傳 JSON。
    使用者問題: "{query}"

    請判斷：
    1. wants_alternatives: 使用者是否想找「備案」、「保底」、「推薦適合學校」？ (bool)
    2. contains_profile: 是否提到 GPA, TOEFL, 或 GRE 分數？ (bool)
    3. extracted_profile: 從問題中提取的分數。
    4. school: 使用者是否有提到想申請的學校

    回傳範例（純 JSON，不要廢話）：
    {{
        "wants_alternatives": true,
        "contains_profile": true,
        "extracted_profile": {{"gpa": 3.1, "toefl": null, "gre": 50}},
        "school": CMU
    }}
    """

    try:
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": "You are a helpful assistant that outputs JSON."},
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"}
        )
        
        result = json.loads(completion.choices[0].message.content)
        
        # 數值校正
        if result.get("extracted_profile"):
            p = result["extracted_profile"]
            if p.get("gpa"): p["gpa"] = float(p["gpa"])
            if p.get("gre"): p["gre"] = int(p["gre"])
            if p.get("toefl"): p["toefl"] = int(p["toefl"])
            
        return result

    except Exception as e:
        print(f"  [Groq Error] 分析失敗: {e}")
        return {"wants_alternatives": "備案" in query, "contains_profile": False, "extracted_profile": {}}

def evaluate_attainability(profile: dict, school_id: str, admission_stats: dict) -> bool:
    """
    根據使用者 profile 評估是否達到某學校的中位錄取標準
    """
    print(f"  [RAG] 評估 {school_id} 的達成度...")
    stats = admission_stats.get(school_id)

    if not stats:
        print(f"  [RAG] 找不到 {school_id} 的錄取統計資料，跳過評估。")
        return True

    gpa = profile.get("gpa")
    toefl = profile.get("toefl")
    gre = profile.get("gre")

    median_gpa = stats.get("median_gpa")
    median_toefl = stats.get("median_toefl")
    median_gre = stats.get("median_gre")

    print(f"  [Debug] Profile: GPA={gpa}, TOEFL={toefl}, GRE={gre}")
    print(f"  [Debug] {school_id} Median: GPA={median_gpa}, TOEFL={median_toefl}, GRE={median_gre}")

    score = 0
    total = 0

    if gpa and median_gpa:
        total += 1
        if gpa >= median_gpa:
            score += 1

    if toefl and median_toefl:
        total += 1
        if toefl >= median_toefl:
            score += 1

    if gre and median_gre:
        total += 1
        if gre >= median_gre:
            score += 1

    if total == 0:
        return True

    ratio = score / total
    print(f"  [Debug] Match Score: {score}/{total} ({ratio:.2f})")
    return ratio >= 0.6

def _build_exp_doc(school_id: str, exp: dict) -> dict:
    """將論壇經驗轉換成統一文件格式。"""
    return {
        "is_experience_data": True,
        "school_id":          school_id,
        "source_url":         exp.get("url", ""),
        "chunk_text": (
            f"[備案學校：{school_id}] 申請結果: {exp.get('result')}\n"
            f"專業: {exp.get('major')}\n"
            f"GPA: {exp.get('gpa')}, TOEFL: {exp.get('toefl')}, GRE: {exp.get('gre')}\n"
            f"背景: {exp.get('undergrad_school')}\n"
            f"內容: {(exp.get('content') or '')[:300]}"
        ),
    }

def alt_searcher(query: str, profile: dict, admission_stats: dict, admission_exp: dict) -> dict:
    """
    從 admission_stats + admission_experience 組裝備案文件，
    塞進 collected_docs 供 Finalizer 使用。
    """
    print(f"\n[AltSearcher] 開始尋找備案學校，申請人背景：{profile}")
    #_emit({"type": "thinking", "step": "alt_searcher"})
 
    # 取得備案清單
    try:
        alt_schools = search_alternative(profile, admission_stats, admission_exp)
        alt_schools = alt_schools[:MAX_ALT_SCHOOLS]
        
    except Exception as e:
        print(f"[AltSearcher] search_alternative 失敗：{e}，使用預設備案")
        alt_schools = []
 
    if not alt_schools:
        print("[AltSearcher] 無法取得備案清單，略過")
        return {"alt_schools": [], "distribution": [], "collected_docs": []}
 
    print(f"[AltSearcher] 備案學校：{alt_schools}")
    
    #_emit({"type":    "tool_result", "tool": "alt_searcher", "preview": f"推薦備案：{', '.join(alt_schools)}",})
 
    alt_docs:    list[dict] = []
    distribution: list[dict] = []
 
    for sid in alt_schools:
        # 1. 收集錄取統計
        stat = admission_stats.get(sid)
        if stat:
            distribution.append(stat)
 
        # 2. 向量檢索官方入學要求
        #_emit({"type": "tool_call","tool": "search_school","args": {"query": f"{sid} admission requirements", "school_id": sid},})
        official_docs = search_core(
            query=f"{sid} admission requirements",
            top_k=2,
            use_rerank=True,
            school_id=sid,
        )
        if official_docs:
            alt_docs.extend(official_docs)
            print(f"  {sid}: 找到 {len(official_docs)} 筆官方文件")
        else:
            print(f"  {sid}: 無官方文件")
 
        # 3. 載入論壇申請經驗
        experiences = admission_exp.get(sid, [])
        for exp in experiences[:MAX_EXP_PER_SCHOOL]:
            alt_docs.append(_build_exp_doc(sid, exp))
        if experiences:
            print(f"  {sid}: 載入 {min(len(experiences), MAX_EXP_PER_SCHOOL)} 筆申請經驗")
 
    print(f"[AltSearcher] 共組裝 {len(alt_docs)} 筆備案文件")
    print(f"[AltSearcher] alt_docs : {alt_docs} ")

    return {
        "alt_schools":    alt_schools,
        "distribution":   distribution,
        "collected_docs": alt_docs,   # 透過 Annotated[list, operator.add] 累加
    }

#--------接口---------------------
def analyze_and_evaluate(query: str):
    admission_experience = _load_admission_experience()
    admission_stat = _load_admission_stats()

    intent = analyze_user_intent(query, {})   

    user_profile = intent.get("extracted_profile", {})
    wants_alt = intent.get("wants_alternatives", False)
    user_aim_school = intent.get("school")

    should_find_alt = False

    if wants_alt:
        should_find_alt = True
    elif user_profile and user_aim_school:
        if not evaluate_attainability(user_profile, user_aim_school, admission_stat):
            should_find_alt = True

    if should_find_alt:
        return alt_searcher(query, user_profile, admission_stat, admission_experience)  
    
    return None

if __name__ == "__main__":
    analyze_and_evaluate("我想上CMU 但我的GPA只有3.2")




    

