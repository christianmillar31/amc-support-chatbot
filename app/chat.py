from __future__ import annotations
import csv
import json
import logging
import re
import anthropic
import numpy as np
from app.config import (
    CLAUDE_MODEL, QUERY_EXPANSION_MODEL, ENABLE_QUERY_EXPANSION,
    MAX_TOOL_ROUNDS, BASE_DIR, EMBEDDING_MODEL, EMBEDDING_QUERY_PREFIX,
    ENABLE_RERANKING, RERANK_CANDIDATES, RERANK_TOP_K, TOP_K,
    get_anthropic_client, ENABLE_SINGLE_SHOT,
)
from app.retriever import retrieve, get_indexed_sources
from app.ingest import get_all_pdfs
from app.drive_lookup import lookup_drive, search_drives, detect_part_number, lookup_replacement
from app.reranker import rerank

logger = logging.getLogger(__name__)


def _load_glossary() -> str:
    """Load glossary.csv and return a condensed string for the system prompt.
    Truncates definitions to 80 chars to keep token count under ~4K tokens."""
    glossary_path = BASE_DIR / "glossary.csv"
    if not glossary_path.exists():
        return ""
    lines = []
    with open(glossary_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            term = row.get("term", "").strip()
            definition = row.get("definition", "").strip()
            if term and definition:
                short_def = definition[:80].rstrip() + ("..." if len(definition) > 80 else "")
                lines.append(f"- {term}: {short_def}")
    if not lines:
        return ""
    return "\n\nGLOSSARY — AMC terms/acronyms:\n" + "\n".join(lines)


_GLOSSARY_TEXT = _load_glossary()

_upload_embed_model = None


def _rank_uploaded_chunks(query: str, chunks: list[dict], top_k: int = 6) -> list[dict]:
    """Rank uploaded PDF chunks by semantic similarity to the query, return top-k."""
    if len(chunks) <= top_k:
        return chunks
    global _upload_embed_model
    try:
        if _upload_embed_model is None:
            from sentence_transformers import SentenceTransformer
            _upload_embed_model = SentenceTransformer(EMBEDDING_MODEL)
        texts = [c["text"] for c in chunks]
        q_emb = _upload_embed_model.encode([EMBEDDING_QUERY_PREFIX + query], normalize_embeddings=True)
        c_emb = _upload_embed_model.encode(texts, normalize_embeddings=True)
        scores = np.dot(c_emb, q_emb.T).flatten()
        top_indices = scores.argsort()[::-1][:top_k].tolist()
        return [chunks[i] for i in top_indices]
    except Exception as e:
        logger.warning("Upload chunk ranking failed, using first %d: %s", top_k, e)
        return chunks[:top_k]


class RateLimitExceeded(Exception):
    """Raised when Claude API rate limit is hit after all retries."""
    pass

SYSTEM_PROMPT = """You are AMC's technical support assistant. Be direct, concise, and technical. Users are experienced engineers.

TOOLS: search_manuals (search 372 PDFs), detect_drive_manual (part number → manual routing), find_replacement_drive (discontinued → AxCent replacement), list_available_manuals.

DOC TYPES: comm (protocols/registers), hw (wiring/connectors/pinouts), sw/sw_ref (ACE/DriveWare/ClickMove), app_note (procedures/tuning), datasheet (specs per drive), product_note (retrofits).

STRATEGY:
1. Part number mentioned → call detect_drive_manual first, then search the identified manuals.
2. If user asks about replacing/upgrading a discontinued analog drive → call find_replacement_drive.
3. Use doc_type filter to narrow searches. Use manual_filter when you know the exact manual.
4. One focused search usually suffices. Only do a second search if the first had low relevance (<0.15) or missed key info.
5. For specs (current, voltage, dimensions) → search datasheets. For procedures → search app_notes.

ANSWER RULES:
1. Be CONCISE. Give the direct answer with exact values, steps, or parameters. No filler. Aim for 3-8 sentences for simple questions, longer only for multi-step procedures.
2. Cite sources: [Source: filename, Page X]. Include section heading if available.
3. Quote exact register addresses, hex values, pin numbers, and parameter settings from results. ALWAYS include units with numeric values (A, V, W, ms, RPM, mm, in, etc.).
4. Use numbered steps for procedures, tables for specs/pinouts, bullets for lists.
5. If info is incomplete, state what's missing and which manual section to check.
6. Max 2 search rounds per question. Don't over-search.
7. Format answers for readability: use markdown headers (##), line breaks between sections, and keep paragraphs short (2-3 sentences max per paragraph).
8. If search results from different manuals contain conflicting information, cite both sources and note the discrepancy. Prefer: (1) drive-specific datasheet over general manual, (2) newer document revision over older, (3) more specific manual (e.g., comm manual) over general HW manual.
9. Chunks marked [UPLOADED DOCUMENT] are user-provided (e.g., motor datasheets), not official AMC sources. Use their specs but clearly note the data came from the user's uploaded document.

FOLLOW-UP GUIDANCE:
- At the end of your answer, suggest 1-2 natural follow-up questions the user might want to ask next, formatted as:
  **Related questions you might have:**
  - [follow-up question 1]
  - [follow-up question 2]
- Only include follow-ups that are genuinely useful and related to what was just asked. Skip this section if the answer is self-contained and no follow-up would be helpful.

ABSOLUTE RULES — VIOLATION OF THESE IS A CRITICAL FAILURE:
10. NEVER invent drive model numbers, SKUs, or part numbers. AMC drive SKUs follow SPECIFIC patterns: FlexPro = FE/FM/FD/FMP/FX + voltage code + current + protocol (e.g., FE060-25-EM). DigiFlex = DP/DZ/DX + model code (e.g., DPRALTE-020B080). AxCent = AZ + model (e.g., AZBH10A4). If you cannot find a specific drive model in search results, DO NOT make one up. Say "Search the AMC product selector at a-m-c.com/products/servo-drives for drives matching your requirements."
11. NEVER invent document names, page numbers, section names, register addresses, or technical specifications. Every single fact, number, and citation in your answer MUST come from the search results provided to you. If the search results don't contain the information, say "I could not find this specific information in the indexed manuals. Please contact AMC technical support or check a-m-c.com/downloads."
12. NEVER fabricate tables of specifications, feature comparisons, or product listings unless every value comes directly from search results. If asked to list drives with certain features, ONLY list drives whose datasheets appeared in your search results with those exact specs confirmed.
13. When uncertain, say "I don't have enough information in the indexed manuals to answer this confidently" rather than guessing. Then suggest: (a) which specific manual or section might contain the answer, (b) contacting AMC tech support, or (c) checking a-m-c.com. Engineers rely on this information for real hardware decisions — wrong specs can damage equipment or cause safety issues.

OFF-TOPIC / NON-TECHNICAL MESSAGES:
- If the user sends greetings ("hi", "hello"), respond briefly and ask how you can help with AMC drives.
- If the user sends abuse, complaints, or non-technical messages ("you are useless", "this sucks"), respond calmly and professionally: acknowledge their frustration, then redirect — e.g., "I'm sorry to hear that. Let me know what specific AMC drive question I can help with and I'll do my best to find the answer."
- Do NOT call any search tools for off-topic messages. Just respond directly.
- If asked about non-AMC topics (weather, coding, etc.), politely explain you only assist with AMC servo drive questions.

KEY NOTES:
- "EM" = EtherCAT, "IPM" = Ethernet/IP — NEVER confuse these.
- DigiFlex "RA" drives: ask user if Serial or Modbus.
- AxCent: analog/PWM only, no comm manual.
- ACE is default software tool. DriveWare is alternative.
- Machine Embedded/Dev Board use PCB Mount HW manual. FlexPro Panel uses FlexPro PCB HW manual.

CRITICAL — DRIVE CLASSIFICATION:
- There is NO SUCH THING as an "analog DigiFlex drive." ALL DigiFlex drives are DIGITAL servo drives.
- Some DigiFlex drives accept ±10V analog COMMAND INPUT — this is an input option, NOT the drive type.
- "Analog drives" refers ONLY to the Classic/Analog family (B-series, like B30A40, 100A40, 120A10). These are a completely separate product line.
- AxCent drives also accept analog/PWM command input but they are DIGITAL drives, not "analog drives."
- NEVER describe a FlexPro, DigiFlex, or AxCent drive as an "analog drive." Say "accepts ±10V analog command input" instead.
- DPQ drives (e.g., DPQNNIE) are SynqNet protocol drives.""" + _GLOSSARY_TEXT


# ---------------------------------------------------------------------------
# Tool schemas for Anthropic tool-use API
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "search_manuals",
        "description": (
            "Search AMC manuals (communication, hardware installation, and software) for relevant information. "
            "Uses keyword matching, so provide specific technical terms, register names, "
            "object dictionary indices, protocol keywords, connector names, pinouts, etc. "
            "You can call this multiple times with different queries to find more information. "
            "Use manual_filter when you know which specific manual to search."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query. Use specific technical terms, register names, protocol keywords. More specific = better results.",
                },
                "manual_filter": {
                    "type": "string",
                    "description": "Optional: filter results to a specific manual filename (e.g., 'AMC_CommManual_FP_EtherCAT.pdf' or 'AMC_HWManual_FlexPro_PCB.pdf'). Use when you know which manual is relevant.",
                },
                "doc_type": {
                    "type": "string",
                    "enum": ["comm", "hw", "sw", "sw_ref", "app_note", "product_note", "datasheet"],
                    "description": "Optional: filter by document type. 'comm' = communication manuals, 'hw' = hardware installation manuals, 'sw' = software manuals, 'sw_ref' = software quick references, 'app_note' = application notes (detailed how-to guides), 'product_note' = product notes (retrofit guides, wiring recommendations), 'datasheet' = per-drive datasheets with specifications, current ratings, voltages, dimensions, weight, pinouts. Use when you want all manuals of a type without specifying a filename.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "detect_drive_manual",
        "description": (
            "Look up an AMC drive part number in the product database and return its drive family, "
            "form factor, network communication type, and the exact comm manual and HW install manual filenames. "
            "Call this whenever the user mentions a part number like FE060-25-EM, DPRALTE-020B080, or AZBH10A4. "
            "This uses a complete database of 120+ drives — it will give you definitive routing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "part_number": {
                    "type": "string",
                    "description": "AMC drive part number / SKU (e.g., 'FE060-25-EM', 'DPRALTE-020B080', 'AZBH10A4')",
                },
            },
            "required": ["part_number"],
        },
    },
    {
        "name": "find_replacement_drive",
        "description": (
            "Look up the AxCent replacement for a discontinued analog drive model. "
            "Use this when a user asks what replaces an older AMC analog drive like 12A8, 30A8, B15A8, BE25A20, etc. "
            "Covers all discontinued Brushed (12A8, 25A8, 20A14, 20A20, 30A8, 50A8, 25A20, 50A20, 16A20AC, 30A20AC) "
            "and Brushless (B12A6, B15A8, BE12A6, BE15A8, BX15A20, B30A8, BE30A8, B40A8, BE40A8, B25A20, BE25A20, B40A20, BE40A20) "
            "analog drive families. Returns the AxCent model(s) that replace each discontinued drive, "
            "including mode-specific replacements."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "part_number": {
                    "type": "string",
                    "description": "Discontinued analog drive model number (e.g., '12A8', 'B15A8', 'BE25A20', '30A8I'). Revision letters and suffixes like -INV, -QD are automatically stripped.",
                },
            },
            "required": ["part_number"],
        },
    },
    {
        "name": "list_available_manuals",
        "description": (
            "List all AMC manuals (communication, hardware installation, and software) that are indexed and available for searching. "
            "Use this when you're unsure which manual to search or want to see what's available."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
        "cache_control": {"type": "ephemeral"},  # Cache tools across rounds
    },
]

