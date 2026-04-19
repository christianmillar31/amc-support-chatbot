from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from app.config import BASE_DIR
from app.support_catalog import resolve_datasheet_sku


DEFAULT_FALLBACK_URL = "https://www.a-m-c.com/downloads/"
BASE_URL = "https://www.a-m-c.com"


_PDF_URL_MAP: dict[str, str] = {}
_MAP_LOADED = False


def _load_map() -> dict[str, str]:
    global _MAP_LOADED, _PDF_URL_MAP
    if _MAP_LOADED:
        return _PDF_URL_MAP

    path = BASE_DIR / "site_data" / "pdf_url_map.json"
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            _PDF_URL_MAP = dict(payload.get("mappings") or {})
        except Exception as exc:  # pragma: no cover - defensive
            print(f"WARNING: Failed to load pdf_url_map.json: {exc}")
            _PDF_URL_MAP = {}
    _MAP_LOADED = True
    return _PDF_URL_MAP


def resolve_source_url(source: Mapping[str, object] | str | None) -> str:
    """Return a best-effort web URL for a citation source.

    Accepts either a source dict (as emitted by chat/support_core) or a bare
    filename string. Order of resolution:

    1. Exact match in pdf_url_map.json.
    2. Datasheet heuristic (AMC_Datasheet_<SKU>.pdf -> product page for the
       canonical base SKU).
    3. Global fallback (downloads page).
    """
    mapping = _load_map()

    if source is None:
        return DEFAULT_FALLBACK_URL

    if isinstance(source, str):
        filename = source
    else:
        filename = str(source.get("source") or "")

    if not filename:
        return DEFAULT_FALLBACK_URL

    url = mapping.get(filename)
    if url:
        return url

    # Ensure a .pdf suffix for the heuristic lookups below.
    stem = filename
    if not stem.endswith(".pdf"):
        url = mapping.get(stem + ".pdf")
        if url:
            return url
        stem = stem + ".pdf"

    # Datasheet heuristic: route to the canonical base SKU's product page.
    if filename.startswith("AMC_Datasheet_"):
        sku = filename.removeprefix("AMC_Datasheet_").removesuffix(".pdf")
        if sku:
            canonical = resolve_datasheet_sku(sku) or sku
            return f"{BASE_URL}/product/{canonical.lower()}/"

    return DEFAULT_FALLBACK_URL


def enrich_sources(sources: list[dict]) -> list[dict]:
    """Return a shallow copy of `sources` with a `url` field populated per item."""
    enriched: list[dict] = []
    for s in sources or []:
        if not isinstance(s, Mapping):
            continue
        copy = dict(s)
        if not copy.get("url"):
            copy["url"] = resolve_source_url(copy)
        enriched.append(copy)
    return enriched
