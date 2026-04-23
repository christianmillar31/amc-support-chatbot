from __future__ import annotations
import csv
import json
import logging
import re
import time
import anthropic
import numpy as np
from app import config as _config
from app.config import (
    ANSWER_MAX_TOKENS,
    ANSWER_PROVIDER,
    CHEAP_TASK_PROVIDER,
    CLAUDE_MODEL, QUERY_EXPANSION_MODEL, ENABLE_QUERY_EXPANSION,
    DISABLE_QUERY_EXPANSION,
    MAX_TOOL_ROUNDS, BASE_DIR, EMBEDDING_MODEL, EMBEDDING_QUERY_PREFIX,
    ENABLE_RERANKING, PILOT_CONTEXT_MAX_CHARS, PILOT_ENABLE_AGENTIC_FALLBACK,
    PILOT_RETRIEVAL_TOP_K, RERANK_CANDIDATES, RERANK_TOP_K, TOP_K,
    get_anthropic_client, ENABLE_SINGLE_SHOT,
    LLM_BACKEND,
)
from app.retriever import retrieve, get_indexed_sources
from app.ingest import get_all_pdfs
from app.drive_lookup import (
    lookup_drive,
    search_drives,
    detect_part_number,
    lookup_replacement,
    build_canonical_context,
)
from app.model_provider import get_provider
from app.reranker import rerank
from app.support_catalog import build_support_note

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

DOC TYPES: comm (protocols/registers), hw (wiring/connectors/pinouts), sw/sw_ref (ACE/DriveWare/ClickMove/DriveLibrary), sw_release (release notes), app_note (procedures/tuning), datasheet (specs per drive), product_note (retrofits), compliance (UL/CE/REACH/STO — only when user asks about certifications/region). Marketing brochures and RMA docs are indexed but OFF by default — do not request them unless the user explicitly asks about use-cases, industry flyers, or RMA process.

STRATEGY:
1. Part number mentioned → call detect_drive_manual FIRST, BEFORE any other tool. This is MANDATORY. If it returns PART_NUMBER_NOT_FOUND, STOP and ask the user to verify (see rule 14). NEVER skip detect_drive_manual and go straight to search_manuals or find_replacement_drive when the user has mentioned a part number — doing so risks surfacing irrelevant chunks for a drive that does not exist.
2. If user asks about replacing/upgrading a discontinued analog drive BY PART NUMBER → step 1 still applies: call detect_drive_manual first. Only call find_replacement_drive AFTER detect_drive_manual confirms the drive exists OR the user has clarified the exact discontinued model.
3. Use doc_type filter to narrow searches. Use manual_filter when you know the exact manual.
4. One focused search usually suffices. Only do a second search if the first had low relevance (<0.15) or missed key info.
5. For specs (current, voltage, dimensions) → search datasheets. For procedures → search app_notes. SPEC KEYWORDS THAT LIVE IN DATASHEETS ONLY: continuous current, peak current, DC/AC supply voltage, power output, switching frequency, PWM frequency, weight, dimensions, form factor, resolver transformation ratio, resolver excitation voltage, resolver excitation frequency, encoder supply, Hall supply, motor type support, operating mode, control command input, commutation type. If the user asks about any of these, the FIRST search MUST include doc_type="datasheet" — do not exclude datasheets even if the question sounds like troubleshooting. When a SKU is mentioned, always filter the datasheet search by that drive's datasheet filename first.
6. If drive lookup reports support_bucket=core_drive_missing, DO NOT pretend a local datasheet exists. Use the hardware manual, communication manual, application notes, and product metadata instead, and say clearly when exact local datasheet coverage is missing.
7. If drive lookup reports a reserved drive status, prefer concise support guidance and avoid implying the product has full current-product coverage.

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

