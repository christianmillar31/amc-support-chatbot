import json
import logging
from app.config import QUERY_EXPANSION_MODEL, get_anthropic_client

logger = logging.getLogger(__name__)


def rerank(query: str, chunks: list[dict], top_k: int = 6) -> list[dict]:
    """
    Re-rank retrieved chunks using Haiku for semantic relevance scoring.
    Sends a single API call with all candidates (not per-chunk).
    Falls back to original order on any error.
    """
    if not chunks or len(chunks) <= top_k:
        return chunks

    try:
        client = get_anthropic_client()

        # Build excerpts with metadata (truncate to 800 chars for more context)
        excerpts = []
        for i, chunk in enumerate(chunks):
            text = chunk["text"][:800]
            source = chunk.get("source", "")
            page = chunk.get("page", "?")
            heading = chunk.get("heading", "")
            meta = f"{source}, Page {page}"
            if heading:
                meta += f", Section: {heading}"
            excerpts.append(f"Excerpt {i+1} [{meta}]:\n{text}")

        excerpts_text = "\n\n".join(excerpts)

        response = client.messages.create(
            model=QUERY_EXPANSION_MODEL,
            max_tokens=200,
            system=(
                "You are a relevance scorer for AMC servo drive technical manuals. "
                "Rate each excerpt's relevance to the user's query on a scale of 0-10. "
                "10 = directly answers the query, 0 = completely irrelevant. "
                "Return ONLY a JSON array of integers, one per excerpt, in the same order. "
                "Example: [8, 3, 9, 1, 7, 5]"
            ),
            messages=[{
                "role": "user",
                "content": f"Query: {query}\n\n{excerpts_text}",
            }],
        )

        # Parse scores from response
        scores_text = response.content[0].text.strip()
        # Handle cases where the model wraps in markdown code blocks
        if scores_text.startswith("```"):
            scores_text = scores_text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        scores = json.loads(scores_text)

        if not isinstance(scores, list) or len(scores) != len(chunks):
            logger.warning("Reranker returned %d scores for %d chunks, falling back", len(scores) if isinstance(scores, list) else 0, len(chunks))
            return chunks[:top_k]

        # Pair scores with chunks, sort descending
        scored = list(zip(scores, chunks))
        scored.sort(key=lambda x: x[0], reverse=True)

        return [chunk for _, chunk in scored[:top_k]]

    except Exception as e:
        logger.warning("Re-ranking failed, using TF-IDF order: %s", e)
        return chunks[:top_k]
