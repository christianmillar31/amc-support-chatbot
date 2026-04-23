from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable

from rapidfuzz import distance as rf_distance
from rapidfuzz import fuzz as rf_fuzz
from rapidfuzz import process as rf_process

from app.config import BASE_DIR
from app.drive_lookup import _DRIVE_DB, _load_csv
from app.retrofit_lookup import load_retrofit_map
from app.support_catalog import normalize_lookup_sku


# SKU-shaped tokens: allow letters, digits, and one or more dashes. Accepts forms
# like "FE060-25-EM", "DPRALTE-020B080", "AZBH10A4", "100A40", "B30A8I", "FE60-5-EM".
# Narrower than a generic identifier — requires at least two characters and at
# least one digit.
_SKU_TOKEN_RE = re.compile(r"[A-Z][A-Z0-9-]{2,30}", re.IGNORECASE)


# Stop words that occasionally look SKU-like but should never be treated as drive
# candidates (prevents noise from common English words in the user's question).
_STOP_WORDS = {
    "EM", "EMS", "EMF", "EMI", "CAN", "CANOPEN", "RS", "RS-485", "RS-232", "DC", "AC",
    "AMC", "AWG", "PWM", "CSV", "PDF", "PID", "GND", "VAC", "VDC", "KHZ", "HZ", "MHZ",
    "MAX", "MIN", "OK", "UL", "CE", "USA", "USB", "URL", "APP", "APPNOTE",
    "I", "IN", "OF", "TO", "FOR", "THE", "AND", "OR", "BUT", "WITH", "FROM", "ON",
    "GO", "NOT", "ALL", "ANY", "IS", "AT", "BE", "MY", "WE", "YOU", "CAN",
    "HELLO", "HI", "HEY", "HOW", "WHAT", "WHY", "WHEN", "WHERE",
}


_KNOWN_SKUS_CACHE: list[str] | None = None


def _known_skus() -> list[str]:
    """Return the deduplicated union of authoritative SKUs we can match against."""
    global _KNOWN_SKUS_CACHE
    if _KNOWN_SKUS_CACHE is not None:
        return _KNOWN_SKUS_CACHE

    _load_csv()
    skus: set[str] = set(_DRIVE_DB.keys())

    # Retrofit map adds classic analog discontinued SKUs (~38)
    payload = load_retrofit_map() or {}
    for row in payload.get("retrofits", []):
        sku = (row.get("classic_sku") or "").strip().upper()
        if sku:
            skus.add(sku)

    # site_data/amc_classic_products.json covers the same set but double-check
    classic_path = BASE_DIR / "site_data" / "amc_classic_products.json"
    if classic_path.exists():
        try:
            data = json.loads(classic_path.read_text(encoding="utf-8"))
            for p in data.get("products", []):
                sku = (p.get("sku") or "").strip().upper()
                if sku:
                    skus.add(sku)
        except Exception:
            pass

    _KNOWN_SKUS_CACHE = sorted(skus)
    return _KNOWN_SKUS_CACHE


def _candidate_tokens(message: str) -> list[str]:
    """Return SKU-shaped tokens in a message.

    Handles two forms:
    - Normal per-word tokens (e.g. `AZBH10-A4`, `FE060-25-EM`) pulled by regex.
    - Space-separated pairs where the combined form looks SKU-shaped (e.g.
      `DPRALTE 020B080` → `DPRALTE-020B080`).
    """
    text = (message or "").upper()

    def _viable(token: str) -> str | None:
        cleaned = token.strip("-")
        if len(cleaned) < 3:
            return None
        if cleaned in _STOP_WORDS:
            return None
        if not any(ch.isdigit() for ch in cleaned):
            return None
        if not any(ch.isalpha() for ch in cleaned):
            return None
        return cleaned

    # Word-by-word tokenization — never span whitespace except via the glue pass
    # below. This keeps "the AZBH10-A4 datasheet" from collapsing into one token.
    # Allow digit-starting tokens (e.g. classic analog SKUs like 12A8, 100A40);
    # _viable() still requires at least one letter AND one digit so pure numbers
    # like "123" won't pass.
    word_tokens = re.findall(r"[A-Z0-9][A-Z0-9-]{2,30}", text)

    candidates: list[str] = []
    for token in word_tokens:
        cleaned = _viable(token)
        if cleaned and cleaned not in candidates:
            candidates.append(cleaned)

    # Space-glue pass for adjacent word pairs that together look SKU-shaped
    # (e.g. "DPRALTE 020B080"). We can't use a single non-overlapping regex
    # pattern — regex's left-to-right consumption would pair up ("THE",
    # "DPRALTE") and leave "020B080" dangling. Split on whitespace and check
    # every adjacent pair instead.
    raw_words = re.split(r"\s+", text)
    word_re = re.compile(r"^[A-Z0-9][A-Z0-9-]{1,15}$")
    for left, right in zip(raw_words, raw_words[1:]):
        left_strip = left.strip(".,?!:;()[]{}\"'")
        right_strip = right.strip(".,?!:;()[]{}\"'")
        if not word_re.match(left_strip) or not word_re.match(right_strip):
            continue
        if left_strip in _STOP_WORDS or right_strip in _STOP_WORDS:
            continue
        if not (any(ch.isdigit() for ch in left_strip) or any(ch.isdigit() for ch in right_strip)):
            continue
        combined = f"{left_strip}-{right_strip}"
        cleaned = _viable(combined)
        if cleaned and cleaned not in candidates:
            candidates.append(cleaned)

    return candidates