# Cached system prompt — saves ~90% on re-sends after first round
SYSTEM_PROMPT_CACHED = [
    {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
]


# Labels for manual filenames (used by list_available_manuals)
MANUAL_LABELS = {
    # Communication manuals
    "AMC_CommManual_CANopen": "Comm: DigiFlex CANopen",
    "AMC_CommManual_EtherCAT": "Comm: DigiFlex EtherCAT",
    "AMC_CommManual_EthernetIP_FP": "Comm: FlexPro Ethernet/IP",
    "AMC_CommManual_FP_CANopen": "Comm: FlexPro CANopen",
    "AMC_CommManual_FP_EtherCAT": "Comm: FlexPro EtherCAT",
    "AMC_CommManual_FP_Serial": "Comm: FlexPro Serial",
    "AMC_CommManual_Modbus": "Comm: DigiFlex Modbus RTU",
    "AMC_CommManual_RS485": "Comm: DigiFlex RS485 Serial",
    # Hardware installation manuals
    "AMC_HWManual_AnalogDrives": "HW: Analog Drives",
    "AMC_HWManual_AxCent_PCB": "HW: AxCent PCB",
    "AMC_HWManual_AxCent_Panel": "HW: AxCent Panel",
    "AMC_HWManual_AxCent_Vehicle": "HW: AxCent Vehicle",
    "AMC_HWManual_DigiFlex_PCB_CANopen": "HW: DigiFlex PCB CANopen",
    "AMC_HWManual_DigiFlex_PCB_RS485-ModbusRTU": "HW: DigiFlex PCB RS485/Modbus",
    "AMC_HWManual_DigiFlex_PCB_XEnv": "HW: DigiFlex PCB XEnv (EtherCAT/POWERLINK/DxM)",
    "AMC_HWManual_DigiFlex_Panel_CANopen": "HW: DigiFlex Panel CANopen",
    "AMC_HWManual_DigiFlex_Panel_EtherCAT": "HW: DigiFlex Panel EtherCAT",
    "AMC_HWManual_DigiFlex_Panel_RS485-ModbusRTU": "HW: DigiFlex Panel RS485/Modbus",
    "AMC_HWManual_DigiFlex_Vehicle": "HW: DigiFlex Vehicle",
    "AMC_HWManual_FlexPro_PCB": "HW: FlexPro PCB (all form factors)",
    # Software manuals
    "AMC_SW_Manual_ACE": "SW: ACE Configuration & Tuning",
    "AMC_SW_Manual_DriveWare": "SW: DriveWare Configuration",
    "AMC_SW_QuickRef_ClickMove": "SW: ClickMove Quick Reference",
    "AMC_SW_QuickRef_DriveWare": "SW: DriveWare Quick Reference",
    "AMC_SW_QuickRef_ACE": "SW: ACE Quick Reference",
}


from cachetools import TTLCache

# Global query expansion cache — avoids re-expanding frequently asked questions
_expansion_cache: TTLCache = TTLCache(maxsize=500, ttl=3600)


def expand_query(user_message: str, context: str = "") -> str:
    """Use Claude Haiku to generate alternative search terms for better TF-IDF retrieval."""
    if not ENABLE_QUERY_EXPANSION:
        return user_message

    try:
        client = get_anthropic_client()
        prompt_content = user_message
        if context:
            prompt_content = f"Recent conversation context:\n{context}\n\nCurrent question: {user_message}"

        response = client.messages.create(
            model=QUERY_EXPANSION_MODEL,
            max_tokens=150,
            system=(
                "You are a query expansion engine for AMC servo drive technical manuals "
                "covering communication protocols (CANopen, EtherCAT, Ethernet/IP, Modbus, Serial, RS485) "
                "hardware installation (wiring, connectors, pinouts, mounting, power, I/O), "
                "and software tools (ACE setup, DriveWare configuration, ClickMove motion, auto-tune, scope, firmware update). "
                "AMC has drive families: FlexPro (part numbers FE/FM/FD/FMP/FX), "
                "DigiFlex (part numbers DV/DP/DZ/DX), and AxCent (AZ). "
                "Given a user question (and optional conversation context), output 5-8 alternative phrasings and key technical terms "
                "that would appear in the manual. Use conversation context to add drive-specific or topic-specific terms. "
                "Include synonyms, abbreviations, register names, "
                "object dictionary indices, specific configuration parameters, connector names, and pin numbers. "
                "For comm questions, include terms like: node address, PDO mapping, SDO, object dictionary, baud rate. "
                "For HW questions, include terms like: connector, pinout, wiring diagram, mounting, thermal, LED, power supply, fuse. "
                "For software questions, include terms like: setup wizard, connection, auto-tune, scope, parameter tree, firmware, project, workspace, motion profile. "
                "Output ONLY the search terms, one per line. No explanation."
            ),
            messages=[{"role": "user", "content": prompt_content}],
        )
        expanded_terms = response.content[0].text.strip()
        return f"{user_message} {expanded_terms}"
    except Exception as e:
        logger.warning("Query expansion failed: %s", e)
        return user_message


def _expand_query_cached(query: str, cache: dict, context: str = "") -> str:
    """expand_query() with a per-request + global cache to avoid redundant Haiku calls."""
    cache_key = f"{query}||{context}"
    # Check per-request cache first
    if cache_key in cache:
        return cache[cache_key]
    # Check global cache
    if cache_key in _expansion_cache:
        result = _expansion_cache[cache_key]
        cache[cache_key] = result
        return result
    result = expand_query(query, context=context)
    cache[cache_key] = result
    _expansion_cache[cache_key] = result
    return result


def rewrite_followup(user_message: str, history: list[dict]) -> str:
    """Rewrite a vague follow-up question into a standalone question using conversation context."""
    followup_indicators = [r'\bthem\b', r'\bthose\b', r'\bsame\b', r'\balso\b',
                           r'\bwhat about\b', r'\bhow about\b', r'\band for\b']
    msg_lower = user_message.lower()
    is_followup = (
        len(user_message.split()) < 6
        and any(re.search(pattern, msg_lower) for pattern in followup_indicators)
    )

    if not is_followup or not history:
        return user_message

    recent = history[-4:]
    context_str = "\n".join(
        f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content'][:300]}"
        for m in recent
    )

    try:
        client = get_anthropic_client()
        response = client.messages.create(
            model=QUERY_EXPANSION_MODEL,
            max_tokens=100,
            system=(
                "Given the conversation history and the user's follow-up question, "
                "rewrite the follow-up as a complete standalone question. "
                "Output ONLY the rewritten question, nothing else."
            ),
            messages=[{
                "role": "user",
                "content": f"Conversation:\n{context_str}\n\nFollow-up: {user_message}",
            }],
        )
        return response.content[0].text.strip()
    except Exception as e:
        logger.warning("Follow-up rewrite failed: %s", e)
        return user_message


def build_context(chunks: list[dict], max_chunk_chars: int = 1400) -> str:
    """Format retrieved chunks into a context block for tool results.
    Caps each chunk to max_chunk_chars to control token usage.
    Increased from 1000 to 1400 to preserve spec tables and complete procedures."""
    if not chunks:
        return "No results found for this search query."
    context_parts = []
    for i, chunk in enumerate(chunks, 1):
        heading = chunk.get("heading", "")
        heading_str = f", Section: {heading}" if heading else ""
        doc_type = chunk.get("doc_type", "")
        doc_type_str = f" [{doc_type}]" if doc_type else ""
        score = chunk.get("score", 0)
        text = chunk['text'][:max_chunk_chars]
        if len(chunk['text']) > max_chunk_chars:
            text += "..."
        context_parts.append(
            f"--- [{i}] {chunk['source']}, p.{chunk['page']}{heading_str}{doc_type_str} (rel:{score:.2f}) ---\n"
            f"{text}\n"
        )
    return "\n".join(context_parts)


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

def handle_search_manuals(query: str, manual_filter: str | None = None, doc_type_filter: str | None = None, expansion_cache: dict | None = None, conversation_context: str = "") -> tuple[str, list[dict]]:
    """Search manuals with query expansion and optional re-ranking."""
    expanded_query = _expand_query_cached(query, expansion_cache, context=conversation_context) if expansion_cache is not None else expand_query(query, context=conversation_context)

    # Fetch more candidates when re-ranking is enabled
    fetch_k = RERANK_CANDIDATES if ENABLE_RERANKING else TOP_K
    supplemented = False

    if manual_filter:
        chunks = retrieve(query, top_k=fetch_k, source_filter=manual_filter, expanded_query=expanded_query)
        # Supplement with unfiltered results if too few matches
        if len(chunks) < 3:
            supplemented = True
            extra = retrieve(query, top_k=fetch_k, doc_type_filter=doc_type_filter, expanded_query=expanded_query)
            seen_texts = {c["text"][:100] for c in chunks}
            for c in extra:
                if c["text"][:100] not in seen_texts:
                    chunks.append(c)
                    if len(chunks) >= fetch_k:
                        break
    else:
        chunks = retrieve(query, top_k=fetch_k, doc_type_filter=doc_type_filter, expanded_query=expanded_query)

    candidates_before_rerank = len(chunks)

    # Re-rank with Haiku for semantic relevance
    # Note: rerank uses original query (not expanded) — this prevents expansion
    # noise from inflating scores. Haiku judges against what the user actually asked.
    if ENABLE_RERANKING and len(chunks) > RERANK_TOP_K:
        chunks = rerank(query, chunks, top_k=RERANK_TOP_K)

    # Build context with retrieval quality stats
    scores = [c.get("score", 0) for c in chunks]
    avg_score = sum(scores) / len(scores) if scores else 0
    max_score = max(scores) if scores else 0

    header_lines = [
        f'Search results for "{query}" (manual: {manual_filter or "all"}):',
        f"  - Candidates retrieved: {candidates_before_rerank}, After reranking: {len(chunks)}",
        f"  - Average relevance: {avg_score:.2f}, Highest: {max_score:.2f}",
    ]
    if supplemented:
        header_lines.append("  - NOTE: Filtered search had <3 results, supplemented with results from other manuals.")
    if avg_score < 0.15:
        header_lines.append("  - LOW RELEVANCE: Consider rephrasing your search with more specific technical terms.")
    header = "\n".join(header_lines) + "\n\n"

    return header + build_context(chunks), chunks


def handle_detect_drive_manual(part_number: str) -> tuple[str, list[dict]]:
    """Look up a part number using the CSV-powered drive database."""
    result = lookup_drive(part_number)

    if result:
        info = {
            "part_number": result["sku"],
            "title": result["title"],
            "drive_family": result["family"],
            "form_factor": result["form_factor"],
            "network_communication": result["network"],
            "comm_protocol": result["comm_protocol"],
            "comm_manual": result["comm_manual"],
            "hw_manual": result["hw_manual"],
        }

        if result["comm_ambiguous"]:
            info["WARNING"] = (
                "This drive supports both Serial (RS-485) and Modbus RTU. "
                "Ask the user which protocol they are using before searching."
            )
            info["comm_options"] = result["comm_options"]

        notes = []
        if result["comm_manual"]:
            notes.append(f"For comm questions, use manual_filter='{result['comm_manual']}'")
        if result["hw_manual"]:
            notes.append(f"For HW/installation questions, use manual_filter='{result['hw_manual']}'")
        notes.append("For software/configuration questions, search AMC_SW_Manual_ACE.pdf or AMC_SW_Manual_DriveWare.pdf")
        if notes:
            info["usage_notes"] = notes

        return json.dumps(info, indent=2), []

    # Fallback: try regex-based detection for part numbers not in CSV
    pn_upper = part_number.strip().upper()
    family = None
    if re.match(r'(FE|FM|FD|FMP|FX)', pn_upper):
        family = "FlexPro"
    elif re.match(r'(DV|DP|DZ|DX)', pn_upper):
        family = "DigiFlex Performance"
    elif re.match(r'AZ', pn_upper):
        family = "AxCent"

    protocol = None
    if re.search(r'[-.]IPM\b', pn_upper):
        protocol = "Ethernet/IP"
    elif re.search(r'[-.]EM\b', pn_upper):
        protocol = "EtherCAT"
    elif re.search(r'[-.]RM\b', pn_upper):
        protocol = "Serial"
    elif re.search(r'[-.]CM\b', pn_upper):
        protocol = "CANopen"
    elif "EAN" in pn_upper:
        protocol = "EtherCAT"
    elif re.search(r'\bDVC', pn_upper) or re.search(r'\bCAN\b', pn_upper):
        protocol = "CANopen"
    elif re.search(r'[-.]RA\b', pn_upper):
        protocol = "ambiguous - could be Serial (RS485) or Modbus RTU"

    result = {
        "part_number": part_number,
        "drive_family": family,
        "protocol": protocol,
        "comm_manual": None,
        "hw_manual": None,
        "note": "Part number not found in product database. Using regex-based detection (less reliable).",
    }

    return json.dumps(result, indent=2), []


def handle_find_replacement(part_number: str) -> tuple[str, list[dict]]:
    """Look up the AxCent replacement for a discontinued analog drive."""
    result = lookup_replacement(part_number)

    if result:
        info = {
            "discontinued_model": result["discontinued_model"],
            "size": result["size"],
            "motor_type": result["motor_type"],
            "replacements": result["replacements"],
        }
        if result["notes"]:
            info["notes"] = result["notes"]
        info["reference_documents"] = [
            "AMC_ProductNote_AxCent_Retrofit_Small.pdf (small size models)",
            "AMC_ProductNote_AxCent_Retrofit_Large.pdf (large size models)",
        ]
        return json.dumps(info, indent=2), []

    return json.dumps({
        "error": f"No retrofit mapping found for '{part_number}'.",
        "note": "This drive may not be a discontinued analog model, or may not have an AxCent replacement. Try searching the manuals for more information.",
    }, indent=2), []


def handle_list_available_manuals() -> tuple[str, list[dict]]:
    """List all indexed manuals with protocol labels."""
    pdfs = get_all_pdfs()
    if not pdfs:
        return "No manuals are currently indexed.", []

    comm_manuals = []
    hw_manuals = []
    sw_manuals = []
    app_notes = []
    product_notes = []
    datasheets = []
    other_docs = []
    for pdf in pdfs:
        name = pdf.stem
        label = MANUAL_LABELS.get(name, "")
        label_str = f" ({label})" if label else ""
        entry = f"  - {pdf.name}{label_str}"
        if "HWManual" in name:
            hw_manuals.append(entry)
        elif "CommManual" in name:
            comm_manuals.append(entry)
        elif "SW_" in name:
            sw_manuals.append(entry)
        elif "Datasheet" in name:
            datasheets.append(entry)
        elif "AppNote" in name:
            app_notes.append(entry)
        elif "ProductNote" in name:
            product_notes.append(entry)
        elif "WhitePaper" in name:
            other_docs.append(entry)
        else:
            other_docs.append(entry)

    lines = ["Available AMC manuals:\n"]
    if comm_manuals:
        lines.append(f"COMMUNICATION MANUALS ({len(comm_manuals)}):")
        lines.extend(comm_manuals)
        lines.append("")
    if hw_manuals:
        lines.append(f"HARDWARE INSTALLATION MANUALS ({len(hw_manuals)}):")
        lines.extend(hw_manuals)
        lines.append("")
    if sw_manuals:
        lines.append(f"SOFTWARE MANUALS ({len(sw_manuals)}):")
        lines.extend(sw_manuals)
        lines.append("")
    if datasheets:
        lines.append(f"DATASHEETS ({len(datasheets)}): Per-drive specification sheets — search by SKU")
        lines.append(f"  (Use manual_filter='AMC_Datasheet_{{SKU}}.pdf' or doc_type='datasheet')")
        lines.append("")
    if app_notes:
        lines.append(f"APPLICATION NOTES ({len(app_notes)}):")
        lines.extend(app_notes)
        lines.append("")
    if product_notes:
        lines.append(f"PRODUCT NOTES ({len(product_notes)}):")
        lines.extend(product_notes)
        lines.append("")
    if other_docs:
        lines.append(f"OTHER DOCUMENTS ({len(other_docs)}):")
        lines.extend(other_docs)

    return "\n".join(lines), []


def _dedup_sources(all_sources: list[dict]) -> list[dict]:
    """Deduplicate sources by (source, page) key."""
    sources = []
    seen = set()
    for chunk in all_sources:
        key = (chunk["source"], chunk["page"])
        if key not in seen:
            seen.add(key)
            sources.append({
                "source": chunk["source"],
                "page": chunk["page"],
                "heading": chunk.get("heading", ""),
            })
    return sources


def dispatch_tool(tool_name: str, tool_input: dict, expansion_cache: dict | None = None, conversation_context: str = "") -> tuple[str, list[dict]]:
    """Route a tool call to the appropriate handler. Returns (result_text, chunks)."""
    try:
        if tool_name == "search_manuals":
            return handle_search_manuals(
                query=tool_input["query"],
                manual_filter=tool_input.get("manual_filter"),
                doc_type_filter=tool_input.get("doc_type"),
                expansion_cache=expansion_cache,
                conversation_context=conversation_context,
            )
        elif tool_name == "detect_drive_manual":
            return handle_detect_drive_manual(
                part_number=tool_input["part_number"],
            )
        elif tool_name == "find_replacement_drive":
            return handle_find_replacement(
                part_number=tool_input["part_number"],
            )
        elif tool_name == "list_available_manuals":
            return handle_list_available_manuals()
        else:
            return f"Unknown tool: {tool_name}", []
    except Exception as e:
        return f"Tool error: {e}", []


# ---------------------------------------------------------------------------
# Smart routing — decide what to search without calling an LLM
# ---------------------------------------------------------------------------

def _classify_query_type(message: str) -> str:
    """Rule-based classification of query intent. No LLM needed."""
    msg_lower = message.lower()

    # Keyword-based doc type detection
    hw_keywords = ['wiring', 'wire', 'connector', 'pinout', 'pin ', 'mounting', 'mount',
                   'dimension', 'thermal', 'led', 'power supply', 'fuse', 'grounding',
                   'shielding', 'installation', 'install', 'weight', 'size']
    sw_keywords = ['ace ', 'driveware', 'clickmove', 'click&move', 'software', 'auto-tune',
                   'autotune', 'firmware', 'scope', 'parameter', 'workspace', 'project',
                   'tuning', 'tune']
    comm_keywords = ['canopen', 'ethercat', 'ethernet/ip', 'modbus', 'rs485', 'rs232',
                     'serial', 'pdo', 'sdo', 'object dictionary', 'baud', 'register',
                     'protocol', 'communication', 'network', 'node id', 'node address']
    spec_keywords = ['current rating', 'voltage range', 'specification', 'specs', 'datasheet',
                     'continuous current', 'peak current', 'supply voltage']
    appnote_keywords = ['pvt', 'trajectory', 'current loop', 'stepper', 'twincat',
                        'sequencing', 'g-code', 'gcode', 'mode switch', 'inrush',
                        'ferrite', 'power supply sizing']

    if any(kw in msg_lower for kw in spec_keywords):
        return 'datasheet'
    if any(kw in msg_lower for kw in appnote_keywords):
        return 'app_note'
    if any(kw in msg_lower for kw in hw_keywords):
        return 'hw'
    if any(kw in msg_lower for kw in sw_keywords):
        return 'sw'
    if any(kw in msg_lower for kw in comm_keywords):
        return 'comm'
    return None  # Can't determine — let the search be unfiltered


def _smart_route(user_message: str, history: list[dict] = None, drive_context: dict = None) -> tuple[str, list[dict], str]:
    """
    Search without calling any LLM. Returns (context_text, source_chunks, drive_info).
    Uses rule-based routing + BM25/semantic search directly.
    If drive_context is provided (user pre-selected a drive), searches in priority order:
      1. Drive datasheet  2. HW manual  3. Comm manual  4. App notes + fallback
    """
    expansion_cache = {}
    conv_context = ""
    if history:
        recent = history[-4:]
        conv_context = "\n".join(
            f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content'][:400]}"
            for m in recent
        )

    drive_info = ""
    result = drive_context  # Pre-resolved from frontend selector

    # If no pre-selected drive, detect from message
    if not result:
        part_number = detect_part_number(user_message)
        if part_number:
            result = lookup_drive(part_number)

    if result:
        drive_info = json.dumps({
            "part_number": result["sku"],
            "family": result["family"],
            "form_factor": result["form_factor"],
            "network": result["network"],
            "comm_manual": result["comm_manual"],
            "hw_manual": result["hw_manual"],
        }, indent=2)

    # Search with query expansion
    expanded_query = _expand_query_cached(user_message, expansion_cache, context=conv_context)
    fetch_k = RERANK_CANDIDATES if ENABLE_RERANKING else TOP_K

    if result:
        # --- Drive-aware priority search ---
        # Priority 1: Drive datasheet (most specific to this exact drive)
        # Priority 2: HW manual (wiring, connectors, mounting)
        # Priority 3: Comm manual (protocol, registers, object dictionary)
        # Priority 4: App notes + everything else (fallback)
        chunks = []
        seen = set()

        priority_manuals = []
        datasheet_name = f"AMC_Datasheet_{result['sku']}.pdf"
        indexed_sources = get_indexed_sources()
        if datasheet_name in indexed_sources:
            priority_manuals.append(datasheet_name)
        else:
            logger.warning("Datasheet not found in index: %s", datasheet_name)
        if result.get("hw_manual"):
            priority_manuals.append(result["hw_manual"])
        if result.get("comm_manual"):
            priority_manuals.append(result["comm_manual"])

        # Search each priority manual (3-way RRF: BM25 original + semantic original + semantic expanded)
        for manual in priority_manuals:
            manual_chunks = retrieve(user_message, top_k=fetch_k, source_filter=manual, expanded_query=expanded_query)
            for c in manual_chunks:
                key = c["text"][:100]
                if key not in seen:
                    seen.add(key)
                    chunks.append(c)

        # Always search app notes as supplement (procedures, tuning, troubleshooting)
        app_chunks = retrieve(user_message, top_k=fetch_k, doc_type_filter="app_note", expanded_query=expanded_query)
        for c in app_chunks:
            key = c["text"][:100]
            if key not in seen:
                seen.add(key)
                chunks.append(c)

        # If still not enough, search everything
        if len(chunks) < 3:
            extra = retrieve(user_message, top_k=fetch_k, expanded_query=expanded_query)
            for c in extra:
                key = c["text"][:100]
                if key not in seen:
                    seen.add(key)
                    chunks.append(c)

        # Trim to fetch_k before reranking
        chunks = chunks[:fetch_k]
    else:
        # No drive context — search by query type
        doc_type = _classify_query_type(user_message)
        chunks = retrieve(user_message, top_k=fetch_k, doc_type_filter=doc_type, expanded_query=expanded_query)

    # Rerank
    if ENABLE_RERANKING and len(chunks) > RERANK_TOP_K:
        chunks = rerank(user_message, chunks, top_k=RERANK_TOP_K)

    context_text = build_context(chunks)
    return context_text, chunks, drive_info


def single_shot_chat_stream(user_message: str, history: list[dict] = None, drive_context: dict = None, uploaded_chunks: list = None):
    """
    Single-shot RAG: search in Python (0 Sonnet calls), then 1 Sonnet call for the answer.
    Falls back to agentic mode if Sonnet says it needs more info.
    Yields same event format as chat_stream().
    """
    if history is None:
        history = []

    yield {"type": "status", "text": "Searching manuals..."}

    # Rewrite follow-ups
    standalone_query = rewrite_followup(user_message, history)

    # Search entirely in Python — no LLM calls
    context_text, chunks, drive_info = _smart_route(standalone_query, history, drive_context=drive_context)
    all_sources = list(chunks)

    # Quality gate: if results are weak, fall back to agentic multi-round search
    if chunks:
        avg_score = sum(c.get("score", 0) for c in chunks) / len(chunks)
        if avg_score < 0.01 or len(chunks) < 2:
            logger.info("Single-shot quality gate triggered (avg_score=%.3f, chunks=%d) — falling back to agentic mode", avg_score, len(chunks))
            yield {"type": "status", "text": "Results uncertain, searching deeper..."}
            for event in chat_stream(user_message, history, drive_context, uploaded_chunks):
                yield event
            return
        yield {"type": "status", "text": f"Found {len(chunks)} results, generating answer..."}
    elif not uploaded_chunks:
        # No chunks at all and no uploaded docs — fall back to agentic
        logger.info("Single-shot found 0 results — falling back to agentic mode")
        yield {"type": "status", "text": "No direct results, searching deeper..."}
        for event in chat_stream(user_message, history, drive_context, uploaded_chunks):
            yield event
        return

    # Build a single prompt with all context
    user_content = standalone_query
    if drive_info:
        user_content += f"\n\n[Drive Info]\n{drive_info}"

    # Inject uploaded PDF content — ranked by semantic relevance to the query
    if uploaded_chunks:
        filename = uploaded_chunks[0].get("source", "uploaded document") if uploaded_chunks else "uploaded document"
        # Rank uploaded chunks by relevance instead of injecting all
        ranked_upload = _rank_uploaded_chunks(standalone_query, uploaded_chunks, top_k=6)
        upload_text = f"\n\n=== UPLOADED DOCUMENT: {filename} ===\n"
        upload_text += "The user uploaded this document. Use its specs (voltage, current, feedback type, encoder resolution, motor parameters) to answer compatibility questions.\n\n"
        for chunk in ranked_upload:
            heading = chunk.get("heading", "")
            heading_str = f" — {heading}" if heading else ""
            upload_text += f"--- [UPLOADED DOCUMENT] {filename}, Page {chunk.get('page', '?')}{heading_str} ---\n{chunk['text']}\n\n"
        user_content += upload_text

    if context_text:
        user_content += f"\n\n[AMC Manual Search Results]\n{context_text}"
    else:
        user_content += "\n\n[No search results found. Answer from general knowledge or suggest what to search.]"

    # Build messages with history
    messages = []
    for msg in history[-6:]:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": user_content})

    # ONE Sonnet call — stream the answer
    client = get_anthropic_client()
    answer = ""

    try:
        with client.messages.stream(
            model=CLAUDE_MODEL,
            max_tokens=4096,
            system=SYSTEM_PROMPT_CACHED,
            messages=messages,
        ) as stream:
            for text in stream.text_stream:
                yield {"type": "token", "text": text}
                answer += text
    except anthropic.RateLimitError as e:
        logger.warning("Rate limit hit: %s", e)
        raise RateLimitExceeded("The AI service is busy. Please wait a moment and try again.") from e
    except Exception as e:
        logger.error("Single-shot error: %s", e, exc_info=True)
        yield {"type": "token", "text": "An error occurred generating the answer. Please try again."}

    yield {"type": "done", "sources": _dedup_sources(all_sources)}


# ---------------------------------------------------------------------------
# Main chat function — agentic tool-use loop (fallback)
# ---------------------------------------------------------------------------

def chat(user_message: str, history: list[dict] = None) -> dict:
    """
    Process a user question using agentic tool-use.
    Claude decides what to search for and can do multiple retrieval passes.
    Returns dict with 'answer' and 'sources'.
    """
    if history is None:
        history = []

    # Rewrite follow-up questions into standalone queries
    standalone_query = rewrite_followup(user_message, history)

    # Build messages array with conversation history + user question
    messages = []
    for msg in history[-6:]:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": standalone_query})

    # Build conversation context for query expansion (last 2 exchanges)
    conv_context = ""
    if history:
        recent = history[-4:]
        conv_context = "\n".join(
            f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content'][:400]}"
            for m in recent
        )

    # Agentic tool-use loop
    client = get_anthropic_client()
    all_sources = []
    answer = ""
    expansion_cache: dict = {}

    for _ in range(MAX_TOOL_ROUNDS):
        try:
            response = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=4096,
                system=SYSTEM_PROMPT_CACHED,
                tools=TOOLS,
                messages=messages,
            )
        except anthropic.RateLimitError as e:
            logger.warning("Rate limit hit after retries: %s", e)
            raise RateLimitExceeded("The AI service is busy. Please wait a moment and try again.") from e
        except anthropic.APIStatusError as e:
            logger.error("Claude API error: %s", e)
            raise

        # If Claude is done (no more tool calls), extract the final answer
        if response.stop_reason == "end_turn":
            for block in response.content:
                if block.type == "text":
                    answer += block.text
            break

        # Append Claude's response (contains tool_use blocks) to messages
        messages.append({"role": "assistant", "content": response.content})

        # Process each tool call and build tool_result messages
        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                result_text, chunks = dispatch_tool(block.name, block.input, expansion_cache, conversation_context=conv_context)
                all_sources.extend(chunks)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_text,
                })

        # Send tool results back to Claude
        messages.append({"role": "user", "content": tool_results})

    else:
        # Safety: hit MAX_TOOL_ROUNDS without end_turn
        for block in response.content:
            if block.type == "text":
                answer += block.text
        if not answer:
            answer = "I was unable to complete the search. Please try rephrasing your question."

    return {"answer": answer, "sources": _dedup_sources(all_sources)}


