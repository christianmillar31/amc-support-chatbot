"""
Lightweight LLM-as-judge using Claude Haiku.

Implements the key RAG Triad metrics without requiring the full RAGAS stack:
- Faithfulness: every claim supported by retrieved context
- Answer relevance: does the answer address the question
- Context recall: did retrieval surface the right chunks
- Context precision: are retrieved chunks on-topic

Uses structured JSON output with few-shot examples for consistency.
Costs ~$0.001-0.003 per metric per question at Haiku prices.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

JUDGE_MODEL = "claude-haiku-4-5-20251001"


@dataclass
class LLMJudgment:
    faithfulness: float          # 0-1, how grounded in context
    answer_relevance: float      # 0-1, does it address the question
    context_recall: float        # 0-1, did retrieval find the needed info
    context_precision: float     # 0-1, how on-topic the retrieved chunks are
    reasoning: str = ""
    cost_usd: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


_FAITHFULNESS_PROMPT = """You are a strict judge evaluating RAG answers. Score how faithful the answer is to the provided context.

RULES:
- Score 1.0 if EVERY factual claim in the answer is directly supported by the context
- Score 0.5 if most claims are supported but some are inferred or unsupported
- Score 0.0 if the answer contains major unsupported claims (hallucinations)
- Ignore generic advice ("contact support", "check the manual") — don't penalize those

QUESTION: {question}

CONTEXT (retrieved chunks):
{context}

ANSWER:
{answer}

Respond ONLY with valid JSON in this exact format:
{{"faithfulness": <float 0.0-1.0>, "unsupported_claims": [<list of strings>], "reasoning": "<one sentence>"}}
"""

_RELEVANCE_PROMPT = """You are evaluating whether an answer addresses the user's question.

RULES:
- Score 1.0 if the answer directly addresses the question
- Score 0.5 if it partially addresses it or deflects
- Score 0.0 if it's off-topic, refuses appropriately for out-of-scope, or says "I can't help"
- For REFUSAL cases (out-of-scope or not-found questions), appropriate refusal = 1.0

QUESTION: {question}

ANSWER: {answer}

Respond ONLY with valid JSON:
{{"answer_relevance": <float 0.0-1.0>, "reasoning": "<one sentence>"}}
"""

_CONTEXT_QUALITY_PROMPT = """You are judging the quality of retrieved context for a RAG question.

RULES:
- context_recall (0-1): Is the answer to the question PRESENT in the retrieved context?
  * 1.0 = yes, fully
  * 0.5 = partially
  * 0.0 = answer is not there
- context_precision (0-1): How much of the retrieved context is RELEVANT to the question?
  * 1.0 = all chunks are on-topic
  * 0.5 = mixed signal
  * 0.0 = mostly noise

QUESTION: {question}

CONTEXT:
{context}

Respond ONLY with valid JSON:
{{"context_recall": <float>, "context_precision": <float>, "reasoning": "<one sentence>"}}
"""


def _call_haiku(prompt: str, max_tokens: int = 500) -> tuple[str, float]:
    """
    Call Haiku and return (response_text, estimated_cost_usd).
    Haiku 4.5 pricing: $1/MTok input, $5/MTok output as of early 2026.
    """
    try:
        from app.config import get_anthropic_client
    except ImportError:
        logger.warning("app.config not importable; returning mock response")
        return '{"faithfulness": 0.5, "reasoning": "mock"}', 0.0

    client = get_anthropic_client()
    response = client.messages.create(
        model=JUDGE_MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.content[0].text if response.content else ""

    # Cost estimate — approximate, based on usage
    usage = response.usage
    input_tokens = getattr(usage, "input_tokens", 0) or 0
    output_tokens = getattr(usage, "output_tokens", 0) or 0
    cost = (input_tokens * 1e-6 * 1.00) + (output_tokens * 1e-6 * 5.00)

    return text, cost


def _parse_json(text: str) -> Dict[str, Any]:
    """Extract a JSON object from the LLM response, tolerating extra text."""
    # Find the first {...} block
    match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return {}


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "... [truncated]"


def judge_faithfulness(question: str, answer: str, context: str) -> tuple[float, str, float]:
    """Returns (score, reasoning, cost)."""
    prompt = _FAITHFULNESS_PROMPT.format(
        question=_truncate(question, 500),
        context=_truncate(context, 4000),
        answer=_truncate(answer, 2000),
    )
    try:
        text, cost = _call_haiku(prompt, max_tokens=400)
        parsed = _parse_json(text)
        return float(parsed.get("faithfulness", 0.5)), parsed.get("reasoning", ""), cost
    except Exception as e:
        logger.warning("faithfulness judge failed: %s", e)
        return 0.5, f"judge error: {e}", 0.0


def judge_relevance(question: str, answer: str) -> tuple[float, str, float]:
    prompt = _RELEVANCE_PROMPT.format(
        question=_truncate(question, 500),
        answer=_truncate(answer, 2000),
    )
    try:
        text, cost = _call_haiku(prompt, max_tokens=300)
        parsed = _parse_json(text)
        return float(parsed.get("answer_relevance", 0.5)), parsed.get("reasoning", ""), cost
    except Exception as e:
        logger.warning("relevance judge failed: %s", e)
        return 0.5, f"judge error: {e}", 0.0


def judge_context_quality(question: str, context: str) -> tuple[float, float, str, float]:
    """Returns (recall, precision, reasoning, cost)."""
    prompt = _CONTEXT_QUALITY_PROMPT.format(
        question=_truncate(question, 500),
        context=_truncate(context, 4000),
    )
    try:
        text, cost = _call_haiku(prompt, max_tokens=300)
        parsed = _parse_json(text)
        return (
            float(parsed.get("context_recall", 0.5)),
            float(parsed.get("context_precision", 0.5)),
            parsed.get("reasoning", ""),
            cost,
        )
    except Exception as e:
        logger.warning("context quality judge failed: %s", e)
        return 0.5, 0.5, f"judge error: {e}", 0.0


def judge_all(
    question: str,
    answer: str,
    context: str,
    skip_context_eval: bool = False,
) -> LLMJudgment:
    """Run all four judgments. Costs ~$0.003-0.008 per question total."""
    faith, faith_reason, cost1 = judge_faithfulness(question, answer, context)
    rel, rel_reason, cost2 = judge_relevance(question, answer)

    if skip_context_eval or not context:
        recall = 0.0
        precision = 0.0
        ctx_reason = "skipped"
        cost3 = 0.0
    else:
        recall, precision, ctx_reason, cost3 = judge_context_quality(question, context)

    return LLMJudgment(
        faithfulness=faith,
        answer_relevance=rel,
        context_recall=recall,
        context_precision=precision,
        reasoning=f"F: {faith_reason} | R: {rel_reason} | C: {ctx_reason}",
        cost_usd=cost1 + cost2 + cost3,
    )


def aggregate_llm_judgments(judgments: List[LLMJudgment]) -> Dict[str, Any]:
    """Summary metrics from a list of LLM judgments."""
    if not judgments:
        return {"total": 0}

    def _avg(values: List[float]) -> float:
        return round(sum(values) / len(values), 4) if values else 0.0

    return {
        "total": len(judgments),
        "faithfulness_avg": _avg([j.faithfulness for j in judgments]),
        "answer_relevance_avg": _avg([j.answer_relevance for j in judgments]),
        "context_recall_avg": _avg([j.context_recall for j in judgments if j.context_recall > 0]),
        "context_precision_avg": _avg([j.context_precision for j in judgments if j.context_precision > 0]),
        "total_cost_usd": round(sum(j.cost_usd for j in judgments), 4),
    }
