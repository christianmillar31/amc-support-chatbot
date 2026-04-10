import logging
from app.config import RERANK_MODEL, MIN_RERANK_SCORE

logger = logging.getLogger(__name__)

# Lazy-loaded cross-encoder model (singleton)
_cross_encoder = None


def _load_model():
    global _cross_encoder
    if _cross_encoder is None:
        try:
            from sentence_transformers import CrossEncoder
            _cross_encoder = CrossEncoder(RERANK_MODEL)
            logger.info("Cross-encoder reranker loaded: %s", RERANK_MODEL)
        except ImportError:
            logger.warning("sentence-transformers not installed. Reranking disabled.")
        except Exception as e:
            logger.warning("Failed to load cross-encoder: %s. Reranking disabled.", e)


def rerank(query: str, chunks: list[dict], top_k: int = 6) -> list[dict]:
    """
    Re-rank retrieved chunks using a cross-encoder model.
    Cross-encoders examine full query-document pairs simultaneously,
    achieving higher accuracy than bi-encoders or LLM-based scoring.

    Always runs the cross-encoder regardless of pool size — even <=top_k
    chunks benefit from reranking by true relevance rather than retrieval order.

    Falls back to original order if the model is unavailable.
    """
    if not chunks:
        return chunks

    _load_model()

    if _cross_encoder is None:
        return chunks[:top_k]

    try:
        # Build query-document pairs for the cross-encoder
        pairs = [(query, chunk["text"]) for chunk in chunks]

        # Score all pairs in a single batch
        scores = _cross_encoder.predict(pairs)

        # Pair scores with chunks, sort descending
        scored = sorted(zip(scores, chunks), key=lambda x: x[0], reverse=True)

        # Attach normalized cross-encoder scores (0-1) so Claude sees true relevance
        # Filter out low-confidence results but always keep at least 1
        max_score = float(max(scores)) if float(max(scores)) > 0 else 1.0
        result = []
        for s, chunk in scored[:top_k]:
            norm_score = float(s / max_score)
            if norm_score < MIN_RERANK_SCORE and result:
                continue  # Skip low-confidence, but keep at least 1
            chunk["score"] = norm_score
            result.append(chunk)
        return result

    except Exception as e:
        logger.warning("Cross-encoder reranking failed, using retrieval order: %s", e)
        return chunks[:top_k]