candidate_sku_tokens = _candidate_tokens


def fuzzy_candidates(
    raw_sku: str,
    *,
    max_distance: int = 2,
    top_k: int = 3,
    score_cutoff: float = 82.0,
) -> list[dict]:
    """Return ranked close matches for a SKU that failed exact lookup.

    Each item: {sku, distance, score}. Higher score = better. rapidfuzz.fuzz.ratio
    is used as the primary similarity; Levenshtein edit distance is reported for
    downstream threshold logic.
    """
    skus = _known_skus()
    if not skus or not raw_sku:
        return []

    query = normalize_lookup_sku(raw_sku)
    if not query:
        return []

    # First, exact hit after normalization wins immediately.
    if query in skus:
        return [{"sku": query, "distance": 0, "score": 100.0}]

    # RapidFuzz process.extract picks best matches using fuzz.ratio similarity.
    hits = rf_process.extract(
        query,
        skus,
        scorer=rf_fuzz.ratio,
        limit=max(top_k * 3, 10),
        score_cutoff=score_cutoff,
    )

    out: list[dict] = []
    for sku, score, _idx in hits:
        dist = rf_distance.Levenshtein.distance(query, sku)
        if dist > max_distance:
            continue
        out.append({"sku": sku, "distance": int(dist), "score": float(score)})
        if len(out) >= top_k:
            break
    return out


def detect_typo_hits(message: str) -> list[dict]:
    """Inspect a user message for SKU-shaped tokens that miss the authoritative
    SKU list but are close to a known drive. Returns one record per candidate
    token; callers decide what to do with ambiguity or misses.

    Each record: {
        "raw": "FE60-5-EM",
        "normalized": "FE60-5-EM",
        "exact_match": False,
        "candidates": [{"sku", "distance", "score"}, ...],
    }
    """
    skus = set(_known_skus())
    records: list[dict] = []
    for raw in _candidate_tokens(message):
        normalized = normalize_lookup_sku(raw)
        if normalized in skus:
            records.append(
                {"raw": raw, "normalized": normalized, "exact_match": True, "candidates": []}
            )
            continue

        # Only classify as a potential typo if it actually looks like a drive SKU.
        # Drives always contain at least one letter AND at least one digit, and
        # are 4+ characters. We've already filtered above; also require length 4+.
        if len(normalized) < 4:
            continue

        candidates = fuzzy_candidates(normalized)
        if candidates:
            records.append(
                {
                    "raw": raw,
                    "normalized": normalized,
                    "exact_match": False,
                    "candidates": candidates,
                }
            )
    return records


# Known AMC family prefixes. Ordered longest-first so specific variants win
# when we test for "starts with".
_KNOWN_PREFIXES = (
    # AxCent PCB variants (keep these BEFORE AZ / AB to win the prefix test)
    "AZBDC", "AZBH", "AZBE", "AZBD",
    # AxCent Retrofit base prefixes
    "AVB",
    # FlexPro
    "FMP", "FXM", "FXE", "FXD", "FE", "FM", "FD", "FX",
    # DigiFlex Performance
    "DVC", "DPC", "DPE", "DPM", "DPP", "DPR", "DPS", "DPQ",
    "DZXC", "DZX", "DZC", "DZS", "DZE", "DZP", "DZM",
    "DP", "DZ", "DV", "DX",
    # AxCent base
    "AZB", "AZ",
    # AxCent Retrofit base
    "AB",
)


