"""
Measure how well an answer's claims are grounded in the retrieved context.

Uses the cross-encoder that's already part of the chatbot's retrieval pipeline
(cross-encoder/ms-marco-MiniLM-L-12-v2) — no new model downloads.

Output: a grounding score from 0.0 (completely ungrounded) to 1.0 (fully grounded).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class GroundingResult:
    overall_score: float          # 0-1, higher is better
    claim_scores: List[float]     # per-sentence score
    claims: List[str]             # the sentences that were scored

    @property
    def poorly_grounded_claims(self) -> List[tuple[str, float]]:
        return [(c, s) for c, s in zip(self.claims, self.claim_scores) if s < 0.3]


def split_claims(answer: str) -> List[str]:
    """Split an answer into individual sentences/claims for grounding."""
    if not answer:
        return []
    # Strip markdown formatting
    text = re.sub(r"[*_`#>]", "", answer)
    # Split on sentence boundaries, keeping code blocks together
    raw = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text)
    # Filter: skip very short sentences (under 20 chars — too short to be a claim)
    claims = [s.strip() for s in raw if len(s.strip()) >= 20]
    return claims


_cross_encoder = None


def _load_cross_encoder():
    """Lazy-load the cross-encoder (same model the retriever uses)."""
    global _cross_encoder
    if _cross_encoder is not None:
        return _cross_encoder
    try:
        from sentence_transformers import CrossEncoder
        _cross_encoder = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-12-v2")
    except Exception as e:
        print(f"Warning: cross-encoder unavailable ({e}); grounding disabled")
        _cross_encoder = False  # Sentinel for failed load
    return _cross_encoder


def compute_grounding(
    answer: str,
    retrieved_contexts: List[str],
    threshold: float = 0.0,
) -> GroundingResult:
    """
    For each sentence in the answer, find its best match in the retrieved contexts
    using the cross-encoder. Return an overall grounding score.

    Args:
        answer: The LLM's answer text
        retrieved_contexts: List of chunks that were retrieved for the question
        threshold: Cross-encoder scores above this count as "grounded"

    Returns:
        GroundingResult with overall_score in [0, 1].
    """
    claims = split_claims(answer)
    if not claims:
        return GroundingResult(overall_score=1.0, claim_scores=[], claims=[])
    if not retrieved_contexts:
        return GroundingResult(overall_score=0.0, claim_scores=[0.0] * len(claims), claims=claims)

    model = _load_cross_encoder()
    if model is False:
        # Model unavailable — return neutral score
        return GroundingResult(overall_score=0.5, claim_scores=[0.5] * len(claims), claims=claims)

    # Score each claim against each context, take the best
    claim_scores = []
    for claim in claims:
        pairs = [(claim, ctx) for ctx in retrieved_contexts]
        scores = model.predict(pairs)
        best = float(max(scores)) if len(scores) else 0.0
        # Sigmoid-normalize cross-encoder raw scores (they're logits)
        import math
        normalized = 1.0 / (1.0 + math.exp(-best))
        claim_scores.append(normalized)

    overall = sum(claim_scores) / len(claim_scores) if claim_scores else 0.0

    return GroundingResult(
        overall_score=overall,
        claim_scores=claim_scores,
        claims=claims,
    )
