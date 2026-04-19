"""
Deterministic AMC-specific metrics — no LLM calls, free, fast.

Produces a comprehensive judgment for a single (test, answer, sources) triple.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List

from eval.guardrails.part_number_verifier import verify_part_numbers, HallucinatedSKU
from eval.guardrails.citation_verifier import verify_citations, Citation


# Phrases that indicate the bot is refusing / asking for clarification
_REFUSAL_MARKERS = [
    "couldn't find",
    "could not find",
    "not in the amc",
    "not found in the amc",
    "not listed in",
    "not specifically listed",
    "doesn't appear to match",
    "does not appear to match",
    "doesn't match",
    "does not match",
    "may not be a standard",
    "not a standard amc",
    "unusual for the",
    "verify the spelling",
    "verify the exact",
    "verify the part",
    "double-check the",
    "double check the",
    "could you verify",
    "can you verify",
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
    "if you meant",
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
    is_api_error: bool = False              # True if the bot errored out (credits, rate limit, timeout)

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


_API_ERROR_MARKERS = [
    "[ERROR]",
    "credit balance is too low",
    "BadRequestError",
    "RateLimitError",
    "authentication_error",
    "Insufficient credit",
    "request timed out",
    "An error occurred generating the answer",
    "An unexpected error occurred",
    "Your credit balance",
]


def _is_api_error(answer: str) -> bool:
    """Detect if the bot answer is actually an infrastructure error, not a real response."""
    if not answer:
        return False
    return any(marker in answer for marker in _API_ERROR_MARKERS)


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

    # --- API error check ---
    # If the bot errored out (credits, rate limit, timeout), don't count it against
    # answer quality. Mark it as an API error so it can be excluded from metrics.
    if _is_api_error(answer):
        j.is_api_error = True
        j.passed = False
        j.failure_reason = "API_ERROR (not counted against quality metrics)"
        return j

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

    # --- Required substring checks (coverage-state / routing nuance) ---
    answer_lower = answer.lower()

    required_all = test.get("required_substrings_all", [])
    if required_all:
        missing = [text for text in required_all if text.lower() not in answer_lower]
        if missing:
            j.passed = False
            if j.failure_reason:
                j.failure_reason += "; "
            j.failure_reason += f"Missing required substrings: {missing}"

    required_any = test.get("required_substrings_any", [])
    if required_any and not any(text.lower() in answer_lower for text in required_any):
        j.passed = False
        if j.failure_reason:
            j.failure_reason += "; "
        j.failure_reason += f"Missing all optional required substrings: {required_any}"

    # --- Expected phrase check (for FAQ / routing tests) ---
    # Lenient matching: normalize punctuation and require 40% of content tokens
    # to appear as substrings in the answer. Handles wording variations like
    # "Boot-up" vs "Bootup", "Pre-operational" vs "Pre-operational state".
    expected_phrase = test.get("expected_answer_contains", "")
    if expected_phrase and not expected_refuse:
        import re as _re
        def _normalize(s: str) -> str:
            s = s.lower()
            s = _re.sub(r"[-_/]", "", s)           # Remove dashes/underscores
            s = _re.sub(r"[^\w\s]", " ", s)       # Replace other punct with space
            s = _re.sub(r"\s+", " ", s).strip()   # Collapse whitespace
            return s

        norm_expected = _normalize(expected_phrase)
        norm_answer = _normalize(answer)

        # Get content tokens (length > 3, not common stopwords)
        stopwords = {"with", "from", "that", "this", "have", "been", "will", "what",
                     "when", "where", "which", "should", "would", "could", "their",
                     "these", "those", "into", "about", "than", "then"}
        expected_tokens = [
            t for t in norm_expected.split()
            if len(t) > 3 and t not in stopwords
        ]
        if expected_tokens:
            matched = sum(1 for t in expected_tokens if t in norm_answer)
            coverage = matched / len(expected_tokens)
            j.expected_phrase_found = coverage >= 0.4  # at least 40% of the keywords
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

    # Separate API errors from real results — they shouldn't count against quality
    api_errors = [j for j in judgments if j.is_api_error]
    valid = [j for j in judgments if not j.is_api_error]
    valid_count = len(valid)

    passed = sum(1 for j in valid if j.passed)

    # Per-category breakdown (excluding API errors)
    categories: Dict[str, Dict[str, int]] = {}
    for j in valid:
        cat = j.category
        if cat not in categories:
            categories[cat] = {"total": 0, "passed": 0}
        categories[cat]["total"] += 1
        if j.passed:
            categories[cat]["passed"] += 1

    # Specific metric rollups (on valid results only)
    total_halluc_skus = sum(len(j.hallucinated_skus) for j in valid)
    total_fab_citations = sum(len(j.fabricated_citations) for j in valid)

    refusal_tests = [j for j in valid if j.refused_correctly is not None]
    refusal_correct = sum(1 for j in refusal_tests if j.refused_correctly)

    denom = valid_count if valid_count > 0 else 1

    return {
        "total_tests": total,
        "valid_tests": valid_count,
        "api_errors": len(api_errors),
        "passed": passed,
        "failed": valid_count - passed,
        "pass_rate": round(passed / denom, 4),

        "part_number_hallucinations": total_halluc_skus,
        "part_number_hallucination_rate": round(total_halluc_skus / denom, 4),

        "fabricated_citations": total_fab_citations,
        "fabricated_citation_rate": round(total_fab_citations / denom, 4),

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
