import json
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import pydantic
from pydantic import BaseModel
from cachetools import TTLCache

from app.config import BASE_DIR, SESSION_TTL_SECONDS, MAX_SESSIONS, INGEST_API_KEY
from app.ingest import build_index, is_indexed
from app.chat import chat, chat_stream, RateLimitExceeded
from app.retriever import reload as reload_index
from app.feedback import log_feedback
from app.chatlog import log_chat, get_chatlog

logger = logging.getLogger(__name__)

# In-memory session store with auto-eviction
sessions: TTLCache = TTLCache(maxsize=MAX_SESSIONS, ttl=SESSION_TTL_SECONDS)
MAX_HISTORY = 20  # max messages per session (10 exchanges)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # On startup: auto-ingest PDFs if not already indexed
    print("Starting AMC Support Chatbot...", flush=True)
    if not is_indexed():
        print("No existing index found. Ingesting PDFs...", flush=True)
        build_index()
    else:
        print("Index already exists. Skipping ingestion.", flush=True)
    print("Startup complete. Ready to serve requests.", flush=True)
    yield


app = FastAPI(title="AMC Support Chatbot", lifespan=lifespan)

# Serve static files
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


class ChatRequest(BaseModel):
    message: str = pydantic.Field(max_length=2000)
    session_id: Optional[str] = None


class ChatResponse(BaseModel):
    answer: str
    sources: list[dict]


@app.get("/")
async def index():
    return FileResponse(str(BASE_DIR / "static" / "index.html"))


@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest):
    if not request.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    # Get or create session history
    session_id = request.session_id or "default"
    history = sessions.get(session_id, [])

    try:
        result = chat(request.message, history=history)

        # Update session history
        history.append({"role": "user", "content": request.message})
        history.append({"role": "assistant", "content": result["answer"]})
        # Trim to max history
        if len(history) > MAX_HISTORY:
            history = history[-MAX_HISTORY:]
        sessions[session_id] = history

        # Log chat for manager dashboard
        try:
            log_chat(session_id, request.message, result["answer"], result["sources"])
        except Exception as e:
            logger.warning("Chat logging failed: %s", e)

        return ChatResponse(answer=result["answer"], sources=result["sources"])
    except RateLimitExceeded:
        return JSONResponse(
            status_code=429,
            content={"detail": "The AI service is busy. Please wait a moment and try again.", "retry_after": 30},
        )
    except Exception as e:
        logger.error("Chat error: %s", e, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"detail": "An unexpected error occurred. Please try again."},
        )


@app.post("/chat/stream")
async def chat_stream_endpoint(request: ChatRequest):
    """Streaming chat endpoint using Server-Sent Events."""
    if not request.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    session_id = request.session_id or "default"
    history = sessions.get(session_id, [])

    async def event_generator():
        try:
            full_answer = ""
            all_sources = []
            for event in chat_stream(request.message, history=history):
                if event["type"] == "status":
                    yield f"event: status\ndata: {json.dumps(event)}\n\n"
                elif event["type"] == "token":
                    full_answer += event["text"]
                    yield f"event: token\ndata: {json.dumps(event)}\n\n"
                elif event["type"] == "done":
                    all_sources = event.get("sources", [])
                    yield f"event: done\ndata: {json.dumps(event)}\n\n"

            # Update session history after streaming completes
            history.append({"role": "user", "content": request.message})
            history.append({"role": "assistant", "content": full_answer})
            sessions[session_id] = history[-MAX_HISTORY:]

            # Log chat for manager dashboard
            try:
                log_chat(session_id, request.message, full_answer, all_sources)
            except Exception as e:
                logger.warning("Chat logging failed: %s", e)

        except RateLimitExceeded:
            yield f"event: error\ndata: {json.dumps({'detail': 'The AI service is busy. Please wait a moment and try again.', 'retry_after': 30})}\n\n"
        except Exception as e:
            logger.error("Stream error: %s", e, exc_info=True)
            yield f"event: error\ndata: {json.dumps({'detail': 'An unexpected error occurred.'})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


class FeedbackRequest(BaseModel):
    session_id: str
    question: str
    answer: str
    sources: list[dict] = []
    rating: str  # "up" or "down"
    comment: str = ""


@app.post("/feedback")
async def feedback_endpoint(request: FeedbackRequest):
    """Log user feedback (thumbs up/down) for a response."""
    if request.rating not in ("up", "down"):
        raise HTTPException(status_code=400, detail="Rating must be 'up' or 'down'")
    try:
        log_feedback(
            session_id=request.session_id,
            question=request.question,
            answer=request.answer,
            sources=request.sources,
            rating=request.rating,
            comment=request.comment,
        )
        return {"status": "ok"}
    except Exception as e:
        logger.error("Feedback error: %s", e, exc_info=True)
        return JSONResponse(status_code=500, content={"detail": "Failed to save feedback."})


@app.get("/chatlog")
async def chatlog_page():
    """Serve the chat log dashboard."""
    return FileResponse(BASE_DIR / "static" / "chatlog.html")


@app.get("/api/chatlog")
async def chatlog_api():
    """Return chat log entries as JSON."""
    entries = get_chatlog()
    return {"entries": entries, "total": len(entries)}


@app.post("/ingest")
async def ingest_endpoint(request: Request):
    """Re-ingest all PDFs (admin use)."""
    if INGEST_API_KEY and request.headers.get("Authorization") != f"Bearer {INGEST_API_KEY}":
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        count = build_index()
        reload_index()
        return {"status": "ok", "chunks_indexed": count}
    except Exception as e:
        logger.error("Ingest error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Ingestion failed. Check server logs.")
