"""
Chat logging — automatically logs every question + answer for manager review.
Stored in chatlog.json. Viewable at /chatlog dashboard.
"""
import json
import logging
import os
import tempfile
from datetime import datetime, timezone

from app.config import BASE_DIR

logger = logging.getLogger(__name__)

CHATLOG_FILE = BASE_DIR / "chatlog.json"


def log_chat(
    session_id: str,
    question: str,
    answer: str,
    sources: list,
) -> None:
    """Append a chat entry to chatlog.json."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "question": question[:2000],
        "answer": answer[:5000],
        "sources": [
            {"source": s.get("source", ""), "page": s.get("page", ""), "heading": s.get("heading", "")}
            for s in (sources or [])
        ],
    }

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

    # Atomic write
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


def get_chatlog() -> list:
    """Read all chat log entries."""
    if not CHATLOG_FILE.exists():
        return []
    try:
        with open(CHATLOG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []
