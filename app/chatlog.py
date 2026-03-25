"""
Chat logging — automatically logs every question + answer for manager review.
Stored locally in chatlog.json + synced to HF Dataset repo for persistence.
Viewable at /chatlog dashboard.
"""
import json
import logging
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

from app.config import BASE_DIR

logger = logging.getLogger(__name__)

CHATLOG_FILE = BASE_DIR / "chatlog.json"

# HF Hub sync config — pushes chatlog to a private dataset repo
HF_TOKEN = os.getenv("HF_TOKEN", "")
HF_CHATLOG_REPO = os.getenv("HF_CHATLOG_REPO", "FlameEnterprise/amc-chatlog")

# Email config — SMTP2GO REST API (HTTPS-based, works on HF Spaces)
SMTP2GO_API_KEY = os.getenv("SMTP2GO_API_KEY", "")
NOTIFY_EMAIL = os.getenv("NOTIFY_EMAIL", "cmillar@a-m-c.com,christianmillar31@gmail.com")
SENDER_EMAIL = os.getenv("SENDER_EMAIL", "christianmillar31@gmail.com")


def _sync_to_hf_async(entries: list) -> None:
    """Push chatlog.json to a private HF Dataset repo in background thread."""
    if not HF_TOKEN:
        return

    def _sync():
        try:
            from huggingface_hub import HfApi
            api = HfApi(token=HF_TOKEN)

            # Ensure repo exists (create if not)
            try:
                api.create_repo(
                    repo_id=HF_CHATLOG_REPO,
                    repo_type="dataset",
                    private=True,
                    exist_ok=True,
                )
            except Exception:
                pass  # Already exists

            # Write to temp file and upload
            tmp = tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False, encoding="utf-8"
            )
            try:
                json.dump(entries, tmp, indent=2, ensure_ascii=False)
                tmp.close()
                api.upload_file(
                    path_or_fileobj=tmp.name,
                    path_in_repo="chatlog.json",
                    repo_id=HF_CHATLOG_REPO,
                    repo_type="dataset",
                    commit_message=f"Chatlog update: {len(entries)} entries",
                )
                logger.info("Chatlog synced to HF: %d entries", len(entries))
            finally:
                os.unlink(tmp.name)
        except Exception as e:
            logger.warning("HF chatlog sync failed: %s", e)

    threading.Thread(target=_sync, daemon=True).start()


def _load_from_hf() -> list:
    """Load chatlog from HF Dataset repo on startup (if local file is empty)."""
    if not HF_TOKEN:
        return []
    try:
        from huggingface_hub import hf_hub_download
        path = hf_hub_download(
            repo_id=HF_CHATLOG_REPO,
            filename="chatlog.json",
            repo_type="dataset",
            token=HF_TOKEN,
        )
        with open(path, "r", encoding="utf-8") as f:
            entries = json.load(f)
        logger.info("Loaded %d chatlog entries from HF repo", len(entries))
        return entries
    except Exception as e:
        logger.info("No existing chatlog on HF: %s", e)
        return []


def _send_email_async(subject: str, body: str) -> None:
    """Send email via SMTP2GO REST API in background thread (non-blocking)."""
    if not SMTP2GO_API_KEY or not NOTIFY_EMAIL:
        return

    def _send():
        try:
            from urllib.request import Request, urlopen
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
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(req, timeout=10) as resp:
                result = resp.read().decode()
                logger.info("SMTP2GO email sent: %s", result)
        except Exception as e:
            logger.warning("SMTP2GO email failed: %s", e)

    threading.Thread(target=_send, daemon=True).start()


def log_chat(
    session_id: str,
    question: str,
    answer: str,
    sources: list,
) -> None:
    """Append a chat entry to chatlog.json, sync to HF, and send email."""
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

    entries = get_chatlog()
    entries.append(entry)

    if len(entries) > 1000:
        entries = entries[-1000:]

    _write_entries(entries)
    _sync_to_hf_async(entries)


def _write_entries(entries: list) -> None:
    """Atomic write entries to local chatlog.json."""
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
    _sync_to_hf_async(entries)

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
    """Read chat log entries. Loads from HF on first call if local file is empty."""
    if CHATLOG_FILE.exists():
        try:
            with open(CHATLOG_FILE, "r", encoding="utf-8") as f:
                entries = json.load(f)
            if entries:
                return entries
        except Exception:
            pass

    # Local file empty or missing — try loading from HF
    entries = _load_from_hf()
    if entries:
        _write_entries(entries)  # Cache locally
    return entries
