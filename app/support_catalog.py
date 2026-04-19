from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Mapping

from app.config import BASE_DIR


SUPPORT_BUCKET_DESCRIPTIONS = {
    "core_drive_covered": "Covered drive with local datasheet/manual support.",
    "core_drive_variant_match": "Requested SKU should route through a canonical/base local datasheet.",
    "core_drive_missing": "Active drive with no exact local datasheet coverage.",
    "core_drive_reserved_gap": "Reserved drive that should be handled cautiously.",
    "adjacent_product_scope_decision": "Non-drive or adjacent product awaiting explicit support policy.",
}

RECOMMENDED_ACTION_DESCRIPTIONS = {
    "use_local_datasheet_and_site_metadata": "Use the local datasheet/manual corpus and site metadata normally.",
    "add_sku_alias_mapping": "Preserve the requested SKU but route retrieval through a canonical/base datasheet.",
    "prioritize_missing_drive_ingest": "Treat as an active high-priority coverage gap for ingestion.",
    "metadata_first_reserved_support": "Answer cautiously from metadata/manual context without assuming full active coverage.",
    "decide_category_scope_before_ingest": "Do not expand this category until routing, evals, and UX are defined.",
}

_SUPPORT_CATALOG_BY_SKU: dict[str, dict] = {}
_SUPPORT_CATALOG_PAYLOAD: dict = {}


def normalize_lookup_sku(raw: str) -> str:
    """Normalize a small set of known SKU variant forms for lookup only."""
    sku = raw.strip().upper()
    sku = sku.replace("–", "-").replace("—", "-")
    sku = re.sub(r"\s+", "", sku)
    sku = re.sub(r"-{2,}", "-", sku)
    if sku.endswith("-10"):
        sku = sku[:-3]
    return sku


def load_support_catalog() -> dict:
    """Load the generated support catalog once and cache it."""
    global _SUPPORT_CATALOG_PAYLOAD
    if _SUPPORT_CATALOG_PAYLOAD:
        return _SUPPORT_CATALOG_PAYLOAD

    catalog_path = BASE_DIR / "site_data" / "support_catalog.json"
    if not catalog_path.exists():
        return {}

    try:
        payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"WARNING: Failed to load support catalog: {exc}")
        return {}

    for row in payload.get("products", []):
        sku = (row.get("sku") or "").strip().upper()
        if sku:
            _SUPPORT_CATALOG_BY_SKU[sku] = row

    _SUPPORT_CATALOG_PAYLOAD = payload
    return _SUPPORT_CATALOG_PAYLOAD


def get_support_catalog_row(sku: str | None, allow_normalized: bool = True) -> dict:
    """Return the support catalog row for an SKU when available."""
    load_support_catalog()
    normalized = str(sku or "").strip().upper()
    if not normalized:
        return {}

    row = _SUPPORT_CATALOG_BY_SKU.get(normalized)
    if row:
        return row

    if allow_normalized:
        alias = normalize_lookup_sku(normalized)
        if alias != normalized:
            return _SUPPORT_CATALOG_BY_SKU.get(alias, {})

    return {}


def get_support_catalog_summary() -> dict:
    """Expose a small derived summary for internal reporting/UI use."""
    payload = load_support_catalog()
    if not payload:
        return {}

    return {
        "generated_at": payload.get("generated_at"),
        "site_product_count": payload.get("site_product_count"),
        "priority_examples": payload.get("priority_examples", {}),
        "summary": payload.get("summary", {}),
    }


def _extract_datasheet_sku(filename: str) -> str:
    stem = Path(filename).stem
    return stem.removeprefix("AMC_Datasheet_")


def resolve_datasheet_sku(sku: str) -> str:
    """Choose the best local datasheet SKU for a looked-up drive."""
    requested = str(sku or "").strip().upper()
    if not requested:
        return requested

    exact_name = BASE_DIR / f"AMC_Datasheet_{requested}.pdf"
    if exact_name.exists():
        return requested

    support_row = get_support_catalog_row(requested)
    local_exact = str(support_row.get("local_datasheet_exact") or "").strip()
    if local_exact:
        return _extract_datasheet_sku(local_exact)

    local_matches = support_row.get("local_datasheet_matches") or []
    for match in local_matches:
        candidate = _extract_datasheet_sku(match)
        if (BASE_DIR / f"AMC_Datasheet_{candidate}.pdf").exists():
            return candidate

    normalized = normalize_lookup_sku(requested)
    normalized_name = BASE_DIR / f"AMC_Datasheet_{normalized}.pdf"
    if normalized != requested and normalized_name.exists():
        return normalized

    return requested


def build_support_note(product: Mapping[str, object]) -> str:
    """Return a short user-facing coverage/routing note for a product."""
    requested_sku = str(product.get("requested_sku") or product.get("sku") or "").strip()
    datasheet_sku = str(product.get("datasheet_sku") or product.get("canonical_sku") or requested_sku).strip()
    support_bucket = str(product.get("support_bucket") or "").strip()
    site_status = str(product.get("site_status") or "").strip()
    site_category = str(product.get("site_category") or product.get("category") or "").strip()

    if support_bucket == "core_drive_missing":
        return (
            f"{requested_sku} is an active AMC product, but this local support corpus does not "
            "currently include its exact datasheet. Answer from the hardware manual, communication manual, "
            "application notes, and product metadata without implying that the local datasheet exists."
        )

    if support_bucket == "core_drive_reserved_gap":
        return (
            f"{requested_sku} is marked Reserved on the AMC product site. Provide cautious support "
            "guidance and avoid implying full current-product coverage."
        )

    if support_bucket == "core_drive_variant_match":
        return (
            f"{requested_sku} routes to the local datasheet for {datasheet_sku}. "
            "Keep the requested SKU visible while using the base datasheet for retrieval."
        )

    if support_bucket == "adjacent_product_scope_decision":
        product_label = site_category.lower() if site_category else "product"
        return (
            f"{requested_sku} is currently classified as adjacent AMC {product_label} scope. "
            "Answer from product metadata carefully and avoid drive-specific assumptions unless this "
            "category has explicit runtime support."
        )

    if site_status == "Reserved":
        return (
            f"{requested_sku} is marked Reserved on the AMC product site. Keep answers concise and "
            "careful about current-product assumptions."
        )

    return ""
