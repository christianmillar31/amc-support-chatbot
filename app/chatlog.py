"""
Chat logging — automatically logs every question + answer for manager review.
Stored locally in chatlog.json + synced to HF Dataset repo for persistence.
Viewable at /chatlog dashboard.
"""
from __future__ import annotations

from collections import Counter
import json
import logging
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

from app.config import BASE_DIR

logger = logging.getLogger(__name__)

# Use /tmp on Linux containers (always writable), fall back to BASE_DIR for local dev
_CHATLOG_DIR = Path("/tmp") if (Path("/tmp").exists() and os.access("/tmp", os.W_OK)) else BASE_DIR
CHATLOG_FILE = _CHATLOG_DIR / "chatlog.json"

# Write test at import time — fail loudly if broken
try:
    _test_path = CHATLOG_FILE.parent / ".chatlog_write_test"
    _test_path.write_text("ok")
    _test_path.unlink()
    logger.info("Chatlog write OK: %s", CHATLOG_FILE)
except Exception as e:
    logger.error("CHATLOG WRITE TEST FAILED at %s: %s", CHATLOG_FILE, e)

# HF Hub sync config
HF_TOKEN = os.getenv("HF_TOKEN", "")
HF_CHATLOG_REPO = os.getenv("HF_CHATLOG_REPO", "FlameEnterprise/amc-chatlog")

# Email config
SMTP2GO_API_KEY = os.getenv("SMTP2GO_API_KEY", "")
NOTIFY_EMAIL = os.getenv("NOTIFY_EMAIL", "cmillar@a-m-c.com,christianmillar31@gmail.com")
SENDER_EMAIL = os.getenv("SENDER_EMAIL", "christianmillar31@gmail.com")

# In-memory cache of all entries (survives local file issues)
_entries_cache: list = []
_cache_loaded = False


def _load_from_hf() -> list:
    """Download chatlog.json from HF Dataset repo. Force-skips cache."""
    if not HF_TOKEN:
        logger.info("HF_TOKEN not set — skipping HF chatlog load")
        return []
    try:
        from huggingface_hub import hf_hub_download
        logger.info("Loading chatlog from HF repo: %s", HF_CHATLOG_REPO)
        path = hf_hub_download(
            repo_id=HF_CHATLOG_REPO,
            filename="chatlog.json",
            repo_type="dataset",
            token=HF_TOKEN,
            force_download=True,
        )
        with open(path, "r", encoding="utf-8") as f:
            entries = json.load(f)
        # Filter out debug/test entries
        entries = [e for e in entries if not e.get("test")]
        logger.info("Loaded %d chatlog entries from HF repo", len(entries))
        return entries
    except Exception as e:
        logger.warning("Could not load chatlog from HF: %s", e)
        return []


def _sync_to_hf(entries: list) -> None:
    """Push chatlog.json to HF Dataset repo. Runs in background thread."""
    if not HF_TOKEN:
        return

    def _do_sync():
        try:
            from huggingface_hub import HfApi
            api = HfApi(token=HF_TOKEN)

            # Ensure repo exists
            try:
                api.create_repo(
                    repo_id=HF_CHATLOG_REPO,
                    repo_type="dataset",
                    private=True,
                    exist_ok=True,
                )
            except Exception:
                pass

            # Write and upload
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
                    commit_message=f"Chatlog: {len(entries)} entries",
                )
                logger.info("Chatlog synced to HF: %d entries", len(entries))
            finally:
                try:
                    os.unlink(tmp.name)
                except OSError:
                    pass
        except Exception as e:
            logger.warning("HF chatlog sync failed: %s", e)

    threading.Thread(target=_do_sync, daemon=True).start()


def _send_email_async(subject: str, body: str) -> None:
    """Send email via SMTP2GO REST API in background thread."""
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
                resp.read()
        except Exception as e:
            logger.warning("Email failed: %s", e)

    threading.Thread(target=_send, daemon=True).start()


def _write_local(entries: list) -> None:
    """Atomic write to local chatlog.json."""
    try:
        tmp_fd, tmp_path = tempfile.mkstemp(dir=CHATLOG_FILE.parent, suffix=".tmp")
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, CHATLOG_FILE)
    except Exception as e:
        logger.error("LOCAL CHATLOG WRITE FAILED at %s: %s", CHATLOG_FILE, e, exc_info=True)
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def _ensure_loaded() -> list:
    """Load entries from local file + HF repo on first call. Merge and deduplicate."""
    global _entries_cache, _cache_loaded
    if _cache_loaded:
        return _entries_cache

    _cache_loaded = True

    # Load local entries
    local_entries = []
    if CHATLOG_FILE.exists():
        try:
            with open(CHATLOG_FILE, "r", encoding="utf-8") as f:
                local_entries = json.load(f)
        except Exception:
            pass

    # Load HF entries
    hf_entries = _load_from_hf()

    # Merge: combine both, deduplicate by timestamp+question
    seen = set()
    merged = []
    for entry in hf_entries + local_entries:
        key = (entry.get("timestamp", ""), entry.get("question", "")[:100])
        if key not in seen:
            seen.add(key)
            merged.append(entry)

    # Sort by timestamp
    merged.sort(key=lambda e: e.get("timestamp", ""))

    # Cap at 1000
    if len(merged) > 1000:
        merged = merged[-1000:]

    _entries_cache = merged

    # Write merged result locally
    if merged:
        _write_local(merged)

    logger.info("Chatlog loaded: %d local + %d HF = %d merged entries",
                len(local_entries), len(hf_entries), len(merged))

    return _entries_cache