def _tool_call_description(name: str, tool_input: dict) -> str:
    """Generate a human-readable description of a tool call for status updates."""
    if name == "search_manuals":
        query = tool_input.get("query", "")[:60]
        manual = tool_input.get("manual_filter", "")
        if manual:
            short_manual = manual.replace("AMC_CommManual_", "").replace("AMC_HWManual_", "").replace("AMC_SW_Manual_", "").replace(".pdf", "")
            return f"Searching {short_manual} for \"{query}\"..."
        return f"Searching manuals for \"{query}\"..."
    elif name == "detect_drive_manual":
        pn = tool_input.get("part_number", "")
        return f"Looking up drive {pn}..."
    elif name == "find_replacement_drive":
        pn = tool_input.get("part_number", "")
        return f"Finding replacement for {pn}..."
    elif name == "list_available_manuals":
        return "Listing available manuals..."
    return f"Running {name}..."


def chat_stream(user_message: str, history: list[dict] = None, drive_context: dict = None, uploaded_chunks: list = None):
    """
    Streaming version of chat(). Yields event dicts:
    - {"type": "status", "text": "..."} — progress updates during tool calls
    - {"type": "token", "text": "..."} — streamed text tokens from final answer
    - {"type": "done", "sources": [...]} — completion with sources
    """
    if history is None:
        history = []

    yield {"type": "status", "text": "Analyzing your question..."}

    # Rewrite follow-up questions
    standalone_query = rewrite_followup(user_message, history)

    # Build messages
    messages = []
    for msg in history[-6:]:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": standalone_query})

    # Build conversation context for query expansion
    conv_context = ""
    if history:
        recent = history[-4:]
        conv_context = "\n".join(
            f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content'][:400]}"
            for m in recent
        )

    client = get_anthropic_client()
    all_sources = []
    answer = ""
    round_num = 0
    expansion_cache: dict = {}

    for _ in range(MAX_TOOL_ROUNDS):
        round_num += 1

        try:
            # For each round, first try non-streaming to check if there are tool calls
            response = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=4096,
                system=SYSTEM_PROMPT_CACHED,
                tools=TOOLS,
                messages=messages,
            )
        except anthropic.RateLimitError as e:
            logger.warning("Rate limit hit after retries: %s", e)
            raise RateLimitExceeded("The AI service is busy. Please wait a moment and try again.") from e
        except anthropic.APIStatusError as e:
            logger.error("Claude API error: %s", e)
            raise

        # Check if this is the final answer (no tool calls)
        has_tool_use = any(block.type == "tool_use" for block in response.content)

        if not has_tool_use:
            # Final answer — re-request with true streaming for real-time token delivery
            try:
                with client.messages.stream(
                    model=CLAUDE_MODEL,
                    max_tokens=4096,
                    system=SYSTEM_PROMPT_CACHED,
                    tools=TOOLS,
                    messages=messages,
                ) as stream:
                    for text in stream.text_stream:
                        yield {"type": "token", "text": text}
                        answer += text
            except Exception:
                # Fallback: use the already-received non-streaming response
                for block in response.content:
                    if block.type == "text":
                        yield {"type": "token", "text": block.text}
                        answer += block.text
            break

        # Tool calls — process them and yield status updates
        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        round_result_count = 0
        for block in response.content:
            if block.type == "tool_use":
                yield {"type": "status", "text": _tool_call_description(block.name, block.input)}
                result_text, chunks = dispatch_tool(block.name, block.input, expansion_cache, conversation_context=conv_context)
                all_sources.extend(chunks)
                round_result_count += len(chunks)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_text,
                })

        if round_result_count > 0:
            yield {"type": "status", "text": f"Found {round_result_count} results, analyzing..."}

        messages.append({"role": "user", "content": tool_results})

    else:
        # Hit MAX_TOOL_ROUNDS
        for block in response.content:
            if block.type == "text":
                yield {"type": "token", "text": block.text}
                answer += block.text
        if not answer:
            yield {"type": "token", "text": "I was unable to complete the search. Please try rephrasing your question."}

    yield {"type": "done", "sources": _dedup_sources(all_sources)}
