# FastAPI 後端入口

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
import threading
from threading import Thread

SCRIPTS_DIR = Path(__file__).resolve().parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from retriever.agent import run_agent

app = FastAPI(title="Study Abroad Advisor API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    query: str
    max_steps: int = 5


@app.post("/api/chat")
async def chat(request: ChatRequest):
    """
    SSE 端點：串流回傳 Agent 推理步驟與最終答案。

    事件格式（每行 JSON）：
      {"type": "thinking",    "step": 1}
      {"type": "tool_call",   "tool": "search_school", "args": {...}}
      {"type": "tool_result", "tool": "search_school", "preview": "..."}
      {"type": "answer",      "text": "..."}
      {"type": "error",       "message": "..."}
    """
    cancel_event: threading.Event = threading.Event()
    event_queue: asyncio.Queue[dict] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def on_event(event: dict) -> None:
        if not cancel_event.is_set():
            loop.call_soon_threadsafe(event_queue.put_nowait, event)

    def run_in_thread() -> None:
        try:
            run_agent(
                query=request.query,
                max_steps=request.max_steps,
                verbose=False,
                on_event=on_event,
                cancel_event=cancel_event,
            )
        except Exception as exc:
            if not cancel_event.is_set():
                on_event({"type": "error", "message": str(exc)})

    Thread(target=run_in_thread, daemon=True).start()

    async def event_stream():
        try:
            while True:
                event = await event_queue.get()
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event["type"] in ("answer", "error"):
                    break
        finally:
            # 無論是正常結束、client 斷線還是 Ctrl+C，都通知 agent 停止
            cancel_event.set()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/health")
async def health():
    return {"status": "ok"}