def log_chat(
    session_id: str,
    question: str,
    answer: str,
    sources: list,
    metadata: dict | None = None,
) -> None:
    """Log a chat entry. Saves locally, syncs to HF, sends email."""
    metadata = metadata or {}
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
        "provider_used": metadata.get("provider_used"),
        "model_used": metadata.get("model_used"),
        "latency_ms": int(metadata.get("latency_ms", 0) or 0),
        "estimated_cost_usd": float(metadata.get("estimated_cost_usd", 0.0) or 0.0),
        "support_bucket": metadata.get("support_bucket"),
        "retrieval_chunk_count": int(metadata.get("retrieval_chunk_count", 0) or 0),
        "used_fallback": bool(metadata.get("used_fallback", False)),
        "broad_retrieval": bool(metadata.get("broad_retrieval", False)),
        "channel": metadata.get("channel"),
        "drive_sku": metadata.get("requested_sku") or metadata.get("drive_sku"),
        "support_note": metadata.get("support_note"),
    }

    # Email notification
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

    # Add to cache and persist
    entries = _ensure_loaded()
    entries.append(entry)

    if len(entries) > 1000:
        entries = entries[-1000:]

    _entries_cache.clear()
    _entries_cache.extend(entries)

    _write_local(entries)
    _sync_to_hf(entries)


def update_rating(session_id: str, question: str, rating: str) -> None:
    """Update rating on the most recent matching entry."""
    entries = _ensure_loaded()
    matched_entry = None
    for entry in reversed(entries):
        if entry.get("session_id") == session_id and entry.get("question", "")[:100] == question[:100]:
            entry["rating"] = rating
            matched_entry = entry
            break

    if not matched_entry:
        return

    _write_local(entries)
    _sync_to_hf(entries)

    if rating == "down":
        _send_email_async(
            subject=f"AMC Bot: THUMBS DOWN — {question[:50]}",
            body=f"""
            <h3 style="color:red;">&#x1F44E; Negative Feedback</h3>
            <p><b>Question:</b> {question[:500]}</p>
            <p><b>Answer given:</b> {matched_entry.get('answer', '')[:1000]}</p>
            <p><small>Session: {session_id}</small></p>
            """
        )


def get_chatlog() -> list:
    """Return all chat log entries (merged local + HF)."""
    return list(_ensure_loaded())


def summarize_chatlog(entries: list | None = None, *, top_n: int = 5) -> dict:
    """Return lightweight admin metrics derived from request metadata."""
    entries = list(entries) if entries is not None else get_chatlog()
    today_prefix = datetime.now(timezone.utc).date().isoformat()
    today_entries = [entry for entry in entries if str(entry.get("timestamp", "")).startswith(today_prefix)]

    def _top_items(sorted_entries: list[dict], key_name: str, value_name: str) -> list[dict]:
        items = []
        for entry in sorted_entries[:top_n]:
            items.append({
                "question": entry.get("question", ""),
                "value": entry.get(key_name, 0),
                "label": value_name,
                "session_id": entry.get("session_id", ""),
            })
        return items

    expensive = sorted(entries, key=lambda entry: float(entry.get("estimated_cost_usd", 0.0) or 0.0), reverse=True)
    slow = sorted(entries, key=lambda entry: int(entry.get("latency_ms", 0) or 0), reverse=True)

    sku_counts = Counter(entry.get("drive_sku") for entry in entries if entry.get("drive_sku"))
    question_counts = Counter(entry.get("question", "").strip().lower() for entry in entries if entry.get("question"))

    common_questions = []
    for normalized_question, count in question_counts.most_common(top_n):
        exemplar = next((entry.get("question", "") for entry in entries if entry.get("question", "").strip().lower() == normalized_question), normalized_question)
        common_questions.append({"question": exemplar, "count": count})

    broad_or_fallback = [
        {
            "question": entry.get("question", ""),
            "retrieval_chunk_count": entry.get("retrieval_chunk_count", 0),
            "used_fallback": bool(entry.get("used_fallback", False)),
            "broad_retrieval": bool(entry.get("broad_retrieval", False)),
            "support_bucket": entry.get("support_bucket"),
        }
        for entry in entries
        if entry.get("used_fallback") or entry.get("broad_retrieval") or int(entry.get("retrieval_chunk_count", 0) or 0) >= 6
    ][:top_n]

    return {
        "today_requests": len(today_entries),
        "today_cost_usd": round(sum(float(entry.get("estimated_cost_usd", 0.0) or 0.0) for entry in today_entries), 6),
        "today_avg_latency_ms": round(
            sum(int(entry.get("latency_ms", 0) or 0) for entry in today_entries) / len(today_entries),
            1,
        ) if today_entries else 0.0,
        "most_expensive": _top_items(expensive, "estimated_cost_usd", "Estimated cost"),
        "highest_latency": _top_items(slow, "latency_ms", "Latency"),
        "common_skus": [{"sku": sku, "count": count} for sku, count in sku_counts.most_common(top_n)],
        "common_questions": common_questions,
        "broad_retrieval_or_fallback": broad_or_fallback,
    }
