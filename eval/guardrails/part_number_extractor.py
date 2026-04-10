"""
Extract candidate AMC part numbers (SKUs) from free-form text.

Covers all drive families:
- FlexPro:   FE/FM/FD/FMP/FX/FXM/FXE + digits + optional suffix
- DigiFlex:  DP/DV/DZ/DX + digits
- AxCent:    AZ + digits
- Classic:   bare numeric models like 25A20, 30A8, B15A8, BE25A20, BDC12A8
- DVC:       DVCNET-*, DVC200A100

False-positive guards:
- Skip 4-digit hex addresses like 6040h, 0x6040
- Skip register addresses like 2020h.01h
- Skip page references like p.25
"""
import re
from typing import List, Set


# Main pattern: families with letter prefix
_PREFIX_PATTERNS = [
    # FlexPro — strict: needs dash-separated sections
    r"\b(?:FE|FM|FD|FMP|FX|FXM|FXE)\d{3}-\d+-[A-Z]+\b",
    # DigiFlex Panel/PCB/Vehicle — needs dash
    r"\b(?:DP|DV|DZ|DX)[A-Z]{0,6}-?\d+[A-Z]\d+\b",
    r"\b(?:DP|DV|DZ|DX)[A-Z]{2,6}-\d+[A-Z]\d+(?:-\d+)?\b",
    # DVC controllers
    r"\bDVCNET-\d+[A-Z]\d+(?:-\d+)?\b",
    r"\bDVC\d+[A-Z]\d+\b",
    # AxCent (AZ) — includes variants like AZBH10A4, AZBH25A20-10, AZBDC60A8
    r"\bAZ[A-Z]{0,4}\d+[A-Z]\d+(?:-\d+)?\b",
    # Classic bare current-voltage: B30A40, BE25A20, BDC12A8, 30A8, 12A8
    r"\b(?:B|BE|BDC)?\d{2,3}A\d{1,3}(?:AC)?\b",
]

_COMBINED = re.compile("|".join(_PREFIX_PATTERNS), re.IGNORECASE)

# Exclusion patterns — these are NOT SKUs even if they match above
_EXCLUSIONS = [
    re.compile(r"^\d{1,2}A\d$", re.IGNORECASE),            # Too short like "5A2"
    re.compile(r"[0-9a-f]{4}h", re.IGNORECASE),            # hex addresses like 6040h
    re.compile(r"0x[0-9a-f]+", re.IGNORECASE),             # hex literals 0x6040
    re.compile(r"^p\.?\d+$", re.IGNORECASE),               # page refs p.25
    re.compile(r"^page\s*\d+$", re.IGNORECASE),            # "page 25"
]

# Common false positives (English words that match the classic pattern)
_WORD_BLACKLIST = {
    "B2B", "B2C", "C2C", "3D", "4D", "2D",
}


def extract_part_numbers(text: str) -> List[str]:
    """
    Extract all candidate AMC part numbers from text.
    Returns a deduplicated, uppercased list preserving order of first appearance.
    """
    if not text:
        return []

    matches = _COMBINED.findall(text)
    seen = set()
    result = []

    for match in matches:
        pn = match.upper().strip()

        # Skip exclusions
        if any(ex.fullmatch(pn) or ex.match(pn) for ex in _EXCLUSIONS):
            continue
        if pn in _WORD_BLACKLIST:
            continue

        # Deduplicate
        if pn not in seen:
            seen.add(pn)
            result.append(pn)

    return result


def is_valid_sku_format(pn: str) -> bool:
    """Check whether a string matches a known AMC SKU pattern (without DB lookup)."""
    return bool(_COMBINED.fullmatch(pn.strip().upper()))
