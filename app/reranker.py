import logging
from app.config import RERANK_MODEL

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

    Falls back to original order if the model is unavailable.
    """
    if not chunks or len(chunks) <= top_k:
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

        return [chunk for _, chunk in scored[:top_k]]

    except Exception as e:
        logger.warning("Cross-encoder reranking failed, using retrieval order: %s", e)
        return chunks[:top_k]
