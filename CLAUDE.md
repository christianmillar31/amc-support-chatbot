# AMC Support Chatbot

## Project Overview
RAG chatbot for AMC support engineers to search 372 PDFs (manuals, datasheets, app notes) using a local LLM (Ollama). Supports 644 drives across FlexPro, DigiFlex, AxCent, Classic, and Analog families. Deployed on Hugging Face Spaces.

## Tech Stack
- **Backend**: Python 3.11, FastAPI, uvicorn
- **Frontend**: Single-page HTML with inline CSS/JS, marked.js for markdown
- **AI**: Ollama (local, default `qwen3:8b`) — set via `LLM_BACKEND=ollama` + `OLLAMA_MODEL`. Anthropic Claude Sonnet 4 / Haiku 4.5 still supported when `LLM_BACKEND=anthropic`.
- **Search**: BM25 (rank_bm25) + semantic embeddings (sentence-transformers) with RRF fusion
- **Reranking**: Cross-encoder (ms-marco-MiniLM-L-12-v2) replaces LLM-based reranking
- **Embeddings**: BAAI/bge-base-en-v1.5
- **PDF Parsing**: PyMuPDF (fitz) with table extraction
- **Drive Database**: CSV-powered lookup (CM Servo Info.csv) — 644 drives
- **FAQ**: CSV-powered instant answers (faq_index.csv) — 152 entries, $0 token cost
- **Deployment**: HF Spaces (Docker), GitHub for source

## Project Structure
```
app/
  main.py          — FastAPI server, routes, session management, debug endpoints
  config.py        — All configuration (API keys, models, thresholds)
  ingest.py        — PDF extraction, smart chunking, BM25 + embedding index building
  chat.py          — Agentic + single-shot chat, query expansion, FAQ matching, Claude RAG
  retriever.py     — BM25 + semantic hybrid retrieval with RRF fusion and dedup
  reranker.py      — Cross-encoder reranking (replaces LLM-based scoring)
  drive_lookup.py  — CSV-powered drive→manual routing (644 drives)
  feedback.py      — Thumbs up/down feedback logging
  chatlog.py       — Chat logging for manager dashboard + email notifications
static/
  index.html       — Chat UI with drive selector, quick-start cards, streaming
  chatlog.html     — Manager dashboard (question/answer log with ratings)
  marked.min.js    — Local markdown renderer
index_data/        — Pre-built search index (26,719 chunks)
  chunks.json      — All text chunks with metadata
  bm25.pkl         — BM25 keyword index
  embeddings.npy   — Semantic embedding vectors
faq_index.csv      — 152 FAQ entries for instant answers
CM Servo Info.csv  — Master product database (644 drives)
*.pdf              — 372 PDFs (gitignored — too large for repo)
Dockerfile         — HF Spaces deployment config
```

## Document Corpus (372 PDFs, 26,719 chunks)
- **10 Communication manuals** — CANopen, EtherCAT, Ethernet/IP, Modbus, POWERLINK, RS485, Serial (DigiFlex + FlexPro)
- **15 Hardware installation manuals** — FlexPro, DigiFlex, AxCent, Analog (Panel/PCB/Vehicle/XEnv)
- **6 Software manuals** — ACE, DriveWare, ClickMove (full manuals + quick references)
- **55 Application notes** — Detailed how-to guides (tuning, PVT, TwinCAT, stepper setup, etc.)
- **268 Datasheets** — Per-drive spec sheets with pinouts, ratings, dimensions
- **6 Product notes** — Retrofit guides, wiring recommendations
- **2 White papers** — EtherCAT advantage, visual programming
- **10+ Other** — Compliance, certifications

## Running Locally
```bash
# Set API key
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env

# Start server (auto-loads pre-built index)
python -m uvicorn app.main:app --port 8001

# Re-ingest PDFs (only needed when adding new PDFs)
# Requires all PDFs in project root
python -c "from app.ingest import build_index; build_index()"
```

## Architecture

### Search Pipeline
```
User question
    ↓
FAQ match? → Yes → Instant answer ($0 tokens)
    ↓ No
Drive pre-selected? → Yes → Route to specific manuals
    ↓ No
Query expansion (Haiku) → BM25 + Semantic search → RRF fusion → Cross-encoder rerank → Claude Sonnet (single-shot or agentic)
```

### Key Features
- **FAQ system**: 152 pre-built Q&A pairs checked BEFORE calling Claude — $0 cost for common questions
- **Drive selector**: User can pre-select a drive on the UI, skipping the detect_drive_manual tool call
- **Single-shot mode**: Searches in Python first, sends results to Sonnet once (saves ~60% tokens vs full agentic loop)
- **Agentic fallback**: If single-shot finds weak results, falls back to multi-round agentic search
- **Hybrid retrieval**: BM25 (keyword) + BGE embeddings (semantic) merged with Reciprocal Rank Fusion
- **Cross-encoder reranking**: ms-marco-MiniLM-L-12-v2 scores query-document pairs (replaces fragile LLM reranking)
- **Chat logging**: Every Q&A logged to /chatlog dashboard with thumbs up/down ratings
- **Streaming**: Server-Sent Events for real-time answer delivery

### Tools (for agentic mode)
- `search_manuals` — Search all indexed docs with optional manual/doc_type filter
- `detect_drive_manual` — Part number → family, protocol, comm manual, HW manual
- `list_available_manuals` — Show all indexed manuals by category

## Deployment (HF Spaces)
- Docker-based deployment via `Dockerfile`
- Pre-built index included in repo (index_data/ via Git LFS)
- Environment variables set as HF Space secrets:
  - `ANTHROPIC_API_KEY` — Required
  - `SMTP2GO_API_KEY` — Optional, for email notifications
  - `NOTIFY_EMAIL` — Email recipients for chat notifications

## Drive Routing Logic
Powered by CM Servo Info.csv (644 drives). Key rules:
- FlexPro (FE/FM/FD/FMP/FX): EM→EtherCAT, IPM→Ethernet/IP, RM→Serial, CM→CANopen
- DigiFlex (DV/DP/DZ/DX): EAN→EtherCAT, CAN→CANopen, RA→Serial or Modbus (ask user)
- DVC→DigiFlex CANopen
- AxCent (AZ): Analog/PWM only — no comm manual, HW manual only
- Machine Embedded & Development Board → use PCB Mount HW manual
- FlexPro Panel → uses FlexPro PCB HW manual
- DigiFlex PCB XEnv = EtherCAT/POWERLINK/DxM drives

## Design Tokens
- **Primary**: #1e3a5f (AMC dark blue)
- **Primary Light**: #2563eb (interactive blue)
- **Surface**: #ffffff
- **Background**: #f8fafc
- **Border**: #e2e8f0
- **Text Primary**: #1e293b
- **Text Secondary**: #64748b
- **Font**: Inter (headings), system-ui (body)
- **Border Radius**: 12px (cards), 8px (buttons), 24px (pills)
