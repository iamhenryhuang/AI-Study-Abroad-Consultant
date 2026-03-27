import os
import re
from pathlib import Path
from dotenv import load_dotenv
from google import genai

load_dotenv()

_client = None


def _sanitize_ssl_env() -> None:
    """Ignore broken SSL env vars that point to non-existent cert paths."""
    cert_file = os.getenv("SSL_CERT_FILE")
    if cert_file and not Path(cert_file).exists():
        print(f"[gemini] SSL_CERT_FILE 無效，已忽略：{cert_file}")
        os.environ.pop("SSL_CERT_FILE", None)

    cert_dir = os.getenv("SSL_CERT_DIR")
    if cert_dir and not Path(cert_dir).exists():
        print(f"[gemini] SSL_CERT_DIR 無效，已忽略：{cert_dir}")
        os.environ.pop("SSL_CERT_DIR", None)


def get_gemini_client():
    """取得 Gemini GenAI Client。"""
    global _client
    if _client is None:
        _sanitize_ssl_env()
        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError("未在 .env 中找到 GOOGLE_API_KEY")
        _client = genai.Client(api_key=api_key)
    return _client


def _primary_type(doc: dict) -> str:
    """從 passed_types 取得主要類型。"""
    passed = doc.get("passed_types") or []
    if not passed:
        return "general"
    return max(passed, key=lambda x: x.get("score", 0))["type"]


def format_context_for_prompt(context_docs: list[dict]) -> tuple[str, dict[str, str]]:
    """
    將檢索到的文件格式化為 LLM 易讀的字串，同時建立來源對照表。

    每筆資料賦予固定編號 S1, S2, S3…，格式：
      [Sx] school/type | url
      chunk_text

    Returns:
        (context_str, source_map)
        source_map: {"S1": url, "S2": url, ...}
    """
    blocks: list[str] = []
    source_map: dict[str, str] = {}

    for i, doc in enumerate(context_docs):
        sid   = f"S{i + 1}"
        school = doc.get("school_id") or doc.get("university_name", "未知")
        ptype  = _primary_type(doc)
        url    = doc.get("source_url", "")
        text   = doc.get("chunk_text", "").strip()

        source_map[sid] = url

        header = f"[{sid}] {school}/{ptype}"
        if url:
            header += f" | {url}"

        blocks.append(f"{header}\n{text}")

    return "\n\n".join(blocks), source_map


def _postprocess_citations(text: str, source_map: dict[str, str]) -> str:
    """
    將回答中的 [Sx] 標記自動轉換為 Markdown 連結 [Sx](url)。
    已是連結格式（[Sx](...)）的不重複處理。
    找不到對應來源的 [Sx] 保持原樣。
    """
    def replace(m: re.Match) -> str:
        sid = m.group(1)           # e.g. "S3"
        url = source_map.get(sid)
        if url:
            return f"[{sid}]({url})"
        return m.group(0)          # 找不到來源，原樣保留

    # 只替換尚未帶括號的 [Sx]（避免對已處理的連結重複處理）
    return re.sub(r"\[(S\d+)\](?!\()", replace, text)


