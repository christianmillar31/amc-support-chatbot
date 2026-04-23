"""Ambiguity refusal gate for under-specified support questions.

Runs after the FAQ gate (so deterministic FAQ answers still win) and before
the live-chat path in ``support_core.stream_support_request``. Fires only on
messages that name no SKU, no family, no tool/product name, and carry one of
a short list of vague-referent phrases or short imperative forms. On hit, the
caller emits a "which drive?" refusal rather than letting the single-shot
model improvise.

Target cases:
- "How do I set up the drive?"
- "What's the current limit?"
- "It's not working. Help."
- "Which manual do I need?"
- "Tune it."

Red-team note: an earlier draft triggered on the phrase ``how do I set up``
alone, which would false-refuse legitimate FAQ rows like "how do I set up
homing in DriveWare?" and "how do I set up EtherCAT communication on a
FlexPro drive?". The family/tool-keyword bailout below handles those; the
remaining triggers are exact vague-referent phrases or ≤5-word imperatives.
"""

from __future__ import annotations

import re


_FAMILY_KEYWORDS = re.compile(
    r"\b(flexpro|digiflex|axcent|classic|analog)\b",
    re.IGNORECASE,
)
_TOOL_KEYWORDS = re.compile(
    r"\b(driveware|clickmove|twincat|ace|sdo|pdo|"
    r"ethercat|canopen|ether\s*cat|can\s*open|powerlink|ethernet\s*/?\s*ip|"
    r"modbus|rs\s*-?\s*485|rs\s*-?\s*232|serial|homing|pvt\s+mode|"
    r"brushless|brushed|stepper)\b",
    re.IGNORECASE,
)

# Exact phrases that strongly indicate a missing referent.
_VAGUE_PHRASES = re.compile(
    r"\b("
    r"the\s+drive|this\s+drive|my\s+drive|a\s+drive|"
    r"tune\s+it|fix\s+it|check\s+it|"
    r"it's\s+not\s+working|its\s+not\s+working|"
    r"not\s+working\.?\s+help|"
    r"what(?:'s|\s+is)?\s+the\s+current\s+limit|"
    r"which\s+manual\s+do\s+i\s+need|what\s+manual\s+do\s+i\s+need"
    r")\b",
    re.IGNORECASE,
)

# Generic imperative verbs used as a ≤5-word short-message trigger.
_IMPERATIVE_VERBS = re.compile(
    r"\b(tune|configure|setup|set\s+up|fix|check|troubleshoot|help|debug|"
    r"calibrate|wire|connect)\b",
    re.IGNORECASE,
)


def _word_count(message: str) -> int:
    return len([w for w in re.split(r"\s+", message.strip()) if w])


def is_ambiguous_question(
    message: str,
    has_sku: bool,
    has_drive_context: bool,
) -> bool:
    """Return True when the message should short-circuit to a "which drive?" refusal.

    Parameters
    ----------
    message:
        Raw user message (typo-corrected form preferred, matching what FAQ sees).
    has_sku:
        True if a SKU-shaped token resolved to a real drive in the message.
    has_drive_context:
        True if the caller supplied a UI-preselected drive context.
    """
    if has_sku or has_drive_context:
        return False
    text = (message or "").strip()
    if not text:
        # Empty messages are handled upstream; treat as not-ambiguous here so
        # the caller can decide (usually validates earlier).
        return False

    # Family or tool-name keywords make the question specific enough to route.
    if _FAMILY_KEYWORDS.search(text):
        return False
    if _TOOL_KEYWORDS.search(text):
        return False

    # Exact vague-referent phrases trigger regardless of length.
    if _VAGUE_PHRASES.search(text):
        return True

    # Short imperatives (≤5 words) with a generic verb and no specificity.
    if _word_count(text) <= 5 and _IMPERATIVE_VERBS.search(text):
        return True

    return False


REFUSAL_MESSAGE = (
    "I need more information to help. Could you provide the drive part number "
    "(e.g. `AZBH10A4`, `FE060-25-EM`) and briefly what you're trying to do? "
    "Which drive you're working with matters — capabilities, wiring, and "
    "tuning all differ by family. If the part number isn't handy, the family "
    "name (FlexPro, DigiFlex, AxCent, Classic) plus the goal — setup, wiring, "
    "tuning, a spec — is enough to get started."
)
