"""
Deterministic AMC-specific metrics — no LLM calls, free, fast.

Produces a comprehensive judgment for a single (test, answer, sources) triple.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List

from eval.guardrails.part_number_verifier import verify_part_numbers, HallucinatedSKU
from eval.guardrails.citation_verifier import verify_citations, Citation


# Phrases that indicate the bot is refusing to answer (for adversarial tests)
_REFUSAL_MARKERS = [
    "couldn't find",
    "could not find",
    "not in the amc",
    "not found in the amc",
    "verify the spelling",
    "could you clarify",
    "can you clarify",
    "can you specify",
    "which drive",
    "which specific",
    "i don't have",
    "i cannot",
    "i can't",
    "i'm not able",
    "i am not able",
    "outside my scope",
    "only assist with amc",
    "only help with amc",
    "not in my knowledge",
    "not sure",
]


@dataclass
class DeterministicJudgment:
    test_id: str
    category: str
    passed: bool

    # Specific metrics
    hallucinated_skus: List[str] = field(default_factory=list)
    fabricated_citations: List[str] = field(default_factory=list)
    refused_correctly: bool | None = None   # None if test doesn't require refusal
    expected_phrase_found: bool | None = None  # None if test has no expected phrase
    forbidden_claims_found: List[str] = field(default_factory=list)

    # Raw signals
    part_number_count: int = 0
    citation_count: int = 0

    # Failure reason
    failure_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def detect_refusal(answer: str) -> bool:
    """Heuristic: does this answer refuse to produce content / ask for clarification?"""
    if not answer:
        return True
    lower = answer.lower()
    return any(marker in lower for marker in _REFUSAL_MARKERS)


def judge_deterministic(
    test: Dict[str, Any],
    answer: str,
    retrieved_context: str = "",
) -> DeterministicJudgment:
    """
    Run all deterministic checks on a single test/answer pair.

    Args:
        test: A golden-set test case dict
        answer: The bot's answer text
        retrieved_context: Concatenated text of all retrieved chunks (for SKU grounding)
    """
    j = DeterministicJudgment(
        test_id=test.get("id", "unknown"),
        category=test.get("category", "unknown"),
        passed=True,
    )

    # --- Part number hallucination check ---
    # IMPORTANT: if the question itself contains the SKU (user asked about it),
    # the bot quoting it back to refuse ("I couldn't find 'XYZ'") is NOT a hallucination.
    # Treat the question text as extra "context" for SKU verification purposes.
    question = test.get("question", "")
    combined_context = (retrieved_context or "") + " " + question

    sku_results = verify_part_numbers(answer, combined_context)
    j.part_number_count = len(sku_results)
    j.hallucinated_skus = [r.sku for r in sku_results if r.is_hallucination]

    # --- Citation validity check ---
    citations = verify_citations(answer)
    j.citation_count = len(citations)
    j.fabricated_citations = [
        f"{c.filename}{'p.' + str(c.page) if c.page else ''}"
        for c in citations if c.is_fabricated
    ]

    # --- Refusal check (for adversarial tests) ---
    expected_refuse = test.get("expected_refuse", False)
    is_refusing = detect_refusal(answer)

    if expected_refuse:
        j.refused_correctly = is_refusing
        if not is_refusing:
            j.passed = False
            j.failure_reason = "Expected refusal but bot answered confidently"

    # --- Forbidden claims check (adversarial) ---
    # Only trigger if the bot is NOT refusing. If the bot refused (e.g. "I couldn't find X"),
    # it's allowed to quote the fake SKU back — that's expected behavior.
    forbidden = test.get("forbidden_claims", [])
    if forbidden and not (expected_refuse and is_refusing):
        answer_lower = answer.lower()
        found = [c for c in forbidden if c.lower() in answer_lower]
        j.forbidden_claims_found = found
        if found:
            j.passed = False
            if j.failure_reason:
                j.failure_reason += "; "
            j.failure_reason += f"Contains forbidden claims: {found}"

    # --- Expected phrase check (for FAQ / routing tests) ---
    expected_phrase = test.get("expected_answer_contains", "")
    if expected_phrase and not expected_refuse:
        # Case-insensitive substring check — loose because answers vary in wording
        # Only require individual tokens for longer phrases
        expected_tokens = [t for t in expected_phrase.lower().split() if len(t) > 3]
        if expected_tokens:
            answer_lower = answer.lower()
            matched = sum(1 for t in expected_tokens if t in answer_lower)
            coverage = matched / len(expected_tokens)
            j.expected_phrase_found = coverage >= 0.5  # at least half the keywords
            if not j.expected_phrase_found:
                j.passed = False
                if j.failure_reason:
                    j.failure_reason += "; "
                j.failure_reason += (
                    f"Expected answer to contain keywords from '{expected_phrase[:60]}' "
                    f"(matched {matched}/{len(expected_tokens)})"
                )

    # --- Hard failures ---
    if j.hallucinated_skus:
        j.passed = False
        if j.failure_reason:
            j.failure_reason += "; "
        j.failure_reason += f"Hallucinated SKUs: {j.hallucinated_skus}"

    if j.fabricated_citations:
        j.passed = False
        if j.failure_reason:
            j.failure_reason += "; "
        j.failure_reason += f"Fabricated citations: {j.fabricated_citations}"

    return j


def aggregate(judgments: List[DeterministicJudgment]) -> Dict[str, Any]:
    """Produce summary metrics from a list of judgments."""
    if not judgments:
        return {
            "total_tests": 0,
            "passed": 0,
            "failed": 0,
            "pass_rate": 0.0,
        }

    total = len(judgments)
    passed = sum(1 for j in judgments if j.passed)

    # Per-category breakdown
    categories: Dict[str, Dict[str, int]] = {}
    for j in judgments:
        cat = j.category
        if cat not in categories:
            categories[cat] = {"total": 0, "passed": 0}
        categories[cat]["total"] += 1
        if j.passed:
            categories[cat]["passed"] += 1

    # Specific metric rollups
    total_halluc_skus = sum(len(j.hallucinated_skus) for j in judgments)
    total_fab_citations = sum(len(j.fabricated_citations) for j in judgments)

    refusal_tests = [j for j in judgments if j.refused_correctly is not None]
    refusal_correct = sum(1 for j in refusal_tests if j.refused_correctly)

    return {
        "total_tests": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": round(passed / total, 4),

        "part_number_hallucinations": total_halluc_skus,
        "part_number_hallucination_rate": round(total_halluc_skus / total, 4),

        "fabricated_citations": total_fab_citations,
        "fabricated_citation_rate": round(total_fab_citations / total, 4),

        "refusal_tests": len(refusal_tests),
        "refusal_correct": refusal_correct,
        "refusal_rate": round(refusal_correct / len(refusal_tests), 4) if refusal_tests else None,

        "by_category": {
            cat: {
                "total": v["total"],
                "passed": v["passed"],
                "pass_rate": round(v["passed"] / v["total"], 4) if v["total"] else 0.0,
            }
            for cat, v in categories.items()
        },
    }
