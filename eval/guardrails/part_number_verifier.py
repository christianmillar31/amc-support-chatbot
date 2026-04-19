"""
Verify that AMC part numbers mentioned in answers actually exist.

A SKU is a hallucination if:
1. It doesn't exist in any AMC product catalog (CM Servo Info.csv + site_data
   scrapes + retrofit map), AND
2. It doesn't exist as a model-code suffix inside any known full SKU (power
   module codes like `030A400` are shorthand for drives named `DPCANIA-030A400`
   etc.), AND
3. It doesn't exist in the retrieved context chunks passed to the LLM.

This is the single most important deterministic hallucination check.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import List, Set

from eval.guardrails.part_number_extractor import extract_part_numbers

BASE = Path(__file__).resolve().parent.parent.parent
CSV_PATH = BASE / "CM Servo Info.csv"
SITE_DATA = BASE / "site_data"


@lru_cache(maxsize=1)
def load_valid_skus() -> frozenset[str]:
    """Load the full AMC SKU catalog — the drive CSV plus every scraped
    product file plus the retrofit map. Cached.
    """
    skus: set[str] = set()

    # 1. Authoritative drive catalog
    if CSV_PATH.exists():
        with open(CSV_PATH, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                sku = (row.get("Sku") or "").strip().upper()
                if sku:
                    skus.add(sku)
                title = (row.get("Title") or "").strip().upper()
                if title and "(DISCONTINUED)" not in title and "(RESERVED)" not in title:
                    skus.add(title)

    # 2. Scraped site catalog — includes non-drive products (power supplies,
    # mounting cards, shunt regulators, etc.) that drive-CSV doesn't cover.
    for filename in ("amc_products.json", "amc_classic_products.json"):
        path = SITE_DATA / filename
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for p in data.get("products", []):
                s = (p.get("sku") or "").strip().upper()
                if s:
                    skus.add(s)
        except Exception:
            pass

    # 3. Retrofit map classic SKUs
    retrofit_path = SITE_DATA / "retrofit_map.json"
    if retrofit_path.exists():
        try:
            data = json.loads(retrofit_path.read_text(encoding="utf-8"))
            for r in data.get("retrofits", []):
                s = (r.get("classic_sku") or "").strip().upper()
                if s:
                    skus.add(s)
        except Exception:
            pass

    return frozenset(skus)


@lru_cache(maxsize=1)
def _suffix_catalog() -> frozenset[str]:
    """Set of all model-code suffixes that appear after a dash in real SKUs.

    For example, every full SKU like `DPCANIA-030A400` contributes `030A400`
    to this set. This lets the hallucination detector accept shorthand like
    "030A400 power module" as legitimate, since the code really is an AMC
    product identifier — just embedded in the longer drive SKU.
    """
    suffixes: set[str] = set()
    for sku in load_valid_skus():
        parts = sku.split("-")
        # Only count suffixes that look like model codes (letters AND digits,
        # at least 4 characters). Prevents single-digit / purely-numeric pieces
        # from becoming a permissive whitelist.
        for part in parts[1:]:
            if len(part) < 4:
                continue
            if not any(ch.isdigit() for ch in part) or not any(ch.isalpha() for ch in part):
                continue
            suffixes.add(part)
    return frozenset(suffixes)


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
    suffixes = _suffix_catalog()
    extra = {s.upper() for s in (allowed_extra_skus or set())}
    context_upper = retrieved_context.upper() if retrieved_context else ""

    candidates = extract_part_numbers(answer)
    results = []

    for sku in candidates:
        in_csv = sku in valid_skus
        in_context = sku in context_upper
        in_whitelist = sku in extra
        # Accept model-code suffixes that appear inside real SKUs
        # (e.g. "030A400" is shorthand for DPCANIA-030A400 et al.).
        in_suffix = sku in suffixes

        effectively_grounded = in_csv or in_context or in_whitelist or in_suffix

        severity = "warning" if effectively_grounded else "critical"

        results.append(HallucinatedSKU(
            sku=sku,
            in_csv=in_csv,
            in_context=in_context or in_whitelist or in_suffix,
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
