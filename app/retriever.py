import hashlib
import json
import logging
import pickle
import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

from app.config import INDEX_DIR, TOP_K, MIN_RELEVANCE_SCORE, EMBEDDING_MODEL, EMBEDDING_QUERY_PREFIX


# Doc types that are OFF by default: not included in retrieval unless the
# caller explicitly passes ``doc_type_filter="marketing"`` (etc). These are
# indexed so they can be surfaced by opt-in queries, but kept out of the
# default top-K to avoid polluting technical-support answers with
# sales/marketing or RMA-process material.
OFF_BY_DEFAULT_DOC_TYPES: frozenset[str] = frozenset({"marketing", "rma"})

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
            logger.info("Hybrid retrieval enabled (BM25 + semantic embeddings, dims=%d).", _embeddings.shape[1])
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


def get_indexed_sources() -> set[str]:
    """Return the set of all source filenames in the loaded index."""
    _load()
    if not _metadatas:
        return set()
    return {m["source"] for m in _metadatas}


def get_chunk_count() -> int:
    """Return the number of chunks in the loaded index."""
    _load()
    return len(_chunks) if _chunks else 0


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


def _semantic_rank(query: str, candidate_count: int) -> list[int]:
    """Get semantic rankings using embedding similarity.
    Applies BGE query prefix for asymmetric search."""
    prefixed_query = EMBEDDING_QUERY_PREFIX + query
    query_embedding = _embed_model.encode([prefixed_query])
    semantic_sims = cosine_similarity(query_embedding, _embeddings).flatten()
    return semantic_sims.argsort()[::-1][:candidate_count].tolist()


def _normalize_doc_type_filter(doc_type_filter):
    """Accept None, a single string, a comma-separated string, or a list/set.

    The agentic tool-use path tends to emit comma-separated strings (e.g.
    ``"app_note,hw"``) even when the schema says single-value. Before this
    normalizer the retriever silently returned zero chunks because no
    metadata's ``doc_type`` field equals the literal string
    ``"app_note,hw"``. Now we accept any of:
        None              -> None (no filter)
        "datasheet"       -> {"datasheet"}
        "app_note,hw"     -> {"app_note", "hw"}
        ["hw", "comm"]    -> {"hw", "comm"}
    """
    if doc_type_filter is None:
        return None
    if isinstance(doc_type_filter, (list, tuple, set)):
        values = {str(v).strip() for v in doc_type_filter if str(v).strip()}
    else:
        values = {v.strip() for v in str(doc_type_filter).split(",") if v.strip()}
    return values or None


def retrieve(query: str, top_k: int = TOP_K, source_filter: str = None,
             doc_type_filter=None, expanded_query: str = None) -> list[dict]:
    """
    Retrieve the most relevant manual chunks for a query.

    Uses 3-way hybrid retrieval when expanded_query is provided:
      1. BM25 on original query (precise keyword matching)
      2. Semantic on original query (precise intent)
      3. Semantic on expanded query (broader recall)
    All merged with Reciprocal Rank Fusion.

    Falls back gracefully to 2-way or BM25-only if embeddings unavailable.
    """
    _load()

    candidate_count = top_k * 8

    # --- Sparse ranking: BM25 on ORIGINAL query only (expansion dilutes BM25) ---
    if _bm25 is not None:
        sparse_ranked, sparse_scores = _bm25_rank(query, candidate_count)
    else:
        sparse_ranked, sparse_scores = _tfidf_rank(query, candidate_count)

    # --- Semantic ranking (if available) ---
    rrf_scores: dict[int, float] = {}  # Track fused scores for hybrid mode
    use_hybrid = False

    if _embeddings is not None and _embed_model is not None:
        try:
            # Semantic on original query
            semantic_ranked_orig = _semantic_rank(query, candidate_count)
            ranked_lists = [sparse_ranked, semantic_ranked_orig]

            # Semantic on expanded query (3-way RRF) — broader recall without BM25 dilution
            if expanded_query and expanded_query != query:
                semantic_ranked_exp = _semantic_rank(expanded_query, candidate_count)
                ranked_lists.append(semantic_ranked_exp)

            # Merge all ranked lists with RRF
            fused = _reciprocal_rank_fusion(ranked_lists)
            rrf_scores = {idx: score for idx, score in fused}
            candidate_indices = [idx for idx, _ in fused[:candidate_count]]
            use_hybrid = True
        except Exception as e:
            logger.warning("Semantic retrieval failed, using sparse only: %s", e)
            candidate_indices = sparse_ranked
    else:
        candidate_indices = sparse_ranked

    # --- Filter, dedup, and build results ---
    # Normalize doc_type_filter once (not per-chunk) — supports None, a single
    # string, a comma-separated string, or a list/set.
    dtf_set = _normalize_doc_type_filter(doc_type_filter)

    results = []
    seen_hashes: set[str] = set()

    for idx in candidate_indices:
        if len(results) >= top_k:
            break

        # Use RRF fusion score when hybrid mode is active, raw BM25 otherwise
        score = rrf_scores.get(idx, float(sparse_scores[idx])) if use_hybrid else float(sparse_scores[idx])

        # Skip near-zero relevance (but always keep at least 1 result)
        if score < MIN_RELEVANCE_SCORE and results:
            continue

        # Source filtering
        if source_filter:
            if _metadatas[idx]["source"] != source_filter:
                continue

        # Doc type filtering (dtf_set was normalized once before the loop).
        chunk_doc_type = _metadatas[idx].get("doc_type", "")
        if dtf_set:
            if chunk_doc_type not in dtf_set:
                continue
        else:
            # No explicit doc_type asked for — drop types flagged as off-by-default
            # (marketing, rma). They can still be reached via explicit filter.
            if chunk_doc_type in OFF_BY_DEFAULT_DOC_TYPES:
                continue

        # Deduplication (hash-based on full text)
        chunk_text = _chunks[idx]
        chunk_hash = hashlib.md5(chunk_text.strip().encode()).hexdigest()
        if chunk_hash in seen_hashes:
            continue
        seen_hashes.add(chunk_hash)
        results.append({
            "text": chunk_text,
            "source": _metadatas[idx]["source"],
            "page": _metadatas[idx]["page"],
            "heading": _metadatas[idx].get("heading", ""),
            "doc_type": _metadatas[idx].get("doc_type", ""),
            "score": score,
        })

    return results
