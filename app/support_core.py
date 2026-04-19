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
from app.faq import match_faq
from app.support_catalog import build_support_note


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

    if FAQ_ENABLED and not uploaded_chunks:
        faq_result = match_faq(request.message)
        if faq_result:
            sources = [{
                "source": faq_result.get("source", ""),
                "page": int(faq_result["page"]) if str(faq_result.get("page", "")).isdigit() else 0,
                "heading": faq_result.get("section", ""),
            }]
            if support_note:
                yield {"type": "status", "text": support_note}
            yield {"type": "status", "text": "Found a direct FAQ answer..."}
            yield {"type": "token", "text": faq_result.get("answer", "")}
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

    for event in single_shot_chat_stream(
        request.message,
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
