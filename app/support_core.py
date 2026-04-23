from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Iterator

from app.chat import single_shot_chat_stream
from app.config import (
    ANSWER_PROVIDER,
    CHEAP_TASK_PROVIDER,
    FAQ_ENABLED,
    LOCAL_PROVIDER,
    PILOT_BUDGET_MODE,
    PILOT_BUDGET_WARNING_RATIO,
    PILOT_DAILY_BUDGET_USD,
    PILOT_ENABLE_AGENTIC_FALLBACK,
    PILOT_SESSION_REQUEST_CAP,
)
from app.chatlog import get_chatlog
from app.escalation import (
    build_escalation_summary,
    detect_escalation_cues,
    match_escalation_pattern,
)
from app.ambiguity_gate import REFUSAL_MESSAGE as AMBIGUITY_REFUSAL
from app.ambiguity_gate import is_ambiguous_question
from app.competitor_lookup import (
    detect_competitor,
    find_amc_matches,
    format_competitor_answer,
    parse_competitor_specs,
)
from app.faq import match_faq
from app.retrofit_lookup import format_retrofit_answer, is_retrofit_question
from app.sku_matcher import (
    candidate_sku_tokens,
    format_typo_ambiguous_answer,
    format_typo_correction_status,
    format_typo_refusal_answer,
    interpret_typo_hits,
)
from app.spec_validator import (
    detect_impossible_combo,
    resolve_drive_from_message,
    try_spec_answer,
)
from app.support_catalog import build_support_note
from app.url_resolver import enrich_sources, resolve_source_url


class SessionLimitExceeded(Exception):
    """Raised when a pilot session exceeds the configured request cap."""


class BudgetLimitExceeded(Exception):
    """Raised when the daily pilot budget is exhausted and hard-stop mode is enabled."""


@dataclass
class SupportRequest:
    message: str
    session_id: str = "default"
    drive_sku: str | None = None
    channel: str = "web"


@dataclass
class SupportResponse:
    answer: str
    sources: list[dict]
    support_note: str | None = None
    provider_used: str = ANSWER_PROVIDER
    model_used: str | None = None
    latency_ms: int = 0
    estimated_cost_usd: float = 0.0
    support_bucket: str | None = None
    retrieval_chunk_count: int = 0
    used_fallback: bool = False
    broad_retrieval: bool = False
    channel: str = "web"
    session_request_count: int = 0
    cost_budget_state: str = "disabled"

    def to_chatlog_metadata(self) -> dict:
        return asdict(self)


def _session_request_count(history: list[dict] | None) -> int:
    if not history:
        return 0
    return sum(1 for item in history if item.get("role") == "user")


def _today_cost_total() -> float:
    today = datetime.now(timezone.utc).date().isoformat()
    total = 0.0
    for entry in get_chatlog():
        if str(entry.get("timestamp", "")).startswith(today):
            total += float(entry.get("estimated_cost_usd", 0.0) or 0.0)
    return round(total, 6)


def _budget_state(today_cost_usd: float) -> str:
    if PILOT_DAILY_BUDGET_USD <= 0:
        return "disabled"
    if today_cost_usd >= PILOT_DAILY_BUDGET_USD:
        return "over_budget"
    if today_cost_usd >= PILOT_DAILY_BUDGET_USD * PILOT_BUDGET_WARNING_RATIO:
        return "near_budget"
    return "within_budget"


def _resolve_provider_for_request() -> tuple[str, bool, str]:
    today_cost_usd = _today_cost_total()
    budget_state = _budget_state(today_cost_usd)
    provider_name = ANSWER_PROVIDER
    used_fallback = False

    if budget_state == "over_budget":
        if PILOT_BUDGET_MODE == "hard_stop":
            raise BudgetLimitExceeded("Daily pilot budget reached.")
        if PILOT_BUDGET_MODE == "local_fallback":
            provider_name = LOCAL_PROVIDER
            used_fallback = True

    return provider_name, used_fallback, budget_state