def _family_prefix(sku: str) -> str:
    """Return the longest known AMC family prefix that `sku` starts with, or ''."""
    s = (sku or "").upper()
    for p in _KNOWN_PREFIXES:
        if s.startswith(p):
            return p
    # Pure-numeric prefix = Classic analog (e.g. 100A40, 12A8)
    if s and s[0].isdigit():
        return "CLASSIC_NUMERIC"
    return ""


def interpret_typo_hits(message: str) -> dict:
    """High-level decision helper used by the runtime gate.

    Returns a dict describing what the caller should do next:

        {"action": "none"}                                # no typo-shaped tokens
        {"action": "exact_ok", "sku": "AZB60A8"}          # existing exact hit
        {"action": "correct", "raw": "FE60-5-EM", "corrected": "FE060-5-EM", "candidates": [...]}
        {"action": "ambiguous", "raw": "...", "candidates": [...]}
        {"action": "refuse", "raw": "..."}                 # no close match at all
    """
    records = detect_typo_hits(message)
    if not records:
        return {"action": "none"}

    # Prefer the first exact match if any.
    for r in records:
        if r["exact_match"]:
            return {"action": "exact_ok", "sku": r["normalized"]}

    # Take the first non-exact token that had candidates (most common case).
    for r in records:
        if not r["candidates"]:
            continue
        top = r["candidates"][0]
        ambiguous = (
            len(r["candidates"]) >= 2
            and (r["candidates"][1]["score"] >= top["score"] - 1.0)
            and (r["candidates"][1]["distance"] <= top["distance"])
        )
        # Safety: if the raw token already starts with a known AMC family prefix
        # AND the corrected SKU's prefix is a different known family, downgrade
        # to ambiguous so we ask the user instead of silently switching family
        # (e.g. AB25A20 -> AZB25A20 changes AxCent Retrofit -> AxCent PCB).
        raw_prefix = _family_prefix(r["normalized"])
        corr_prefix = _family_prefix(top["sku"])
        family_mismatch = (
            raw_prefix
            and corr_prefix
            and raw_prefix != corr_prefix
            and not corr_prefix.startswith(raw_prefix)
            and not raw_prefix.startswith(corr_prefix)
        )

        # Strong single-candidate correction: distance 1, score 92+
        if not ambiguous and not family_mismatch and top["distance"] <= 1 and top["score"] >= 92.0:
            return {
                "action": "correct",
                "raw": r["raw"],
                "corrected": top["sku"],
                "candidates": r["candidates"],
            }
        # Distance 2 is usually still a reasonable correction, but flag for user.
        if not ambiguous and not family_mismatch and top["distance"] <= 2 and top["score"] >= 85.0:
            return {
                "action": "correct",
                "raw": r["raw"],
                "corrected": top["sku"],
                "candidates": r["candidates"],
            }
        if family_mismatch:
            return {
                "action": "ambiguous",
                "raw": r["raw"],
                "candidates": r["candidates"],
            }
        return {
            "action": "ambiguous",
            "raw": r["raw"],
            "candidates": r["candidates"],
        }

    # A token looked SKU-shaped but had no close matches at all.
    raw = records[0]["raw"]
    return {"action": "refuse", "raw": raw}


def format_typo_refusal_answer(raw: str) -> str:
    return (
        f"I couldn't find a drive matching `{raw}` in the AMC product database. "
        "Please double-check the part number, or search https://www.a-m-c.com/products/servo-drives "
        "for the exact spelling."
    )


def format_typo_ambiguous_answer(raw: str, candidates: Iterable[dict]) -> str:
    candidates = list(candidates)
    lines = [
        f"I'm not sure which drive `{raw}` refers to. The closest matches I can find in the "
        "AMC product database are:",
        "",
    ]
    for c in candidates[:3]:
        sku = c.get("sku", "")
        lines.append(f"- **{sku}** — https://www.a-m-c.com/product/{sku.lower()}/")
    lines.append("")
    lines.append("Please reply with the exact part number so I can pull the right documentation.")
    return "\n".join(lines)


def format_typo_correction_status(raw: str, corrected: str) -> str:
    return (
        f"Interpreting `{raw}` as `{corrected}` based on the AMC product database. "
        "If that's wrong, reply with the exact part number and I'll re-run the search."
    )
