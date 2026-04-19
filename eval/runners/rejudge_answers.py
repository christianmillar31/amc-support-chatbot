#!/usr/bin/env python3
"""Re-apply the deterministic judge to answers from an existing pilot run.

Use this when you change the judge (hallucination whitelist, refusal markers,
required-substring logic, etc.) and want to see the new pass rate without
re-calling the LLM — i.e. without burning provider tokens.

Inputs:
    eval/results/pilot_runtime_benchmark_<tag>.json

Outputs:
    Same shape, tagged `_rejudged`, with recomputed deterministic metrics.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from eval.judges.amc_deterministic import aggregate, judge_deterministic  # noqa: E402
from eval.runners.run_eval import load_tests  # noqa: E402


def load_test_specs() -> dict[str, dict]:
    specs: dict[str, dict] = {}
    for t in load_tests():
        tid = t.get("id")
        if tid:
            specs[tid] = t
    return specs


def rejudge(src_path: Path, out_path: Path) -> dict:
    src = json.loads(src_path.read_text(encoding="utf-8"))
    specs = load_test_specs()

    cases = src.get("cases") or []
    judgments = []
    for case in cases:
        tid = case.get("test_id")
        spec = specs.get(tid) or {}
        ans = case.get("answer") or ""
        # Build a minimal retrieved-context proxy from the sources list so the
        # hallucination verifier accepts SKUs that appeared in source filenames.
        ctx = " ".join(
            f"{s.get('source','')} {s.get('heading','')}" for s in (case.get("sources") or [])
        )
        j = judge_deterministic(spec, ans, retrieved_context=ctx)
        # Mutate the case in place with the new judgment
        case["passed"] = bool(j.passed)
        case["failure_reason"] = j.failure_reason
        judgments.append(j)

    # Rebuild the deterministic summary from the new judgments
    summary = aggregate(judgments)
    src["deterministic_summary"] = summary
    # Also refresh the high-level target_checks section that drives the
    # acceptance-gate display.
    if "target_checks" in src:
        rate = summary.get("part_number_hallucination_rate", 0.0) or 0.0
        src["target_checks"]["fake_sku_hallucination_rate_zero"] = rate <= 0.0
        src["target_checks"]["fabricated_citation_rate_zero"] = (
            summary.get("fabricated_citation_rate", 0.0) or 0.0
        ) <= 0.0

    out_path.write_text(json.dumps(src, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("json_path", type=Path, help="Existing pilot runtime benchmark JSON")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    src = args.json_path.resolve()
    out = args.out or src.with_name(src.stem + "_rejudged.json")
    summary = rejudge(src, out)

    print(f"Re-judged: {src.name} -> {out.name}")
    total = summary.get("valid_tests", summary.get("total_tests", 0))
    passed = summary.get("passed", 0)
    print(f"New pass rate: {summary.get('pass_rate', 0.0) * 100:.2f}% ({passed}/{total} valid tests)")
    print(f"API errors excluded: {summary.get('api_errors', summary.get('api_errors_excluded', 0))}")
    print(f"Part-number hallucination rate: {summary.get('part_number_hallucination_rate', 0.0) * 100:.2f}%")
    print(f"Fabricated citation rate: {summary.get('fabricated_citation_rate', 0.0) * 100:.2f}%")
    print()
    print("By category:")
    for cat, v in sorted((summary.get("by_category") or {}).items()):
        print(f"  {cat}: {v.get('passed',0)}/{v.get('total',0)} ({v.get('pass_rate',0)*100:.1f}%)")


if __name__ == "__main__":
    main()