_SYSTEM_PROMPT = """你是一位北美 CS 研究所申請諮詢助理。你只能根據下方「參考資料」中的內容作答。

核心規則（違反任何一條視為嚴重錯誤）：
1. 嚴禁幻覺：不得憑空捏造任何數字、日期、政策或要求。若參考資料中找不到答案，必須明確說「資料不足」，並請使用者前往官方網站查詢。
2. 不確定就說不知道：若參考資料只有部分相關、不夠明確，請直接告知「資料不足，建議前往官方網站確認」。
3. 禁止補充自己的知識：即使你知道答案，也不得提供參考資料以外的資訊。

【引用規則（硬性規定，最高優先級）】：
- 參考資料中每段文字都有固定編號，格式為 [Sx]（如 [S1]、[S2]）
- 凡涉及以下內容，句尾必須加上對應的來源標記 [Sx]：
  * 任何具體數字（分數、人數、金額、比例等）
  * 任何日期或截止時間
  * 任何硬性申請要求（GPA 門檻、語言分數、必交文件等）
  * 教授的研究結論、論文標題、實驗室介紹
- 若某事實在參考資料中沒有對應的 [Sx] 來源，必須直接輸出「資料不足」，不得推測或補充
- [Sx] 標記會在輸出後自動轉為超連結，只需輸出正確的編號即可
- 一般性背景描述、分析、建議等無需加 [Sx]

【答案格式規定】：
- 主要答案內容務必保持「流暢可讀」，不要在句子中間穿插長 URL
- 善用 Markdown：使用無序列表 (`-`) 或有序列表 (`1.`) 讓資訊層次分明。不得使用 **粗體**。
- 直接切入重點，不要多餘的開場白或客套話。

【對比問題格式規定】（這是最重要的）：
當問題涉及多所學校時，必須按「維度」組織回答，不能按學校分段。且必須使用 Markdown 標題（如 `### [GPA 要求]`）。

正確格式：
### [GPA 要求]
- Stanford: 最低 3.5 [S1]
- CMU: 最低 3.0 [S2]

### [截止日期]
- Stanford: 12 月 15 日 [S3]
- CMU: 12 月 1 日 [S4]

錯誤格式（不得使用）：
Stanford: GPA 要求...，截止日期...
CMU: GPA 要求...，截止日期...

若某所學校的某個維度資訊不存在於參考資料中，就寫「資料不足，請查官網」，不得胡亂填寫。

【教授研究與個人資訊排版規範】：
1. 必須以教授姓名為首：每個教授的資訊必須以 `### [教授姓名]` 作為標題開頭。
2. 善用列表：具體分點說明其研究領域、重點實驗室、以及代表性成果（每項末尾附 [Sx]）。
3. 綜整分析：總結該教授近年的研究主題，避免單純條列論文。
4. 來源標注：每條具體論文或研究結論後面加 [Sx]。

【一般問題回答排版規範】：
- 層次分明：大量使用 `-` 條列式說明，避免擠在一起的冗長段落。
- 重點突出：重要結論或數據應放置於段落開頭，且每個數字、日期、硬性要求後加 [Sx]。
- 邏輯清晰：按「核心問題回答」→「詳細資訊解析」→「補充建議與來源」的結構組織。

【語言規範】：
- 不論使用者問題或參考資料是中文或英文，你一律使用「繁體中文」回答。
- 嚴禁使用簡體字（例如：「说」應為「說」、「这」應為「這」）。
- 英文專業術語可保留括號標注（例如：分散式系統 (Distributed Systems)）。
"""


def generate_answer(
    query: str,
    context_docs: list[dict],
    model_name: str = "gemini-2.5-flash",
) -> str | None:
    """
    根據檢索到的文件生成回答。

    Args:
        query:        使用者問題
        context_docs: 檢索並排序後的文件清單
        model_name:   模型名稱

    Returns:
        回答字串（[Sx] 已轉為 Markdown 連結），或 None（API 呼叫失敗時）
    """
    client = get_gemini_client()

    context_text, source_map = format_context_for_prompt(context_docs)

    prompt = f"""{_SYSTEM_PROMPT}

--- 參考資料（共 {len(context_docs)} 筆，每段開頭有編號 [Sx]） ---
{context_text}

--- 使用者問題 ---
{query}

--- 你的回答 ---
（請嚴格遵守引用規則：數字、日期、硬性要求、研究結論句尾必須加 [Sx]；無對應來源請輸出「資料不足」）
"""

    try:
        response = client.models.generate_content(
            model=model_name,
            contents=prompt,
        )
        text = (response.text or "")

        # 清除殘留星號（不使用粗體）
        text = text.replace("**", "")

        # 兼容舊輸出：移除 <span> 包裹
        text = re.sub(
            r"<span[^>]*>\s*(\[[^\]]+\]\([^\)]+\))\s*</span>",
            r"\1",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(r"</?span[^>]*>", "", text, flags=re.IGNORECASE)

        # 將 [Sx] 轉為 Markdown 連結
        text = _postprocess_citations(text, source_map)

        return text.strip()
    except Exception as e:
        print(f"[Gemini] 生成回答時發生錯誤: {e}")
        return None
