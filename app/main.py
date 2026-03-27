import json
import logging
import os
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
            except Exception as e:
                logger.error("FAQ chat logging failed: %s", e, exc_info=True)

        return StreamingResponse(faq_event_generator(), media_type="text/event-stream")

    # Shared state — the generator writes into these, background task reads them
    stream_state = {"answer": "", "sources": [], "logged": False}

    async def event_generator():
        try:
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
                    stream_state["answer"] += event["text"]
                    yield f"event: token\ndata: {json.dumps(event)}\n\n"
                elif event["type"] == "done":
                    stream_state["sources"] = event.get("sources", [])
                    yield f"event: done\ndata: {json.dumps(event)}\n\n"

            # Update session history
            history.append({"role": "user", "content": request.message})
            history.append({"role": "assistant", "content": stream_state["answer"]})
            sessions[session_id] = history[-MAX_HISTORY:]

        except RateLimitExceeded:
            yield f"event: error\ndata: {json.dumps({'detail': 'The AI service is busy. Please wait a moment and try again.', 'retry_after': 30})}\n\n"
        except Exception as e:
            logger.error("Stream error: %s", e, exc_info=True)
            yield f"event: error\ndata: {json.dumps({'detail': 'An unexpected error occurred.'})}\n\n"
        finally:
            # ALWAYS log — even if client disconnects mid-stream
            if stream_state["answer"] and not stream_state["logged"]:
                stream_state["logged"] = True
                try:
                    log_chat(session_id, request.message, stream_state["answer"], stream_state["sources"])
                    logger.info("Chat logged: %s (%d chars)", request.message[:50], len(stream_state["answer"]))
                except Exception as e:
                    logger.error("CHAT LOGGING FAILED: %s", e, exc_info=True)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/debug/email")
async def debug_email():
    """Debug endpoint — tests SMTP2GO API and sends a test email."""
    from urllib.request import Request, urlopen
    from urllib.error import URLError

    api_key = os.getenv("SMTP2GO_API_KEY", "")
    notify_email = os.getenv("NOTIFY_EMAIL", "cmillar@a-m-c.com,christianmillar31@gmail.com")
    sender = os.getenv("SENDER_EMAIL", "christianmillar31@gmail.com")

    config = {
        "SMTP2GO_API_KEY": (api_key[:8] + "..." + api_key[-4:]) if api_key else "NOT SET",
        "SMTP2GO_KEY_LENGTH": len(api_key),
        "NOTIFY_EMAIL": notify_email,
        "SENDER_EMAIL": sender,
    }

    if not api_key:
        return {"status": "FAIL", "error": "SMTP2GO_API_KEY not set", "config": config}

    steps = []
    try:
        recipients = [e.strip() for e in notify_email.split(",")]
        steps.append(f"Sending to {recipients} via SMTP2GO API...")

        payload = json.dumps({
            "api_key": api_key,
            "to": recipients,
            "sender": sender,
            "subject": "AMC Bot: Email Test",
            "html_body": "<h3>AMC Chatbot Email Test</h3><p>If you see this, SMTP2GO email notifications are working!</p>",
        }).encode("utf-8")

        req = Request(
            "https://api.smtp2go.com/v3/mail/send",
            data=payload,
            headers={
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlopen(req, timeout=10) as resp:
            result = resp.read().decode()
            steps.append(f"SMTP2GO response: {result}")

        return {"status": "OK", "steps": steps, "config": config}

    except URLError as e:
        steps.append(f"FAILED: {e}")
        return {"status": "FAIL", "steps": steps, "config": config, "error": str(e)}
    except Exception as e:
        steps.append(f"FAILED: {type(e).__name__}: {e}")
        return {"status": "FAIL", "steps": steps, "config": config, "error": str(e)}


@app.get("/debug/chatlog-sync")
async def debug_chatlog_sync():
    """Debug endpoint — READ-ONLY check of HF Dataset sync status."""
    import os as _os
    hf_token = _os.getenv("HF_TOKEN", "")
    hf_repo = _os.getenv("HF_CHATLOG_REPO", "FlameEnterprise/amc-chatlog")

    info = {
        "HF_TOKEN": (hf_token[:8] + "..." + hf_token[-4:]) if hf_token else "NOT SET",
        "HF_TOKEN_LENGTH": len(hf_token),
        "HF_CHATLOG_REPO": hf_repo,
        "local_entries": len(get_chatlog()),
    }

    if not hf_token:
        return {"status": "FAIL", "error": "HF_TOKEN not set", "info": info}

    try:
        from huggingface_hub import HfApi
        api = HfApi(token=hf_token)

        # Check repo exists (read-only — does NOT upload or overwrite)
        repo_info = api.repo_info(repo_id=hf_repo, repo_type="dataset", token=hf_token)
        info["repo_exists"] = True
        info["repo_private"] = repo_info.private

        # Try to read the chatlog from HF
        try:
            from huggingface_hub import hf_hub_download
            path = hf_hub_download(
                repo_id=hf_repo, filename="chatlog.json",
                repo_type="dataset", token=hf_token, force_download=True,
            )
            with open(path, "r", encoding="utf-8") as f:
                hf_entries = json.load(f)
            info["hf_entries"] = len(hf_entries)
        except Exception as e:
            info["hf_entries"] = 0
            info["hf_read_error"] = str(e)

        return {"status": "OK", "info": info}
    except Exception as e:
        info["error"] = f"{type(e).__name__}: {e}"
        return {"status": "FAIL", "info": info}


@app.get("/debug/chatlog-write-test")
async def debug_chatlog_write_test():
    """Test that chatlog can actually write to disk."""
    from app.chatlog import CHATLOG_FILE, _write_local, get_chatlog
    import os as _os

    info = {
        "chatlog_path": str(CHATLOG_FILE),
        "parent_exists": CHATLOG_FILE.parent.exists(),
        "parent_writable": _os.access(str(CHATLOG_FILE.parent), _os.W_OK),
    }

    # Test 1: Can we write a file?
    test_file = CHATLOG_FILE.parent / ".write_test"
    try:
        test_file.write_text("test")
        test_file.unlink()
        info["write_test"] = "PASS"
    except Exception as e:
        info["write_test"] = f"FAIL: {e}"
        return {"status": "FAIL", "info": info}

    # Test 2: Current entry count
    try:
        entries = get_chatlog()
        info["current_entries"] = len(entries)
    except Exception as e:
        info["current_entries"] = f"ERROR: {e}"

    # Test 3: Can we write and read back?
    try:
        test_entry = {
            "timestamp": "write-test",
            "session_id": "debug",
            "question": "Write test",
            "answer": "OK",
            "rating": None,
            "sources": [],
        }
        existing = get_chatlog()
        existing.append(test_entry)
        _write_local(existing)

        # Read back
        if CHATLOG_FILE.exists():
            with open(CHATLOG_FILE, "r") as f:
                readback = json.load(f)
            info["write_readback"] = f"PASS ({len(readback)} entries)"
            # Remove the test entry
            readback = [e for e in readback if e.get("timestamp") != "write-test"]
            _write_local(readback)
        else:
            info["write_readback"] = "FAIL: file doesn't exist after write"
    except Exception as e:
        info["write_readback"] = f"FAIL: {e}"

    return {"status": "OK" if "PASS" in str(info.get("write_readback", "")) else "FAIL", "info": info}


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
