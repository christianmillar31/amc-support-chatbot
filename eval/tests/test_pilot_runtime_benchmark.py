"""
Lightweight tests for the Claude-first pilot runtime benchmark runner.
Run with: python -m pytest eval/tests/test_pilot_runtime_benchmark.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from eval.runners.benchmark_pilot_runtime import PilotCaseResult, _build_result  # noqa: E402


def test_build_result_computes_runtime_medians_and_provider_counts():
    tests = [
        {"id": "faq_1", "category": "faq", "question": "How do I connect ACE?"},
        {"id": "drive_1", "category": "drive_routing", "question": "What is FE060-25-EM voltage?"},
    ]
    cases = [
        PilotCaseResult(
            test_id="faq_1",
            category="faq",
            question="How do I connect ACE?",
            answer="Use ACE over USB. [Source: AMC_SW_Manual_ACE.pdf, Page 25]",
            passed=True,
            failure_reason="",
            provider_used="faq",
            model_used=None,
            latency_ms=0,
            estimated_cost_usd=0.0,
            support_bucket=None,
            retrieval_chunk_count=1,
            used_fallback=False,
            broad_retrieval=False,
            sources=[{"source": "AMC_SW_Manual_ACE.pdf", "page": 25, "heading": "Connect To Drive"}],
        ),
        PilotCaseResult(
            test_id="drive_1",
            category="drive_routing",
            question="What is FE060-25-EM voltage?",
            answer="The FE060-25-EM supply range is listed in the datasheet. [Source: AMC_Datasheet_FE060-25-EM.pdf, Page 1]",
            passed=True,
            failure_reason="",
            provider_used="anthropic",
            model_used="claude-sonnet-test",
            latency_ms=8200,
            estimated_cost_usd=0.0123,
            support_bucket="core_drive_covered",
            retrieval_chunk_count=4,
            used_fallback=False,
            broad_retrieval=False,
            sources=[{"source": "AMC_Datasheet_FE060-25-EM.pdf", "page": 1, "heading": ""}],
        ),
    ]

    result = _build_result(
        cases=cases,
        tests=tests,
        provider="anthropic",
        cheap_task_provider="anthropic_haiku",
        local_provider="ollama",
        full=False,
        limit=2,
        duration_seconds=8.5,
    )

    assert result.provider_counts["faq"] == 1
    assert result.provider_counts["anthropic"] == 1
    assert result.median_latency_ms == 4100.0
    assert result.median_latency_ms_nonfaq == 8200.0
    assert result.median_cost_usd_nonfaq == 0.0123
    assert result.total_estimated_cost_usd == 0.0123
    assert result.target_checks["api_errors_zero"] is True
    assert result.target_checks["median_latency_target_met"] is True
    assert result.target_checks["median_cost_target_met"] is True


def test_build_result_flags_fallback_and_hallucination_failures():
    tests = [
        {"id": "adv_1", "category": "adversarial", "question": "What about FE999-99-EM?", "expected_refuse": True},
    ]
    cases = [
        PilotCaseResult(
            test_id="adv_1",
            category="adversarial",
            question="What about FE999-99-EM?",
            answer="The FE999-99-EM is a FlexPro EtherCAT drive.",
            passed=False,
            failure_reason="hallucinated",
            provider_used="anthropic",
            model_used="claude-sonnet-test",
            latency_ms=13000,
            estimated_cost_usd=0.07,
            support_bucket=None,
            retrieval_chunk_count=7,
            used_fallback=True,
            broad_retrieval=True,
            sources=[],
        ),
    ]

    result = _build_result(
        cases=cases,
        tests=tests,
        provider="anthropic",
        cheap_task_provider="anthropic_haiku",
        local_provider="ollama",
        full=False,
        limit=1,
        duration_seconds=13.0,
    )

    assert result.fallback_rate == 1.0
    assert result.broad_retrieval_rate == 1.0
    assert result.target_checks["single_provider_call_default"] is False
    assert result.target_checks["api_errors_zero"] is True
    assert result.target_checks["median_latency_target_met"] is False
    assert result.target_checks["median_cost_target_met"] is False
    assert result.deterministic_summary["pass_rate"] == 0.0
    assert result.target_checks["fake_sku_hallucination_rate_zero"] is True
