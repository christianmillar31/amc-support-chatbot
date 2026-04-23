"""Regression tests for the equivalent-drive lookup gate.

Locks in the fix for the hallucination class where the bot invented
AZBH12A12 and misquoted AZBH6A8's current when asked for alternatives
to AZBH25A20-10.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.drive_lookup import lookup_drive
from app.equivalent_drives import (
    detect_equivalent_query,
    find_equivalents,
    format_equivalent_answer,
    resolve_reference_drive,
)


# ---------- Intent detection ----------

def test_intent_if_discontinued():
    assert detect_equivalent_query(
        "if AZBH25A20-10 gets discontinued, will we have anything similar?"
    ) is True


def test_intent_equivalent_to():
    assert detect_equivalent_query("what's equivalent to AZBH25A20-10?") is True


def test_intent_similar_drive():
    assert detect_equivalent_query("any similar drive to AZBH25A20-10?") is True


def test_intent_drop_in_replacement():
    assert detect_equivalent_query(
        "I need a drop-in replacement for AZBH25A20-10"
    ) is True


def test_intent_not_fired_on_spec_question():
    assert detect_equivalent_query("what's the continuous current on AZBH10A4?") is False


def test_intent_not_fired_on_tuning_question():
    assert detect_equivalent_query("how do I tune the velocity loop?") is False


def test_intent_not_fired_on_basic_retrofit_question():
    # Retrofit gate runs first in support_core, but the intent regex
    # should NOT match generic "what replaces it" phrasing alone —
    # we only fire on explicit "similar / equivalent / alternative"
    # language. This test documents that boundary so we don't accidentally
    # widen intent too much and take over retrofit territory.
    assert detect_equivalent_query("my 12A8 is discontinued, what replaces it?") is False


# ---------- Candidate search ----------

def test_equivalents_for_azbh25a20_10_no_hallucination():
    """The original failure case — all returned SKUs must exist in CSV."""
    ref = lookup_drive("AZBH25A20-10")
    assert ref is not None, "AZBH25A20-10 must be in the DB"
    buckets = find_equivalents(ref, prefer_modes={"Hall Velocity"})
    all_skus = (
        [c["sku"] for c in buckets["same_voltage_similar_current"]]
        + [c["sku"] for c in buckets["same_current_different_voltage"]]
        + [c["sku"] for c in buckets["same_voltage_upsize"]]
    )
    # Every returned SKU must resolve — no hallucinations
    for sku in all_skus:
        assert lookup_drive(sku) is not None, f"Invented SKU returned: {sku}"
    # The hallucinated SKU from the failure report must NOT appear
    assert "AZBH12A12" not in all_skus


def test_equivalents_exclude_the_reference_itself():
    ref = lookup_drive("AZBH10A4")
    assert ref is not None
    buckets = find_equivalents(ref)
    all_skus = (
        [c["sku"] for c in buckets["same_voltage_similar_current"]]
        + [c["sku"] for c in buckets["same_current_different_voltage"]]
        + [c["sku"] for c in buckets["same_voltage_upsize"]]
    )
    assert "AZBH10A4" not in all_skus


def test_equivalents_exclude_discontinued():
    ref = lookup_drive("AZBH10A4")  # Active
    buckets = find_equivalents(ref)
    all_drives = (
        buckets["same_voltage_similar_current"]
        + buckets["same_current_different_voltage"]
        + buckets["same_voltage_upsize"]
    )
    for c in all_drives:
        assert "discontin" not in (c.get("status") or "").lower(), (
            f"Discontinued drive returned: {c['sku']} ({c['status']})"
        )


def test_mode_filter_respects_hall_velocity():
    """If prefer_modes={'Hall Velocity'}, every candidate must support it."""
    ref = lookup_drive("AZBH25A20-10")
    buckets = find_equivalents(ref, prefer_modes={"Hall Velocity"})
    all_drives = (
        buckets["same_voltage_similar_current"]
        + buckets["same_current_different_voltage"]
        + buckets["same_voltage_upsize"]
    )
    for c in all_drives:
        assert "Hall Velocity" in (c.get("operating_mode") or ""), (
            f"Non-Hall-Velocity drive returned when Hall requested: "
            f"{c['sku']} modes={c.get('operating_mode')}"
        )


# ---------- Reference-drive resolution ----------

def test_resolve_reference_from_message_sku():
    r = resolve_reference_drive("if AZBH10A4 gets discontinued, anything similar?")
    assert r is not None
    assert r["sku"] == "AZBH10A4"


def test_resolve_reference_from_drive_context():
    ctx = {"canonical_sku": "FE060-25-EM"}
    r = resolve_reference_drive("anything similar?", drive_context=ctx)
    assert r is not None
    assert r["sku"] == "FE060-25-EM"


def test_resolve_reference_returns_none_on_unknown_sku():
    r = resolve_reference_drive("what's similar to AZBH12A12?")  # hallucinated SKU
    assert r is None


# ---------- Formatted response ----------

def test_formatted_answer_contains_reference_sku_and_only_real_skus():
    ref = lookup_drive("AZBH25A20-10")
    buckets = find_equivalents(ref, prefer_modes={"Hall Velocity"})
    text = format_equivalent_answer(ref, buckets)
    assert "AZBH25A20-10" in text
    # No fabricated SKUs in the text. Check against everything starting with AZBH
    import re
    azbh_candidates = re.findall(r"AZB\w+", text)
    for sku in azbh_candidates:
        # Every AZB* token that looks like a real SKU (>5 chars) must resolve
        if len(sku) >= 6:
            assert lookup_drive(sku) is not None or sku == "AZBH25A20-10", (
                f"Formatted answer contains unresolvable SKU: {sku}"
            )


def test_formatted_answer_when_no_matches_states_so_honestly():
    """Edge case: find_equivalents might return empty for an exotic spec."""
    # Build a fake ref that's unlikely to have matches — e.g. a very-low-
    # current drive. AZBH10A4 is 5 A / 10 A at 10-36 V; require Hall
    # Velocity and absurd voltage requirements.
    ref = lookup_drive("AZBH10A4")
    # Force empty buckets
    empty = {
        "same_voltage_similar_current": [],
        "same_current_different_voltage": [],
        "same_voltage_upsize": [],
    }
    text = format_equivalent_answer(ref, empty)
    assert "No current-production" in text
