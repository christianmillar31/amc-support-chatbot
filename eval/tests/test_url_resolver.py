"""Unit tests for app.url_resolver — the PDF filename → AMC web URL map."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from app.url_resolver import (  # noqa: E402
    DEFAULT_FALLBACK_URL,
    enrich_sources,
    resolve_source_url,
)


def test_known_hw_manual_uses_curated_url():
    url = resolve_source_url("AMC_HWManual_FlexPro_PCB.pdf")
    assert url.startswith("https://www.a-m-c.com/d/?h=")


def test_datasheet_heuristic_routes_to_product_page():
    # AMC_Datasheet_FE060-25-EM.pdf -> /product/fe060-25-em/
    url = resolve_source_url("AMC_Datasheet_FE060-25-EM.pdf")
    assert url == "https://www.a-m-c.com/product/fe060-25-em/"


def test_datasheet_variant_suffix_routes_to_canonical_base():
    # AMC_Datasheet_AZBH25A20-10.pdf should route to the base /product/azbh25a20/
    url = resolve_source_url("AMC_Datasheet_AZBH25A20-10.pdf")
    assert url == "https://www.a-m-c.com/product/azbh25a20/"


def test_retrofit_map_small_size():
    url = resolve_source_url("AMC_ProductNote_AxCent_Retrofit_Small.pdf")
    assert url == "https://www.a-m-c.com/d/?h=ab0a26b"


def test_retrofit_map_large_size():
    url = resolve_source_url("AMC_ProductNote_AxCent_Retrofit_Large.pdf")
    assert url == "https://www.a-m-c.com/d/?h=1023aaa"


def test_unknown_pdf_falls_back_to_downloads_page():
    url = resolve_source_url("AMC_Compliance_CE.pdf")
    assert url == DEFAULT_FALLBACK_URL


def test_resolve_accepts_source_dict():
    url = resolve_source_url({"source": "AMC_Datasheet_100A40.pdf", "page": 1})
    assert url == "https://www.a-m-c.com/product/100a40/"


def test_resolve_none_returns_fallback():
    assert resolve_source_url(None) == DEFAULT_FALLBACK_URL
    assert resolve_source_url("") == DEFAULT_FALLBACK_URL


def test_enrich_sources_populates_url():
    sources = [
        {"source": "AMC_HWManual_FlexPro_PCB.pdf", "page": 12, "heading": "Wiring"},
        {"source": "AMC_Datasheet_AZB60A8.pdf", "page": 1, "heading": ""},
    ]
    out = enrich_sources(sources)
    assert len(out) == 2
    assert out[0]["url"].startswith("https://www.a-m-c.com/d/?h=")
    assert out[1]["url"] == "https://www.a-m-c.com/product/azb60a8/"


def test_enrich_preserves_existing_url():
    # Retrofit short-circuit pre-populates url — don't overwrite.
    sources = [{"source": "X.pdf", "page": 0, "heading": "", "url": "https://example.com/explicit"}]
    out = enrich_sources(sources)
    assert out[0]["url"] == "https://example.com/explicit"


def test_pdf_url_map_coverage_at_least_95_percent():
    """pdf_url_map.json should cover at least 95% of the local PDF corpus."""
    map_path = ROOT / "site_data" / "pdf_url_map.json"
    assert map_path.exists(), "site_data/pdf_url_map.json should be generated"
    data = json.loads(map_path.read_text(encoding="utf-8"))
    mapping = data.get("mappings") or {}
    local_pdfs = sorted(p.name for p in ROOT.glob("*.pdf"))
    if not local_pdfs:
        pytest.skip("No local PDFs available in repo — skipping coverage check")
    mapped = [p for p in local_pdfs if p in mapping]
    coverage = len(mapped) / len(local_pdfs)
    assert coverage >= 0.95, f"PDF URL map coverage is {coverage:.1%}, need >= 95%"
