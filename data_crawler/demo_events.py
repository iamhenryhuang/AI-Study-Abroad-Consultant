"""Crawler demo 的結構化事件流。

每個事件都是單行 JSON；寫入失敗不得影響正式 crawler。
"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any


EVENT_DIR = Path(__file__).resolve().parent / "output"
_LOCK = threading.Lock()


def event_path(school_id: str) -> Path:
    safe_id = "".join(ch for ch in school_id if ch.isalnum() or ch in ("-", "_"))
    return EVENT_DIR / f"{safe_id}_events.jsonl"


def reset_events(school_id: str) -> None:
    try:
        EVENT_DIR.mkdir(parents=True, exist_ok=True)
        with _LOCK:
            event_path(school_id).write_text("", encoding="utf-8")
    except Exception:
        pass


def emit_event(
    school_id: str,
    node: str,
    status: str,
    message: str,
    *,
    url: str | None = None,
    data: Any = None,
) -> None:
    if not school_id or school_id == "unknown":
        return
    event = {
        "timestamp": datetime.now().isoformat(timespec="milliseconds"),
        "school_id": school_id,
        "node": node,
        "status": status,
        "message": message,
    }
    if url:
        event["url"] = url
    if data is not None:
        event["data"] = data
    try:
        EVENT_DIR.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event, ensure_ascii=False, default=str)
        with _LOCK:
            with event_path(school_id).open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")
    except Exception:
        pass


def preview(value: Any, limit: int = 420) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[:limit].rstrip() + "…"
