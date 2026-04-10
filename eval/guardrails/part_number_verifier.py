"""
Verify that AMC part numbers mentioned in answers actually exist.

A SKU is a hallucination if:
1. It doesn't exist in CM Servo Info.csv (644 drives), AND
2. It doesn't exist in the retrieved context chunks passed to the LLM

This is the single most important deterministic hallucination check.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import List, Set

from eval.guardrails.part_number_extractor import extract_part_numbers

BASE = Path(__file__).resolve().parent.parent.parent
CSV_PATH = BASE / "CM Servo Info.csv"


@lru_cache(maxsize=1)
def load_valid_skus() -> frozenset[str]:
    """Load the 644 canonical SKUs from CM Servo Info.csv (cached)."""
    if not CSV_PATH.exists():
        return frozenset()

    skus = set()
    with open(CSV_PATH, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sku = row.get("Sku", "").strip().upper()
            if sku:
                skus.add(sku)
            # Also add the title field — some rows use Title instead of Sku
            title = row.get("Title", "").strip().upper()
            if title and "(DISCONTINUED)" not in title and "(RESERVED)" not in title:
                skus.add(title)
    return frozenset(skus)


@dataclass
class HallucinatedSKU:
    sku: str
    in_csv: bool
    in_context: bool
    severity: str  # "critical" | "warning"

    @property
    def is_hallucination(self) -> bool:
        return not self.in_csv and not self.in_context


def verify_part_numbers(
    answer: str,
    retrieved_context: str = "",
    allowed_extra_skus: Set[str] | None = None,
) -> List[HallucinatedSKU]:
    """
    Check every part number mentioned in `answer`.

    A SKU passes if it's in the CSV OR in the retrieved context
    (or in any additional whitelist the caller provides).

    Returns a list of all SKUs analyzed with their status.
    Hallucinations are those where `is_hallucination == True`.
    """
    valid_skus = load_valid_skus()
    extra = {s.upper() for s in (allowed_extra_skus or set())}
    context_upper = retrieved_context.upper() if retrieved_context else ""

    candidates = extract_part_numbers(answer)
    results = []

    for sku in candidates:
        in_csv = sku in valid_skus
        in_context = sku in context_upper
        in_whitelist = sku in extra

        # Consider context SKUs valid even if we don't detect them exactly
        effectively_grounded = in_csv or in_context or in_whitelist

        severity = "warning" if effectively_grounded else "critical"

        results.append(HallucinatedSKU(
            sku=sku,
            in_csv=in_csv,
            in_context=in_context or in_whitelist,
            severity=severity,
        ))

    return results


def hallucination_count(answer: str, retrieved_context: str = "") -> int:
    """Quick scalar: how many hallucinated SKUs are in this answer?"""
    results = verify_part_numbers(answer, retrieved_context)
    return sum(1 for r in results if r.is_hallucination)


def hallucinated_skus(answer: str, retrieved_context: str = "") -> List[str]:
    """Quick list: which SKUs in this answer are hallucinations?"""
    results = verify_part_numbers(answer, retrieved_context)
    return [r.sku for r in results if r.is_hallucination]
