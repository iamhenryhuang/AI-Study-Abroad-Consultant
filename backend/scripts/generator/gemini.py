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


def format_context_for_prompt(context_docs: list[dict]) -> str:
    """
    將檢索到的文件列表格式化為 LLM 易讀的字串。

    每筆 doc 包含：
      - chunk_text     : 段落文字
      - university_name: 學校全名
      - school_id      : 學校識別碼
      - passed_types   : [{type, score}, ...] 頁面類型列表
      - source_url     : 原始頁面 URL
    """
    formatted_docs = []
    sources_list = []

    for i, doc in enumerate(context_docs):
        univ  = doc.get("university_name", "未知學校")
        sid   = doc.get("school_id", "")
        ptype = _primary_type(doc)
        url   = doc.get("source_url", "")
        text  = doc.get("chunk_text", "").strip()

        formatted_docs.append(f"【{univ} {ptype}】\n{text}")
        sources_list.append({
            "index": i + 1,
            "school": sid,
            "type": ptype,
            "url": url,
        })

    # 將來源清單附加在文本最後，作為 Gemini 的參考
    formatted_text = "\n\n".join(formatted_docs)
    
    # 提供來源對應表供 Gemini 使用
    sources_section = "\n\n--- 來源列表（在答案中用 Markdown 連結引用） ---\n"
    for s in sources_list:
        sources_section += f"[{s['school']} - {s['type']}]({s['url']})\n"
    
    return formatted_text + sources_section


