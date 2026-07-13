import os
import re
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

# 與 db/connection.py 一致，明確載入 backend/.env——
# 無參數的 load_dotenv() 只找 CWD 的 .env（專案根沒有這個檔），
# 先前能運作是仰賴 db.connection 先被 import 的副作用。
_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(_BACKEND_ROOT / ".env")

_client = None

# 分級模型：
#   DEFAULT_MODEL —— 判斷/結構型任務（decomposer 意圖、子問題拆解、verifier、critic、
#                    refiner、text-to-SQL）。這些只需輸出 JSON 判斷，用便宜快的 mini 即可。
#   ANSWER_MODEL  —— 最終答案生成（面向使用者的長文），值得用強一點的模型。
# 兩者皆可用環境變數各自覆寫，不需改 code。
DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1")
ANSWER_MODEL = os.getenv("OPENAI_ANSWER_MODEL", "gpt-4o")


def _sanitize_ssl_env() -> None:
    """Ignore broken SSL env vars that point to non-existent cert paths."""
    cert_file = os.getenv("SSL_CERT_FILE")
    if cert_file and not Path(cert_file).exists():
        print(f"[openai] SSL_CERT_FILE 無效，已忽略：{cert_file}")
        os.environ.pop("SSL_CERT_FILE", None)

    cert_dir = os.getenv("SSL_CERT_DIR")
    if cert_dir and not Path(cert_dir).exists():
        print(f"[openai] SSL_CERT_DIR 無效，已忽略：{cert_dir}")
        os.environ.pop("SSL_CERT_DIR", None)


def get_openai_client() -> OpenAI:
    """取得 OpenAI Client（singleton）。"""
    global _client
    if _client is None:
        _sanitize_ssl_env()
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("未在 .env 中找到 OPENAI_API_KEY")
        _client = OpenAI(api_key=api_key)
    return _client


def call_llm(prompt: str, model_name: str = DEFAULT_MODEL, temperature: float = 0.0) -> str:
    """呼叫 OpenAI Chat Completions，回傳純文字。

    預設 temperature=0：decomposer / verifier / critic / text-to-SQL 這些判斷型任務
    需要輸出穩定可重現，避免同一問題時而通過時而拒答。
    """
    client = get_openai_client()
    response = client.chat.completions.create(
        model=model_name,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
    )
    return (response.choices[0].message.content or "").strip()


def clean_answer_text(text: str) -> str:
    """移除答案中的 ** 粗體標記與殘留的 <span> 標籤（保留其中的 Markdown 連結）。"""
    text = text.replace("**", "")
    text = re.sub(
        r"<span[^>]*>\s*(\[[^\]]+\]\([^\)]+\))\s*</span>",
        r"\1", text, flags=re.IGNORECASE,
    )
    text = re.sub(r"</?span[^>]*>", "", text, flags=re.IGNORECASE)
    return text.strip()


def format_context_for_prompt(context_docs: list[dict]) -> str:
    """
    將 SQL 查詢結果（結構化 dict）與教授擴充資料格式化為 LLM 易讀的字串。

    每筆 doc 為平鋪 dict，可能是：
      - 結構化學校要求：{school_id, university_name, source_url, <各申請要求欄位>...}
        （school_id/university_name/source_url 以外的非 None 欄位都視為要顯示的申請要求）
      - 教授資料（chunk 格式）：{chunk_text, source_url, school_id, ...}
    """
    formatted_docs = []
    sources_list = []

    for i, doc in enumerate(context_docs):
        sid = doc.get("school_id", "")
        url = doc.get("source_url", "")

        if doc.get("type") == "applicant_experience":
            # 申請經驗回報（非官方，個別案例）——標頭明確標示，避免被當官方資料
            formatted_docs.append(
                f"【網路申請經驗回報（非官方，個別案例）】\n{doc['chunk_text'].strip()}"
            )
            if url:
                sources_list.append({"school": sid, "type": "applicant_experience", "url": url})
        elif doc.get("chunk_text"):
            # 教授資料（沿用純文字格式）
            univ = doc.get("university_name", sid.upper() if sid else "未知學校")
            formatted_docs.append(f"【{univ} 教授資料】\n{doc['chunk_text'].strip()}")
            if url:
                sources_list.append({"school": sid, "type": "professor", "url": url})
        else:
            # 結構化 SQL 查詢結果（"query" 是內部的子問題標記，不屬於申請要求，不給 LLM 看）
            univ = doc.get("university_name", sid.upper() if sid else "未知學校")
            fields = {k: v for k, v in doc.items()
                      if k not in ("school_id", "university_name", "source_url", "query") and v is not None}
            field_lines = "\n".join(f"  - {k}: {v}" for k, v in fields.items())
            formatted_docs.append(f"【{univ} 申請要求】\n{field_lines}")
            if url:
                sources_list.append({"school": sid, "type": "requirements", "url": url})

    formatted_text = "\n\n".join(formatted_docs)

    sources_section = "\n\n--- 來源列表（在答案中用 Markdown 連結引用） ---\n"
    for s in sources_list:
        sources_section += f"[{s['school']} - {s['type']}]({s['url']})\n"

    return formatted_text + sources_section


