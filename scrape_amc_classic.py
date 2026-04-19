#!/usr/bin/env python3
"""
Scrape classic/discontinued AMC product pages that are NOT in the sitemap.

Seeds from `site_data/retrofit_map.json` so every classic analog SKU in the
retrofit map is fetched and captured. Writes `site_data/amc_classic_products.json`.

Run:
    python scrape_amc_classic.py --sleep 0.25

This is additive. It does not overwrite `amc_products.json`.
"""
from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict
from pathlib import Path

from scrape_amc_site import make_session, parse_product_page


BASE_URL = "https://www.a-m-c.com"
ROOT = Path(__file__).resolve().parent
SITE_DATA = ROOT / "site_data"


def load_classic_skus() -> list[str]:
    path = SITE_DATA / "retrofit_map.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [row["classic_sku"] for row in payload.get("retrofits", []) if row.get("classic_sku")]


def product_url_for(sku: str) -> str:
    return f"{BASE_URL}/product/{sku.lower()}/"


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape classic/discontinued AMC product pages.")
    parser.add_argument("--sleep", type=float, default=0.25, help="Delay between requests.")
    args = parser.parse_args()

    session = make_session()
    skus = load_classic_skus()

    records: list[dict] = []
    errors: list[dict] = []
    for sku in skus:
        url = product_url_for(sku)
        try:
            record = parse_product_page(session, url)
            records.append(asdict(record))
            print(f"[classic] {record.sku} <- {url}")
        except Exception as exc:
            errors.append({"sku": sku, "url": url, "error": str(exc)})
            print(f"[classic ERROR] {sku} <- {url}: {exc}")
        time.sleep(args.sleep)

    out = SITE_DATA / "amc_classic_products.json"
    out.write_text(
        json.dumps(
            {
                "source": BASE_URL,
                "scraped_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "count": len(records),
                "errors": errors,
                "products": records,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"\nWrote {len(records)} classic products to {out} ({len(errors)} errors)")


if __name__ == "__main__":
    main()
