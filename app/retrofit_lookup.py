from __future__ import annotations

import json
import re
from pathlib import Path

from app.config import BASE_DIR


RETROFIT_TRIGGER_WORDS = (
    "retrofit",
    "replace",
    "replaces",
    "replacement",
    "discontinued",
    "obsolete",
    "end of life",
    "end-of-life",
    "eol",
)

SKU_PATTERN = re.compile(r"[A-Z]{0,3}\d{1,3}[A-Z]\d{1,3}[A-Z]{0,3}(?:-[A-Z0-9]+)?")

_RETROFIT_BY_SKU: dict[str, dict] = {}
_RETROFIT_PAYLOAD: dict = {}


def _normalize(sku: str) -> str:
    s = (sku or "").strip().upper()
    s = s.replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", "", s)


def _strip_option_suffix(sku: str) -> str:
    """Strip the -INV / -QD / -QDI / -ANP option suffixes documented in the retrofit PDFs."""
    for suffix in ("-ANP", "-QDI", "-QD", "-INV"):
        if sku.endswith(suffix):
            return sku[: -len(suffix)]
    return sku


def _strip_revision_letter(sku: str) -> str:
    """12A8J -> 12A8 (classic analog revision letter). Only applied as a fallback."""
    if len(sku) > 3 and sku[-1].isalpha() and sku[-2].isdigit():
        # protect explicit documented variants like BE15A8-H or *AC / *I suffixes
        return sku[:-1]
    return sku


def load_retrofit_map() -> dict:
    global _RETROFIT_PAYLOAD
    if _RETROFIT_PAYLOAD:
        return _RETROFIT_PAYLOAD

    path = BASE_DIR / "site_data" / "retrofit_map.json"
    if not path.exists():
        return {}

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"WARNING: Failed to load retrofit map: {exc}")
        return {}

    for row in payload.get("retrofits", []):
        sku = _normalize(row.get("classic_sku", ""))
        if sku:
            _RETROFIT_BY_SKU[sku] = row

    _RETROFIT_PAYLOAD = payload
    return _RETROFIT_PAYLOAD


def lookup_retrofit(sku: str) -> dict:
    """Return the retrofit record for a classic-analog SKU, trying progressively normalized forms."""
    load_retrofit_map()
    candidate = _normalize(sku)
    if not candidate:
        return {}

    for attempt in (
        candidate,
        _strip_option_suffix(candidate),
        _strip_revision_letter(_strip_option_suffix(candidate)),
    ):
        if attempt in _RETROFIT_BY_SKU:
            return _RETROFIT_BY_SKU[attempt]
    return {}


def is_retrofit_question(message: str, drive_sku: str | None = None) -> dict:
    """Decide whether a user message is a retrofit/replacement question for a classic drive.

    Returns the retrofit record when matched, otherwise an empty dict.
    """
    load_retrofit_map()
    text = (message or "").lower()
    if not any(word in text for word in RETROFIT_TRIGGER_WORDS):
        return {}

    if drive_sku:
        hit = lookup_retrofit(drive_sku)
        if hit:
            return hit

    for token in SKU_PATTERN.findall((message or "").upper()):
        hit = lookup_retrofit(token)
        if hit:
            return hit
    return {}


def format_retrofit_answer(record: dict) -> str:
    """Compose a deterministic, source-cited retrofit answer from a map record.

    When mode-specific alternates exist, both the default replacement AND each
    alternate are surfaced prominently so the user picks the right one for
    their mode of operation — not buried in a sub-bullet.
    """
    if not record:
        return ""

    classic = record.get("classic_sku", "")
    default_replacement = record.get("default_replacement", "")
    motor = record.get("motor_type", "")
    size = record.get("size", "")

    payload = load_retrofit_map()
    doc = (payload.get("retrofit_document") or {}).get(f"{size}_size", {})
    doc_title = doc.get("title", "AxCent Retrofit")
    doc_local = doc.get("local_pdf", "")

    alternates = record.get("alternate_modes") or {}

    lines: list[str] = []
    lines.append(
        f"The **{classic}** is a discontinued Classic Analog servo drive. "
        "The AxCent replacement depends on your mode of operation:"
    )
    lines.append("")
    lines.append("**Recommended replacement(s):**")
    lines.append(
        f"- **{default_replacement}** — for Current, Voltage, or Duty Cycle mode "
        "(the standard default mapping)."
    )
    for mode, replacement in alternates.items():
        # Each alternate replacement SKU is bolded at the same level as the default
        # so a user scanning the answer sees all options at a glance.
        if replacement and replacement.lower() == "contact amc":
            lines.append(f"- For **{mode}**: contact AMC — no direct AxCent replacement is listed.")
        else:
            lines.append(f"- **{replacement}** — if you use **{mode}** mode.")

    lines.append("")
    if motor:
        lines.append(f"- Motor type: {motor}")
    lines.append(
        f"- Retrofit guide: {doc_title}" + (f" ({doc_local})" if doc_local else "")
    )

    lines.append("")
    lines.append(
        f"Refer to the {doc_title} document for connector, wiring, and configuration differences "
        "between the classic analog drive and the AxCent replacement."
    )
    return "\n".join(lines)
