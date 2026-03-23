import json
import pickle
from sklearn.metrics.pairwise import cosine_similarity

from app.config import INDEX_DIR, TOP_K, DEDUP_THRESHOLD, MIN_RELEVANCE_SCORE

# Module-level cache
_vectorizer = None
_tfidf_matrix = None
_chunks = None
_metadatas = None


def _load():
    global _vectorizer, _tfidf_matrix, _chunks, _metadatas
    if _vectorizer is None:
        with open(INDEX_DIR / "vectorizer.pkl", "rb") as f:
            _vectorizer = pickle.load(f)
        with open(INDEX_DIR / "tfidf_matrix.pkl", "rb") as f:
            _tfidf_matrix = pickle.load(f)
        with open(INDEX_DIR / "chunks.json", "r", encoding="utf-8") as f:
            data = json.load(f)
        _chunks = data["chunks"]
        _metadatas = data["metadatas"]


def reload():
    """Clear cached index so next retrieve() loads fresh data from disk."""
    global _vectorizer, _tfidf_matrix, _chunks, _metadatas
    _vectorizer = None
    _tfidf_matrix = None
    _chunks = None
    _metadatas = None


def retrieve(query: str, top_k: int = TOP_K, source_filter: str = None) -> list[dict]:
    """
    Retrieve the most relevant manual chunks for a query.
    Uses TF-IDF cosine similarity with vector-based dedup and score threshold.

    If source_filter is provided, only return chunks from matching source files.
    """
    _load()

    query_vec = _vectorizer.transform([query])
    similarities = cosine_similarity(query_vec, _tfidf_matrix).flatten()

    # Get more candidates than needed for dedup filtering
    candidate_count = top_k * 8  # more candidates when filtering by source
    top_indices = similarities.argsort()[::-1][:candidate_count]

    results = []
    selected_indices = []

    for idx in top_indices:
        if len(results) >= top_k:
            break

        score = float(similarities[idx])

        # Skip near-zero relevance (but always keep at least 1 result)
        if score < MIN_RELEVANCE_SCORE and results:
            continue

        # Source filtering: only include chunks from the target manual
        if source_filter:
            chunk_source = _metadatas[idx]["source"]
            if source_filter not in chunk_source:
                continue

        # Vector-based deduplication: compare TF-IDF vectors, not char prefixes
        if selected_indices:
            candidate_vec = _tfidf_matrix[idx]
            selected_vecs = _tfidf_matrix[selected_indices]
            pairwise_sims = cosine_similarity(candidate_vec, selected_vecs).flatten()
            if pairwise_sims.max() > DEDUP_THRESHOLD:
                continue

        selected_indices.append(idx)
        results.append({
            "text": _chunks[idx],
            "source": _metadatas[idx]["source"],
            "page": _metadatas[idx]["page"],
            "heading": _metadatas[idx].get("heading", ""),
            "score": score,
        })

    return results