ABSOLUTE RULES — VIOLATION OF THESE IS A CRITICAL FAILURE:
10. NEVER invent drive model numbers, SKUs, or part numbers. AMC drive SKUs follow SPECIFIC patterns: FlexPro = FE/FM/FD/FMP/FX + voltage code + current + protocol (e.g., FE060-25-EM). DigiFlex = DP/DZ/DX + model code (e.g., DPRALTE-020B080). AxCent = AZ + model (e.g., AZBH10A4). If you cannot find a specific drive model in search results, DO NOT make one up. Say "Search the AMC product selector at a-m-c.com/products/servo-drives for drives matching your requirements."
11. NEVER invent document names, page numbers, section names, register addresses, or technical specifications. Every single fact, number, and citation in your answer MUST come from the search results provided to you. If the search results don't contain the information, say "I could not find this specific information in the indexed manuals. Please contact AMC technical support or check a-m-c.com/downloads."
12. NEVER fabricate tables of specifications, feature comparisons, or product listings unless every value comes directly from search results. If asked to list drives with certain features, ONLY list drives whose datasheets appeared in your search results with those exact specs confirmed.
13. When uncertain, say "I don't have enough information in the indexed manuals to answer this confidently" rather than guessing. Then suggest: (a) which specific manual or section might contain the answer, (b) contacting AMC tech support, or (c) checking a-m-c.com. Engineers rely on this information for real hardware decisions — wrong specs can damage equipment or cause safety issues.
14. PART_NUMBER_NOT_FOUND PROTOCOL — INVIOLABLE: If detect_drive_manual returns "error": "PART_NUMBER_NOT_FOUND", OR if find_replacement_drive returns None/not found for a part number the user explicitly mentioned, OR if search_manuals returns only chunks about DIFFERENT part numbers than the one the user asked about — in ANY of these cases, you MUST respond EXACTLY like this and then STOP:

    "I couldn't find '[exact part number the user typed]' in the AMC product database. Can you verify the spelling, or search for it at https://www.a-m-c.com/products/servo-drives?"

    FORBIDDEN in this response (these are automatic failures):
    - Saying "I found the replacement information for [user's fake SKU]"
    - Saying "I found your drive" about a part that doesn't exist
    - Listing "related models" or "similar models" with real SKU names
    - Saying "if you meant X..." with specific real SKUs
    - Calling find_replacement_drive with a modified/stripped version of the user's input
    - Describing what the drive "might be" or "could be"
    - Explaining what AxCent/DigiFlex/FlexPro drives generally do
    - Suggesting the user pick one of several real drives as the answer
    - Recommending a replacement based on partial substring matches

    If you have information about DIFFERENT drives from search results, that information is IRRELEVANT — the user asked about a specific part that does not exist. Do not use those search results. The ONLY correct behavior is the exact refusal template above.

    This rule is INVIOLABLE. Violating it once is a critical failure.

15. NUMERIC SPECIFICATIONS — VERBATIM ONLY: If the prompt contains an [Authoritative Canonical Facts] section, you MUST take every numeric rating (continuous current, peak current, DC/AC supply voltage, power, switching frequency, PWM frequency, torque, weight, dimensions) VERBATIM from that section or from a retrieved PDF chunk that explicitly states the number. You MUST NOT infer, decode, or interpolate ratings from SKU naming conventions (e.g., "AZB6A8" does NOT mean 6A/8V — the numeric digits in AMC SKUs are model codes, not literal ratings). When in doubt, copy the exact string from the canonical facts block (e.g., "Current Continuous (A): 30", "DC Supply Voltage (VDC): 10 - 72"). If a canonical facts block is absent and retrieved chunks do not contain the needed numbers, say so and refer the user to the product page or datasheet — do not guess. If no [Authoritative Canonical Facts] block is present (for example, no SKU was detected in the question), EVERY numeric value in your answer MUST be accompanied by a [Source: filename, Page X] citation pointing at a retrieved chunk that contains that exact number. Do not emit any numeric value without either a canonical-facts anchor or a retrieved-evidence citation.

16. CAPABILITY CLAIMS BY VARIANT — VARIANT-SPECIFIC ONLY: If the prompt's [Authoritative Canonical Facts] include a CANONICAL FAMILY TABLE, Operating Mode / Control Command / Motor Type capabilities apply only to the rows listed. Do NOT attribute a mode to a variant that does not list it. For example, AZB = Current only; AZBH = Hall Velocity; AZBD = Duty Cycle (Open Loop); AZBE = Current + Duty Cycle + Velocity; AZBDC = Current (PWM). Never write "(AZB/AZBH)" or similar conflations — cite each variant's own capability.

