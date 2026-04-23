from __future__ import annotations
"""
FAQ instant-match system — answers common questions in <1 second with zero API tokens.

Uses the same embedding model as the retriever to compute semantic similarity
between the user's question and the pre-built FAQ database.
"""
import csv
import logging
import re
import numpy as np
from pathlib import Path

from app.config import BASE_DIR, EMBEDDING_MODEL, EMBEDDING_QUERY_PREFIX

logger = logging.getLogger(__name__)

FAQ_FILE = BASE_DIR / "faq_index.csv"
FAQ_SIMILARITY_THRESHOLD = 0.78  # Lowered from 0.82 to catch more near-matches

# Phrases that indicate the user is asking about a whole family / multiple
# variants rather than a specific SKU. When the question has one of these
# indicators AND does not name a specific drive, we skip the FAQ shortcut so
# the live retrieval + canonical-spec path can answer with per-variant data.
# This prevents a narrow curated FAQ row (e.g. "Classic B-series only") from
# being served for a broad question that actually spans multiple families.
_BROAD_SCOPE_INDICATORS = (
    "all axcent", "all classic", "all digiflex", "all flexpro", "all amc",
    "which axcent", "which classic", "which digiflex", "which flexpro",
    "every axcent", "every classic", "every digiflex", "every flexpro",
    "all drives", "what drives", "which drives",
    "which variants", "all variants", "every variant",
    "analog drives", "axcent drives", "digiflex drives", "flexpro drives",
    "axcent pcb", "axcent panel", "axcent vehicle",
    "flexpro pcb", "flexpro panel",
    "digiflex pcb", "digiflex panel", "digiflex vehicle",
)

_faq_entries = None
_faq_embeddings = None
_embed_model = None


def _load_faq():
    """Load FAQ entries and compute embeddings."""
    global _faq_entries, _faq_embeddings, _embed_model

    if _faq_entries is not None:
        return

    if not FAQ_FILE.exists():
        logger.warning("FAQ file not found: %s", FAQ_FILE)
        _faq_entries = []
        return

    # Load CSV
    entries = []
    with open(FAQ_FILE, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            entries.append({
                "question": row.get("question", ""),
                "answer": row.get("answer_summary", ""),
                "source": row.get("manual_source", ""),
                "page": row.get("page", ""),
                "section": row.get("section", ""),
            })

    if not entries:
        _faq_entries = []
        return

    _faq_entries = entries

    # Compute embeddings for FAQ questions
    try:
        from sentence_transformers import SentenceTransformer
        _embed_model = SentenceTransformer(EMBEDDING_MODEL)
        questions = [e["question"] for e in entries]
        _faq_embeddings = _embed_model.encode(questions, batch_size=32, normalize_embeddings=True)
        logger.info("FAQ system loaded: %d entries with embeddings.", len(entries))
    except Exception as e:
        logger.warning("FAQ embeddings failed: %s. FAQ matching disabled.", e)
        _faq_embeddings = None


def _is_broad_scope_question(user_question: str) -> bool:
    """Return True when the question spans a whole family / multiple variants
    and does not name a specific SKU. In that case, a static FAQ row is
    likely too narrow — defer to the live retrieval path.
    """
    text = (user_question or "").lower()
    if not any(ind in text for ind in _BROAD_SCOPE_INDICATORS):
        return False
    # If the user also names a specific SKU, the FAQ route can stay; the
    # family phrase is likely incidental context. Very cheap check: any
    # alphanumeric token of length ≥ 5 with both letters and digits.
    import re
    for tok in re.findall(r"[A-Z0-9][A-Z0-9-]{4,}", user_question.upper()):
        if any(ch.isalpha() for ch in tok) and any(ch.isdigit() for ch in tok):
            return False
    return True


_DIAGNOSIS_QUESTION_RE = re.compile(
    r"\b(diagnose|diagnosing|troubleshoot|troubleshooting|fault|following\s+error|"
    r"not\s+working|won't\s+\w+|wont\s+\w+|doesn't\s+\w+|cannot\s+\w+|can'?t\s+\w+|"
    r"why\s+(is|does|won't|can't)|help\s+(me\s+)?(fix|diagnose|debug)|"
    r"fix\s+(this|the|my)|what's\s+wrong|whats\s+wrong|"
    r"under.?voltage|over.?voltage|over.?current|over.?temperature|"
    r"error\s+code|fault\s+code|red\s+led|drive\s+(error|fault|failed))\b",
    re.IGNORECASE,
)

_DIAGNOSIS_FAQ_RE = re.compile(
    r"\b(diagnose|diagnosing|troubleshoot|troubleshooting|fault|error|"
    r"not\s+working|won't|cannot|fix|what's\s+wrong|"
    r"under.?voltage|over.?voltage|over.?current|over.?temperature|"
    r"red\s+led|fault\s+code)\b",
    re.IGNORECASE,
)


def _is_diagnosis_question(user_question: str) -> bool:
    """Does the user's question look like a troubleshoot/diagnosis ask?"""
    return bool(_DIAGNOSIS_QUESTION_RE.search(user_question or ""))


def _faq_is_diagnostic(entry: dict) -> bool:
    """Does the FAQ row itself look like a troubleshoot/diagnosis answer?"""
    for field in ("question", "section"):
        if _DIAGNOSIS_FAQ_RE.search(entry.get(field, "") or ""):
            return True
    return False


def match_faq(user_question: str, threshold: float = FAQ_SIMILARITY_THRESHOLD) -> dict | None:
    """
    Check if the user's question matches an FAQ entry.
    Returns the FAQ entry dict if similarity >= threshold, else None.
    """
    _load_faq()

    if not _faq_entries or _faq_embeddings is None or _embed_model is None:
        return None

    # Scope guard: broad family/variant questions should go to the live
    # retrieval path so canonical family tables can ground the answer.
    if _is_broad_scope_question(user_question):
        logger.info("FAQ skipped for broad-scope question: %s", user_question[:80])
        return None

    # Intent guard: if the user asked a diagnosis/troubleshoot question, a FAQ
    # row about setup or tuning (even when semantically similar) is the wrong
    # answer. Only let the FAQ match through if the row itself looks
    # diagnostic. Otherwise fall through to the retrieval path, which will
    # pull the SW manual's troubleshooting tables (ACE p.141 / DriveWare
    # p.130) that actually answer the question.
    asked_diagnosis = _is_diagnosis_question(user_question)

    try:
        # Encode user question (with BGE query prefix for asymmetric search)
        q_embedding = _embed_model.encode([EMBEDDING_QUERY_PREFIX + user_question], normalize_embeddings=True)

        # Compute cosine similarity (embeddings are already normalized)
        similarities = np.dot(_faq_embeddings, q_embedding.T).flatten()

        best_idx = int(np.argmax(similarities))
        best_score = float(similarities[best_idx])

        if best_score >= threshold:
            entry = _faq_entries[best_idx]

            if asked_diagnosis and not _faq_is_diagnostic(entry):
                logger.info(
                    "FAQ skipped — diagnosis intent (%.2f): '%s' would have matched "
                    "non-diagnostic row '%s'; falling through to retrieval.",
                    best_score, user_question[:60], entry["question"][:60]
                )
                return None

            logger.info(
                "FAQ match (%.2f): '%s' → '%s'",
                best_score, user_question[:60], entry["question"][:60]
            )
            return {
                "question": entry["question"],
                "answer": entry["answer"],
                "source": entry["source"],
                "page": entry["page"],
                "section": entry["section"],
                "similarity": best_score,
            }

        return None

    except Exception as e:
        logger.warning("FAQ matching error: %s", e)
        return None
