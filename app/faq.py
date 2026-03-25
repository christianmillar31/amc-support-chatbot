"""
FAQ instant-match system — answers common questions in <1 second with zero API tokens.

Uses the same embedding model as the retriever to compute semantic similarity
between the user's question and the pre-built FAQ database.
"""
import csv
import logging
import numpy as np
from pathlib import Path

from app.config import BASE_DIR, EMBEDDING_MODEL

logger = logging.getLogger(__name__)

FAQ_FILE = BASE_DIR / "faq_index.csv"
FAQ_SIMILARITY_THRESHOLD = 0.82  # Must be very confident to skip Claude

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


def match_faq(user_question: str, threshold: float = FAQ_SIMILARITY_THRESHOLD) -> dict | None:
    """
    Check if the user's question matches an FAQ entry.
    Returns the FAQ entry dict if similarity >= threshold, else None.
    """
    _load_faq()

    if not _faq_entries or _faq_embeddings is None or _embed_model is None:
        return None

    try:
        # Encode user question
        q_embedding = _embed_model.encode([user_question], normalize_embeddings=True)

        # Compute cosine similarity (embeddings are already normalized)
        similarities = np.dot(_faq_embeddings, q_embedding.T).flatten()

        best_idx = int(np.argmax(similarities))
        best_score = float(similarities[best_idx])

        if best_score >= threshold:
            entry = _faq_entries[best_idx]
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
