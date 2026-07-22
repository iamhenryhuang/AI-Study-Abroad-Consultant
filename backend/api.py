# FastAPI 後端入口

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
import threading
from contextlib import asynccontextmanager

SCRIPTS_DIR = Path(__file__).resolve().parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from fastapi import FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from retriever.agent import run_agent
from db.experiences import create_experience, ensure_experience_schema, list_experiences


@asynccontextmanager
async def lifespan(_: FastAPI):
    await asyncio.to_thread(ensure_experience_schema)
    yield


app = FastAPI(title="Study Abroad Advisor API", lifespan=lifespan)

cors_origins = [origin.strip() for origin in os.getenv(
    "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
).split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)


class ChatRequest(BaseModel):
    query: str
    max_steps: int = 5


class ExperienceItem(BaseModel):
    item: str = Field(min_length=1, max_length=100)
    result: str = Field(min_length=1, max_length=500)

    @field_validator("item", "result", mode="before")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip() if isinstance(value, str) else value


class ExperienceItemOut(BaseModel):
    """讀取用；不套寫入長度限制，理由同 ExperienceResponse。"""
    item: str = ""
    result: str = ""


class ExperienceCreate(BaseModel):
    graduate_school: str = Field(min_length=1, max_length=200)
    country: str = Field(min_length=1, max_length=100)
    apply_school: str = Field(min_length=1, max_length=200)
    apply_program: str = Field(min_length=1, max_length=200)
    gpa: float | None = Field(default=None, ge=0, le=100)
    class_rank: int | None = Field(default=None, ge=1)
    class_size: int | None = Field(default=None, ge=1)
    experience: list[ExperienceItem] = Field(default_factory=list, max_length=30)
    review: str = Field(min_length=1, max_length=10000)

    @field_validator("graduate_school", "country", "apply_school", "apply_program", "review")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("不得為空白")
        return value

    @model_validator(mode="after")
    def validate_rank(self):
        if (self.class_rank is None) != (self.class_size is None):
            raise ValueError("系排與系人數必須一起填寫")
        if self.class_rank is not None and self.class_rank > self.class_size:
            raise ValueError("系排不得大於系人數")
        return self


class ExperienceResponse(BaseModel):
    """回傳既有資料，欄位對齊 DB schema 而非寫入規則。

    刻意不繼承 ExperienceCreate：寫入時的限制（系排與系人數必須成對、
    經歷至多 30 筆等）比 DB CHECK 嚴格，若套用在讀取上，任何經由 API 以外
    路徑寫入的舊資料都會讓整個 GET 回應驗證失敗。
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    graduate_school: str
    country: str
    apply_school: str
    apply_program: str
    gpa: float | None = None
    class_rank: int | None = None
    class_size: int | None = None
    experience: list[ExperienceItemOut] = Field(default_factory=list)
    review: str
    created_at: str


class ExperienceListResponse(BaseModel):
    items: list[ExperienceResponse]
    total: int
    limit: int
    offset: int


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
    terminal_event_sent: threading.Event = threading.Event()
    event_queue: asyncio.Queue[dict] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def on_event(event: dict) -> None:
        if not cancel_event.is_set():
            if event.get("type") in ("answer", "error"):
                terminal_event_sent.set()
            loop.call_soon_threadsafe(event_queue.put_nowait, event)

    def run_in_thread() -> None:
        try:
            result = run_agent(
                query=request.query,
                max_steps=request.max_steps,
                verbose=False,
                on_event=on_event,
                cancel_event=cancel_event,
            )
            if not cancel_event.is_set() and not terminal_event_sent.is_set():
                if result:
                    on_event({"type": "answer", "text": result})
                else:
                    on_event({"type": "error", "message": "Agent finished without a final answer."})
        except Exception as exc:
            if not cancel_event.is_set():
                on_event({"type": "error", "message": str(exc)})

    threading.Thread(target=run_in_thread, daemon=True).start()

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


@app.post("/api/experiences", response_model=ExperienceResponse,
          status_code=status.HTTP_201_CREATED)
async def upload_experience(request: ExperienceCreate):
    try:
        row = await asyncio.to_thread(create_experience, request.model_dump())
        row["created_at"] = row["created_at"].isoformat()
        return row
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="經驗資料儲存失敗") from exc


@app.get("/api/experiences", response_model=ExperienceListResponse)
async def search_experiences(
    apply_school: str = Query(min_length=1, max_length=200),
    apply_program: str | None = Query(default=None, min_length=1, max_length=200),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    school = apply_school.strip()
    program = apply_program.strip() if apply_program else None
    if not school:
        raise HTTPException(status_code=422, detail="申請學校不得為空白")
    try:
        rows, total = await asyncio.to_thread(
            list_experiences, school, program, limit, offset
        )
        for row in rows:
            row["created_at"] = row["created_at"].isoformat()
        return {"items": rows, "total": total, "limit": limit, "offset": offset}
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="經驗資料查詢失敗") from exc
