#!/usr/bin/env python3
"""
Benchmark the Claude-first AMC support-core runtime on the eval suites.

This runner measures the actual pilot answer path:
  support_core -> deterministic routing -> retrieval -> final answer provider

It combines deterministic quality metrics with runtime telemetry such as:
  - provider distribution
  - latency
  - estimated cost
  - retrieval chunk counts
  - fallback / broad-retrieval rate

Usage:
    # Fast 12-test balanced pilot screen
    python eval/runners/benchmark_pilot_runtime.py --limit 12

    # Same screen, but compare the local provider path
    python eval/runners/benchmark_pilot_runtime.py --provider ollama --limit 12

    # Full eval set with a tagged output file
    python eval/runners/benchmark_pilot_runtime.py --full --tag full

    # Dry run — no provider calls, just exercises the harness
    python eval/runners/benchmark_pilot_runtime.py --dry-run
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import statistics
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)

from eval.judges.amc_deterministic import aggregate, judge_deterministic  # noqa: E402
from eval.runners.benchmark_ollama import balanced_sample, benchmark_group, normalize_tag  # noqa: E402
from eval.runners.run_eval import load_tests  # noqa: E402

RESULTS = ROOT / "eval" / "results"
RESULTS.mkdir(parents=True, exist_ok=True)


@dataclass
class PilotCaseResult:
    test_id: str
    category: str
    question: str
    answer: str
    passed: bool
    failure_reason: str
    provider_used: str | None
    model_used: str | None
    latency_ms: int
    estimated_cost_usd: float
    support_bucket: str | None
    retrieval_chunk_count: int
    used_fallback: bool
    broad_retrieval: bool
    sources: list[dict] = field(default_factory=list)


@dataclass
class PilotBenchmarkResult:
    provider: str
    cheap_task_provider: str
    local_provider: str
    full: bool
    limit: int | None
    total_tests: int
    completed: int
    errors: int
    duration_seconds: float
    deterministic_summary: dict[str, Any] = field(default_factory=dict)
    provider_counts: dict[str, int] = field(default_factory=dict)
    support_bucket_counts: dict[str, int] = field(default_factory=dict)
    median_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    median_latency_ms_nonfaq: float = 0.0
    median_cost_usd: float = 0.0
    median_cost_usd_nonfaq: float = 0.0
    total_estimated_cost_usd: float = 0.0
    avg_retrieval_chunk_count: float = 0.0
    fallback_rate: float = 0.0
    broad_retrieval_rate: float = 0.0
    target_checks: dict[str, Any] = field(default_factory=dict)
    cases: list[PilotCaseResult] = field(default_factory=list)


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    index = (len(ordered) - 1) * pct
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = index - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _select_tests(full: bool, limit: int | None, category: str | None) -> list[dict]:
    if category:
        return load_tests(category_filter=category, limit=None if full else limit)
    if full:
        return load_tests()
    return balanced_sample(limit or 12)


def _configure_runtime(
    *,
    provider: str,
    cheap_task_provider: str,
    local_provider: str,
    faq_enabled: bool,
    allow_agentic_fallback: bool,
):
    os.environ["ANSWER_PROVIDER"] = provider
    os.environ["CHEAP_TASK_PROVIDER"] = cheap_task_provider
    os.environ["LOCAL_PROVIDER"] = local_provider
    os.environ["FAQ_ENABLED"] = "true" if faq_enabled else "false"
    os.environ["PILOT_ENABLE_AGENTIC_FALLBACK"] = "true" if allow_agentic_fallback else "false"
    os.environ["PILOT_DAILY_BUDGET_USD"] = "0"
    os.environ["PILOT_SESSION_REQUEST_CAP"] = "0"

    from app import config as _config
    import app.chat as _chat
    import app.model_provider as _provider
    import app.support_core as _core

    importlib.reload(_config)
    importlib.reload(_provider)
    importlib.reload(_chat)
    importlib.reload(_core)

    # Disable unrelated chatlog/budget I/O when benchmarking answer latency.
    _core._today_cost_total = lambda: 0.0  # type: ignore[attr-defined]
    return _core


def _run_case(test: dict, *, support_core_module, dry_run: bool, channel: str) -> PilotCaseResult:
    question = test.get("question", "")
    if dry_run:
        answer = f"[DRY RUN] placeholder answer for: {question[:80]}"
        sources: list[dict] = []
        provider_used = "dry_run"
        model_used = None
        latency_ms = 0
        estimated_cost_usd = 0.0
        support_bucket = None
        retrieval_chunk_count = 0
        used_fallback = False
        broad_retrieval = False
    else:
        request = support_core_module.SupportRequest(
            message=question,
            session_id=f"pilot-benchmark-{test.get('id', 'unknown')}",
            channel=channel,
        )
        result = support_core_module.run_support_request(request, history=[], drive_context=None, uploaded_chunks=None)
        answer = result.answer
        sources = result.sources
        provider_used = result.provider_used
        model_used = result.model_used
        latency_ms = result.latency_ms
        estimated_cost_usd = result.estimated_cost_usd
        support_bucket = result.support_bucket
        retrieval_chunk_count = result.retrieval_chunk_count
        used_fallback = result.used_fallback
        broad_retrieval = result.broad_retrieval

    context_text = json.dumps(sources, ensure_ascii=False)
    judgment = judge_deterministic(test, answer, retrieved_context=context_text)

    return PilotCaseResult(
        test_id=test.get("id", "unknown"),
        category=test.get("category", "unknown"),
        question=question,
        answer=answer[:2000],
        passed=judgment.passed,
        failure_reason=judgment.failure_reason,
        provider_used=provider_used,
        model_used=model_used,
        latency_ms=latency_ms,
        estimated_cost_usd=estimated_cost_usd,
        support_bucket=support_bucket,
        retrieval_chunk_count=retrieval_chunk_count,
        used_fallback=used_fallback,
        broad_retrieval=broad_retrieval,
        sources=sources,
    )


def _build_result(
    *,
    cases: list[PilotCaseResult],
    tests: list[dict],
    provider: str,
    cheap_task_provider: str,
    local_provider: str,
    full: bool,
    limit: int | None,
    duration_seconds: float,
) -> PilotBenchmarkResult:
    judgments = [
        judge_deterministic(
            test,
            case.answer,
            retrieved_context=json.dumps(case.sources, ensure_ascii=False),
        )
        for test, case in zip(tests, cases)
    ]
    det_summary = aggregate(judgments)

    latencies = [float(case.latency_ms) for case in cases]
    nonfaq_latencies = [float(case.latency_ms) for case in cases if case.provider_used != "faq"]
    costs = [float(case.estimated_cost_usd) for case in cases]
    nonfaq_costs = [float(case.estimated_cost_usd) for case in cases if case.provider_used != "faq"]
    retrieval_counts = [case.retrieval_chunk_count for case in cases]

    provider_counts = Counter(case.provider_used or "unknown" for case in cases)
    support_bucket_counts = Counter(case.support_bucket or "none" for case in cases)

    total_estimated_cost_usd = round(sum(costs), 6)
    median_latency_ms = round(statistics.median(latencies), 1) if latencies else 0.0
    p95_latency_ms = round(_percentile(latencies, 0.95), 1) if latencies else 0.0
    median_latency_ms_nonfaq = round(statistics.median(nonfaq_latencies), 1) if nonfaq_latencies else 0.0
    median_cost_usd = round(statistics.median(costs), 6) if costs else 0.0
    median_cost_usd_nonfaq = round(statistics.median(nonfaq_costs), 6) if nonfaq_costs else 0.0
    avg_retrieval_chunk_count = round(sum(retrieval_counts) / len(retrieval_counts), 2) if retrieval_counts else 0.0
    fallback_rate = round(sum(1 for case in cases if case.used_fallback) / len(cases), 4) if cases else 0.0
    broad_retrieval_rate = round(sum(1 for case in cases if case.broad_retrieval) / len(cases), 4) if cases else 0.0

    target_checks = {
        "single_provider_call_default": all(not case.used_fallback for case in cases),
        "api_errors_zero": det_summary.get("api_errors", 0) == 0,
        "median_latency_target_met": median_latency_ms_nonfaq <= 12_000 if nonfaq_latencies else False,
        "median_cost_target_met": median_cost_usd_nonfaq <= 0.05 if nonfaq_costs else False,
        "fake_sku_hallucination_rate_zero": det_summary.get("part_number_hallucination_rate", 1.0) == 0.0,
        "fabricated_citation_rate_zero": det_summary.get("fabricated_citation_rate", 1.0) == 0.0,
    }

    return PilotBenchmarkResult(
        provider=provider,
        cheap_task_provider=cheap_task_provider,
        local_provider=local_provider,
        full=full,
        limit=limit,
        total_tests=len(tests),
        completed=len(cases),
        errors=0,
        duration_seconds=round(duration_seconds, 2),
        deterministic_summary=det_summary,
        provider_counts=dict(provider_counts),
        support_bucket_counts=dict(support_bucket_counts),
        median_latency_ms=median_latency_ms,
        p95_latency_ms=p95_latency_ms,
        median_latency_ms_nonfaq=median_latency_ms_nonfaq,
        median_cost_usd=median_cost_usd,
        median_cost_usd_nonfaq=median_cost_usd_nonfaq,
        total_estimated_cost_usd=total_estimated_cost_usd,
        avg_retrieval_chunk_count=avg_retrieval_chunk_count,
        fallback_rate=fallback_rate,
        broad_retrieval_rate=broad_retrieval_rate,
        target_checks=target_checks,
        cases=cases,
    )


def _write_markdown(result: PilotBenchmarkResult, path: Path) -> None:
    det = result.deterministic_summary
    lines: list[str] = []
    lines.append("# Claude-First Pilot Runtime Benchmark")
    lines.append("")
    phase = f"full {result.total_tests}-test eval" if result.full else f"{result.limit}-test balanced screen"
    lines.append(f"**Phase:** {phase}")
    lines.append(f"**Answer provider:** `{result.provider}`")
    lines.append(f"**Cheap-task provider:** `{result.cheap_task_provider}`")
    lines.append(f"**Duration:** {result.duration_seconds:.2f}s")
    api_errors = result.deterministic_summary.get("api_errors", 0)
    if api_errors:
        lines.append("")
        lines.append(f"> Warning: `{api_errors}` cases hit provider/API failures and were excluded from quality scoring. Latency and cost on those cases reflect failure-time behavior, not a successful full answer path.")
    lines.append("")
    lines.append("## Quality")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(f"| Deterministic pass rate | **{det.get('pass_rate', 0.0) * 100:.1f}%** |")
    lines.append(f"| API errors excluded from quality | {det.get('api_errors', 0)} |")
    lines.append(f"| Part-number hallucination rate | {det.get('part_number_hallucination_rate', 0.0) * 100:.2f}% |")
    lines.append(f"| Fabricated citation rate | {det.get('fabricated_citation_rate', 0.0) * 100:.2f}% |")
    refusal = det.get("refusal_rate")
    lines.append(f"| Refusal rate | {refusal * 100:.1f}% |" if refusal is not None else "| Refusal rate | — |")
    lines.append("")
    lines.append("## Runtime")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(f"| Median latency (all) | {result.median_latency_ms:.1f} ms |")
    lines.append(f"| P95 latency (all) | {result.p95_latency_ms:.1f} ms |")
    lines.append(f"| Median latency (non-FAQ) | {result.median_latency_ms_nonfaq:.1f} ms |")
    lines.append(f"| Median estimated cost (all) | ${result.median_cost_usd:.6f} |")
    lines.append(f"| Median estimated cost (non-FAQ) | ${result.median_cost_usd_nonfaq:.6f} |")
    lines.append(f"| Total estimated cost | ${result.total_estimated_cost_usd:.6f} |")
    lines.append(f"| Avg retrieval chunk count | {result.avg_retrieval_chunk_count:.2f} |")
    lines.append(f"| Fallback rate | {result.fallback_rate * 100:.1f}% |")
    lines.append(f"| Broad-retrieval rate | {result.broad_retrieval_rate * 100:.1f}% |")
    lines.append("")
    lines.append("## Acceptance Targets")
    lines.append("")
    lines.append("| Target | Status |")
    lines.append("|---|---|")
    for label, status in result.target_checks.items():
        lines.append(f"| `{label}` | {'PASS' if status else 'FAIL'} |")
    lines.append("")
    lines.append("## Provider Distribution")
    lines.append("")
    lines.append("| Provider | Count |")
    lines.append("|---|---|")
    for provider_name, count in sorted(result.provider_counts.items()):
        lines.append(f"| `{provider_name}` | {count} |")
    lines.append("")
    lines.append("## Category Breakdown")
    lines.append("")
    lines.append("| Category | Passed | Total | Pass rate |")
    lines.append("|---|---|---|---|")
    for category, summary in sorted((det.get("by_category") or {}).items()):
        lines.append(f"| {category} | {summary.get('passed', 0)} | {summary.get('total', 0)} | {summary.get('pass_rate', 0.0) * 100:.1f}% |")
    lines.append("")
    lines.append("## Slowest / Costliest Cases")
    lines.append("")

    slowest = sorted(result.cases, key=lambda case: case.latency_ms, reverse=True)[:5]
    costliest = sorted(result.cases, key=lambda case: case.estimated_cost_usd, reverse=True)[:5]

    lines.append("### Slowest")
    lines.append("")
    lines.append("| Test | Provider | Latency | Chunks | Question |")
    lines.append("|---|---|---|---|---|")
    for case in slowest:
        lines.append(f"| `{case.test_id}` | `{case.provider_used}` | {case.latency_ms} ms | {case.retrieval_chunk_count} | {case.question[:90]} |")
    lines.append("")
    lines.append("### Costliest")
    lines.append("")
    lines.append("| Test | Provider | Cost | Chunks | Question |")
    lines.append("|---|---|---|---|---|")
    for case in costliest:
        lines.append(f"| `{case.test_id}` | `{case.provider_used}` | ${case.estimated_cost_usd:.6f} | {case.retrieval_chunk_count} | {case.question[:90]} |")
    lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def run_benchmark(
    *,
    provider: str,
    cheap_task_provider: str,
    local_provider: str,
    limit: int | None,
    full: bool,
    category: str | None,
    dry_run: bool,
    faq_enabled: bool,
    allow_agentic_fallback: bool,
    channel: str,
) -> PilotBenchmarkResult:
    tests = _select_tests(full=full, limit=limit, category=category)
    support_core_module = _configure_runtime(
        provider=provider,
        cheap_task_provider=cheap_task_provider,
        local_provider=local_provider,
        faq_enabled=faq_enabled,
        allow_agentic_fallback=allow_agentic_fallback,
    )

    start = time.time()
    cases: list[PilotCaseResult] = []
    for index, test in enumerate(tests, 1):
        print(f"[{index}/{len(tests)}] {test.get('id', 'unknown')}: {test.get('question', '')[:90]}")
        case = _run_case(test, support_core_module=support_core_module, dry_run=dry_run, channel=channel)
        status = "PASS" if case.passed else "FAIL"
        print(
            f"    {status} provider={case.provider_used} latency={case.latency_ms}ms "
            f"cost=${case.estimated_cost_usd:.6f} chunks={case.retrieval_chunk_count}"
        )
        cases.append(case)

    return _build_result(
        cases=cases,
        tests=tests,
        provider=provider,
        cheap_task_provider=cheap_task_provider,
        local_provider=local_provider,
        full=full,
        limit=limit,
        duration_seconds=time.time() - start,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark the Claude-first AMC support-core runtime.")
    parser.add_argument("--provider", default="anthropic", help="Final answer provider (`anthropic` or `ollama`)")
    parser.add_argument("--cheap-task-provider", default="anthropic_haiku", help="Provider for rewrite/query-expansion work")
    parser.add_argument("--local-provider", default="ollama", help="Local fallback provider label")
    parser.add_argument("--limit", type=int, default=12, help="Balanced sample size for fast runs")
    parser.add_argument("--full", action="store_true", help="Run the full eval set")
    parser.add_argument("--category", default=None, help="Optional category filter")
    parser.add_argument("--dry-run", action="store_true", help="Skip provider calls and use placeholders")
    parser.add_argument("--disable-faq", action="store_true", help="Disable FAQ short-circuiting during the run")
    parser.add_argument("--allow-agentic-fallback", action="store_true", help="Allow the old multi-round fallback path")
    parser.add_argument("--channel", default="web", help="Channel metadata to pass into the support core")
    parser.add_argument("--tag", default="", help="Optional filename suffix")
    args = parser.parse_args()

    print("=" * 70)
    print("AMC Support Bot — Pilot Runtime Benchmark")
    print("=" * 70)
    print(f"Answer provider:      {args.provider}")
    print(f"Cheap-task provider:  {args.cheap_task_provider}")
    print(f"Local provider:       {args.local_provider}")
    print(f"Phase:                {'FULL' if args.full else f'FAST SCREEN ({args.limit} balanced tests)'}")
    print(f"FAQ enabled:          {not args.disable_faq}")
    print(f"Agentic fallback:     {args.allow_agentic_fallback}")
    print(f"Dry run:              {args.dry_run}")
    print()

    result = run_benchmark(
        provider=args.provider,
        cheap_task_provider=args.cheap_task_provider,
        local_provider=args.local_provider,
        limit=args.limit,
        full=args.full,
        category=args.category,
        dry_run=args.dry_run,
        faq_enabled=not args.disable_faq,
        allow_agentic_fallback=args.allow_agentic_fallback,
        channel=args.channel,
    )

    normalized_tag = normalize_tag(args.tag)
    suffix = f"_{normalized_tag}" if normalized_tag else ""
    json_path = RESULTS / f"pilot_runtime_benchmark{suffix}.json"
    md_path = RESULTS / f"pilot_runtime_benchmark{suffix}.md"
    json_path.write_text(json.dumps(asdict(result), indent=2), encoding="utf-8")
    _write_markdown(result, md_path)

    print()
    print("=" * 70)
    print("Summary")
    print(f"Deterministic pass rate: {result.deterministic_summary.get('pass_rate', 0.0) * 100:.1f}%")
    print(f"API errors excluded:      {result.deterministic_summary.get('api_errors', 0)}")
    print(f"Median latency (non-FAQ): {result.median_latency_ms_nonfaq:.1f} ms")
    print(f"Median cost (non-FAQ):    ${result.median_cost_usd_nonfaq:.6f}")
    print(f"Total estimated cost:     ${result.total_estimated_cost_usd:.6f}")
    print(f"Fallback rate:            {result.fallback_rate * 100:.1f}%")
    print(f"Results:                  {md_path}")
    print(f"                          {json_path}")


if __name__ == "__main__":
    main()
