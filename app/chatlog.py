"""
Chat logging — automatically logs every question + answer for manager review.
Stored in chatlog.json. Viewable at /chatlog dashboard.
Sends email notifications via SMTP2GO REST API (HTTPS, works on HF Spaces).
"""
import json
import logging
import os
import tempfile
import threading
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import URLError

from app.config import BASE_DIR

logger = logging.getLogger(__name__)

CHATLOG_FILE = BASE_DIR / "chatlog.json"

# Email config — SMTP2GO REST API (HTTPS-based, works on HF Spaces)
SMTP2GO_API_KEY = os.getenv("SMTP2GO_API_KEY", "")
NOTIFY_EMAIL = os.getenv("NOTIFY_EMAIL", "cmillar@a-m-c.com,christianmillar31@gmail.com")
SENDER_EMAIL = os.getenv("SENDER_EMAIL", "christianmillar31@gmail.com")


def _send_email_async(subject: str, body: str) -> None:
    """Send email via SMTP2GO REST API in background thread (non-blocking)."""
    if not SMTP2GO_API_KEY or not NOTIFY_EMAIL:
        return

    def _send():
        try:
            recipients = [e.strip() for e in NOTIFY_EMAIL.split(",")]

            payload = json.dumps({
                "api_key": SMTP2GO_API_KEY,
                "to": recipients,
                "sender": SENDER_EMAIL,
                "subject": subject,
                "html_body": body,
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
                logger.info("SMTP2GO email sent: %s", result)
        except URLError as e:
            logger.warning("SMTP2GO email failed: %s", e)
        except Exception as e:
            logger.warning("SMTP2GO email failed: %s", e)

    threading.Thread(target=_send, daemon=True).start()


def log_chat(
    session_id: str,
    question: str,
    answer: str,
    sources: list,
) -> None:
    """Append a chat entry to chatlog.json and send email notification."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "question": question[:2000],
        "answer": answer[:5000],
        "rating": None,
        "sources": [
            {"source": s.get("source", ""), "page": s.get("page", ""), "heading": s.get("heading", "")}
            for s in (sources or [])
        ],
    }

    # Send email notification
    source_list = ", ".join(s.get("source", "") for s in (sources or [])[:3])
    _send_email_async(
        subject=f"AMC Bot: {question[:60]}",
        body=f"""
        <h3>New Question</h3>
        <p><b>Question:</b> {question[:500]}</p>
        <p><b>Answer:</b> {answer[:1000]}</p>
        <p><b>Sources:</b> {source_list}</p>
        <p><small>Session: {session_id} | {entry['timestamp']}</small></p>
        """
    )

    entries = []
    if CHATLOG_FILE.exists():
        try:
            with open(CHATLOG_FILE, "r", encoding="utf-8") as f:
                entries = json.load(f)
        except Exception:
            logger.warning("Could not read chatlog file, starting fresh")

    entries.append(entry)

    if len(entries) > 500:
        entries = entries[-500:]

    _write_entries(entries)


def _write_entries(entries: list) -> None:
    """Atomic write entries to chatlog.json."""
    tmp_fd, tmp_path = tempfile.mkstemp(dir=CHATLOG_FILE.parent, suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, CHATLOG_FILE)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def update_rating(session_id: str, question: str, rating: str) -> None:
    """Update the rating on the most recent matching chatlog entry."""
    entries = get_chatlog()
    for entry in reversed(entries):
        if entry.get("session_id") == session_id and entry.get("question", "")[:100] == question[:100]:
            entry["rating"] = rating
            break
    else:
        return

    _write_entries(entries)

    if rating == "down":
        _send_email_async(
            subject=f"AMC Bot: THUMBS DOWN — {question[:50]}",
            body=f"""
            <h3 style="color:red;">&#x1F44E; Negative Feedback</h3>
            <p><b>Question:</b> {question[:500]}</p>
            <p><b>Answer given:</b> {entry.get('answer', '')[:1000]}</p>
            <p><small>Session: {session_id}</small></p>
            """
        )


def get_chatlog() -> list:
    """Read all chat log entries."""
    if not CHATLOG_FILE.exists():
        return []
    try:
        with open(CHATLOG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []
