"""Unit tests for the spec-and-capability validator (Batch A item 1).

Run with: python -m pytest eval/tests/test_spec_validator.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.drive_lookup import lookup_drive
from app.spec_validator import (
    detect_impossible_combo,
    resolve_drive_from_message,
    try_spec_answer,
)


# ---------- resolve_drive_from_message ----------

def test_resolve_from_explicit_context_wins_over_message():
    # Context SKU resolves even if message also contains a different SKU token.
    ctx = {"canonical_sku": "FE060-25-EM"}
    hit = resolve_drive_from_message("tell me about AZBH10A4", drive_context=ctx)
    assert hit is not None
    assert hit["canonical_sku"] == "FE060-25-EM"


def test_resolve_from_message_token():
    hit = resolve_drive_from_message("what's the continuous current on the AZBH10A4?")
    assert hit is not None
    assert hit["canonical_sku"].upper().startswith("AZBH10A4")


def test_resolve_returns_none_when_no_sku():
    assert resolve_drive_from_message("how do I set up the drive?") is None


# ---------- detect_impossible_combo ----------

def test_powerlink_on_ethercat_flexpro_refuses():
    drive = lookup_drive("FE060-5-EM")
    assert drive is not None, "FE060-5-EM must exist in the drive DB"
    refusal = detect_impossible_combo(
        "How do I set up POWERLINK on the FE060-5-EM?",
        drive,
    )
    assert refusal is not None
    assert "POWERLINK" in refusal["answer"]
    assert "EtherCAT" in refusal["answer"]
    assert refusal["provider_used"] == "impossible_combo_refusal"


def test_ethernetip_on_axcent_refuses():
    drive = lookup_drive("AZBH10A4")
    assert drive is not None
    refusal = detect_impossible_combo(
        "Which Ethernet/IP address do I use with the AZBH10A4?",
        drive,
    )
    assert refusal is not None
    assert "Ethernet/IP" in refusal["answer"]
    assert "analog" in refusal["answer"].lower() or "pwm" in refusal["answer"].lower()


def test_canopen_on_classic_analog_refuses():
    drive = lookup_drive("100A40")
    if drive is None:
        # Coverage gap; skip cleanly rather than failing on missing data.
        import pytest
        pytest.skip("100A40 not present in CM Servo Info.csv")
    refusal = detect_impossible_combo(
        "What's the CANopen node ID for the 100A40?",
        drive,
    )
    assert refusal is not None
    assert "CANopen" in refusal["answer"]


def test_matching_protocol_does_not_refuse():
    drive = lookup_drive("FE060-5-EM")
    # User asks about EtherCAT on an EtherCAT drive — pass through.
    assert detect_impossible_combo("EtherCAT setup for FE060-5-EM", drive) is None


def test_no_protocol_mention_does_not_refuse():
    drive = lookup_drive("FE060-5-EM")
    assert detect_impossible_combo("what's the continuous current on FE060-5-EM?", drive) is None


def test_neutral_question_form_does_not_trigger_refusal():
    # "is X EtherCAT or CANopen?" is a questioning form — no use-intent. The
    # spec validator should let this fall through so try_spec_answer or the
    # single-shot path can answer directly.
    drive = lookup_drive("FE060-5-EM")
    refusal = detect_impossible_combo("Is FE060-5-EM CANopen?", drive)
    assert refusal is None


# ---------- try_spec_answer ----------

def test_continuous_current_answer_is_canonical():
    drive = lookup_drive("AZBH10A4")
    assert drive is not None
    result = try_spec_answer("what's the continuous current on the AZBH10A4?", drive)
    assert result is not None
    assert result["provider_used"] == "canonical_spec"
    assert "Continuous current" in result["answer"]
    # Canonical value must be present (format "N A").
    assert " A" in result["answer"]


def test_protocol_question_answers_canonically():
    drive = lookup_drive("FE060-5-EM")
    result = try_spec_answer("what protocol does the FE060-5-EM use?", drive)
    assert result is not None
    assert "EtherCAT" in result["answer"]


def test_no_spec_keyword_falls_through():
    drive = lookup_drive("AZBH10A4")
    # Not a spec-style question — validator should decline.
    assert try_spec_answer("tell me about the AZBH10A4", drive) is None


def test_unknown_sku_lookup_returns_none():
    # lookup_drive returns None for fabricated SKUs — try_spec_answer is never
    # called in that path in support_core, but the defensive caller contract
    # is still a clean None when inputs are unusable.
    assert lookup_drive("AB25A20-10") is None
