"""
Lightweight tests for eval runner planning and sampling.
Run with: python -m pytest eval/tests/test_eval_runners.py -v
Or directly: python eval/tests/test_eval_runners.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from eval.runners.benchmark_ollama import balanced_sample, benchmark_group
from eval.runners.run_regression import build_regression_plan


def test_benchmark_group_collapses_adversarial_variants():
    assert benchmark_group("adversarial_fake_sku") == "adversarial"
    assert benchmark_group("adversarial_typo") == "adversarial"
    assert benchmark_group("coverage_state") == "coverage_state"
    assert benchmark_group("drive_routing") == "drive_routing"


def test_balanced_sample_includes_coverage_state_suite():
    sample = balanced_sample(10)
    categories = {benchmark_group(test["category"]) for test in sample}
    assert "coverage_state" in categories
    assert "faq" in categories
    assert "drive_routing" in categories
    assert "retrofit" in categories
    assert "adversarial" in categories


def test_regression_plan_default_includes_coverage_state():
    plan = build_regression_plan(65)
    allocations = dict(plan)
    assert allocations["coverage_state"] >= 5
    assert sum(allocations.values()) == 65


def test_regression_plan_small_limit_still_keeps_coverage_state():
    plan = build_regression_plan(5)
    allocations = dict(plan)
    assert allocations["coverage_state"] == 1
    assert sum(allocations.values()) == 5


if __name__ == "__main__":
    import traceback

    tests = [
        (name, obj) for name, obj in globals().items()
        if name.startswith("test_") and callable(obj)
    ]

    passed = 0
    failed = 0
    errors = []

    for name, func in tests:
        try:
            func()
            print(f"  PASS  {name}")
            passed += 1
        except AssertionError as exc:
            print(f"  FAIL  {name}: {exc}")
            failed += 1
            errors.append((name, str(exc)))
        except Exception as exc:
            print(f"  ERROR {name}: {type(exc).__name__}: {exc}")
            failed += 1
            errors.append((name, traceback.format_exc()))

    print()
    print(f"Results: {passed} passed, {failed} failed (of {passed + failed})")

    if failed:
        sys.exit(1)
