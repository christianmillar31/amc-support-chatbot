import json
import logging
import os
import tempfile
from datetime import datetime, timezone

from app.config import BASE_DIR

logger = logging.getLogger(__name__)

FEEDBACK_FILE = BASE_DIR / "feedback.json"


def log_feedback(
    session_id: str,
    question: str,
    answer: str,
    sources: list[dict],
    rating: str,
    comment: str = "",
) -> None:
    """Append a feedback entry to feedback.json."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "question": question[:1000],
        "answer": answer[:2000],
        "sources": sources,
        "rating": rating,
        "comment": comment[:500],
    }

    entries = []
    if FEEDBACK_FILE.exists():
        try:
            with open(FEEDBACK_FILE, "r", encoding="utf-8") as f:
                entries = json.load(f)
        except Exception:
            logger.warning("Could not read feedback file, starting fresh")

    entries.append(entry)

    # Atomic write: temp file + rename to prevent corruption
    tmp_fd, tmp_path = tempfile.mkstemp(dir=FEEDBACK_FILE.parent, suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, FEEDBACK_FILE)
    except Exception:
        os.unlink(tmp_path)
        raise

    logger.info("Feedback logged: %s for session %s", rating, session_id)
