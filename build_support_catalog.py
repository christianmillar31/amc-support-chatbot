#!/usr/bin/env python3
"""
Build a unified AMC support catalog from live site metadata, local PDFs, and
the drive CSV.

Outputs `site_data/support_catalog.json` with per-product support coverage and
recommended next action so the repo can move from ad-hoc analysis to a reusable
catalog artifact.
"""
from __future__ import annotations

import csv
import json
import time
from collections import Counter
from pathlib import Path

from analyze_inventory_coverage import classify_site_category, normalize_family, normalize_sku


ROOT = Path(__file__).resolve().parent
SITE_DATA_DIR = ROOT / "site_data"
DRIVE_CSV = ROOT / "CM Servo Info.csv"


def load_json(name: str) -> object:
    return json.loads((SITE_DATA_DIR / name).read_text(encoding="utf-8"))


def load_drive_index() -> dict[str, dict[str, str]]:
    with DRIVE_CSV.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        return {
            normalize_sku((row.get("Sku") or "").strip()): row
            for row in reader
            if (row.get("Sku") or "").strip()
        }


def build_local_datasheet_indexes(manifest: dict) -> tuple[dict[str, dict], dict[str, list[dict]]]:
    exact: dict[str, dict] = {}
    normalized: dict[str, list[dict]] = {}
    for row in manifest["pdfs"]:
        if row.get("doc_type") != "datasheet" or not row.get("sku"):
            continue
        exact[row["sku"]] = row
        normalized.setdefault(row["normalized_sku"], []).append(row)
    return exact, {key: sorted(value, key=lambda item: item["sku"]) for key, value in normalized.items()}


def classify_support_bucket(
    *,
    category: str,
    has_local_exact: bool,
    has_local_normalized: bool,
    site_status: str,
) -> str:
    if category == "Servo Drives":
        if has_local_exact:
            return "core_drive_covered"
        if has_local_normalized:
            return "core_drive_variant_match"
        if "Reserved" in site_status:
            return "core_drive_reserved_gap"
        return "core_drive_missing"

    if category in {"Controls", "Power Supplies", "Mounting Cards", "I/O Boards", "Connector Kits", "Filter Cards", "Shunt Regulators", "Tools"}:
        return "adjacent_product_scope_decision"

    return "unclassified_scope_decision"


def recommend_next_action(bucket: str) -> str:
    if bucket == "core_drive_covered":
        return "use_local_datasheet_and_site_metadata"
    if bucket == "core_drive_variant_match":
        return "add_sku_alias_mapping"
    if bucket == "core_drive_reserved_gap":
        return "capture_reserved_drive_metadata_and_deprioritize_pdf_ingest"
    if bucket == "core_drive_missing":
        return "prioritize_missing_drive_ingest"
    if bucket == "adjacent_product_scope_decision":
        return "decide_whether_to_expand_support_scope"
    return "review_manually"


def main() -> None:
    site_payload = load_json("amc_products.json")
    local_manifest = load_json("local_pdf_manifest.json")
    drive_index = load_drive_index()
    local_exact, local_normalized = build_local_datasheet_indexes(local_manifest)

    products: list[dict] = []
    bucket_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    recommendation_counts: Counter[str] = Counter()

    for product in site_payload["products"]:
        sku = product.get("sku", "")
        if not sku:
            continue

        normalized_sku = normalize_sku(sku)
        category = classify_site_category(product)
        specs = product.get("specifications", {})
        site_status = specs.get("Product Status", "") or "Unknown"
        site_family = normalize_family(specs.get("Family", "") or "")
        local_exact_row = local_exact.get(sku)
        local_normalized_rows = local_normalized.get(normalized_sku, [])
        drive_row = drive_index.get(normalized_sku)

        bucket = classify_support_bucket(
            category=category,
            has_local_exact=local_exact_row is not None,
            has_local_normalized=bool(local_normalized_rows),
            site_status=site_status,
        )
        next_action = recommend_next_action(bucket)

        products.append(
            {
                "sku": sku,
                "normalized_sku": normalized_sku,
                "title": product.get("title", ""),
                "url": product.get("url", ""),
                "category": category,
                "site_status": site_status,
                "site_family": site_family,
                "site_network_communication": specs.get("Network Communication", "") or "",
                "local_datasheet_exact": local_exact_row["filename"] if local_exact_row else "",
                "local_datasheet_matches": [row["filename"] for row in local_normalized_rows],
                "drive_csv_match": bool(drive_row),
                "drive_csv_family": (drive_row or {}).get("Family", ""),
                "drive_csv_status": (drive_row or {}).get("Status", ""),
                "support_bucket": bucket,
                "recommended_next_action": next_action,
            }
        )
        bucket_counts[bucket] += 1
        category_counts[category] += 1
        recommendation_counts[next_action] += 1

    output = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "site_product_count": len(products),
        "summary": {
            "support_bucket_counts": dict(sorted(bucket_counts.items())),
            "category_counts": dict(sorted(category_counts.items())),
            "recommended_next_action_counts": dict(sorted(recommendation_counts.items())),
        },
        "priority_examples": {
            "core_drive_missing": [row["sku"] for row in products if row["support_bucket"] == "core_drive_missing"][:50],
            "core_drive_variant_match": [row["sku"] for row in products if row["support_bucket"] == "core_drive_variant_match"][:25],
            "adjacent_product_scope_decision": [row["sku"] for row in products if row["support_bucket"] == "adjacent_product_scope_decision"][:80],
        },
        "products": products,
    }

    output_path = SITE_DATA_DIR / "support_catalog.json"
    output_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"Wrote {output_path}")
    print(json.dumps(output["summary"], indent=2))


if __name__ == "__main__":
    main()