17. REGION-SPECIFIC COMPLIANCE CONTENT: Retrieved chunks that describe LVD (Low Voltage Directive), CE, UL, TUV, or other region-specific compliance requirements apply only when the user asks about that specific region or compliance standard. Do NOT lead a general wiring, power, or motor-connection answer with "European-approved" or "CE-required" language unless the user asked about compliance. For general wiring questions, cover the universal electrical guidance first (wire gauge for the drive's current rating, grounding, shielding, connector pinout) and mention region-specific compliance only as a supplemental note.

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
                    "description": "Optional: filter by document type. Single value (e.g. 'datasheet') OR comma-separated list (e.g. 'datasheet,hw,app_note') to search multiple types at once. Valid types: 'comm' = communication manuals, 'hw' = hardware installation manuals, 'sw' = software manuals (ACE, DriveWare, DriveLibrary), 'sw_ref' = software quick references, 'sw_release' = software release notes / readmes, 'app_note' = application notes (detailed how-to guides), 'product_note' = product notes (retrofit guides, wiring recommendations), 'datasheet' = per-drive datasheets with specifications, current ratings, voltages, dimensions, pinouts, resolver transformation ratio, resolver excitation, encoder supply, form factor, operating modes (ALWAYS include datasheet when asking about any numeric spec — these fields live NOWHERE else), 'compliance' = UL/CE/REACH/RoHS/STO/functional-safety certifications (use ONLY when the user asks about compliance or a specific region/standard), 'marketing' = brochures, industry flyers, product flyers, presentations (OFF by default; request explicitly only for industry/use-case questions), 'rma' = RMA / product-return process docs.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "detect_drive_manual",
        "description": (
            "Look up an AMC drive part number in the 644-drive product database. "
            "Returns drive family, form factor, network, and manual filenames if the part number exists. "
            "Call this whenever the user mentions a part number like FE060-25-EM, DPRALTE-020B080, or AZBH10A4. "
            "IMPORTANT: If the part number is not found, returns error=PART_NUMBER_NOT_FOUND. "
            "When this happens, you MUST NOT invent, guess, correct, or describe the drive. "
            "You MUST ask the user to verify the part number and STOP — do not call other tools to compensate."
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


def _runtime_disable_expansion() -> bool:
    """Re-check the DISABLE_QUERY_EXPANSION flag at call time.

    Needed because entry points like ``eval/runners/run_regression.py --no-llm``
    set the env var after ``app.config`` has already been imported. A runtime
    check keeps the flag effective regardless of import order.
    """
    import os  # local import keeps module load light; this is a hot-path helper
    return DISABLE_QUERY_EXPANSION or os.getenv("DISABLE_QUERY_EXPANSION", "").strip().lower() in {"1", "true", "yes", "on"}


def expand_query(user_message: str, context: str = "", provider_name: str | None = None) -> str:
    """Use the configured cheap-task provider to generate better search terms."""
    if not ENABLE_QUERY_EXPANSION or _runtime_disable_expansion():
        return user_message

    try:
        prompt_content = user_message
        if context:
            prompt_content = f"Recent conversation context:\n{context}\n\nCurrent question: {user_message}"

        response = get_provider(provider_name or CHEAP_TASK_PROVIDER).complete(
            max_tokens=150,
            system_prompt=(
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
            temperature=0.0,
        )
        expanded_terms = response.text.strip()
        return f"{user_message} {expanded_terms}"
    except Exception as e:
        logger.warning("Query expansion failed: %s", e)
        return user_message


def _should_expand_query(drive_result: dict | None, detected_sku: str | None) -> bool:
    """Gate the Haiku query-expansion call.

    Skip expansion when:
    - ``DISABLE_QUERY_EXPANSION`` env flag is set (escape hatch; ``--no-llm``
      regression also sets this to guarantee zero cloud-model calls).
    - Global ``ENABLE_QUERY_EXPANSION`` is False.
    - A drive has already been resolved (either UI-preselected or detected by
      SKU). In those cases ``_smart_route`` has narrowed retrieval to the
      drive's priority manuals and expansion tends to pull noisy neighbors.
    """
    if _runtime_disable_expansion():
        return False
    if not ENABLE_QUERY_EXPANSION:
        return False
    if drive_result or detected_sku:
        return False
    return True


def _expand_query_cached(query: str, cache: dict, context: str = "", provider_name: str | None = None) -> str:
    """expand_query() with a per-request + global cache to avoid redundant Haiku calls."""
    provider_key = provider_name or CHEAP_TASK_PROVIDER
    cache_key = f"{provider_key}||{query}||{context}"
    # Check per-request cache first
    if cache_key in cache:
        return cache[cache_key]
    # Check global cache
    if cache_key in _expansion_cache:
        result = _expansion_cache[cache_key]
        cache[cache_key] = result
        return result
    result = expand_query(query, context=context, provider_name=provider_name)
    cache[cache_key] = result
    _expansion_cache[cache_key] = result
    return result


def rewrite_followup(user_message: str, history: list[dict], provider_name: str | None = None) -> str:
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
        response = get_provider(provider_name or CHEAP_TASK_PROVIDER).complete(
            max_tokens=100,
            system_prompt=(
                "Given the conversation history and the user's follow-up question, "
                "rewrite the follow-up as a complete standalone question. "
                "Output ONLY the rewritten question, nothing else."
            ),
            messages=[{
                "role": "user",
                "content": f"Conversation:\n{context_str}\n\nFollow-up: {user_message}",
            }],
            temperature=0.0,
        )
        return response.text.strip()
    except Exception as e:
        logger.warning("Follow-up rewrite failed: %s", e)
        return user_message


def build_context(chunks: list[dict], max_chunk_chars: int = PILOT_CONTEXT_MAX_CHARS) -> str:
    """Format retrieved chunks into a context block for tool results.
    Caps each chunk to control token usage for the pilot runtime."""
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


def _build_drive_search_query(result: dict, user_message: str) -> str:
    """Build a richer drive-aware search query without repeating identical tokens."""
    parts: list[str] = []
    for value in [
        result.get("requested_sku"),
        result.get("canonical_sku"),
        result.get("datasheet_sku"),
        result.get("title"),
        user_message,
    ]:
        text = str(value or "").strip()
        if text and text not in parts:
            parts.append(text)
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

def handle_search_manuals(
    query: str,
    manual_filter: str | None = None,
    doc_type_filter: str | None = None,
    expansion_cache: dict | None = None,
    conversation_context: str = "",
    provider_name: str | None = None,
) -> tuple[str, list[dict]]:
    """Search manuals with query expansion and optional re-ranking."""
    expanded_query = (
        _expand_query_cached(query, expansion_cache, context=conversation_context, provider_name=provider_name)
        if expansion_cache is not None
        else expand_query(query, context=conversation_context, provider_name=provider_name)
    )

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
    chunks = chunks[:PILOT_RETRIEVAL_TOP_K]

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
            "part_number": result["requested_sku"],
            "canonical_part_number": result["canonical_sku"],
            "datasheet_part_number": result["datasheet_sku"],
            "title": result["title"],
            "drive_family": result["family"],
            "form_factor": result["form_factor"],
            "network_communication": result["network"],
            "comm_protocol": result["comm_protocol"],
            "comm_manual": result["comm_manual"],
            "hw_manual": result["hw_manual"],
        }
        if result.get("site_status"):
            info["product_status"] = result["site_status"]
        if result.get("support_bucket"):
            info["support_bucket"] = result["support_bucket"]
        if result.get("site_url"):
            info["product_page"] = result["site_url"]
        support_note = build_support_note(result)
        if support_note:
            info["support_note"] = support_note
        if result["alias_resolved"]:
            info["alias_resolution"] = (
                f"'{result['requested_sku']}' maps to the canonical AMC support SKU "
                f"'{result['canonical_sku']}' for local datasheet/manual routing."
            )
        if result["datasheet_sku"] != result["canonical_sku"]:
            info["datasheet_resolution"] = (
                f"Use the local datasheet for '{result['datasheet_sku']}' when the exact "
                f"PDF for '{result['canonical_sku']}' is not present in the corpus."
            )

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
        if result.get("support_bucket") == "core_drive_missing":
            notes.append("Local datasheet coverage is missing for this active drive. Prioritize hardware manual, communication manual, app notes, and product-page metadata.")
        elif result.get("support_bucket") == "core_drive_reserved_gap":
            notes.append("This is a reserved drive. Prefer concise support guidance and rely on hardware/manual metadata instead of assuming full current product coverage.")
        if notes:
            info["usage_notes"] = notes

        return json.dumps(info, indent=2), []

    # Part number NOT found in CSV. Return a HARD error — no regex fallback,
    # no null fields, no room for the assistant to guess.
    error_result = {
        "error": "PART_NUMBER_NOT_FOUND",
        "part_number": part_number,
        "message": f"'{part_number}' is not in the AMC product database of 644 drives.",
        "instructions_for_assistant": (
            "CRITICAL: DO NOT invent, guess, correct, or infer a drive variant from this part number. "
            "DO NOT describe its specs, family, protocol, form factor, or replacement. "
            "DO NOT call find_replacement_drive or any other lookup tool with a modified version. "
            "You MUST respond to the user with: "
            f"\"I couldn't find '{part_number}' in the AMC product database. Can you verify the spelling, "
            "or search for it at https://www.a-m-c.com/products/servo-drives?\" "
            "Then STOP. Wait for the user to confirm the correct part number before proceeding."
        ),
        "valid_sku_patterns": {
            "FlexPro": "FE/FM/FD/FMP/FX + voltage + current + protocol (e.g. FE060-25-EM)",
            "DigiFlex": "DP/DV/DZ/DX + model code (e.g. DPRALTE-020B080)",
            "AxCent": "AZ + model (e.g. AZBH10A4, AZBH25A20-10)",
            "Classic/Analog": "B/BE/BDC + current + voltage (e.g. B30A40, BE25A20)",
        },
    }

    return json.dumps(error_result, indent=2), []


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
    """Deduplicate sources by (source, page) key and enrich with an AMC web URL."""
    from app.url_resolver import resolve_source_url

    sources = []
    seen = set()
    for chunk in all_sources:
        key = (chunk["source"], chunk["page"])
        if key not in seen:
            seen.add(key)
            entry = {
                "source": chunk["source"],
                "page": chunk["page"],
                "heading": chunk.get("heading", ""),
            }
            entry["url"] = resolve_source_url(entry)
            sources.append(entry)
    return sources


def dispatch_tool(
    tool_name: str,
    tool_input: dict,
    expansion_cache: dict | None = None,
    conversation_context: str = "",
    provider_name: str | None = None,
) -> tuple[str, list[dict]]:
    """Route a tool call to the appropriate handler. Returns (result_text, chunks)."""
    try:
        if tool_name == "search_manuals":
            return handle_search_manuals(
                query=tool_input["query"],
                manual_filter=tool_input.get("manual_filter"),
                doc_type_filter=tool_input.get("doc_type"),
                expansion_cache=expansion_cache,
                conversation_context=conversation_context,
                provider_name=provider_name,
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
    compliance_keywords = ['ul-listed', 'ul listed', 'ul certif', 'ce mark', 'ce certif',
                           'reach', 'rohs', 'iso 9001', 'iso9001', 'functional safety',
                           'func safety', 'sto (safe torque', 'safe torque off',
                           'compliance', 'certification', 'certified', 'regulatory']
    marketing_keywords = ['industry flyer', 'brochure', 'use case', 'use-case',
                          'case study', 'applications brochure', 'marketing material']
    # RMA patterns use word boundaries: bare 'rma' substring-matches 'tRANSFoRMAtion'
    # and other innocuous words. Keep the list narrow + specific.
    import re as _re
    rma_patterns = [
        r'\brma\b', r'\brma number\b', r'\brma request\b', r'\breturn authorization\b',
        r'\bbeyond repair\b', r'\breturn for repair\b', r'\bsend.*back for repair\b',
    ]
    rma_hit = any(_re.search(p, msg_lower) for p in rma_patterns)

    # Check compliance / marketing / rma BEFORE the technical categories so a
    # question like "is the FE060-25-EM UL-certified?" routes to compliance
    # rather than to datasheet (which would miss the certification PDFs).
    if any(kw in msg_lower for kw in compliance_keywords):
        return 'compliance'
    if any(kw in msg_lower for kw in marketing_keywords):
        return 'marketing'
    if rma_hit:
        return 'rma'

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


def _smart_route(
    user_message: str,
    history: list[dict] = None,
    drive_context: dict = None,
    cheap_task_provider_name: str | None = None,
    include_metadata: bool = False,
) -> tuple[str, list[dict], str] | tuple[str, list[dict], str, dict]:
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
    detected_sku = None

    # If no pre-selected drive, detect from message
    if not result:
        part_number = detect_part_number(user_message)
        if part_number:
            detected_sku = part_number
            result = lookup_drive(part_number)

    if result:
        support_note = build_support_note(result)
        drive_info = json.dumps({
            "part_number": result["requested_sku"],
            "canonical_part_number": result["canonical_sku"],
            "datasheet_part_number": result["datasheet_sku"],
            "family": result["family"],
            "form_factor": result["form_factor"],
            "network": result["network"],
            "comm_manual": result["comm_manual"],
            "hw_manual": result["hw_manual"],
            "product_status": result.get("site_status"),
            "support_bucket": result.get("support_bucket"),
            "product_page": result.get("site_url"),
            "support_note": support_note,
        }, indent=2)

    # Search with query expansion. Skip expansion when we already have strong
    # routing signal — an exact SKU resolved (drive_context or detected_sku),
    # in which case _smart_route has already narrowed retrieval to priority
    # manuals and expansion tends to pull noisy neighbors. Also skip when the
    # DISABLE_QUERY_EXPANSION escape hatch is set (e.g. --no-llm regression).
    if _should_expand_query(result, detected_sku):
        expanded_query = _expand_query_cached(
            user_message,
            expansion_cache,
            context=conv_context,
            provider_name=cheap_task_provider_name,
        )
    else:
        expanded_query = None
    fetch_k = RERANK_CANDIDATES if ENABLE_RERANKING else TOP_K
    broad_retrieval = False
    priority_manuals: list[str] = []

    if result:
        drive_search_query = user_message
        support_bucket = result.get("support_bucket")
        if support_bucket in {"core_drive_missing", "core_drive_variant_match", "core_drive_reserved_gap"}:
            drive_search_query = _build_drive_search_query(result, user_message)

        # --- Drive-aware priority search ---
        # Priority 1: Drive datasheet (most specific to this exact drive)
        # Priority 2: HW manual (wiring, connectors, mounting)
        # Priority 3: Comm manual (protocol, registers, object dictionary)
        # Priority 4: App notes + everything else (fallback)
        chunks = []
        seen = set()

        datasheet_name = f"AMC_Datasheet_{result['datasheet_sku']}.pdf"
        indexed_sources = get_indexed_sources()
        if datasheet_name in indexed_sources:
            priority_manuals.append(datasheet_name)
        else:
            if result.get("support_bucket") == "core_drive_missing":
                logger.info("Active drive has no local datasheet coverage, using manual-first fallback: %s", datasheet_name)
            else:
                logger.warning("Datasheet not found in index: %s", datasheet_name)
        if result.get("hw_manual"):
            priority_manuals.append(result["hw_manual"])
        if result.get("comm_manual"):
            priority_manuals.append(result["comm_manual"])

        # Search each priority manual (3-way RRF: BM25 original + semantic original + semantic expanded)
        for manual in priority_manuals:
            manual_chunks = retrieve(drive_search_query, top_k=fetch_k, source_filter=manual, expanded_query=expanded_query)
            for c in manual_chunks:
                key = c["text"][:100]
                if key not in seen:
                    seen.add(key)
                    chunks.append(c)

        # Always search app notes as supplement (procedures, tuning, troubleshooting)
        app_chunks = retrieve(drive_search_query, top_k=fetch_k, doc_type_filter="app_note", expanded_query=expanded_query)
        for c in app_chunks:
            key = c["text"][:100]
            if key not in seen:
                seen.add(key)
                chunks.append(c)

        # If still not enough, search everything
        if len(chunks) < 3:
            broad_retrieval = True
            extra = retrieve(drive_search_query, top_k=fetch_k, expanded_query=expanded_query)
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
        broad_retrieval = doc_type is None
        chunks = retrieve(user_message, top_k=fetch_k, doc_type_filter=doc_type, expanded_query=expanded_query)

    # Rerank. Previously gated on len(chunks) > RERANK_TOP_K, which meant that
    # filter-narrowed retrieval (the common case) bypassed reranking entirely
    # and synthesis saw raw RRF order. The cross-encoder handles small pools
    # correctly, so run it whenever we have at least two candidates.
    if ENABLE_RERANKING and len(chunks) >= 2:
        chunks = rerank(user_message, chunks, top_k=RERANK_TOP_K)
    chunks = chunks[:PILOT_RETRIEVAL_TOP_K]

    context_text = build_context(chunks)

    # Build authoritative canonical spec context (P1 spec grounding).
    # Pulls from CM Servo Info.csv so the model cannot fabricate ratings from
    # SKU naming conventions. Keyed off explicitly detected/resolved SKUs and
    # family keywords mentioned in the question.
    canonical_skus: list[str] = []
    if result:
        for key in ("canonical_sku", "datasheet_sku", "requested_sku"):
            val = result.get(key)
            if val and val not in canonical_skus:
                canonical_skus.append(val)
    if detected_sku and detected_sku not in canonical_skus:
        canonical_skus.insert(0, detected_sku)
    canonical_context = build_canonical_context(
        user_message,
        detected_skus=canonical_skus,
        family_limit=12,
    )

    if not include_metadata:
        return context_text, chunks, drive_info

    route_metadata = {
        "support_note": build_support_note(result) if result else "",
        "support_bucket": result.get("support_bucket") if result else None,
        "requested_sku": result.get("requested_sku") if result else None,
        "canonical_sku": result.get("canonical_sku") if result else None,
        "datasheet_sku": result.get("datasheet_sku") if result else None,
        "site_status": result.get("site_status") if result else None,
        "recommended_next_action": result.get("recommended_next_action") if result else None,
        "product_page": result.get("site_url") if result else None,
        "retrieval_chunk_count": len(chunks),
        "broad_retrieval": broad_retrieval,
        "priority_manuals": priority_manuals,
        "canonical_context": canonical_context,
    }
    return context_text, chunks, drive_info, route_metadata


def _build_structured_context_bundle(
    *,
    question: str,
    drive_context: dict | None,
    route_metadata: dict,
    context_text: str,
    uploaded_chunks: list[dict] | None,
) -> str:
    """Build a compact structured prompt payload for the final answer model."""
    bundle = {
        "user_question": question,
        "requested_sku": route_metadata.get("requested_sku"),
        "canonical_sku": route_metadata.get("canonical_sku"),
        "datasheet_sku": route_metadata.get("datasheet_sku"),
        "support_bucket": route_metadata.get("support_bucket"),
        "product_status": route_metadata.get("site_status"),
        "recommended_next_action": route_metadata.get("recommended_next_action"),
        "product_page": route_metadata.get("product_page"),
        "retrieval_chunk_count": route_metadata.get("retrieval_chunk_count"),
        "priority_manuals": route_metadata.get("priority_manuals") or [],
    }
    if drive_context:
        bundle["family"] = drive_context.get("family")
        bundle["form_factor"] = drive_context.get("form_factor")
        bundle["network"] = drive_context.get("network")
        bundle["comm_manual"] = drive_context.get("comm_manual")
        bundle["hw_manual"] = drive_context.get("hw_manual")

    sections = [question, "\n\n[Support Context Bundle]\n" + json.dumps(bundle, indent=2)]

    canonical_context = route_metadata.get("canonical_context")
    if canonical_context:
        sections.append("\n\n[Authoritative Canonical Facts]\n" + canonical_context)

    support_note = route_metadata.get("support_note")
    if support_note:
        sections.append("\n\n[Support Coverage Note]\n" + support_note)

    if uploaded_chunks:
        filename = uploaded_chunks[0].get("source", "uploaded document")
        ranked_upload = _rank_uploaded_chunks(question, uploaded_chunks, top_k=4)
        upload_text = (
            f"\n\n[Uploaded Document Context]\n"
            f"Filename: {filename}\n"
            "Use uploaded specs only when relevant to compatibility or setup.\n"
        )
        for chunk in ranked_upload:
            heading = chunk.get("heading", "")
            heading_str = f" — {heading}" if heading else ""
            excerpt = chunk["text"][:PILOT_CONTEXT_MAX_CHARS]
            if len(chunk["text"]) > PILOT_CONTEXT_MAX_CHARS:
                excerpt += "..."
            upload_text += (
                f"\n--- [UPLOADED DOCUMENT] {filename}, Page {chunk.get('page', '?')}{heading_str} ---\n"
                f"{excerpt}\n"
            )
        sections.append(upload_text)

    if context_text:
        sections.append("\n\n[Top Retrieved Evidence]\n" + context_text)
    else:
        sections.append("\n\n[Top Retrieved Evidence]\nNo relevant manual chunks were retrieved.")

    return "".join(sections)


def single_shot_chat_stream(
    user_message: str,
    history: list[dict] = None,
    drive_context: dict = None,
    uploaded_chunks: list = None,
    answer_provider_name: str | None = None,
    cheap_task_provider_name: str | None = None,
    allow_agentic_fallback: bool | None = None,
    channel: str = "web",
):
    """
    Single-shot RAG: search in Python (0 Sonnet calls), then 1 Sonnet call for the answer.
    Falls back to agentic mode if Sonnet says it needs more info.
    Yields same event format as chat_stream().
    """
    if history is None:
        history = []

    start_time = time.perf_counter()
    answer_provider_name = answer_provider_name or ANSWER_PROVIDER
    cheap_task_provider_name = cheap_task_provider_name or CHEAP_TASK_PROVIDER
    allow_agentic_fallback = (
        PILOT_ENABLE_AGENTIC_FALLBACK if allow_agentic_fallback is None else allow_agentic_fallback
    )

    yield {"type": "status", "text": "Searching manuals..."}

    # Rewrite follow-ups
    standalone_query = rewrite_followup(
        user_message,
        history,
        provider_name=cheap_task_provider_name,
    )

    # Search entirely in Python — no LLM calls
    context_text, chunks, drive_info, route_metadata = _smart_route(
        standalone_query,
        history,
        drive_context=drive_context,
        cheap_task_provider_name=cheap_task_provider_name,
        include_metadata=True,
    )
    all_sources = list(chunks)

    support_note = route_metadata.get("support_note") or (build_support_note(drive_context) if drive_context else "")
    if support_note:
        yield {"type": "status", "text": support_note}

    # Quality gate: if results are weak, fall back to agentic multi-round search
    # (Only for Anthropic backend — Ollama can't do agentic tool-use)
    using_ollama = answer_provider_name == "ollama"
    if chunks:
        avg_score = sum(c.get("score", 0) for c in chunks) / len(chunks)
        if avg_score < 0.01 or len(chunks) < 2:
            if not using_ollama and allow_agentic_fallback:
                logger.info("Single-shot quality gate triggered (avg_score=%.3f, chunks=%d) — falling back to agentic mode", avg_score, len(chunks))
                yield {"type": "status", "text": "Results uncertain, searching deeper..."}
                for event in chat_stream(user_message, history, drive_context, uploaded_chunks):
                    yield event
                return
            else:
                logger.info("Single-shot quality gate triggered but Ollama can't do agentic — proceeding with weak results")
        yield {"type": "status", "text": f"Found {len(chunks)} results, generating answer..."}
    elif not uploaded_chunks:
        if not using_ollama and allow_agentic_fallback:
            logger.info("Single-shot found 0 results — falling back to agentic mode")
            yield {"type": "status", "text": "No direct results, searching deeper..."}
            for event in chat_stream(user_message, history, drive_context, uploaded_chunks):
                yield event
            return
        else:
            yield {"type": "status", "text": "No results found, generating answer..."}

    user_content = _build_structured_context_bundle(
        question=standalone_query,
        drive_context=drive_context,
        route_metadata=route_metadata,
        context_text=context_text,
        uploaded_chunks=uploaded_chunks,
    )

    # Build messages with history
    messages = []
    for msg in history[-4:]:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": user_content})

    # ONE LLM call — stream the answer
    answer = ""
    provider_result = None
    provider = get_provider(answer_provider_name)

    try:
        stream = provider.open_stream(
            messages=messages,
            system_prompt=SYSTEM_PROMPT,
            max_tokens=ANSWER_MAX_TOKENS,
            temperature=0.2,
            cache_system_prompt=answer_provider_name.startswith("anthropic"),
        )
        for text in stream:
            yield {"type": "token", "text": text}
            answer += text
        provider_result = stream.final_result()
    except anthropic.RateLimitError as e:
        logger.warning("Rate limit hit: %s", e)
        raise RateLimitExceeded("The AI service is busy. Please wait a moment and try again.") from e
    except Exception as e:
        logger.error("Single-shot error: %s", e, exc_info=True)
        yield {"type": "token", "text": "An error occurred generating the answer. Please try again."}

    latency_ms = int((time.perf_counter() - start_time) * 1000)
    yield {
        "type": "done",
        "sources": _dedup_sources(all_sources),
        "support_note": support_note or None,
        "provider_used": provider_result.provider_name if provider_result else provider.provider_name,
        "model_used": provider_result.model_name if provider_result else provider.model_name,
        "latency_ms": latency_ms,
        "estimated_cost_usd": provider_result.estimated_cost_usd if provider_result else 0.0,
        "support_bucket": route_metadata.get("support_bucket"),
        "retrieval_chunk_count": route_metadata.get("retrieval_chunk_count", len(chunks)),
        "used_fallback": False,
        "broad_retrieval": route_metadata.get("broad_retrieval", False),
        "channel": channel,
    }


# ---------------------------------------------------------------------------
# Main chat function — agentic tool-use loop (fallback)
# ---------------------------------------------------------------------------

def chat(
    user_message: str,
    history: list[dict] = None,
    drive_context: dict = None,
    uploaded_chunks: list = None,
) -> dict:
    """
    Process a user question using agentic tool-use (Anthropic) or single-shot (Ollama).
    Returns dict with 'answer' and 'sources'.
    """
    if history is None:
        history = []

    # The UI uses the single-shot path by default, so keep the non-streaming API
    # aligned with it when single-shot is enabled.
    if _config.LLM_BACKEND == "ollama" or ENABLE_SINGLE_SHOT:
        answer = ""
        result = {"sources": []}
        for event in single_shot_chat_stream(
            user_message,
            history=history,
            drive_context=drive_context,
            uploaded_chunks=uploaded_chunks,
        ):
            if event["type"] == "token":
                answer += event["text"]
            elif event["type"] == "done":
                result.update(event)
        result["answer"] = answer
        return result

    # Rewrite follow-up questions into standalone queries
    standalone_query = rewrite_followup(user_message, history, provider_name=CHEAP_TASK_PROVIDER)

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
                result_text, chunks = dispatch_tool(
                    block.name,
                    block.input,
                    expansion_cache,
                    conversation_context=conv_context,
                    provider_name=CHEAP_TASK_PROVIDER,
                )
                all_sources.extend(chunks)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_text,
                })

        # Send tool results back to Claude
        messages.append({"role": "user", "content": tool_results})

    else:
        # Safety: hit MAX_TOOL_ROUNDS without end_turn. Force a final answer
        # by disallowing further tool calls. This prevents mid-reasoning text
        # like "Let me try searching for..." from leaking to the user.
        try:
            final_response = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=4096,
                system=SYSTEM_PROMPT_CACHED,
                tools=TOOLS,
                tool_choice={"type": "none"},
                messages=messages + [{
                    "role": "user",
                    "content": "You've reached the search budget. Based on what you found so far, give your best final answer. If you don't have enough information, say so and suggest what the user should check manually. Do NOT mention that you were cut off or try to continue searching.",
                }],
            )
            for block in final_response.content:
                if block.type == "text":
                    answer += block.text
        except Exception as e:
            logger.warning("Final answer generation failed after MAX_TOOL_ROUNDS: %s", e)

        if not answer:
            answer = "I wasn't able to find a confident answer to this question in the indexed manuals. Please try rephrasing or contact AMC support at a-m-c.com."

    return {
        "answer": answer,
        "sources": _dedup_sources(all_sources),
        "support_note": build_support_note(drive_context) if drive_context else None,
        "provider_used": "anthropic_agentic",
        "model_used": CLAUDE_MODEL,
        "latency_ms": 0,
        "estimated_cost_usd": 0.0,
        "support_bucket": drive_context.get("support_bucket") if drive_context else None,
        "retrieval_chunk_count": len(all_sources),
        "used_fallback": True,
        "broad_retrieval": True,
        "channel": "legacy",
    }


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

    start_time = time.perf_counter()
    yield {"type": "status", "text": "Analyzing your question..."}

    # Rewrite follow-up questions
    standalone_query = rewrite_followup(user_message, history, provider_name=CHEAP_TASK_PROVIDER)

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
                result_text, chunks = dispatch_tool(
                    block.name,
                    block.input,
                    expansion_cache,
                    conversation_context=conv_context,
                    provider_name=CHEAP_TASK_PROVIDER,
                )
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
        # Hit MAX_TOOL_ROUNDS — force a final answer without tool calls
        try:
            final_response = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=4096,
                system=SYSTEM_PROMPT_CACHED,
                tools=TOOLS,
                tool_choice={"type": "none"},
                messages=messages + [{
                    "role": "user",
                    "content": "You've reached the search budget. Based on what you found so far, give your best final answer. If you don't have enough information, say so and suggest what the user should check manually. Do NOT mention that you were cut off or try to continue searching.",
                }],
            )
            for block in final_response.content:
                if block.type == "text":
                    yield {"type": "token", "text": block.text}
                    answer += block.text
        except Exception as e:
            logger.warning("Final answer generation failed after MAX_TOOL_ROUNDS: %s", e)

        if not answer:
            fallback = "I wasn't able to find a confident answer to this question in the indexed manuals. Please try rephrasing or contact AMC support at a-m-c.com."
            yield {"type": "token", "text": fallback}
            answer = fallback

    yield {
        "type": "done",
        "sources": _dedup_sources(all_sources),
        "support_note": build_support_note(drive_context) if drive_context else None,
        "provider_used": "anthropic_agentic",
        "model_used": CLAUDE_MODEL,
        "latency_ms": int((time.perf_counter() - start_time) * 1000),
        "estimated_cost_usd": 0.0,
        "support_bucket": drive_context.get("support_bucket") if drive_context else None,
        "retrieval_chunk_count": len(all_sources),
        "used_fallback": True,
        "broad_retrieval": True,
        "channel": "legacy",
    }
