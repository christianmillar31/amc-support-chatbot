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
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")
QUERY_EXPANSION_MODEL = os.getenv("QUERY_EXPANSION_MODEL", "claude-haiku-4-5-20251001")
ENABLE_QUERY_EXPANSION = True
# Hard-disable the cheap-task query expansion path entirely. Used by --no-llm
# regression to guarantee zero cloud-model calls; also honored in prod as an
# escape hatch if the expansion model ever becomes a reliability problem.
DISABLE_QUERY_EXPANSION = os.getenv("DISABLE_QUERY_EXPANSION", "false").strip().lower() in {"1", "true", "yes", "on"}

# Chunking
CHUNK_SIZE = 1500  # characters — larger chunks keep more context together
CHUNK_OVERLAP = 400  # increased from 200 — reduces info loss at chunk boundaries, especially spec tables

# Retrieval
TOP_K = 10  # increased from 6 — more context chunks for Claude to reason over
MIN_RELEVANCE_SCORE = 0.005  # threshold for RRF fusion scores (range ~0.01-0.03 for good results)
MAX_TOOL_ROUNDS = 4  # limit search rounds to control token spend
ENABLE_SINGLE_SHOT = True  # search in Python first, send to Sonnet once (saves ~60% tokens)
ANSWER_MAX_TOKENS = int(os.getenv("ANSWER_MAX_TOKENS", "2200"))
PILOT_RETRIEVAL_TOP_K = int(os.getenv("PILOT_RETRIEVAL_TOP_K", "6"))
PILOT_CONTEXT_MAX_CHARS = int(os.getenv("PILOT_CONTEXT_MAX_CHARS", "950"))

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
ANSWER_PROVIDER = os.getenv("ANSWER_PROVIDER", "anthropic")
CHEAP_TASK_PROVIDER = os.getenv("CHEAP_TASK_PROVIDER", "anthropic_haiku")
LOCAL_PROVIDER = os.getenv("LOCAL_PROVIDER", "ollama")
_LEGACY_LLM_BACKEND = os.getenv("LLM_BACKEND", "").strip().lower()
LLM_BACKEND = _LEGACY_LLM_BACKEND or ("ollama" if ANSWER_PROVIDER == "ollama" else "anthropic")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:8b")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
FAQ_ENABLED = os.getenv("FAQ_ENABLED", "true").strip().lower() not in {"0", "false", "no"}
PILOT_ENABLE_AGENTIC_FALLBACK = os.getenv("PILOT_ENABLE_AGENTIC_FALLBACK", "false").strip().lower() in {"1", "true", "yes", "on"}
PILOT_SESSION_REQUEST_CAP = int(os.getenv("PILOT_SESSION_REQUEST_CAP", "30"))
PILOT_DAILY_BUDGET_USD = float(os.getenv("PILOT_DAILY_BUDGET_USD", "25.0"))
PILOT_BUDGET_MODE = os.getenv("PILOT_BUDGET_MODE", "warn").strip().lower()
PILOT_BUDGET_WARNING_RATIO = float(os.getenv("PILOT_BUDGET_WARNING_RATIO", "0.9"))

# Anthropic pricing defaults (USD / million tokens).
# Override in env if pricing changes for your account or region.
ANTHROPIC_SONNET_INPUT_COST_PER_MTOK = float(os.getenv("ANTHROPIC_SONNET_INPUT_COST_PER_MTOK", "3.0"))
ANTHROPIC_SONNET_OUTPUT_COST_PER_MTOK = float(os.getenv("ANTHROPIC_SONNET_OUTPUT_COST_PER_MTOK", "15.0"))
ANTHROPIC_SONNET_CACHE_WRITE_COST_PER_MTOK = float(os.getenv("ANTHROPIC_SONNET_CACHE_WRITE_COST_PER_MTOK", "3.75"))
ANTHROPIC_SONNET_CACHE_READ_COST_PER_MTOK = float(os.getenv("ANTHROPIC_SONNET_CACHE_READ_COST_PER_MTOK", "0.30"))
ANTHROPIC_HAIKU_INPUT_COST_PER_MTOK = float(os.getenv("ANTHROPIC_HAIKU_INPUT_COST_PER_MTOK", "1.0"))
ANTHROPIC_HAIKU_OUTPUT_COST_PER_MTOK = float(os.getenv("ANTHROPIC_HAIKU_OUTPUT_COST_PER_MTOK", "5.0"))
ANTHROPIC_HAIKU_CACHE_WRITE_COST_PER_MTOK = float(os.getenv("ANTHROPIC_HAIKU_CACHE_WRITE_COST_PER_MTOK", "1.25"))
ANTHROPIC_HAIKU_CACHE_READ_COST_PER_MTOK = float(os.getenv("ANTHROPIC_HAIKU_CACHE_READ_COST_PER_MTOK", "0.10"))

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
