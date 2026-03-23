# AMC Support Chatbot

## Project Overview
RAG chatbot for AMC support engineers to search communication manuals, hardware installation manuals, and software manuals using Claude AI. Supports 120+ drives across FlexPro, DigiFlex, AxCent, and Classic families.

## Tech Stack
- **Backend**: Python 3.13, FastAPI, uvicorn
- **Frontend**: Single-page HTML with inline CSS, marked.js
- **AI**: Anthropic Claude Sonnet 4 (agentic tool-use answers), Haiku 4.5 (query expansion)
- **Search**: TF-IDF (scikit-learn) with cosine similarity, vector dedup
- **PDF Parsing**: PyMuPDF (fitz)
- **Drive Database**: CSV-powered lookup (CM Servo Info.csv) — 644 drives

## Project Structure
```
app/
  main.py          — FastAPI server, routes, session management
  config.py        — All configuration (API keys, chunk sizes, thresholds)
  ingest.py        — PDF extraction, smart chunking, TF-IDF index building
  chat.py          — Agentic tool-use chat, query expansion, Claude RAG prompt
  retriever.py     — TF-IDF cosine similarity search with dedup
  drive_lookup.py  — CSV-powered drive→manual routing (644 drives)
static/
  index.html       — Chat UI (single file)
  marked.min.js    — Local markdown renderer
index_data/        — Serialized TF-IDF index + chunks (4,848 chunks)
*.pdf              — 8 comm manuals + 12 HW install manuals + 5 SW manuals (25 total)
CM Servo Info.csv  — Master product database
```

## Running
```bash
python -m uvicorn app.main:app --port 8001
```

## Architecture
- **Agentic tool-use**: Claude decides what to search, can do multiple retrieval passes
- **Tools**: search_manuals, detect_drive_manual, list_available_manuals
- **Drive lookup**: Part number → CSV database → exact comm manual + HW manual
- **Software manuals**: ACE (primary), DriveWare, ClickMove — tool-specific, not drive-specific
- **Query expansion**: Haiku generates synonyms/technical terms before TF-IDF search
- **Follow-up rewriting**: Haiku rewrites vague follow-ups into standalone questions

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

## Drive Routing Logic
Powered by CM Servo Info.csv (644 drives). Key rules:
- FlexPro (FE/FM/FD/FMP/FX): EM→EtherCAT, IPM→Ethernet/IP, RM→Serial, CM→CANopen
- DigiFlex (DV/DP/DZ/DX): EAN→EtherCAT, CAN→CANopen, RA→Serial or Modbus (ask user)
- DVC→DigiFlex CANopen
- Machine Embedded & Development Board → use PCB Mount HW manual
- FlexPro Panel → uses FlexPro PCB HW manual
- DigiFlex PCB XEnv = EtherCAT/POWERLINK/DxM drives
