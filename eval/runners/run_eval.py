#!/usr/bin/env python3
"""
Full eval runner. Loads all golden sets, runs each test through the chatbot,
runs all deterministic guardrails + LLM judges, produces a report.

Usage:
    python eval/runners/run_eval.py                    # run everything
    python eval/runners/run_eval.py --no-llm-judge     # skip LLM judges (free, fast)
    python eval/runners/run_eval.py --category faq     # only FAQ tests
    python eval/runners/run_eval.py --limit 20         # first 20 tests
    python eval/runners/run_eval.py --dry-run          # don't call chatbot, use placeholder answers
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# Make project root importable
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from eval.judges.amc_deterministic import (
    DeterministicJudgment,
    aggregate,
    judge_deterministic,
)
from eval.judges.llm_judge import LLMJudgment, aggregate_llm_judgments, judge_all

GOLDEN = ROOT / "eval" / "golden"
RESULTS = ROOT / "eval" / "results"
RESULTS.mkdir(parents=True, exist_ok=True)


# ============================================================
# Loading tests
# ============================================================

def load_tests(category_filter: Optional[str] = None, limit: Optional[int] = None) -> List[dict]:
    """Load all golden-set tests, optionally filtered by category."""
    files = [
        "faq_tests.jsonl",
        "drive_routing_tests.jsonl",
        "coverage_state_tests.jsonl",
        "retrofit_tests.jsonl",
        "adversarial_tests.jsonl",
        "spec_accuracy_tests.jsonl",
    ]
    tests = []
    for filename in files:
        path = GOLDEN / filename
        if not path.exists():
            continue
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    test = json.loads(line)
                    if category_filter and category_filter not in test.get("category", ""):
                        continue
                    tests.append(test)
                except json.JSONDecodeError as e:
                    print(f"  WARN: bad JSON in {filename}: {e}")
    if limit:
        tests = tests[:limit]
    return tests


# ============================================================
# Running the chatbot
# ============================================================

def run_chatbot(question: str, dry_run: bool = False) -> Dict[str, Any]:
    """
    Call the chatbot with a question. Returns {answer, sources, context_text}.
    If dry_run=True, return placeholder values (for framework testing without API cost).
    """
    if dry_run:
        return {
            "answer": f"[DRY RUN] placeholder answer for: {question[:80]}",
            "sources": [],
            "context_text": "",
        }

    try:
        from app.chat import chat
        result = chat(question, history=[])
        return {
            "answer": result.get("answer", ""),
            "sources": result.get("sources", []),
            # We don't have direct access to retrieved chunk text here — pass sources as-is
            "context_text": json.dumps(result.get("sources", []), ensure_ascii=False),
        }
    except Exception as e:
        return {
            "answer": f"[ERROR] {type(e).__name__}: {e}",
            "sources": [],
            "context_text": "",
            "error": str(e),
        }


# ============================================================
# Runner
# ============================================================

@dataclass
class EvalRun:
    timestamp: str
    total_tests: int
    completed: int
    errors: int
    duration_seconds: float
    deterministic_summary: Dict[str, Any] = field(default_factory=dict)
    llm_summary: Dict[str, Any] = field(default_factory=dict)
    test_results: List[Dict[str, Any]] = field(default_factory=list)
    config: Dict[str, Any] = field(default_factory=dict)


def run(
    category: Optional[str] = None,
    limit: Optional[int] = None,
    dry_run: bool = False,
    no_llm_judge: bool = False,
    llm_judge_sample: float = 1.0,
    verbose: bool = False,
) -> EvalRun:
    start = time.time()
    tests = load_tests(category_filter=category, limit=limit)
    print(f"Loaded {len(tests)} test cases")
    if category:
        print(f"  filter: category = {category}")
    if limit:
        print(f"  limit: {limit}")
    print()

    run_info = EvalRun(
        timestamp=dt.datetime.utcnow().isoformat() + "Z",
        total_tests=len(tests),
        completed=0,
        errors=0,
        duration_seconds=0,
        config={
            "category_filter": category,
            "limit": limit,
            "dry_run": dry_run,
            "no_llm_judge": no_llm_judge,
            "llm_judge_sample": llm_judge_sample,
        },
    )

    det_judgments: List[DeterministicJudgment] = []
    llm_judgments: List[LLMJudgment] = []

    for i, test in enumerate(tests, 1):
        tid = test.get("id", f"test_{i}")
        question = test.get("question", "")
        if verbose or len(tests) <= 10 or i % 10 == 0:
            print(f"  [{i}/{len(tests)}] {tid}: {question[:80]}")

        # 1. Call the chatbot
        try:
            bot = run_chatbot(question, dry_run=dry_run)
        except Exception as e:
            run_info.errors += 1
            print(f"    ERROR: {e}")
            continue

        answer = bot.get("answer", "")
        context = bot.get("context_text", "")

        # 2. Deterministic judgment (always runs, no cost)
        det = judge_deterministic(test, answer, retrieved_context=context)
        det_judgments.append(det)

        # 3. LLM judgment (optional, costs money)
        llm_result = None
        if not no_llm_judge and not dry_run:
            # Sample if configured
            import random
            if random.random() <= llm_judge_sample:
                try:
                    llm = judge_all(question, answer, context)
                    llm_judgments.append(llm)
                    llm_result = llm.to_dict()
                except Exception as e:
                    print(f"    LLM judge error: {e}")

        # Record this test
        run_info.test_results.append({
            "test": test,
            "answer": answer[:2000],  # Cap long answers
            "sources": bot.get("sources", []),
            "deterministic": det.to_dict(),
            "llm": llm_result,
        })
        run_info.completed += 1

    # Aggregate
    run_info.deterministic_summary = aggregate(det_judgments)
    if llm_judgments:
        run_info.llm_summary = aggregate_llm_judgments(llm_judgments)

    run_info.duration_seconds = round(time.time() - start, 2)
    return run_info


# ============================================================
# Output
# ============================================================

def save_results(run_info: EvalRun) -> tuple[Path, Path]:
    """Save JSON + Markdown report. Returns (json_path, md_path)."""
    # JSON
    json_path = RESULTS / "latest.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(asdict(run_info), f, indent=2, ensure_ascii=False)

    # Append to history log
    history_path = RESULTS / "history.jsonl"
    summary_line = {
        "timestamp": run_info.timestamp,
        "total_tests": run_info.total_tests,
        "completed": run_info.completed,
        "errors": run_info.errors,
        "duration_seconds": run_info.duration_seconds,
        "deterministic_pass_rate": run_info.deterministic_summary.get("pass_rate"),
        "part_number_hallucination_rate": run_info.deterministic_summary.get("part_number_hallucination_rate"),
        "refusal_rate": run_info.deterministic_summary.get("refusal_rate"),
        "llm_faithfulness": run_info.llm_summary.get("faithfulness_avg") if run_info.llm_summary else None,
        "llm_answer_relevance": run_info.llm_summary.get("answer_relevance_avg") if run_info.llm_summary else None,
        "cost_usd": run_info.llm_summary.get("total_cost_usd") if run_info.llm_summary else 0.0,
    }
    with open(history_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(summary_line) + "\n")

    # Markdown report
    md_path = RESULTS / "latest_report.md"
    md = build_markdown_report(run_info)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)

    return json_path, md_path


def build_markdown_report(run_info: EvalRun) -> str:
    lines = []
    lines.append(f"# AMC Support Bot — Eval Report")
    lines.append(f"")
    lines.append(f"**Run time:** {run_info.timestamp}")
    lines.append(f"**Duration:** {run_info.duration_seconds}s")
    lines.append(f"**Tests:** {run_info.completed}/{run_info.total_tests} completed ({run_info.errors} errors)")
    lines.append(f"")

    # Deterministic
    det = run_info.deterministic_summary
    lines.append("## Deterministic Metrics")
    lines.append(f"")
    lines.append(f"| Metric | Value |")
    lines.append(f"|---|---|")
    lines.append(f"| Overall pass rate | **{det.get('pass_rate', 0) * 100:.1f}%** ({det.get('passed', 0)}/{det.get('total_tests', 0)}) |")
    lines.append(f"| Part-number hallucinations | {det.get('part_number_hallucinations', 0)} ({det.get('part_number_hallucination_rate', 0) * 100:.2f}%) |")
    lines.append(f"| Fabricated citations | {det.get('fabricated_citations', 0)} ({det.get('fabricated_citation_rate', 0) * 100:.2f}%) |")
    if det.get("refusal_rate") is not None:
        lines.append(f"| Adversarial refusal rate | {det['refusal_rate'] * 100:.1f}% ({det.get('refusal_correct', 0)}/{det.get('refusal_tests', 0)}) |")
    lines.append(f"")

    # By category
    lines.append("### By Category")
    lines.append(f"")
    lines.append(f"| Category | Tests | Passed | Pass rate |")
    lines.append(f"|---|---|---|---|")
    for cat, stats in sorted(det.get("by_category", {}).items()):
        lines.append(f"| {cat} | {stats['total']} | {stats['passed']} | {stats['pass_rate'] * 100:.1f}% |")
    lines.append(f"")

    # LLM summary
    llm = run_info.llm_summary
    if llm:
        lines.append("## LLM-as-Judge Metrics (Haiku)")
        lines.append(f"")
        lines.append(f"| Metric | Average |")
        lines.append(f"|---|---|")
        lines.append(f"| Faithfulness | **{llm.get('faithfulness_avg', 0):.3f}** |")
        lines.append(f"| Answer relevance | {llm.get('answer_relevance_avg', 0):.3f} |")
        lines.append(f"| Context recall | {llm.get('context_recall_avg', 0):.3f} |")
        lines.append(f"| Context precision | {llm.get('context_precision_avg', 0):.3f} |")
        lines.append(f"| Total cost | ${llm.get('total_cost_usd', 0):.4f} |")
        lines.append(f"")

    # Top failures
    failures = [r for r in run_info.test_results if not r["deterministic"]["passed"]]
    if failures:
        lines.append(f"## Top Failures (first 20 of {len(failures)})")
        lines.append(f"")
        for i, r in enumerate(failures[:20], 1):
            test = r["test"]
            det = r["deterministic"]
            lines.append(f"### {i}. {test.get('id', '?')} — {test.get('category', '?')}")
            lines.append(f"")
            lines.append(f"**Question:** {test.get('question', '')[:200]}")
            lines.append(f"")
            lines.append(f"**Answer:** {r.get('answer', '')[:400]}")
            lines.append(f"")
            lines.append(f"**Failure reason:** `{det.get('failure_reason', '?')}`")
            if det.get("hallucinated_skus"):
                lines.append(f"**Hallucinated SKUs:** `{det['hallucinated_skus']}`")
            lines.append(f"")
            lines.append("---")
            lines.append("")

    return "\n".join(lines)


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Run the full AMC eval.")
    parser.add_argument("--category", help="Only run tests in this category (e.g. 'faq', 'adversarial')")
    parser.add_argument("--limit", type=int, help="Only run the first N tests")
    parser.add_argument("--dry-run", action="store_true", help="Don't actually call the bot, use placeholder answers")
    parser.add_argument("--no-llm-judge", action="store_true", help="Skip LLM-as-judge calls (free, fast)")
    parser.add_argument("--llm-judge-sample", type=float, default=1.0, help="Fraction of tests to run LLM judge on (0-1)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print each test as it runs")
    args = parser.parse_args()

    print("=" * 70)
    print("AMC Support Bot — Eval Run")
    print("=" * 70)
    print()

    run_info = run(
        category=args.category,
        limit=args.limit,
        dry_run=args.dry_run,
        no_llm_judge=args.no_llm_judge,
        llm_judge_sample=args.llm_judge_sample,
        verbose=args.verbose,
    )

    # Save
    json_path, md_path = save_results(run_info)
    print()
    print("=" * 70)
    print("Results saved:")
    print(f"  {json_path}")
    print(f"  {md_path}")
    print("=" * 70)

    # Summary
    det = run_info.deterministic_summary
    print(f"\nOverall pass rate: {det.get('pass_rate', 0) * 100:.1f}%")
    print(f"Part-number hallucinations: {det.get('part_number_hallucinations', 0)}")
    print(f"Fabricated citations: {det.get('fabricated_citations', 0)}")
    if det.get("refusal_rate") is not None:
        print(f"Adversarial refusal rate: {det['refusal_rate'] * 100:.1f}%")
    if run_info.llm_summary:
        print(f"Faithfulness: {run_info.llm_summary.get('faithfulness_avg', 0):.3f}")
        print(f"Cost: ${run_info.llm_summary.get('total_cost_usd', 0):.4f}")
    print(f"Duration: {run_info.duration_seconds}s")


if __name__ == "__main__":
    main()
