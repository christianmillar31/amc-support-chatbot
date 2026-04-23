#!/usr/bin/env python3
"""
Fast regression eval for CI/pre-push. Runs a subset of must-pass tests and
exits non-zero if any metric drops below its target threshold.

Usage:
    python eval/runners/run_regression.py              # fast mode, default targets
    python eval/runners/run_regression.py --no-llm     # deterministic only
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from eval.runners.run_eval import run, save_results, load_tests

# Targets — matches the plan
TARGETS = {
    "pass_rate": 0.75,                    # 75% overall pass rate (baseline)
    "part_number_hallucination_rate": 0.01,  # <1% hallucinated SKUs
    "fabricated_citation_rate": 0.01,     # <1% fabricated citations
    "refusal_rate": 0.80,                 # 80%+ refusal on adversarial tests
    "coverage_state_pass_rate": 1.00,     # coverage-state behavior should be deterministic and strict
    "faithfulness_avg": 0.85,             # 85%+ average faithfulness (when LLM judge used)
    "answer_relevance_avg": 0.80,
}

REGRESSION_BASE_PLAN = [
    ("faq", 20),
    ("drive_routing", 10),
    ("coverage_state", 5),
    ("retrofit", 10),
    ("adversarial", 20),
]


def build_regression_plan(limit: int) -> list[tuple[str, int]]:
    """Scale the regression suite mix while preserving coverage-state checks."""
    if limit <= 0:
        return []

    categories = [name for name, _ in REGRESSION_BASE_PLAN]
    allocations = {name: 0 for name in categories}
    remaining = limit

    if limit >= len(categories):
        for name in categories:
            allocations[name] = 1
            remaining -= 1

    base_total = sum(weight for _, weight in REGRESSION_BASE_PLAN)
    remainders: list[tuple[float, str]] = []
    for name, weight in REGRESSION_BASE_PLAN:
        if remaining <= 0:
            break
        exact = remaining * weight / base_total
        whole = int(exact)
        allocations[name] += whole
        remainders.append((exact - whole, name))

    used = sum(int(remaining * weight / base_total) for _, weight in REGRESSION_BASE_PLAN) if remaining > 0 else 0
    leftover = max(remaining - used, 0)
    for _, name in sorted(remainders, reverse=True)[:leftover]:
        allocations[name] += 1

    available_counts = {
        name: len(load_tests(category_filter=name))
        for name, _ in REGRESSION_BASE_PLAN
    }

    capped = {
        name: min(allocations[name], available_counts[name])
        for name, _ in REGRESSION_BASE_PLAN
    }
    assigned = sum(capped.values())
    deficit = min(limit, sum(available_counts.values())) - assigned
    if deficit > 0:
        for name, weight in sorted(REGRESSION_BASE_PLAN, key=lambda item: item[1], reverse=True):
            spare = available_counts[name] - capped[name]
            if spare <= 0:
                continue
            add = min(spare, deficit)
            capped[name] += add
            deficit -= add
            if deficit == 0:
                break

    plan = []
    for name, _ in REGRESSION_BASE_PLAN:
        count = capped[name]
        if count > 0:
            plan.append((name, count))
    return plan


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-llm", action="store_true", help="Skip LLM judge (deterministic only)")
    parser.add_argument("--limit", type=int, default=65, help="Max tests to run (default: 65)")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    if args.no_llm:
        # --no-llm must be truly token-free. The cheap-task query-expansion
        # path was silently calling Haiku on every retrieval; force it off so
        # CI runs stay clean when the ANTHROPIC_API_KEY secret is unset.
        os.environ["DISABLE_QUERY_EXPANSION"] = "true"

    plan = build_regression_plan(args.limit)

    print("Running regression eval...")
    print(f"  mode: {'deterministic only' if args.no_llm else 'full (det + LLM judge)'}")
    print(f"  limit: {args.limit} tests")
    print("  plan:")
    for category, cat_limit in plan:
        print(f"    - {category}: {cat_limit}")
    print()

    all_results = []
    for category, cat_limit in plan:
        run_info = run(
            category=category,
            limit=cat_limit,
            dry_run=False,
            no_llm_judge=args.no_llm,
            llm_judge_sample=0.3 if not args.no_llm else 0.0,  # Sample 30% for LLM to save cost
            verbose=args.verbose,
        )
        all_results.append(run_info)

    # Merge results
    from eval.runners.run_eval import EvalRun
    import datetime as dt

    merged = EvalRun(
        timestamp=dt.datetime.utcnow().isoformat() + "Z",
        total_tests=sum(r.total_tests for r in all_results),
        completed=sum(r.completed for r in all_results),
        errors=sum(r.errors for r in all_results),
        duration_seconds=round(sum(r.duration_seconds for r in all_results), 2),
        config={"mode": "regression", "no_llm": args.no_llm},
    )
    merged.test_results = [tr for r in all_results for tr in r.test_results]

    # Re-aggregate
    from eval.judges.amc_deterministic import (
        DeterministicJudgment,
        aggregate,
    )
    from eval.judges.llm_judge import LLMJudgment, aggregate_llm_judgments

    det_judgments = [
        DeterministicJudgment(**tr["deterministic"]) if isinstance(tr["deterministic"], dict) else tr["deterministic"]
        for tr in merged.test_results
    ]
    merged.deterministic_summary = aggregate(det_judgments)

    llm_judgments = [
        LLMJudgment(**tr["llm"]) if tr.get("llm") else None
        for tr in merged.test_results
    ]
    llm_judgments = [j for j in llm_judgments if j is not None]
    if llm_judgments:
        merged.llm_summary = aggregate_llm_judgments(llm_judgments)

    save_results(merged)

    # Check targets
    det = merged.deterministic_summary
    llm = merged.llm_summary

    print()
    print("=" * 60)
    print("Regression targets:")
    print("=" * 60)

    failures = []

    def check(name, actual, target, op="ge"):
        """op='ge' means actual should be >= target (higher is better)
        op='le' means actual should be <= target (lower is better)"""
        if actual is None:
            print(f"  SKIP  {name}: no data")
            return
        if op == "ge":
            passed = actual >= target
            status = "PASS" if passed else "FAIL"
            print(f"  {status}  {name}: {actual:.4f} (target: >={target})")
        else:
            passed = actual <= target
            status = "PASS" if passed else "FAIL"
            print(f"  {status}  {name}: {actual:.4f} (target: <={target})")
        if not passed:
            failures.append(f"{name}={actual:.4f} vs target {target}")

    check("pass_rate", det.get("pass_rate"), TARGETS["pass_rate"], "ge")
    check("part_number_hallucination_rate", det.get("part_number_hallucination_rate"), TARGETS["part_number_hallucination_rate"], "le")
    check("fabricated_citation_rate", det.get("fabricated_citation_rate"), TARGETS["fabricated_citation_rate"], "le")
    check("refusal_rate", det.get("refusal_rate"), TARGETS["refusal_rate"], "ge")
    coverage_state_summary = (det.get("by_category") or {}).get("coverage_state", {})
    check("coverage_state_pass_rate", coverage_state_summary.get("pass_rate"), TARGETS["coverage_state_pass_rate"], "ge")

    if llm:
        check("faithfulness", llm.get("faithfulness_avg"), TARGETS["faithfulness_avg"], "ge")
        check("answer_relevance", llm.get("answer_relevance_avg"), TARGETS["answer_relevance_avg"], "ge")

    print()
    if failures:
        print(f"REGRESSION — {len(failures)} metric(s) below target:")
        for f in failures:
            print(f"  * {f}")
        sys.exit(1)
    else:
        print("All targets met ✓")
        sys.exit(0)


if __name__ == "__main__":
    main()
