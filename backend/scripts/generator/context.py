"""Formatting helpers for answer text and retrieved context."""

from __future__ import annotations

import re

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
