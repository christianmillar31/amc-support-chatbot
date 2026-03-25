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
from app.chat import chat, chat_stream, single_shot_chat_stream, RateLimitExceeded
from app.config import ENABLE_SINGLE_SHOT
from app.retriever import reload as reload_index
from app.feedback import log_feedback
from app.chatlog import log_chat, get_chatlog, update_rating
from app.faq import match_faq
from app.drive_lookup import get_all_drives, lookup_drive

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
    drive_sku: Optional[str] = None


class ChatResponse(BaseModel):
    answer: str
    sources: list[dict]


@app.get("/")
async def index():
    return FileResponse(str(BASE_DIR / "static" / "index.html"))


@app.get("/api/drives")
async def drives_endpoint():
    """Return all drives for the frontend autocomplete selector."""
    drives = get_all_drives()
    return {"drives": drives, "total": len(drives)}


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

    # --- FAQ instant-match: answer in <1 second with zero API tokens ---
    faq_result = match_faq(request.message)
    if faq_result:
        async def faq_event_generator():
            heading = faq_result.get("section", "")
            heading_str = f", Section: {heading}" if heading else ""
            answer = faq_result["answer"]
            source = faq_result["source"]
            page = faq_result["page"]

            yield f"event: status\ndata: {json.dumps({'type': 'status', 'text': 'Found in FAQ database...'})}\n\n"
            yield f"event: token\ndata: {json.dumps({'type': 'token', 'text': answer})}\n\n"

            sources = [{"source": source, "page": int(page) if page.isdigit() else 0, "heading": heading}]
            yield f"event: done\ndata: {json.dumps({'type': 'done', 'sources': sources})}\n\n"

            # Update session history
            history.append({"role": "user", "content": request.message})
            history.append({"role": "assistant", "content": answer})
            sessions[session_id] = history[-MAX_HISTORY:]

            # Log for dashboard
            try:
                log_chat(session_id, request.message, answer, sources)
            except Exception:
                pass

        return StreamingResponse(faq_event_generator(), media_type="text/event-stream")

    async def event_generator():
        try:
            full_answer = ""
            all_sources = []
            # Resolve drive context if user pre-selected a drive
            drive_context = None
            if request.drive_sku:
                drive_context = lookup_drive(request.drive_sku)

            # Use single-shot (1 Sonnet call) by default, fall back to agentic if disabled
            stream_fn = single_shot_chat_stream if ENABLE_SINGLE_SHOT else chat_stream
            for event in stream_fn(request.message, history=history, drive_context=drive_context):
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


@app.get("/debug/email")
async def debug_email():
    """Debug endpoint — tests SMTP configuration and sends a test email."""
    import smtplib
    from email.mime.text import MIMEText

    smtp_host = os.getenv("SMTP_HOST", "")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_pass = os.getenv("SMTP_PASS", "")
    notify_email = os.getenv("NOTIFY_EMAIL", "cmillar@a-m-c.com,christianmillar31@gmail.com")

    # Step 1: Check env vars
    config = {
        "SMTP_HOST": smtp_host or "NOT SET",
        "SMTP_PORT": smtp_port,
        "SMTP_USER": smtp_user or "NOT SET",
        "SMTP_PASS": ("*" * len(smtp_pass)) if smtp_pass else "NOT SET",
        "SMTP_PASS_LENGTH": len(smtp_pass),
        "NOTIFY_EMAIL": notify_email,
    }

    if not all([smtp_host, smtp_user, smtp_pass]):
        return {"status": "FAIL", "error": "Missing env vars", "config": config}

    # Step 2: Try SMTP connection
    steps = []
    try:
        steps.append("Connecting to SMTP server...")
        server = smtplib.SMTP(smtp_host, smtp_port, timeout=10)
        steps.append(f"Connected to {smtp_host}:{smtp_port}")

        steps.append("Starting TLS...")
        server.starttls()
        steps.append("TLS OK")

        steps.append("Logging in...")
        server.login(smtp_user, smtp_pass)
        steps.append("Login OK")

        steps.append("Sending test email...")
        msg = MIMEText("<h3>AMC Chatbot Email Test</h3><p>If you see this, email notifications are working.</p>", "html")
        msg["Subject"] = "AMC Bot: Email Test"
        msg["From"] = smtp_user
        recipients = [e.strip() for e in notify_email.split(",")]
        msg["To"] = ", ".join(recipients)
        server.send_message(msg, to_addrs=recipients)
        steps.append(f"Sent to {recipients}")

        server.quit()
        steps.append("SMTP connection closed")

        return {"status": "OK", "steps": steps, "config": config}

    except Exception as e:
        steps.append(f"FAILED: {type(e).__name__}: {str(e)}")
        return {"status": "FAIL", "steps": steps, "config": config, "error": str(e)}


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
        # Also update the chatlog entry with the rating
        try:
            update_rating(request.session_id, request.question, request.rating)
        except Exception:
            pass  # Non-critical — don't fail the feedback save
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
