"""OpenAI client construction and generic LLM calls."""

from __future__ import annotations

import os
import threading
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

# 與 db/connection.py 一致，明確載入 backend/.env——
# 無參數的 load_dotenv() 只找 CWD 的 .env（專案根沒有這個檔），
# 先前能運作是仰賴 db.connection 先被 import 的副作用。
_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(_BACKEND_ROOT / ".env")

_client = None
_llm_unavailable_reason: str | None = None
_llm_state_lock = threading.Lock()


class LLMUnavailableError(RuntimeError):
    pass


_QUOTA_FALLBACK_MESSAGE = "OpenAI quota exhausted; local database fallback is active"


def llm_is_unavailable() -> bool:
    """Return whether this process has already seen a permanent quota error."""
    return _llm_unavailable_reason is not None


def _is_permanent_quota_error(message: str) -> bool:
    low = message.lower()
    return any(marker in low for marker in (
        'insufficient_quota',
        'exceeded your current quota',
        'check your plan and billing',
    ))


def _record_llm_failure(exc: Exception) -> bool:
    global _llm_unavailable_reason
    message = str(exc)
    if not _is_permanent_quota_error(message):
        return False
    with _llm_state_lock:
        if _llm_unavailable_reason is None:
            _llm_unavailable_reason = message
            print('[OpenAI] API quota exhausted (429); switched to local database fallback.')
    return True


def _raise_if_llm_unavailable() -> None:
    if _llm_unavailable_reason is not None:
        raise LLMUnavailableError(_QUOTA_FALLBACK_MESSAGE)

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
    _raise_if_llm_unavailable()
    if _client is None:
        _sanitize_ssl_env()
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("未在 .env 中找到 OPENAI_API_KEY")
        _client = OpenAI(
            api_key=api_key,
            max_retries=int(os.getenv('LLM_SDK_MAX_RETRIES', '0')),
            timeout=float(os.getenv('LLM_REQUEST_TIMEOUT_SECONDS', '45')),
        )
    return _client


def call_llm(prompt: str, model_name: str = DEFAULT_MODEL, temperature: float = 0.0) -> str:
    """呼叫 OpenAI Chat Completions，回傳純文字。

    預設 temperature=0：decomposer / verifier / critic / text-to-SQL 這些判斷型任務
    需要輸出穩定可重現，避免同一問題時而通過時而拒答。
    """
    client = get_openai_client()
    try:
        response = client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
        )
    except Exception as exc:
        if _record_llm_failure(exc):
            raise LLMUnavailableError(_QUOTA_FALLBACK_MESSAGE) from None
        raise
    return (response.choices[0].message.content or "").strip()