def stream_support_request(
    request: SupportRequest,
    *,
    history: list[dict] | None = None,
    drive_context: dict | None = None,
    uploaded_chunks: list[dict] | None = None,
) -> Iterator[dict]:
    request_count = _session_request_count(history) + 1
    if PILOT_SESSION_REQUEST_CAP > 0 and request_count > PILOT_SESSION_REQUEST_CAP:
        raise SessionLimitExceeded(
            f"This pilot session has reached its limit of {PILOT_SESSION_REQUEST_CAP} questions."
        )

    support_note = build_support_note(drive_context or {}) if drive_context else ""
    support_bucket = (drive_context or {}).get("support_bucket")
    provider_name, used_fallback, budget_state = _resolve_provider_for_request()

    # Escalation detection runs once up-front against the original user message.
    # If the message has troubleshooting-escalation cues or matches a known
    # hard pattern, every exit path (retrofit, FAQ, chat) appends the AMC
    # tech-support handoff summary as its last token before "done".
    _escalation_cues = detect_escalation_cues(request.message)
    _escalation_pattern = match_escalation_pattern(request.message)
    _escalation_active = _escalation_cues.should_escalate or _escalation_pattern is not None

    def _maybe_escalation_token():
        if not _escalation_active:
            return None
        summary = build_escalation_summary(
            question=request.message,
            drive_sku=(
                request.drive_sku
                or (drive_context or {}).get("canonical_sku")
                or (drive_context or {}).get("requested_sku")
            ),
            cues=_escalation_cues,
            pattern=_escalation_pattern,
        )
        return {"type": "token", "text": "\n\n" + summary}

    effective_message = request.message

    # Competitor gate: if the user mentions a competitor brand (Elmo,
    # Kollmorgen, Copley, Yaskawa, Beckhoff, ...), don't let the typo gate
    # below treat the competitor SKU as an unknown AMC part. Instead, parse
    # any spec shorthand (Elmo "18/400" style), match against AMC's CSV by
    # current + bus voltage, and return a cross-reference shortlist. Support
    # engineers frequently field migration questions from customers leaving
    # competitors; the generic "couldn't find that SKU" refusal was useless.
    if not uploaded_chunks and not request.drive_sku:
        competitor = detect_competitor(request.message)
        if competitor:
            specs = parse_competitor_specs(request.message, competitor["brand"])
            matches = find_amc_matches(specs["continuous_a"], specs["voltage_dc"]) if specs else []
            answer_text = format_competitor_answer(
                competitor["brand"], specs, matches, request.message
            )
            if support_note:
                yield {"type": "status", "text": support_note}
            yield {
                "type": "status",
                "text": f"Recognized {competitor['brand']} as a competitor — cross-referencing AMC options...",
            }
            yield {"type": "token", "text": answer_text}
            esc_tok = _maybe_escalation_token()
            if esc_tok:
                yield esc_tok
            yield {
                "type": "done",
                "sources": [],
                "support_note": support_note or None,
                "provider_used": "competitor_cross_reference",
                "model_used": None,
                "latency_ms": 0,
                "estimated_cost_usd": 0.0,
                "support_bucket": support_bucket,
                "retrieval_chunk_count": len(matches),
                "used_fallback": used_fallback,
                "broad_retrieval": False,
                "channel": request.channel,
                "session_request_count": request_count,
                "cost_budget_state": budget_state,
            }
            return

    # Typo gate: if the message mentions a SKU-shaped token that's close-but-
    # not-exact to a known drive, either rewrite the query with the corrected
    # SKU (status-only), ask the user to disambiguate, or refuse outright.
    # Runs before retrofit/FAQ so a typo'd classic SKU like "12A9" gets caught.
    if not uploaded_chunks and not request.drive_sku:
        typo_decision = interpret_typo_hits(request.message)
        action = typo_decision.get("action")
        if action == "correct":
            corrected = typo_decision["corrected"]
            raw = typo_decision["raw"]
            correction_notice = format_typo_correction_status(raw, corrected)
            yield {"type": "status", "text": correction_notice}
            # Also emit the correction notice as a token so it shows up in the
            # archived answer body (not just the transient status stream).
            yield {"type": "token", "text": f"_{correction_notice}_\n\n"}
            # Replace the raw token with the corrected SKU in the user message
            # so downstream retrieval and drive lookup use the real part number.
            effective_message = request.message.replace(raw, corrected)
        elif action == "ambiguous":
            raw = typo_decision["raw"]
            candidates = typo_decision.get("candidates") or []
            answer_text = format_typo_ambiguous_answer(raw, candidates)
            yield {"type": "status", "text": "Multiple drives look close to that part number..."}
            yield {"type": "token", "text": answer_text}
            yield {
                "type": "done",
                "sources": [],
                "support_note": None,
                "provider_used": "typo_ambiguous",
                "model_used": None,
                "latency_ms": 0,
                "estimated_cost_usd": 0.0,
                "support_bucket": None,
                "retrieval_chunk_count": 0,
                "used_fallback": used_fallback,
                "broad_retrieval": False,
                "channel": request.channel,
                "session_request_count": request_count,
                "cost_budget_state": budget_state,
            }
            return
        elif action == "refuse":
            raw = typo_decision["raw"]
            answer_text = format_typo_refusal_answer(raw)
            yield {"type": "status", "text": "No close match found in the AMC product database..."}
            yield {"type": "token", "text": answer_text}
            yield {
                "type": "done",
                "sources": [],
                "support_note": None,
                "provider_used": "typo_refusal",
                "model_used": None,
                "latency_ms": 0,
                "estimated_cost_usd": 0.0,
                "support_bucket": None,
                "retrieval_chunk_count": 0,
                "used_fallback": used_fallback,
                "broad_retrieval": False,
                "channel": request.channel,
                "session_request_count": request_count,
                "cost_budget_state": budget_state,
            }
            return

    if not uploaded_chunks:
        retrofit_record = is_retrofit_question(effective_message, request.drive_sku)
        if retrofit_record:
            answer_text = format_retrofit_answer(retrofit_record)
            payload = (retrofit_record or {})
            size = payload.get("size", "small")
            local_pdf = {
                "small": "AMC_ProductNote_AxCent_Retrofit_Small.pdf",
                "large": "AMC_ProductNote_AxCent_Retrofit_Large.pdf",
            }.get(size, "AMC_ProductNote_AxCent_Retrofit_Small.pdf")
            sources = [{
                "source": local_pdf,
                "page": 2,
                "heading": "AxCent Replacement Chart",
                "url": resolve_source_url(local_pdf),
            }]
            if support_note:
                yield {"type": "status", "text": support_note}
            yield {"type": "status", "text": "Found a deterministic retrofit mapping..."}
            yield {"type": "token", "text": answer_text}
            esc_tok = _maybe_escalation_token()
            if esc_tok:
                yield esc_tok
            yield {
                "type": "done",
                "sources": sources,
                "support_note": support_note or None,
                "provider_used": "retrofit_map",
                "model_used": None,
                "latency_ms": 0,
                "estimated_cost_usd": 0.0,
                "support_bucket": support_bucket,
                "retrieval_chunk_count": 1,
                "used_fallback": used_fallback,
                "broad_retrieval": False,
                "channel": request.channel,
                "session_request_count": request_count,
                "cost_budget_state": budget_state,
            }
            return

    # Spec/capability validator: if a SKU resolves and the question is either a
    # direct canonical-spec ask or an impossible protocol/family combination,
    # short-circuit with a model-free answer or refusal. Failures fall through
    # to FAQ → single-shot, so nothing is masked.
    if not uploaded_chunks:
        resolved_drive = resolve_drive_from_message(effective_message, drive_context)
        if resolved_drive:
            combo_refusal = detect_impossible_combo(effective_message, resolved_drive)
            if combo_refusal:
                if support_note:
                    yield {"type": "status", "text": support_note}
                yield {"type": "status", "text": "That protocol isn't supported on that drive — here's what the canonical product table says."}
                yield {"type": "token", "text": combo_refusal["answer"]}
                esc_tok = _maybe_escalation_token()
                if esc_tok:
                    yield esc_tok
                yield {
                    "type": "done",
                    "sources": [],
                    "support_note": support_note or None,
                    "provider_used": combo_refusal.get("provider_used", "impossible_combo_refusal"),
                    "model_used": None,
                    "latency_ms": 0,
                    "estimated_cost_usd": 0.0,
                    "support_bucket": support_bucket,
                    "retrieval_chunk_count": 0,
                    "used_fallback": used_fallback,
                    "broad_retrieval": False,
                    "channel": request.channel,
                    "session_request_count": request_count,
                    "cost_budget_state": budget_state,
                }
                return
            spec_result = try_spec_answer(effective_message, resolved_drive)
            if spec_result:
                if support_note:
                    yield {"type": "status", "text": support_note}
                yield {"type": "status", "text": "Answering from the canonical product table..."}
                yield {"type": "token", "text": spec_result["answer"]}
                esc_tok = _maybe_escalation_token()
                if esc_tok:
                    yield esc_tok
                yield {
                    "type": "done",
                    "sources": spec_result.get("sources", []),
                    "support_note": support_note or None,
                    "provider_used": spec_result.get("provider_used", "canonical_spec"),
                    "model_used": None,
                    "latency_ms": 0,
                    "estimated_cost_usd": 0.0,
                    "support_bucket": support_bucket,
                    "retrieval_chunk_count": 1,
                    "used_fallback": used_fallback,
                    "broad_retrieval": False,
                    "channel": request.channel,
                    "session_request_count": request_count,
                    "cost_budget_state": budget_state,
                }
                return

    if FAQ_ENABLED and not uploaded_chunks:
        faq_result = match_faq(effective_message)
        if faq_result:
            _faq_source = faq_result.get("source", "")
            sources = [{
                "source": _faq_source,
                "page": int(faq_result["page"]) if str(faq_result.get("page", "")).isdigit() else 0,
                "heading": faq_result.get("section", ""),
                "url": resolve_source_url(_faq_source) if _faq_source else "",
            }]
            if support_note:
                yield {"type": "status", "text": support_note}
            yield {"type": "status", "text": "Found a direct FAQ answer..."}
            yield {"type": "token", "text": faq_result.get("answer", "")}
            esc_tok = _maybe_escalation_token()
            if esc_tok:
                yield esc_tok
            yield {
                "type": "done",
                "sources": sources,
                "support_note": support_note or None,
                "provider_used": "faq",
                "model_used": None,
                "latency_ms": 0,
                "estimated_cost_usd": 0.0,
                "support_bucket": support_bucket,
                "retrieval_chunk_count": 1,
                "used_fallback": used_fallback,
                "broad_retrieval": False,
                "channel": request.channel,
                "session_request_count": request_count,
                "cost_budget_state": budget_state,
            }
            return

    # Ambiguity gate: message has no SKU, no family keyword, no tool keyword,
    # no UI-preselected drive, and hits one of a short list of vague-referent
    # phrases or short imperative forms. Emit a "which drive?" refusal rather
    # than letting the single-shot model improvise.
    if not uploaded_chunks:
        has_sku = bool(candidate_sku_tokens(effective_message))
        has_drive_context = bool(drive_context)
        if is_ambiguous_question(effective_message, has_sku, has_drive_context):
            if support_note:
                yield {"type": "status", "text": support_note}
            yield {"type": "status", "text": "The question is a bit open-ended — asking for specifics."}
            yield {"type": "token", "text": AMBIGUITY_REFUSAL}
            esc_tok = _maybe_escalation_token()
            if esc_tok:
                yield esc_tok
            yield {
                "type": "done",
                "sources": [],
                "support_note": support_note or None,
                "provider_used": "ambiguous_refusal",
                "model_used": None,
                "latency_ms": 0,
                "estimated_cost_usd": 0.0,
                "support_bucket": support_bucket,
                "retrieval_chunk_count": 0,
                "used_fallback": used_fallback,
                "broad_retrieval": False,
                "channel": request.channel,
                "session_request_count": request_count,
                "cost_budget_state": budget_state,
            }
            return

    for event in single_shot_chat_stream(
        effective_message,
        history=history or [],
        drive_context=drive_context,
        uploaded_chunks=uploaded_chunks,
        answer_provider_name=provider_name,
        cheap_task_provider_name=CHEAP_TASK_PROVIDER,
        allow_agentic_fallback=PILOT_ENABLE_AGENTIC_FALLBACK,
        channel=request.channel,
    ):
        if event.get("type") == "done":
            event.setdefault("support_note", support_note or None)
            event.setdefault("support_bucket", support_bucket)
            event.setdefault("used_fallback", used_fallback or event.get("used_fallback", False))
            event.setdefault("channel", request.channel)
            event.setdefault("session_request_count", request_count)
            event.setdefault("cost_budget_state", budget_state)
            if _escalation_active:
                event["escalation_active"] = True
                if _escalation_pattern:
                    event["escalation_pattern"] = _escalation_pattern.name
            # Stream the AMC-handoff block BEFORE the done event so the answer
            # body (captured for chatlog) includes it too.
            esc_tok = _maybe_escalation_token()
            if esc_tok:
                yield esc_tok
        yield event