_SYSTEM_PROMPT = """你是一位北美 CS 研究所申請諮詢助理。你只能根據下方「參考資料」中的內容作答。

核心規則（違反任何一條視為嚴重錯誤）：
1. 嚴禁幻覺：不得憑空捏造任何數字、日期、政策或要求。若參考資料中找不到答案，必須明確說「我沒有找到相關資訊」，並請使用者前往官方網站查詢。
2. 不確定就說不知道：若參考資料只有部分相關、不夠明確，請直接告知「資料不足，建議前往官方網站確認」，並提供相關 URL。
3. 禁止補充自己的知識：即使你知道答案，也不得提供參考資料以外的資訊。

【答案格式規定（重點）】：
- 主要答案內容務必保持「流暢可讀」，不要在句子中間穿插長 URL（嚴禁）
- 來源引用方式：僅在以下「容易誤解的關鍵點」直接附上 Markdown 連結：
  * 具體數字、截止日期、申請要求（如 GPA、TOEFL、申請人數等）
  * 硬性政策聲明（如「僅接受這些表格」、「必須完成此步驟」等）
  * 教授論文列表或研究領域總結
- 連結格式：在資訊後面直接附上 Markdown 連結，例如：
    GPA 要求 3.5 [官網](https://...)
    截止日期 12月15日 [申請頁面](https://...)
- 使用「參考資料」後面提供的「來源列表」中的 URL
- 一般性陳述、背景資訊、分析等無需加註來源

4. 教授與論文（professor_profile/paper）：若多項資訊來自同一教授，請改在段落末尾統一附上連結一次即可，禁止為每一篇論文都標註。
5. 找不到資訊的固定回應格式：
   「根據目前取得的資料，我無法確認此問題的答案。建議您直接前往官方網站查詢：[相關 URL，若有的話]」

【排版與格式規定】：
- 語言規範：不論使用者問題或參考資料是中文或英文，你一律使用「繁體中文」回答。嚴禁使用簡體字（例如：「说」應為「說」、「这」應為「這」），包含任何夾雜的簡體用詞皆視為錯誤。
- 翻譯品質：請將英文參考資料中的專業術語準確翻譯為中文，或在必要時保留括號標註（例如：分散式系統 (Distributed Systems)）。
- 善用 Markdown：請使用 `程式碼區塊` 標註專有名詞，並使用無序列表 (`-`) 或有序列表 (`1.`) 讓資訊層次分明。不得使用 **粗體**。
- 直接切入重點，不要多餘的開場白或客套話。

【對比問題格式規定】（這是最重要的）：
當問題涉及多所學校時，必須按「維度」組織回答，不能按學校分段。且必須使用 Markdown 標題（如 `### [GPA 要求]`）。

正確格式：
### [GPA 要求]
- Stanford: ... [官網](https://...)
- CMU: ... [官網](https://...)
- MIT: ... [官網](https://...)

### [截止日期]
- Stanford: ... [申請頁](https://...)
- CMU: ... [申請頁](https://...)
- MIT: ... [申請頁](https://...)

錯誤格式（不得使用）：
Stanford: GPA 要求...^截止日期...^其他...
CMU: GPA 要求...^截止日期...^其他...

維度選擇原則：從使用者的問題抽取核心關心點作為維度，而非列出所有資訊。
若某所學校的某個維度資訊不存在於參考資料中，就寫「資料不足，請查官網」，不得胡亂填寫。

【教授研究與個人資訊排版規範】：
1. 必須以教授姓名為首：每個教授的資訊必須以 `### [教授姓名]` 作為標題開頭。
2. 善用列表：具體分點說明其研究領域、重點實驗室、以及代表性成果。
3. 綜整分析：總結該教授近年的研究主題，避免單純條列論文。
4. 來源標注：在總結的最後統一附上一個連結指向該教授的資料來源。

【一般問題回答排版規範】：
- 層次分明：大量使用 `-` 條列式說明，避免擠在一起的冗長段落。
- 重點突出：重要結論或數據應放置於段落開頭。
- 邏輯清晰：按「核心問題回答」→「詳細資訊解析」→「補充建議與來源」的結構組織。
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
        context_docs: 檢索並排序後的文件清單（v2 schema）
        model_name:   模型名稱

    Returns:
        回答字串，或 None（API 呼叫失敗時）
    """
    client = get_gemini_client()

    context_text = format_context_for_prompt(context_docs)

    prompt = f"""{_SYSTEM_PROMPT}

--- 參考資料（共 {len(context_docs)} 筆） ---
{context_text}

--- 使用者問題 ---
{query}

--- 你的回答 ---
（請嚴格遵守以上規則，若資料不足請直接說不知道並引導查官網）
"""

    try:
        response = client.models.generate_content(
            model=model_name,
            contents=prompt,
        )
        text = (response.text or "")
        # 清除殘留星號（使用者要求不使用粗體）
        text = text.replace("**", "")
        # 兼容舊輸出：若模型仍產生 <span> 包裹連結，轉回純 Markdown
        text = re.sub(
            r"<span[^>]*>\s*(\[[^\]]+\]\([^\)]+\))\s*</span>",
            r"\1",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(r"</?span[^>]*>", "", text, flags=re.IGNORECASE)
        return text.strip()
    except Exception as e:
        print(f"[Gemini] 生成回答時發生錯誤: {e}")
        return None

# ===========================================================

import json
import re

COMPRESS_PROMPT = """
You will receive multiple sets of query and chunk_text pairs. 
Pairs sharing the same id belong to the same set.

Your task is to extract ONLY the exact sentences from each chunk_text that are directly helpful for answering the corresponding query.

Strict Rules (MUST follow all):

1. Extractive only:
   - You MUST copy text exactly from chunk_text.
   - Do NOT paraphrase, summarize, rewrite, or reorder words.
   - Do NOT combine partial sentences.
   - Each extracted unit MUST be a complete, continuous sentence from the original text.

2. Sentence integrity:
   - Do NOT truncate sentences.
   - Do NOT merge sentences.
   - Do NOT stitch text across non-adjacent parts of the chunk.
   - Each sentence must appear exactly as it exists in the original chunk_text.

3. No hallucination:
   - Do NOT add any new words, facts, or explanations.
   - If information is not explicitly present in the chunk_text, do NOT include it.

4. No duplication:
   - Do NOT repeat the same sentence.
   - Each sentence should appear at most once per output.

5. Relevance filtering:
   - Only keep sentences that are clearly useful for answering the query.
   - Remove unrelated, generic, or background sentences.
   - Direct answer sentences (HIGHEST PRIORITY — NEVER REMOVE): Sentences that explicitly answer the query

6. Source isolation:
   - Each output must ONLY contain content from its own chunk_text.
   - Do NOT mix content from different ids or different chunks.

7. Empty handling:
   - If no sentence in the chunk_text is relevant, return an empty string "" for chunk_text.
   
8. 若問題出現requirement、條件、申請需求等等類似語意:
    -保留GPA、english profciency、GRE、資格條件等等資訊，不要過度刪除
    -保留各校的特殊條件

Output format (STRICT):
- Return a valid JSON array ONLY.
- Do NOT include explanations or extra text.
- Keep the original query unchanged.
- Combine selected sentences into a single string, separated by a space.

Example:
[
  {
    "id": 1,
    "query": "original query",
    "chunk_text": "Sentence A. Sentence B."
  }
]

Input data:
"""


def compress_prompt(raw_result: list[dict]) -> str:
    """將 raw_result 組成送給 Gemini 的 prompt 字串。"""
    blocks = []
    for count, v in enumerate(raw_result, start=1):   # 修復：用 enumerate 取代手動 count
        block = (
            f"id: {count}\n"
            f"school_id: {v.get('school_id', '')}\n"   # 修復：key 不含逗號
            f"query: {v.get('query', '')}\n"            # 修復：key 不含逗號
            f"chunk_text: {v.get('chunk_text', '')}"
        )
        blocks.append(block)

    return COMPRESS_PROMPT + "\n\n---\n\n".join(blocks)


def _parse_gemini_json(text: str) -> list[dict]:
    """從 Gemini 回傳文字中解析 JSON 陣列，容忍 markdown code fence。"""
    # 去除 ```json ... ``` 包裝
    cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", text).strip()
    return json.loads(cleaned)


def chunk_compress(raw_chunk: list[dict]) -> list[dict] | None: 
    """
    raw_chunk 每筆包含：
      - query      : 問的問題
      - chunk_text : 待壓縮的文字
      - school_id  : （選填）學校識別碼
    回傳壓縮後的 list[dict]，失敗時回傳 None。
    """
    print(f"本輪蒐集到的文件數量: {len(raw_chunk)}")
    
    """
    print("壓縮前各文件長度：")
    for v in raw_chunk:
        print(f"  query={v.get('query')}  chunk_len={len(v.get('chunk_text', ''))}")
    """

    client = get_gemini_compress_client()
    prompt = compress_prompt(raw_chunk)

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        raw_text = response.text or ""

        compressed = _parse_gemini_json(raw_text)

        # 將壓縮後的 chunk_text merge 回原始 docs，保留 source_url 等 metadata
        for item in compressed:
            idx = item.get("id", 0) - 1   # id 是 1-based
            if 0 <= idx < len(raw_chunk):
                raw_chunk[idx]["chunk_text"] = item.get("chunk_text", raw_chunk[idx]["chunk_text"])

        # 過濾掉壓縮後 chunk_text 為空的 doc（代表無相關內容）
        return [doc for doc in raw_chunk if doc.get("chunk_text", "").strip()]

    except json.JSONDecodeError as e:
        print(f"[Gemini] JSON 解析失敗: {e}\n原始回應: {raw_text}")
        return None
    except Exception as e:
        print(f"[Gemini] 壓縮時發生錯誤: {e}")
        return None


def get_gemini_compress_client():
    """取得 Gemini GenAI Client（singleton）。"""
    global _client
    if _client is None:
        _sanitize_ssl_env()
        api_key = os.getenv("COMPRESS_KEY")
        if not api_key:
            raise ValueError("未在環境變數中找到 COMPRESS_KEY")  # 修復：訊息與變數名一致
        _client = genai.Client(api_key=api_key)
    return _client
    