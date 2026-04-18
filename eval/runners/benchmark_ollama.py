#!/usr/bin/env python3
"""
Benchmark a slate of Ollama models against the AMC golden eval set.

Sweeps OLLAMA_MODEL across candidates, runs the existing eval harness per model,
and produces a leaderboard ranked by accuracy + latency.

Usage:
    # Phase A — fast screen, 40 balanced tests per model
    python eval/runners/benchmark_ollama.py

    # Phase B — top survivors on the full 335-test set
    python eval/runners/benchmark_ollama.py --models qwen3:8b qwen2.5:14b llama3.1:8b --full

    # Dry run — no LLM calls, just exercise the harness
    python eval/runners/benchmark_ollama.py --dry-run

    # Skip the `ollama pull` preflight (use only already-installed models)
    python eval/runners/benchmark_ollama.py --skip-pull
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from eval.runners.run_eval import run as run_eval_core, load_tests  # noqa: E402

RESULTS = ROOT / "eval" / "results"
RESULTS.mkdir(parents=True, exist_ok=True)

# Default 7-model slate — RAG/grounded-extraction focused, Apple Silicon 32GB+
DEFAULT_SLATE = [
    "qwen3:8b",             # current baseline
    "llama3.2:3b",          # speed floor
    "qwen2.5:14b",          # consensus top open-weight for RAG
    "llama3.1:8b",          # Meta 8B sanity check
    "gemma2:9b",            # Google, different architecture
    "granite3.1-dense:8b",  # IBM, trained for enterprise RAG
    "mistral-nemo:12b",     # Mistral mid-size, long-context grounding
]


def normalize_tag(tag: str) -> str:
    """Normalize output tags so one run family maps to one filename suffix."""
    raw = (tag or "").strip()
    if not raw:
        return ""

    lowered = raw.lower().replace("-", "_").replace(" ", "_")
    alias_map = {
        "smoketest": "smoke_test",
        "smoke_test": "smoke_test",
        "phasea": "phase_a",
        "phase_a": "phase_a",
        "phaseb": "phase_b",
        "phase_b": "phase_b",
    }
    canonical = alias_map.get(lowered, lowered)
    canonical = re.sub(r"[^a-z0-9_]+", "_", canonical)
    canonical = re.sub(r"_+", "_", canonical).strip("_")
    return canonical


# ============================================================
# Balanced sampling across categories
# ============================================================

def balanced_sample(limit: int) -> list[dict]:
    """
    Return `limit` tests sampled proportionally across the 4 golden sets.
    Ensures every category is represented in the fast screen.
    """
    all_tests = load_tests()
    by_cat: dict[str, list[dict]] = {}
    for t in all_tests:
        cat = t.get("category", "unknown").split("_")[0]
        by_cat.setdefault(cat, []).append(t)

    # Proportional allocation
    total = sum(len(v) for v in by_cat.values())
    picked: list[dict] = []
    for cat, tests in by_cat.items():
        n = max(1, round(limit * len(tests) / total))
        picked.extend(tests[:n])
    return picked[:limit]


# ============================================================
# Ollama pull preflight
# ============================================================

def installed_models() -> set[str]:
    try:
        out = subprocess.check_output(["ollama", "list"], text=True, timeout=30)
    except Exception:
        return set()
    names = set()
    for line in out.splitlines()[1:]:
        parts = line.split()
        if parts:
            names.add(parts[0])
            # Also add without :latest suffix for easier matching
            if parts[0].endswith(":latest"):
                names.add(parts[0][: -len(":latest")])
    return names


def pull_if_missing(model: str, skip: bool = False) -> bool:
    if skip:
        return True
    inst = installed_models()
    if model in inst or f"{model}:latest" in inst:
        return True
    print(f"  [pull] {model} ...")
    try:
        subprocess.check_call(["ollama", "pull", model])
        return True
    except subprocess.CalledProcessError as e:
        print(f"  [pull FAILED] {model}: {e}")
        return False


# ============================================================
# Per-model run
# ============================================================

@dataclass
class ModelResult:
    model: str
    completed: int
    errors: int
    duration_seconds: float
    avg_seconds_per_q: float
    pass_rate: float
    part_number_hallucination_rate: float
    fabricated_citation_rate: float
    refusal_rate: float | None
    by_category: dict = field(default_factory=dict)
    error: str | None = None


def warmup_model(model: str) -> bool:
    """Ping the model once to load weights into memory. Returns True if successful."""
    import urllib.request
    import urllib.error
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 4,
        "temperature": 0.0,
        "stream": False,
    }).encode("utf-8")
    req = urllib.request.Request(
        "http://localhost:11434/v1/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            resp.read()
        return True
    except Exception as e:
        print(f"  [warmup WARN] {model}: {e}")
        return False


def run_one_model(
    model: str,
    limit: int,
    full: bool,
    dry_run: bool,
    verbose: bool,
) -> ModelResult:
    # Point the in-process config at this model — per-call override reads it
    os.environ["OLLAMA_MODEL"] = model
    os.environ["LLM_BACKEND"] = "ollama"

    # Reload config so the new env vars take effect (chat.py reads via _config.OLLAMA_MODEL)
    import importlib
    from app import config as _cfg
    importlib.reload(_cfg)

    # Warmup: load the model weights so first test doesn't pay the cold-start cost
    if not dry_run:
        print(f"  [warmup] loading {model}...")
        warmup_model(model)

    # Outer wall-clock timer — trusted source of truth for latency
    outer_start = time.time()
    try:
        if full:
            run_info = run_eval_core(
                category=None,
                limit=None,
                dry_run=dry_run,
                no_llm_judge=True,
                llm_judge_sample=0.0,
                verbose=verbose,
            )
        else:
            # Use balanced_sample via a temporary test injection
            sampled = balanced_sample(limit)
            # Monkey-patch load_tests briefly
            import eval.runners.run_eval as _re
            original = _re.load_tests
            _re.load_tests = lambda *a, **k: sampled  # type: ignore
            try:
                run_info = run_eval_core(
                    category=None,
                    limit=None,
                    dry_run=dry_run,
                    no_llm_judge=True,
                    llm_judge_sample=0.0,
                    verbose=verbose,
                )
            finally:
                _re.load_tests = original
    except Exception as e:
        return ModelResult(
            model=model, completed=0, errors=1,
            duration_seconds=time.time() - outer_start,
            avg_seconds_per_q=0.0, pass_rate=0.0,
            part_number_hallucination_rate=0.0,
            fabricated_citation_rate=0.0,
            refusal_rate=None, error=str(e),
        )

    outer_dur = time.time() - outer_start
    det = run_info.deterministic_summary
    completed = run_info.completed
    # Use outer wall-clock — inner timer showed bogus values in smoke test
    dur = outer_dur
    return ModelResult(
        model=model,
        completed=completed,
        errors=run_info.errors,
        duration_seconds=dur,
        avg_seconds_per_q=(dur / completed) if completed else 0.0,
        pass_rate=det.get("pass_rate", 0.0),
        part_number_hallucination_rate=det.get("part_number_hallucination_rate", 0.0),
        fabricated_citation_rate=det.get("fabricated_citation_rate", 0.0),
        refusal_rate=det.get("refusal_rate"),
        by_category={
            cat: {"pass_rate": s.get("pass_rate", 0.0), "passed": s.get("passed", 0), "total": s.get("total", 0)}
            for cat, s in (det.get("by_category") or {}).items()
        },
    )


# ============================================================
# Reporting
# ============================================================

def rank_with_latency_dq(results: list[ModelResult]) -> list[ModelResult]:
    """
    Balanced scoring: rank by pass_rate desc, but DQ any model whose avg latency
    is > 2x the fastest model in the slate. Tiebreaker: lower latency.
    """
    alive = [r for r in results if r.completed > 0 and not r.error]
    if not alive:
        return results
    fastest = min(r.avg_seconds_per_q for r in alive if r.avg_seconds_per_q > 0)
    dq_threshold = fastest * 2.0
    for r in alive:
        r.__dict__["dq"] = r.avg_seconds_per_q > dq_threshold
    alive.sort(key=lambda r: (r.__dict__.get("dq", False), -r.pass_rate, r.avg_seconds_per_q))
    return alive + [r for r in results if r not in alive]


def write_markdown(results: list[ModelResult], path: Path, limit: int, full: bool) -> None:
    lines = []
    lines.append("# Ollama Model Benchmark — AMC Support Bot")
    lines.append("")
    phase = "Phase B — full 335-test eval" if full else f"Phase A — {limit}-test balanced screen"
    lines.append(f"**Phase:** {phase}")
    lines.append(f"**Scoring:** Balanced — pass rate primary, DQ if avg latency > 2× fastest")
    lines.append("")
    lines.append("## Leaderboard")
    lines.append("")
    lines.append("| Rank | Model | Pass rate | PN hallucination | Fab citation | Refusal | Avg s/q | Total s | Status |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(results, 1):
        if r.error:
            lines.append(f"| — | `{r.model}` | — | — | — | — | — | — | ERROR: {r.error[:60]} |")
            continue
        status = "DQ (slow)" if r.__dict__.get("dq") else "OK"
        refusal = f"{r.refusal_rate * 100:.0f}%" if r.refusal_rate is not None else "—"
        lines.append(
            f"| {i} | `{r.model}` | **{r.pass_rate * 100:.1f}%** "
            f"| {r.part_number_hallucination_rate * 100:.2f}% "
            f"| {r.fabricated_citation_rate * 100:.2f}% "
            f"| {refusal} "
            f"| {r.avg_seconds_per_q:.1f} "
            f"| {r.duration_seconds:.0f} "
            f"| {status} |"
        )
    lines.append("")

    # By-category breakdown for top 3
    lines.append("## Category breakdown (top 3)")
    lines.append("")
    for r in results[:3]:
        if r.error or not r.by_category:
            continue
        lines.append(f"### `{r.model}`")
        lines.append("")
        lines.append("| Category | Passed | Total | Pass rate |")
        lines.append("|---|---|---|---|")
        for cat, s in sorted(r.by_category.items()):
            lines.append(f"| {cat} | {s['passed']} | {s['total']} | {s['pass_rate'] * 100:.1f}% |")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


# ============================================================
# CLI
# ============================================================

def main():
    p = argparse.ArgumentParser(description="Benchmark Ollama models on the AMC eval set.")
    p.add_argument("--models", nargs="+", default=DEFAULT_SLATE, help="Ollama model tags to benchmark")
    p.add_argument("--limit", type=int, default=40, help="Tests per model for Phase A (balanced sample)")
    p.add_argument("--full", action="store_true", help="Run the full 335-test eval per model (Phase B)")
    p.add_argument("--dry-run", action="store_true", help="Don't call LLMs, use placeholder answers")
    p.add_argument("--skip-pull", action="store_true", help="Skip `ollama pull` preflight")
    p.add_argument("--verbose", "-v", action="store_true")
    p.add_argument("--tag", default="", help="Suffix to append to output filenames")
    args = p.parse_args()
    normalized_tag = normalize_tag(args.tag)

    if not args.skip_pull and not args.dry_run and not shutil.which("ollama"):
        print("ollama CLI not found on PATH — install from https://ollama.com", file=sys.stderr)
        sys.exit(2)

    print("=" * 70)
    print("AMC Support Bot — Ollama Model Benchmark")
    print("=" * 70)
    print(f"Models: {', '.join(args.models)}")
    print(f"Phase:  {'FULL (335 tests)' if args.full else f'FAST SCREEN ({args.limit} balanced tests)'}")
    print(f"Dry run: {args.dry_run}")
    if args.tag and normalized_tag != args.tag:
        print(f"Tag:    {args.tag} -> {normalized_tag}")
    print()

    results: list[ModelResult] = []
    for model in args.models:
        print(f"[{model}] preparing...")
        if not pull_if_missing(model, skip=args.skip_pull or args.dry_run):
            results.append(ModelResult(
                model=model, completed=0, errors=1, duration_seconds=0.0,
                avg_seconds_per_q=0.0, pass_rate=0.0,
                part_number_hallucination_rate=0.0, fabricated_citation_rate=0.0,
                refusal_rate=None, error="pull failed",
            ))
            continue
        print(f"[{model}] running eval...")
        r = run_one_model(model, limit=args.limit, full=args.full,
                          dry_run=args.dry_run, verbose=args.verbose)
        print(f"[{model}] pass_rate={r.pass_rate * 100:.1f}%  "
              f"avg={r.avg_seconds_per_q:.1f}s/q  total={r.duration_seconds:.0f}s")
        results.append(r)

    ranked = rank_with_latency_dq(results)

    # Output
    suffix = f"_{normalized_tag}" if normalized_tag else ""
    json_path = RESULTS / f"model_benchmark{suffix}.json"
    md_path = RESULTS / f"model_benchmark{suffix}.md"
    json_path.write_text(
        json.dumps({"models": [asdict(r) for r in ranked], "full": args.full, "limit": args.limit}, indent=2),
        encoding="utf-8",
    )
    write_markdown(ranked, md_path, limit=args.limit, full=args.full)

    print()
    print("=" * 70)
    print("Leaderboard:")
    for i, r in enumerate(ranked, 1):
        if r.error:
            print(f"  —  {r.model:30s}  ERROR: {r.error}")
            continue
        flag = " [DQ slow]" if r.__dict__.get("dq") else ""
        print(f"  {i}.  {r.model:30s}  {r.pass_rate * 100:5.1f}%   {r.avg_seconds_per_q:5.1f}s/q{flag}")
    print()
    print(f"Results: {md_path}")
    print(f"         {json_path}")


if __name__ == "__main__":
    main()
