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
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from eval.runners.run_eval import run, save_results

# Targets — matches the plan
TARGETS = {
    "pass_rate": 0.75,                    # 75% overall pass rate (baseline)
    "part_number_hallucination_rate": 0.01,  # <1% hallucinated SKUs
    "fabricated_citation_rate": 0.01,     # <1% fabricated citations
    "refusal_rate": 0.80,                 # 80%+ refusal on adversarial tests
    "faithfulness_avg": 0.85,             # 85%+ average faithfulness (when LLM judge used)
    "answer_relevance_avg": 0.80,
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-llm", action="store_true", help="Skip LLM judge (deterministic only)")
    parser.add_argument("--limit", type=int, default=60, help="Max tests to run (default: 60)")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    print("Running regression eval...")
    print(f"  mode: {'deterministic only' if args.no_llm else 'full (det + LLM judge)'}")
    print(f"  limit: {args.limit} tests")
    print()

    # Run a stratified subset: 20 FAQ + 10 drive routing + 10 retrofit + 20 adversarial
    # by running each category separately and combining
    all_results = []
    for category, cat_limit in [("faq", 20), ("drive_routing", 10), ("retrofit", 10), ("adversarial", 20)]:
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
