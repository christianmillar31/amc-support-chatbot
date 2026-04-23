"""Unit tests for the safety-first gate.

When a customer reports that something has exploded, caught fire, smoked,
sparked, or injured someone, the bot must lead with safety guidance and
a human-contact path (AMC support phone + business hours) — NOT with
"which drive is this?" and NOT with a troubleshooting walkthrough.

Run with: python -m pytest eval/tests/test_safety_gate.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.safety_gate import SAFETY_FIRST_RESPONSE, is_safety_critical


# ---------- hazard phrases that MUST trigger ----------

def test_exploded_triggers():
    assert is_safety_critical("My drive exploded") is True


def test_something_exploded_triggers():
    assert is_safety_critical("something exploded") is True


def test_caught_fire_triggers():
    assert is_safety_critical("the drive caught fire when I powered it on") is True


def test_on_fire_triggers():
    assert is_safety_critical("It's on fire") is True


def test_smoke_triggers():
    assert is_safety_critical("I see smoke coming out of the drive") is True


def test_smoking_triggers():
    assert is_safety_critical("the amplifier is smoking") is True


def test_burning_smell_triggers():
    assert is_safety_critical("there's a burning smell near the cabinet") is True


def test_smells_like_burn_triggers():
    assert is_safety_critical("smells like burnt electronics") is True


def test_sparked_triggers():
    assert is_safety_critical("the drive sparked when I applied power") is True


def test_arc_flash_triggers():
    assert is_safety_critical("there was an arc flash at the bus terminals") is True


def test_shocked_triggers():
    assert is_safety_critical("I got shocked touching the heatsink") is True


def test_capacitor_blew_triggers():
    assert is_safety_critical("a capacitor blew on the DC bus") is True


def test_blew_up_triggers():
    assert is_safety_critical("the board blew up") is True


def test_melted_triggers():
    assert is_safety_critical("the connector melted") is True


def test_fried_drive_triggers():
    assert is_safety_critical("I fried the drive by reversing polarity") is True


def test_capitalization_insensitive():
    assert is_safety_critical("MY DRIVE EXPLODED") is True
    assert is_safety_critical("My Drive Exploded") is True


# ---------- non-hazard phrases that MUST NOT trigger ----------

def test_normal_spec_question_does_not_trigger():
    assert is_safety_critical("what is the continuous current on AZBH10A4?") is False


def test_tuning_question_does_not_trigger():
    assert is_safety_critical("how do I tune the velocity loop?") is False


def test_retrofit_question_does_not_trigger():
    assert is_safety_critical("what's the replacement for a 12A8?") is False


def test_wiring_question_does_not_trigger():
    assert is_safety_critical("how do I wire the encoder to FE060-25-EM?") is False


def test_firewall_substring_does_not_trigger():
    # "firewall" contains "fire" but shouldn't match — we anchor on \bfire\b in
    # the "on fire" / "caught fire" patterns, and "firewall" isn't one of those.
    assert is_safety_critical("can I put the drive behind a firewall?") is False


def test_smoked_glass_substring_does_not_trigger():
    # "smoke" and "smoked" match — this test documents that we accept a
    # potential false positive on "smoked glass" style phrases rather than
    # risk a false negative on real "smoke coming from drive" reports.
    # A false positive just adds safety steps to an irrelevant question;
    # a false negative means we miss a real incident. Trade the right way.
    assert is_safety_critical("can I use smoked glass in the enclosure?") is True


def test_empty_message_does_not_trigger():
    assert is_safety_critical("") is False
    assert is_safety_critical(None) is False  # type: ignore[arg-type]


# ---------- response content ----------

def test_response_contains_power_off_instruction():
    assert "Remove power" in SAFETY_FIRST_RESPONSE or "remove power" in SAFETY_FIRST_RESPONSE.lower()


def test_response_warns_about_capacitor_bus_voltage():
    # Critical safety point: stored DC-bus energy can be lethal even
    # after the supply is off.
    text = SAFETY_FIRST_RESPONSE.lower()
    assert "capacitor" in text
    assert "bleed" in text or "5" in text  # 5-minute bleed-down


def test_response_contains_amc_phone_number():
    # Phone number is authoritative: 805-389-1935 (per site_data/amc_glossary.json)
    assert "805-389-1935" in SAFETY_FIRST_RESPONSE


def test_response_contains_business_hours_pst():
    # User explicitly asked for M-F 8am-5pm PST to appear
    text = SAFETY_FIRST_RESPONSE
    assert "Monday" in text and "Friday" in text
    assert "8:00 AM" in text
    assert "5:00 PM" in text
    assert "PST" in text


def test_response_asks_for_drive_sku_at_end():
    # The SKU ask comes AFTER safety steps, not as the opener
    text = SAFETY_FIRST_RESPONSE
    safety_idx = text.lower().find("remove power")
    sku_idx = text.lower().find("part number")
    assert safety_idx < sku_idx, "Safety guidance must come before asking for the drive SKU"


def test_response_does_not_tell_user_to_reapply_power():
    # Re-applying power to a failed drive can cause worse failure.
    # The response must explicitly warn against it.
    assert "NOT" in SAFETY_FIRST_RESPONSE
    assert "re-apply" in SAFETY_FIRST_RESPONSE.lower() or "reapply" in SAFETY_FIRST_RESPONSE.lower()
