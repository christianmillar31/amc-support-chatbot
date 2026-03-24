import json
import logging
import pickle
import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

from app.config import INDEX_DIR, TOP_K, DEDUP_THRESHOLD, MIN_RELEVANCE_SCORE, EMBEDDING_MODEL

logger = logging.getLogger(__name__)

# Module-level cache
_bm25 = None
_vectorizer = None  # TF-IDF fallback
_tfidf_matrix = None  # TF-IDF fallback
_chunks = None
_metadatas = None
_embeddings = None
_embed_model = None


def _load():
    global _bm25, _vectorizer, _tfidf_matrix, _chunks, _metadatas, _embeddings, _embed_model
    if _chunks is not None:
        return  # Already loaded

    with open(INDEX_DIR / "chunks.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    _chunks = data["chunks"]
    _metadatas = data["metadatas"]

    # Load BM25 index (preferred)
    bm25_path = INDEX_DIR / "bm25.pkl"
    if bm25_path.exists():
        with open(bm25_path, "rb") as f:
            _bm25 = pickle.load(f)
        logger.info("BM25 index loaded (%d chunks).", len(_chunks))
    else:
        # Fallback to TF-IDF if BM25 not available
        with open(INDEX_DIR / "vectorizer.pkl", "rb") as f:
            _vectorizer = pickle.load(f)
        with open(INDEX_DIR / "tfidf_matrix.pkl", "rb") as f:
            _tfidf_matrix = pickle.load(f)
        logger.info("TF-IDF index loaded (BM25 not available).")

    # Load semantic embeddings if available
    embeddings_path = INDEX_DIR / "embeddings.npy"
    if embeddings_path.exists():
        try:
            _embeddings = np.load(embeddings_path)
            from sentence_transformers import SentenceTransformer
            _embed_model = SentenceTransformer(EMBEDDING_MODEL)
            logger.info("Hybrid retrieval enabled (BM25 + semantic embeddings).")
        except Exception as e:
            logger.warning("Could not load semantic embeddings: %s. Using BM25 only.", e)
            _embeddings = None
            _embed_model = None


def reload():
    """Clear cached index so next retrieve() loads fresh data from disk."""
    global _bm25, _vectorizer, _tfidf_matrix, _chunks, _metadatas, _embeddings, _embed_model
    _bm25 = None
    _vectorizer = None
    _tfidf_matrix = None
    _chunks = None
    _metadatas = None
    _embeddings = None
    _embed_model = None


def _reciprocal_rank_fusion(ranked_lists: list[list[int]], k: int = 60) -> list[tuple[int, float]]:
    """
    Merge multiple ranked lists using Reciprocal Rank Fusion (RRF).
    Returns list of (index, rrf_score) sorted by score descending.
    """
    scores: dict[int, float] = {}
    for ranked_list in ranked_lists:
        for rank, idx in enumerate(ranked_list):
            scores[idx] = scores.get(idx, 0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


def _bm25_rank(query: str, candidate_count: int) -> tuple[list[int], np.ndarray]:
    """Get BM25 rankings and scores."""
    tokenized_query = query.lower().split()
    scores = _bm25.get_scores(tokenized_query)
    top_indices = scores.argsort()[::-1][:candidate_count].tolist()
    return top_indices, scores


def _tfidf_rank(query: str, candidate_count: int) -> tuple[list[int], np.ndarray]:
    """Get TF-IDF rankings and scores (fallback)."""
    query_vec = _vectorizer.transform([query])
    scores = cosine_similarity(query_vec, _tfidf_matrix).flatten()
    top_indices = scores.argsort()[::-1][:candidate_count].tolist()
    return top_indices, scores


def retrieve(query: str, top_k: int = TOP_K, source_filter: str = None, doc_type_filter: str = None) -> list[dict]:
    """
    Retrieve the most relevant manual chunks for a query.
    Uses hybrid retrieval (BM25 + semantic embeddings with RRF) when available,
    falls back gracefully to BM25-only or TF-IDF-only.
    """
    _load()

    candidate_count = top_k * 8

    # --- Sparse ranking (BM25 preferred, TF-IDF fallback) ---
    if _bm25 is not None:
        sparse_ranked, sparse_scores = _bm25_rank(query, candidate_count)
    else:
        sparse_ranked, sparse_scores = _tfidf_rank(query, candidate_count)

    # --- Semantic ranking (if available) ---
    if _embeddings is not None and _embed_model is not None:
        try:
            query_embedding = _embed_model.encode([query])
            semantic_sims = cosine_similarity(query_embedding, _embeddings).flatten()
            semantic_ranked = semantic_sims.argsort()[::-1][:candidate_count].tolist()

            # Merge with Reciprocal Rank Fusion
            fused = _reciprocal_rank_fusion([sparse_ranked, semantic_ranked])
            candidate_indices = [idx for idx, _ in fused[:candidate_count]]
        except Exception as e:
            logger.warning("Semantic retrieval failed, using sparse only: %s", e)
            candidate_indices = sparse_ranked
    else:
        candidate_indices = sparse_ranked

    # --- Filter, dedup, and build results ---
    results = []
    selected_texts: list[str] = []  # For text-based dedup when TF-IDF matrix unavailable

    for idx in candidate_indices:
        if len(results) >= top_k:
            break

        score = float(sparse_scores[idx])

        # Skip near-zero relevance (but always keep at least 1 result)
        if score < MIN_RELEVANCE_SCORE and results:
            continue

        # Source filtering
        if source_filter:
            if _metadatas[idx]["source"] != source_filter:
                continue

        # Doc type filtering
        if doc_type_filter:
            if _metadatas[idx].get("doc_type", "") != doc_type_filter:
                continue

        # Deduplication
        chunk_text = _chunks[idx]
        if _tfidf_matrix is not None:
            # Vector-based dedup using TF-IDF vectors (most accurate)
            if selected_texts:
                # Use text overlap as a simpler dedup check
                chunk_start = chunk_text[:200]
                if any(chunk_start == t[:200] for t in selected_texts):
                    continue
        else:
            # Text prefix dedup when no TF-IDF matrix
            chunk_start = chunk_text[:200]
            if any(chunk_start == t[:200] for t in selected_texts):
                continue

        selected_texts.append(chunk_text)
        results.append({
            "text": chunk_text,
            "source": _metadatas[idx]["source"],
            "page": _metadatas[idx]["page"],
            "heading": _metadatas[idx].get("heading", ""),
            "score": score,
        })

    return results
