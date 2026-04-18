#!/usr/bin/env python3
"""
Compare local AMC PDFs, website product metadata, and the drive CSV.
"""
from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SITE_DATA_DIR = ROOT / "site_data"


def load_json(name: str) -> object:
    return json.loads((SITE_DATA_DIR / name).read_text(encoding="utf-8"))


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

    site_status_counts = Counter(
        (product.get("specifications", {}).get("Product Status", "") or "Unknown")
        for product in site_products.values()
    )
    site_family_counts = Counter(
        (product.get("specifications", {}).get("Family", "") or "Unknown")
        for product in site_products.values()
    )

    matched = sorted(set(local_datasheets) & set(site_products))
    local_only = sorted(set(local_datasheets) - set(site_products))
    site_only = sorted(set(site_products) - set(local_datasheets))

    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "local_datasheet_count": len(local_datasheets),
        "site_product_count": len(site_products),
        "matched_skus": {
            "count": len(matched),
            "examples": matched[:50],
        },
        "local_datasheets_without_site_product_page": {
            "count": len(local_only),
            "examples": local_only[:50],
        },
        "site_products_without_local_datasheet": {
            "count": len(site_only),
            "examples": site_only[:50],
        },
        "site_status_counts": dict(sorted(site_status_counts.items())),
        "site_family_counts": dict(sorted(site_family_counts.items())),
    }


def main() -> None:
    report = summarize()
    output_path = SITE_DATA_DIR / "inventory_coverage_report.json"
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Wrote {output_path}")
    print(
        f"Matched SKUs: {report['matched_skus']['count']}, "
        f"site-only: {report['site_products_without_local_datasheet']['count']}, "
        f"local-only: {report['local_datasheets_without_site_product_page']['count']}"
    )


if __name__ == "__main__":
    main()