def run_support_request(
    request: SupportRequest,
    *,
    history: list[dict] | None = None,
    drive_context: dict | None = None,
    uploaded_chunks: list[dict] | None = None,
) -> SupportResponse:
    answer = ""
    done_event: dict = {}

    for event in stream_support_request(
        request,
        history=history,
        drive_context=drive_context,
        uploaded_chunks=uploaded_chunks,
    ):
        if event.get("type") == "token":
            answer += event.get("text", "")
        elif event.get("type") == "done":
            done_event = dict(event)

    return SupportResponse(
        answer=answer,
        sources=done_event.get("sources", []),
        support_note=done_event.get("support_note"),
        provider_used=done_event.get("provider_used", ANSWER_PROVIDER),
        model_used=done_event.get("model_used"),
        latency_ms=int(done_event.get("latency_ms", 0) or 0),
        estimated_cost_usd=float(done_event.get("estimated_cost_usd", 0.0) or 0.0),
        support_bucket=done_event.get("support_bucket"),
        retrieval_chunk_count=int(done_event.get("retrieval_chunk_count", 0) or 0),
        used_fallback=bool(done_event.get("used_fallback", False)),
        broad_retrieval=bool(done_event.get("broad_retrieval", False)),
        channel=done_event.get("channel", request.channel),
        session_request_count=int(done_event.get("session_request_count", 0) or 0),
        cost_budget_state=done_event.get("cost_budget_state", "disabled"),
    )
