"""
Provider-routing and support-core runtime tests.
Run with: python -m pytest eval/tests/test_support_core_runtime.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app import config as app_config
import app.chat as chat
import app.support_core as support_core
from app.chatlog import summarize_chatlog
from app.drive_lookup import lookup_drive
from app.model_provider import ProviderResult, ProviderUsage


class _DummyProviderStream:
    def __iter__(self):
        yield "Answer"

    def final_result(self):
        return ProviderResult(
            text="Answer",
            provider_name="anthropic",
            model_name="claude-sonnet-test",
            usage=ProviderUsage(input_tokens=120, output_tokens=40),
            estimated_cost_usd=0.0042,
        )


class _DummyProvider:
    provider_name = "anthropic"
    model_name = "claude-sonnet-test"

    def open_stream(self, **kwargs):
        return _DummyProviderStream()


def test_provider_defaults_are_claude_first():
    assert app_config.ANSWER_PROVIDER == "anthropic"
    assert app_config.CHEAP_TASK_PROVIDER == "anthropic_haiku"
    assert app_config.LOCAL_PROVIDER == "ollama"


def test_single_shot_done_event_includes_provider_and_cost_metadata():
    drive = lookup_drive("FE060-25-EM")
    route_metadata = {
        "support_note": "",
        "support_bucket": "core_drive_covered",
        "requested_sku": "FE060-25-EM",
        "canonical_sku": "FE060-25-EM",
        "datasheet_sku": "FE060-25-EM",
        "site_status": "Active",
        "recommended_next_action": "use_local_datasheet_and_site_metadata",
        "product_page": "https://example.com/fe060-25-em",
        "retrieval_chunk_count": 2,
        "broad_retrieval": False,
        "priority_manuals": ["AMC_Datasheet_FE060-25-EM.pdf"],
    }
    chunks = [
        {"text": "spec block", "source": "AMC_Datasheet_FE060-25-EM.pdf", "page": 1, "heading": "", "score": 0.8},
        {"text": "wiring block", "source": "AMC_HWManual_FlexPro_PCB.pdf", "page": 10, "heading": "", "score": 0.6},
    ]

    def _unexpected_fallback(*args, **kwargs):
        raise AssertionError("Agentic fallback should not run in the normal single-shot pilot path")

    with patch.object(chat, "rewrite_followup", return_value="What is the supply voltage?"), patch.object(
        chat,
        "_smart_route",
        return_value=(chat.build_context(chunks), chunks, "{}", route_metadata),
    ), patch.object(chat, "get_provider", return_value=_DummyProvider()), patch.object(chat, "chat_stream", side_effect=_unexpected_fallback):
        events = list(
            chat.single_shot_chat_stream(
                "What is the supply voltage?",
                history=[],
                drive_context=drive,
                answer_provider_name="anthropic",
                cheap_task_provider_name="anthropic_haiku",
                allow_agentic_fallback=False,
            )
        )

    done_event = next(event for event in events if event["type"] == "done")
    assert done_event["provider_used"] == "anthropic"
    assert done_event["model_used"] == "claude-sonnet-test"
    assert done_event["estimated_cost_usd"] == 0.0042
    assert done_event["support_bucket"] == "core_drive_covered"
    assert done_event["retrieval_chunk_count"] == 2
    assert done_event["used_fallback"] is False


def test_support_core_faq_path_returns_zero_cost():
    with patch.object(
        support_core,
        "match_faq",
        return_value={
            "answer": "Use ACE to connect over USB first.",
            "source": "AMC_SW_Manual_ACE.pdf",
            "page": "25",
            "section": "Connect To Drive",
        },
    ):
        result = support_core.run_support_request(
            support_core.SupportRequest(message="How do I connect ACE?", session_id="faq-test"),
            history=[],
            drive_context=None,
            uploaded_chunks=None,
        )

    assert result.provider_used == "faq"
    assert result.estimated_cost_usd == 0.0
    assert result.retrieval_chunk_count == 1
    assert result.sources[0]["source"] == "AMC_SW_Manual_ACE.pdf"


def test_support_core_enforces_session_cap():
    with patch.object(support_core, "PILOT_SESSION_REQUEST_CAP", 1):
        try:
            list(
                support_core.stream_support_request(
                    support_core.SupportRequest(message="one more question", session_id="cap-test"),
                    history=[{"role": "user", "content": "already asked"}],
                    drive_context=None,
                    uploaded_chunks=None,
                )
            )
        except support_core.SessionLimitExceeded:
            pass
        else:
            raise AssertionError("Expected SessionLimitExceeded when the session cap is exceeded")


def test_budget_local_fallback_switches_to_local_provider():
    captured = {}

    def fake_stream(*args, **kwargs):
        captured["provider"] = kwargs.get("answer_provider_name")
        yield {"type": "token", "text": "fallback answer"}
        yield {
            "type": "done",
            "sources": [],
            "provider_used": kwargs.get("answer_provider_name"),
            "model_used": "local-test",
            "latency_ms": 5,
            "estimated_cost_usd": 0.0,
            "support_bucket": None,
            "retrieval_chunk_count": 0,
            "used_fallback": True,
            "broad_retrieval": False,
            "channel": "web",
        }

    with patch.object(support_core, "_today_cost_total", return_value=999.0), patch.object(
        support_core,
        "PILOT_DAILY_BUDGET_USD",
        1.0,
    ), patch.object(
        support_core,
        "PILOT_BUDGET_MODE",
        "local_fallback",
    ), patch.object(support_core, "match_faq", return_value=None), patch.object(
        support_core,
        "single_shot_chat_stream",
        side_effect=fake_stream,
    ):
        result = support_core.run_support_request(
            support_core.SupportRequest(message="budget test", session_id="budget-test"),
            history=[],
            drive_context=None,
            uploaded_chunks=None,
        )

    assert captured["provider"] == "ollama"
    assert result.provider_used == "ollama"
    assert result.used_fallback is True


def test_chatlog_summary_surfaces_telemetry():
    entries = [
        {
            "timestamp": "2099-01-01T00:00:00+00:00",
            "question": "What is FE060-25-EM voltage?",
            "estimated_cost_usd": 0.04,
            "latency_ms": 8000,
            "drive_sku": "FE060-25-EM",
            "retrieval_chunk_count": 6,
            "used_fallback": False,
            "broad_retrieval": True,
            "support_bucket": "core_drive_covered",
        },
        {
            "timestamp": "2099-01-01T00:01:00+00:00",
            "question": "What is FE060-25-EM voltage?",
            "estimated_cost_usd": 0.01,
            "latency_ms": 1000,
            "drive_sku": "FE060-25-EM",
            "retrieval_chunk_count": 2,
            "used_fallback": False,
            "broad_retrieval": False,
            "support_bucket": "core_drive_covered",
        },
    ]

    summary = summarize_chatlog(entries)
    assert summary["most_expensive"][0]["value"] == 0.04
    assert summary["highest_latency"][0]["value"] == 8000
    assert summary["common_skus"][0]["sku"] == "FE060-25-EM"
    assert summary["common_questions"][0]["count"] == 2
    assert summary["broad_retrieval_or_fallback"]
