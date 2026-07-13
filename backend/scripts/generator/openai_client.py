"""Backward-compatible exports for the split generator modules."""

from .answer import generate_answer, generate_answer_stream
from .client import (
    ANSWER_MODEL,
    DEFAULT_MODEL,
    _sanitize_ssl_env,
    call_llm,
    get_openai_client,
)
from .context import clean_answer_text, format_context_for_prompt
from .prompts import _SYSTEM_PROMPT, _build_prompt

__all__ = [
    "ANSWER_MODEL",
    "DEFAULT_MODEL",
    "call_llm",
    "clean_answer_text",
    "format_context_for_prompt",
    "generate_answer",
    "generate_answer_stream",
    "get_openai_client",
]
