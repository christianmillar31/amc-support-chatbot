"""
Chat logging — automatically logs every question + answer for manager review.
Stored in chatlog.json. Viewable at /chatlog dashboard.
Optionally sends email notifications.
"""
import json
import logging
import os
import smtplib
import tempfile
import threading
from email.mime.text import MIMEText
from datetime import datetime, timezone

from app.config import BASE_DIR

logger = logging.getLogger(__name__)

CHATLOG_FILE = BASE_DIR / "chatlog.json"

# Email config — set these env vars to enable email notifications
NOTIFY_EMAIL = os.getenv("NOTIFY_EMAIL", "cmillar@a-m-c.com,christianmillar31@gmail.com")
SMTP_HOST = os.getenv("SMTP_HOST", "")  # e.g. smtp.gmail.com
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")


def _send_email_async(subject: str, body: str) -> None:
    """Send email notification in background thread (non-blocking)."""
    if not all([SMTP_HOST, SMTP_USER, SMTP_PASS, NOTIFY_EMAIL]):
        return  # Email not configured

    def _send():
        try:
            msg = MIMEText(body, "html")
            msg["Subject"] = subject
            msg["From"] = SMTP_USER
            recipients = [e.strip() for e in NOTIFY_EMAIL.split(",")]
            msg["To"] = ", ".join(recipients)
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
                server.starttls()
                server.login(SMTP_USER, SMTP_PASS)
                server.send_message(msg, to_addrs=recipients)
            logger.info("Email notification sent to %s", NOTIFY_EMAIL)
        except Exception as e:
            logger.warning("Email notification failed: %s", e)

    threading.Thread(target=_send, daemon=True).start()


def log_chat(
    session_id: str,
    question: str,
    answer: str,
    sources: list,
) -> None:
    """Append a chat entry to chatlog.json and optionally send email."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "question": question[:2000],
        "answer": answer[:5000],
        "rating": None,  # Will be updated by feedback endpoint
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

    # Keep last 500 entries to prevent unbounded growth
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
    # Find the most recent entry matching this session + question
    for entry in reversed(entries):
        if entry.get("session_id") == session_id and entry.get("question", "")[:100] == question[:100]:
            entry["rating"] = rating
            break
    else:
        return  # No match found

    _write_entries(entries)

    # Send email for thumbs-down feedback
    if rating == "down":
        _send_email_async(
            subject=f"AMC Bot: THUMBS DOWN — {question[:50]}",
            body=f"""
            <h3 style="color:red;">Negative Feedback</h3>
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
