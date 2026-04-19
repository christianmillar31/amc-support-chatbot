"""
Coverage-aware runtime tests for the AMC support catalog integration.
Run with: python -m pytest eval/tests/test_support_catalog_runtime.py -v
Or directly: python eval/tests/test_support_catalog_runtime.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

# Make repo root importable when running directly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import app.chat as chat
from app.drive_lookup import get_all_drives, lookup_drive
from app.support_catalog import build_support_note, get_support_catalog_summary


def test_lookup_exact_covered_drive():
    result = lookup_drive("FE060-25-EM")
    assert result is not None
    assert result["canonical_sku"] == "FE060-25-EM"
    assert result["datasheet_sku"] == "FE060-25-EM"
    assert result["support_bucket"] == "core_drive_covered"
    assert result["site_status"] == "Active"


def test_lookup_exact_active_drive_missing_local_datasheet():
    result = lookup_drive("100A40")
    assert result is not None
    assert result["canonical_sku"] == "100A40"
    assert result["datasheet_sku"] == "100A40"
    assert result["support_bucket"] == "core_drive_missing"
    assert result["site_status"] == "Active"


def test_lookup_variant_routes_through_base_datasheet():
    result = lookup_drive("AZBH25A20-10")
    assert result is not None
    assert result["requested_sku"] == "AZBH25A20-10"
    assert result["canonical_sku"] == "AZBH25A20-10"
    assert result["normalized_sku"] == "AZBH25A20"
    assert result["datasheet_sku"] == "AZBH25A20"
    assert result["support_bucket"] == "core_drive_variant_match"
    assert result["site_status"] == "Reserved"


def test_lookup_invalid_part_number_refuses_cleanly():
    assert lookup_drive("FE999-99-EM") is None


def test_drive_selector_payload_is_coverage_aware():
    drives = get_all_drives()
    target = next(d for d in drives if d["sku"] == "FE060-25-EM")
    assert target["canonical_sku"] == "FE060-25-EM"
    assert target["datasheet_sku"] == "FE060-25-EM"
    assert target["title"]
    assert target["site_status"] == "Active"
    assert "support_bucket" in target
    assert "recommended_next_action" in target
    assert "site_url" in target


def test_support_catalog_summary_contains_bucket_counts():
    summary = get_support_catalog_summary()
    assert "summary" in summary
    buckets = summary["summary"]["support_bucket_counts"]
    assert buckets["core_drive_missing"] >= 3
    assert buckets["core_drive_variant_match"] >= 1


def test_support_note_for_missing_active_drive():
    note = build_support_note(lookup_drive("100A40"))
    assert "does not currently include its exact datasheet" in note
    assert "100A40" in note


def test_support_note_for_variant_drive():
    note = build_support_note(lookup_drive("AZBH25A20-10"))
    assert "AZBH25A20-10" in note
    assert "AZBH25A20" in note
    assert "base datasheet" in note


def test_smart_route_missing_drive_skips_absent_datasheet():
    drive = lookup_drive("100A40")
    calls: list[dict] = []

    def fake_retrieve(query: str, top_k: int, source_filter=None, doc_type_filter=None, expanded_query=None):
        calls.append({
            "query": query,
            "source_filter": source_filter,
            "doc_type_filter": doc_type_filter,
            "expanded_query": expanded_query,
        })
        source = source_filter or "AMC_AppNote_000.pdf"
        return [{
            "text": f"{source}-{len(calls)}",
            "source": source,
            "page": 1,
            "heading": "",
            "score": 0.4,
        }]

    with patch.object(chat, "_expand_query_cached", return_value="expanded"), patch.object(chat, "retrieve", side_effect=fake_retrieve), patch.object(chat, "get_indexed_sources", return_value={drive["hw_manual"]}):
        _, _, drive_info = chat._smart_route("Need specs for 100A40", drive_context=drive)

    assert "core_drive_missing" in drive_info
    assert not any(call["source_filter"] == "AMC_Datasheet_100A40.pdf" for call in calls)
    assert any(call["source_filter"] == drive["hw_manual"] for call in calls)
    assert any(call["doc_type_filter"] == "app_note" for call in calls)


def test_smart_route_variant_drive_uses_base_datasheet_context():
    drive = lookup_drive("AZBH25A20-10")
    datasheet_name = f"AMC_Datasheet_{drive['datasheet_sku']}.pdf"
    calls: list[dict] = []

    def fake_retrieve(query: str, top_k: int, source_filter=None, doc_type_filter=None, expanded_query=None):
        calls.append({
            "query": query,
            "source_filter": source_filter,
            "doc_type_filter": doc_type_filter,
            "expanded_query": expanded_query,
        })
        source = source_filter or "AMC_AppNote_000.pdf"
        return [{
            "text": f"{source}-{len(calls)}",
            "source": source,
            "page": 1,
            "heading": "",
            "score": 0.4,
        }]

    with patch.object(chat, "_expand_query_cached", return_value="expanded"), patch.object(chat, "retrieve", side_effect=fake_retrieve), patch.object(chat, "get_indexed_sources", return_value={datasheet_name, drive["hw_manual"]}):
        _, _, drive_info = chat._smart_route("Need dimensions for AZBH25A20-10", drive_context=drive)

    assert "core_drive_variant_match" in drive_info
    assert any(call["source_filter"] == datasheet_name for call in calls)
    datasheet_calls = [call for call in calls if call["source_filter"] == datasheet_name]
    assert datasheet_calls
    assert all("AZBH25A20-10" in call["query"] for call in datasheet_calls)
    assert all("AZBH25A20" in call["query"] for call in datasheet_calls)


if __name__ == "__main__":
    import traceback

    tests = [
        (name, obj) for name, obj in globals().items()
        if name.startswith("test_") and callable(obj)
    ]

    passed = 0
    failed = 0
    errors = []

    for name, func in tests:
        try:
            func()
            print(f"  PASS  {name}")
            passed += 1
        except AssertionError as exc:
            print(f"  FAIL  {name}: {exc}")
            failed += 1
            errors.append((name, str(exc)))
        except Exception as exc:
            print(f"  ERROR {name}: {type(exc).__name__}: {exc}")
            failed += 1
            errors.append((name, traceback.format_exc()))

    print()
    print(f"Results: {passed} passed, {failed} failed (of {passed + failed})")

    if failed:
        sys.exit(1)