_SYSTEM_PROMPT = """你是一位北美 CS 研究所申請諮詢助理。你只能根據下方「參考資料」中的內容作答。

核心規則（違反任何一條視為嚴重錯誤）：
1. 嚴禁幻覺：不得憑空捏造任何數字、日期、政策或要求。若參考資料中找不到答案，必須明確說「我沒有找到相關資訊」，並請使用者前往官方網站查詢。
2. 不確定就說不知道：若參考資料只有部分相關、不夠明確，請直接告知「資料不足，建議前往官方網站確認」，並提供相關 URL。
3. 禁止補充自己的知識：即使你知道答案，也不得提供參考資料以外的資訊。
4. 經驗回報資料的引用護欄（標示為「網路申請經驗回報（非官方，個別案例）」的資料）：
   - 這是個別申請者在 GradCafe / 論壇的主觀回報，非官方、有樣本偏誤，僅供參考。
   - 引用時必須明確標示「根據網路申請經驗回報（非官方）」，不得寫成官方數據或規定。
   - 嚴禁把個別案例當成錄取門檻或保證（例如不得因「有人 GPA 3.1 錄取」就說「3.1 即可錄取」）。
   - 但只要參考資料中有相關的經驗回報案例，就「應該」據實整理呈現，不要因為是非官方就說「資料不足/不明」而拒答。
     正確作法：把案例的 GPA、結果（錄取/被拒/waitlist）具體列出來，描述大致落點與分布，
     例如「回報案例中，錄取者 GPA 多在 X–Y 之間，也有 GPA Z 被拒的案例」，並附上「這是個別回報、僅供參考、實際因人而異」。
     當使用者問「我這個分數有沒有機會」時，用案例的落點給出參考判斷（例如「你的 GPA 落在有錄取案例的區間內，但錄取與否還取決於其他因素」），而不是直接說無法判斷。
   - 官方申請要求（programs 資料）與經驗回報同時存在時，以官方要求為準，經驗回報僅作補充。

【答案格式規定（重點）】：
- 主要答案內容務必保持「流暢可讀」，不要在句子中間穿插長 URL（嚴禁）
- 來源引用方式：僅在以下「容易誤解的關鍵點」直接附上 Markdown 連結：
  * 具體數字、截止日期、申請要求（如 GPA、TOEFL、GRE 等）
  * 硬性政策聲明
  * 教授論文列表或研究領域總結
- 連結格式：在資訊後面直接附上 Markdown 連結，例如：
    GPA 要求 3.5 [官網](https://...)
    截止日期 12月15日 [申請頁面](https://...)
- 使用「參考資料」後面提供的「來源列表」中的 URL
- 一般性陳述、背景資訊、分析等無需加註來源

4. 教授與論文：若多項資訊來自同一教授，請在段落末尾統一附上連結一次即可，禁止為每一篇論文都標註。
5. 找不到資訊的固定回應格式：
   「根據目前取得的資料，我無法確認此問題的答案。建議您直接前往官方網站查詢：[相關 URL，若有的話]」

【排版與格式規定】：
- 語言規範：不論使用者問題或參考資料是中文或英文，你一律使用「繁體中文」回答。嚴禁使用簡體字，包含任何夾雜的簡體用詞皆視為錯誤。
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

維度選擇原則：從使用者的問題抽取核心關心點作為維度，而非列出所有資訊。
若某所學校的某個維度資訊不存在於參考資料中，就寫「資料不足，請查官網」，不得胡亂填寫。

【教授研究與個人資訊排版規範】：
1. 必須以教授姓名為首：每個教授的資訊必須以 `### [教授姓名]` 作為標題開頭。
2. 善用列表：具體分點說明其研究領域、重點實驗室、以及代表性成果。
3. 綜整分析：先總結該教授近年的研究主題，不要一開始就條列論文。
4. 代表性論文：綜整分析之後，另立 `代表性論文` 小節，從參考資料中挑選 3-5 篇最具代表性（優先取被引用數高或年份較新者）的論文，逐篇列出「標題（年份）」並附上該篇論文的 Markdown 連結；每篇獨立一行，不得省略年份或連結。若參考資料中的論文不足 3 篇，就列出實際可用的篇數即可。
5. 來源標注：在代表性論文清單之後，另外統一附上一個連結指向該教授的 Google Scholar 主頁作為總來源。

【一般問題回答排版規範】：
- 層次分明：大量使用 `-` 條列式說明，避免擠在一起的冗長段落。
- 重點突出：重要結論或數據應放置於段落開頭。
- 邏輯清晰：按「核心問題回答」→「詳細資訊解析」→「補充建議與來源」的結構組織。
"""


def _build_prompt(query: str, context_docs: list[dict]) -> str:
    context_text = format_context_for_prompt(context_docs)
    return f"""{_SYSTEM_PROMPT}

--- 參考資料（共 {len(context_docs)} 筆） ---
{context_text}

--- 使用者問題 ---
{query}

--- 你的回答 ---
（請嚴格遵守以上規則，若資料不足請直接說不知道並引導查官網）
"""


def generate_answer_stream(query: str, context_docs: list[dict], model_name: str = ANSWER_MODEL):
    """串流版本：逐 chunk yield 原始文字。若 API 失敗則 raise Exception。"""
    client = get_openai_client()
    prompt = _build_prompt(query, context_docs)

    stream = client.chat.completions.create(
        model=model_name,
        messages=[{"role": "user", "content": prompt}],
        stream=True,
    )
    for chunk in stream:
        delta = chunk.choices[0].delta.content if chunk.choices else None
        yield delta or ""


def generate_answer(query: str, context_docs: list[dict], model_name: str = ANSWER_MODEL) -> str | None:
    """根據檢索到的結構化資料生成回答。"""
    client = get_openai_client()
    prompt = _build_prompt(query, context_docs)

    try:
        response = client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
        )
        return clean_answer_text(response.choices[0].message.content or "")
    except Exception as e:
        print(f"[OpenAI] 生成回答時發生錯誤: {e}")
        return None
