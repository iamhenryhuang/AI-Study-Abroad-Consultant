"""LLM 呼叫 — 沿用專案既有的呼叫方式與金鑰：

- SDK 與介面比照 backend/scripts/generator/openai_client.py（openai.OpenAI singleton）
- 金鑰沿用 backend/.env，支援三個 provider（Groq/Gemini 走 OpenAI 相容端點）：
    * openai → OPENAI_API_KEY（模型 OPENAI_MODEL，預設 gpt-4.1）
    * groq   → GROQ_API_KEY（模型 GROQ_MODEL，預設 llama-3.3-70b-versatile，
               沿用 backend/scripts/retriever/analyzer.py）
    * gemini → GOOGLE_API_KEY（模型 GEMINI_MODEL，預設 gemini-2.0-flash）
- 選擇方式：環境變數 LLM_PROVIDER=openai|groq|gemini；
  未設定時自動偵測（openai → groq → gemini，取第一個有 key 的）
- 同樣的 SSL env 清理

此檔額外加上 JSON mode 包裝（call_llm_json）、限流退避與併發上限。
"""
import json
import os
import re
import threading
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

# 與 backend/scripts/db/connection.py 相同：載入 backend/.env（也容忍根目錄 .env）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")
load_dotenv(_PROJECT_ROOT / "backend" / ".env")

_client = None
_client_lock = threading.Lock()

# 免費層 TPM 限流保護：限制同時進行的 LLM 呼叫數（fan-out 時避免瞬間打爆額度）
_LLM_SEMAPHORE = threading.Semaphore(int(os.getenv("LLM_MAX_CONCURRENCY", "2")))

# provider 設定：key 環境變數 / base_url / 預設模型
_PROVIDERS = {
    "openai": {
        "key_env": "OPENAI_API_KEY",
        "base_url": None,
        "model": lambda: os.getenv("OPENAI_MODEL", "gpt-4.1"),
    },
    "groq": {
        "key_env": "GROQ_API_KEY",
        "base_url": "https://api.groq.com/openai/v1",
        "model": lambda: os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
    },
    "gemini": {
        "key_env": "GOOGLE_API_KEY",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        # 預設 2.5-flash：實測此 key 的 gemini-2.0-flash 免費額度為 0
        "model": lambda: os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
    },
}


def _pick_provider() -> str:
    explicit = os.getenv("LLM_PROVIDER", "").lower()
    if explicit:
        if explicit not in _PROVIDERS:
            raise ValueError(f"LLM_PROVIDER 必須是 {list(_PROVIDERS)}，收到：{explicit}")
        if not os.getenv(_PROVIDERS[explicit]["key_env"]):
            raise ValueError(f"LLM_PROVIDER={explicit} 但未設定 {_PROVIDERS[explicit]['key_env']}")
        return explicit
    for name in ("openai", "groq", "gemini"):
        if os.getenv(_PROVIDERS[name]["key_env"]):
            return name
    raise ValueError("未在 .env 找到 OPENAI_API_KEY / GROQ_API_KEY / GOOGLE_API_KEY 任一金鑰")


PROVIDER = _pick_provider()
DEFAULT_MODEL = _PROVIDERS[PROVIDER]["model"]()

MAX_LLM_RETRY = 4


def _rate_limit_wait_seconds(error_message: str, attempt: int) -> float:
    """429 錯誤：優先用 API 建議的等待秒數（'try again in 7.095s'），否則指數退避。"""
    m = re.search(r"try again in ([\d.]+)s", error_message)
    if m:
        return float(m.group(1)) + 1.0
    return min(2.0 ** attempt * 2, 30.0)


def _sanitize_ssl_env() -> None:
    """與 openai_client.py 相同：忽略指向不存在路徑的 SSL 環境變數。"""
    cert_file = os.getenv("SSL_CERT_FILE")
    if cert_file and not Path(cert_file).exists():
        os.environ.pop("SSL_CERT_FILE", None)
    cert_dir = os.getenv("SSL_CERT_DIR")
    if cert_dir and not Path(cert_dir).exists():
        os.environ.pop("SSL_CERT_DIR", None)


def get_openai_client() -> OpenAI:
    global _client
    with _client_lock:
        if _client is None:
            _sanitize_ssl_env()
            spec = _PROVIDERS[PROVIDER]
            kwargs = {"api_key": os.getenv(spec["key_env"])}
            if spec["base_url"]:
                kwargs["base_url"] = spec["base_url"]
            _client = OpenAI(**kwargs)
            print(f"[LLM] provider={PROVIDER} model={DEFAULT_MODEL}")
    return _client


def call_llm(prompt: str, model_name: str = DEFAULT_MODEL, system: str | None = None) -> str:
    client = get_openai_client()
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    response = client.chat.completions.create(model=model_name, messages=messages)
    return (response.choices[0].message.content or "").strip()


def _extract_json(text: str):
    """容錯解析：直接 loads，失敗再從 ```json fence 或第一個 {...}/[...] 撈。"""
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        try:
            return json.loads(fence.group(1).strip())
        except Exception:
            pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except Exception:
                continue
    raise ValueError(f"LLM 回覆無法解析為 JSON：{text[:200]}")


def call_llm_json(prompt: str, model_name: str = DEFAULT_MODEL, system: str | None = None):
    """JSON mode 呼叫（response_format=json_object），失敗時重試並容錯解析。"""
    client = get_openai_client()
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    import time

    last_err = None
    for attempt in range(MAX_LLM_RETRY + 1):
        try:
            with _LLM_SEMAPHORE:
                response = client.chat.completions.create(
                    model=model_name,
                    messages=messages,
                    response_format={"type": "json_object"},
                    temperature=0,
                )
            return _extract_json(response.choices[0].message.content or "")
        except Exception as e:
            last_err = e
            msg = str(e)
            if "rate_limit" in msg or "429" in msg:
                wait = _rate_limit_wait_seconds(msg, attempt)
                print(f"  ⏳ LLM 限流，等待 {wait:.1f}s 後重試（{attempt + 1}/{MAX_LLM_RETRY + 1}）")
                time.sleep(wait)
            else:
                print(f"  ⚠️ LLM JSON 呼叫失敗（{attempt + 1}/{MAX_LLM_RETRY + 1}）：{msg[:200]}")
    raise last_err
