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
CHUNK_OVERLAP = 400  # increased from 200 — reduces info loss at chunk boundaries, especially spec tables

# Retrieval
TOP_K = 10  # increased from 6 — more context chunks for Claude to reason over
MIN_RELEVANCE_SCORE = 0.005  # threshold for RRF fusion scores (range ~0.01-0.03 for good results)
MAX_TOOL_ROUNDS = 4  # limit search rounds to control token spend
ENABLE_SINGLE_SHOT = True  # search in Python first, send to Sonnet once (saves ~60% tokens)

# API reliability
API_MAX_RETRIES = 4  # SDK handles 429 backoff natively with exponential retry
API_TIMEOUT = 120.0  # seconds per API call

# Session management
SESSION_TTL_SECONDS = 1800  # 30 minutes
MAX_SESSIONS = 100

# PDF Upload
UPLOAD_MAX_PAGES = 20
UPLOAD_MAX_SIZE_MB = 10

# Re-ranking
ENABLE_RERANKING = True
RERANK_CANDIDATES = 25  # increased from 15 — larger pool for cross-encoder to filter
RERANK_TOP_K = 10  # increased from 6 — match new TOP_K
MIN_RERANK_SCORE = 0.15  # post-reranking confidence threshold — filter low-confidence results

# Models
EMBEDDING_MODEL = "BAAI/bge-large-en-v1.5"  # upgraded from bge-base — ~5-10% accuracy gain, 1024 dims
RERANK_MODEL = "BAAI/bge-reranker-base"  # upgraded from ms-marco-MiniLM — 109M params, better technical content
EMBEDDING_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "  # BGE asymmetric search prefix (queries only, not docs)

# LLM Backend — "anthropic" (cloud, costs tokens) or "ollama" (local, free)
LLM_BACKEND = os.getenv("LLM_BACKEND", "anthropic")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:14b")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

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
