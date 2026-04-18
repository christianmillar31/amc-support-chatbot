#!/usr/bin/env python3
"""
Compare local AMC PDFs, website product metadata, and the drive CSV.
"""
from __future__ import annotations

import json
import re
import time
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SITE_DATA_DIR = ROOT / "site_data"


def load_json(name: str) -> object:
    return json.loads((SITE_DATA_DIR / name).read_text(encoding="utf-8"))


def normalize_sku(value: str) -> str:
    token = value.strip().upper()
    token = token.replace("–", "-").replace("—", "-")
    token = re.sub(r"\s+", "", token)
    token = re.sub(r"-{2,}", "-", token)
    if token.endswith("-10"):
        token = token[:-3]
    return token


def normalize_family(value: str) -> str:
    return value.replace(" (Reserved)", "").strip()


def classify_site_category(product: dict) -> str:
    breadcrumb = product.get("breadcrumb") or []
    if len(breadcrumb) >= 2:
        candidate = str(breadcrumb[1]).strip()
        if len(candidate) > 1:
            return candidate
    family = normalize_family(product.get("specifications", {}).get("Family", "") or "")
    if family:
        return "Servo Drives"
    return "Unknown"


def build_norm_index(records: dict[str, dict]) -> dict[str, list[str]]:
    norm_index: dict[str, list[str]] = {}
    for sku in records:
        normalized = normalize_sku(sku)
        if not normalized:
            continue
        norm_index.setdefault(normalized, []).append(sku)
    return {key: sorted(values) for key, values in norm_index.items()}


def summarize() -> dict:
    local_manifest = load_json("local_pdf_manifest.json")
    site_products_payload = load_json("amc_products.json")

    local_datasheets = {
        row["sku"]: row
        for row in local_manifest["pdfs"]
        if row["doc_type"] == "datasheet" and row["sku"]
    }
    site_products = {
        row["sku"]: row
        for row in site_products_payload["products"]
        if row.get("sku")
    }
    site_categories = {
        sku: classify_site_category(product)
        for sku, product in site_products.items()
    }

    site_status_counts = Counter(
        (product.get("specifications", {}).get("Product Status", "") or "Unknown")
        for product in site_products.values()
    )
    site_family_counts = Counter(
        (normalize_family(product.get("specifications", {}).get("Family", "") or "") or "Unknown")
        for product in site_products.values()
    )
    site_category_counts = Counter(site_categories.values())

    matched = sorted(set(local_datasheets) & set(site_products))
    local_only = sorted(set(local_datasheets) - set(site_products))
    site_only = sorted(set(site_products) - set(local_datasheets))
    local_by_norm = build_norm_index(local_datasheets)
    site_by_norm = build_norm_index(site_products)
    normalized_matched = sorted(set(local_by_norm) & set(site_by_norm))
    normalized_local_only = sorted(set(local_by_norm) - set(site_by_norm))
    normalized_site_only = sorted(set(site_by_norm) - set(local_by_norm))

    variant_matches = []
    for normalized in normalized_matched:
        local_skus = local_by_norm[normalized]
        site_skus = site_by_norm[normalized]
        if set(local_skus) == set(site_skus) and len(local_skus) == 1:
            continue
        variant_matches.append(
            {
                "normalized_sku": normalized,
                "local_skus": local_skus,
                "site_skus": site_skus,
            }
        )

    site_only_products = [site_products[sku] for sku in site_only]
    site_only_status = Counter(
        (product.get("specifications", {}).get("Product Status", "") or "Unknown")
        for product in site_only_products
    )
    site_only_category = Counter(site_categories[sku] for sku in site_only)
    site_only_family = Counter(
        (normalize_family(product.get("specifications", {}).get("Family", "") or "") or "Unknown")
        for product in site_only_products
    )
    site_only_reserved_servo = [
        product["sku"]
        for product in site_only_products
        if site_categories[product["sku"]] == "Servo Drives"
        and "Reserved" in (product.get("specifications", {}).get("Product Status", "") or "")
    ]
    site_only_active_servo = [
        product["sku"]
        for product in site_only_products
        if site_categories[product["sku"]] == "Servo Drives"
        and (product.get("specifications", {}).get("Product Status", "") or "") == "Active"
    ]
    site_only_non_drive = [
        product["sku"]
        for product in site_only_products
        if site_categories[product["sku"]] != "Servo Drives"
    ]

    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "local_datasheet_count": len(local_datasheets),
        "site_product_count": len(site_products),
        "exact_sku_matches": {
            "count": len(matched),
            "examples": matched[:50],
        },
        "normalized_sku_matches": {
            "count": len(normalized_matched),
            "examples": normalized_matched[:50],
        },
        "variant_matches_after_normalization": {
            "count": len(variant_matches),
            "examples": variant_matches[:25],
        },
        "local_datasheets_without_site_product_page": {
            "count": len(local_only),
            "examples": local_only[:50],
        },
        "site_products_without_local_datasheet": {
            "count": len(site_only),
            "examples": site_only[:50],
        },
        "normalized_local_only": {
            "count": len(normalized_local_only),
            "examples": normalized_local_only[:50],
        },
        "normalized_site_only": {
            "count": len(normalized_site_only),
            "examples": normalized_site_only[:50],
        },
        "site_status_counts": dict(sorted(site_status_counts.items())),
        "site_family_counts": dict(sorted(site_family_counts.items())),
        "site_category_counts": dict(sorted(site_category_counts.items())),
        "site_only_breakdown": {
            "status_counts": dict(sorted(site_only_status.items())),
            "family_counts": dict(sorted(site_only_family.items())),
            "category_counts": dict(sorted(site_only_category.items())),
            "reserved_servo_drive_examples": site_only_reserved_servo[:50],
            "active_servo_drive_examples": site_only_active_servo[:50],
            "non_drive_examples": site_only_non_drive[:80],
        },
    }


def main() -> None:
    report = summarize()
    output_path = SITE_DATA_DIR / "inventory_coverage_report.json"
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Wrote {output_path}")
    print(
        f"Exact matches: {report['exact_sku_matches']['count']}, "
        f"normalized matches: {report['normalized_sku_matches']['count']}, "
        f"site-only: {report['site_products_without_local_datasheet']['count']}, "
        f"local-only: {report['local_datasheets_without_site_product_page']['count']}"
    )


if __name__ == "__main__":
    main()
