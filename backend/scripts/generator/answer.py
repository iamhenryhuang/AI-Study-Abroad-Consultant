"""Synchronous and streaming answer generation."""

from __future__ import annotations

from .client import ANSWER_MODEL, get_openai_client
from .context import clean_answer_text
from .prompts import _build_prompt

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
