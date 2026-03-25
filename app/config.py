import os
from pathlib import Path
from dotenv import load_dotenv

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env", override=True)
PDF_DIR = BASE_DIR
INDEX_DIR = BASE_DIR / "index_data"

# Anthropic
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
INGEST_API_KEY = os.getenv("INGEST_API_KEY", "")  # Empty = dev mode (no auth required)
CLAUDE_MODEL = "claude-sonnet-4-20250514"
QUERY_EXPANSION_MODEL = "claude-haiku-4-5-20251001"
ENABLE_QUERY_EXPANSION = True

# Chunking
CHUNK_SIZE = 1500  # characters — larger chunks keep more context together
CHUNK_OVERLAP = 200  # more overlap prevents losing info at boundaries

# Retrieval
TOP_K = 6  # fewer chunks = less tokens sent to Claude
DEDUP_THRESHOLD = 0.70  # cosine similarity threshold for deduplication
MIN_RELEVANCE_SCORE = 0.10  # raised to filter noise — only send quality results
MAX_TOOL_ROUNDS = 4  # limit search rounds to control token spend

# API reliability
API_MAX_RETRIES = 4  # SDK handles 429 backoff natively with exponential retry
API_TIMEOUT = 120.0  # seconds per API call

# Session management
SESSION_TTL_SECONDS = 1800  # 30 minutes
MAX_SESSIONS = 100

# Re-ranking
ENABLE_RERANKING = True
RERANK_CANDIDATES = 15  # fetch this many from BM25/hybrid retrieval
RERANK_TOP_K = 6  # keep this many after cross-encoder re-ranking

# Models
EMBEDDING_MODEL = "BAAI/bge-base-en-v1.5"  # semantic embeddings (good accuracy, ~440MB vs 1.3GB for large)
RERANK_MODEL = "cross-encoder/ms-marco-MiniLM-L-12-v2"  # cross-encoder reranker (replaces LLM reranking)

# Shared Anthropic client (singleton)
import anthropic as _anthropic  # noqa: E402

_client = None


def get_anthropic_client() -> _anthropic.Anthropic:
    global _client
    if _client is None:
        _client = _anthropic.Anthropic(
            api_key=ANTHROPIC_API_KEY or os.getenv("ANTHROPIC_API_KEY", ""),
            max_retries=API_MAX_RETRIES,
            timeout=API_TIMEOUT,
        )
    return _client
