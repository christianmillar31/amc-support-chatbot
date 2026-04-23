"""Unit tests for the ambiguity refusal gate (Batch A item 2).

Run with: python -m pytest eval/tests/test_ambiguity_gate.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.ambiguity_gate import is_ambiguous_question


# ---------- adversarial cases that MUST fire ----------

def test_adv_ambig_01_how_do_i_set_up_the_drive():
    # "How do I set up the drive?" — vague referent "the drive"
    assert is_ambiguous_question("How do I set up the drive?", has_sku=False, has_drive_context=False) is True


def test_adv_ambig_02_whats_the_current_limit():
    assert is_ambiguous_question("What's the current limit?", has_sku=False, has_drive_context=False) is True


def test_adv_ambig_03_its_not_working_help():
    assert is_ambiguous_question("It's not working. Help.", has_sku=False, has_drive_context=False) is True


def test_adv_ambig_04_which_manual_do_i_need():
    assert is_ambiguous_question("Which manual do I need?", has_sku=False, has_drive_context=False) is True


def test_adv_ambig_05_tune_it():
    assert is_ambiguous_question("Tune it.", has_sku=False, has_drive_context=False) is True


# ---------- legitimate FAQ-style questions that MUST NOT fire ----------

def test_faq_homing_in_driveware_not_refused():
    # Red-team false-positive: "how do I set up homing in DriveWare?" must
    # not be caught by the gate (DriveWare is a tool keyword → bailout).
    assert is_ambiguous_question(
        "How do I set up homing in DriveWare?",
        has_sku=False,
        has_drive_context=False,
    ) is False


def test_faq_ethercat_on_flexpro_not_refused():
    # "How do I set up EtherCAT communication on a FlexPro drive?" — has both
    # a family keyword (FlexPro) and a protocol keyword (EtherCAT), so it
    # bails out of the ambiguity gate even though it includes the phrase
    # "the drive" (wait — it says "a FlexPro drive", not "the drive"). Either
    # way the family bailout saves it.
    assert is_ambiguous_question(
        "How do I set up EtherCAT communication on a FlexPro drive?",
        has_sku=False,
        has_drive_context=False,
    ) is False


def test_canopen_question_not_refused():
    # Protocol keyword present — bails out even without a SKU.
    assert is_ambiguous_question(
        "How is CANopen node id configured?",
        has_sku=False,
        has_drive_context=False,
    ) is False


def test_sku_present_bypasses_gate():
    # has_sku=True short-circuits regardless of phrasing.
    assert is_ambiguous_question(
        "How do I set up the drive?",
        has_sku=True,
        has_drive_context=False,
    ) is False


def test_drive_context_bypasses_gate():
    assert is_ambiguous_question(
        "Tune it.",
        has_sku=False,
        has_drive_context=True,
    ) is False


# ---------- edge cases ----------

def test_empty_message_is_not_flagged():
    assert is_ambiguous_question("", has_sku=False, has_drive_context=False) is False


def test_whitespace_only_message_is_not_flagged():
    assert is_ambiguous_question("   \t\n", has_sku=False, has_drive_context=False) is False


def test_long_generic_message_not_flagged_without_vague_phrase():
    # "What's the best way to diagnose a motor that stalls intermittently?"
    # is vague-ish but doesn't match any exact vague-referent phrase, and
    # it's > 5 words so the short-imperative branch doesn't fire either.
    assert is_ambiguous_question(
        "What's the best way to diagnose a motor that stalls intermittently?",
        has_sku=False,
        has_drive_context=False,
    ) is False


def test_short_help_message_flagged():
    # "help" alone is a 1-word imperative verb → fire.
    assert is_ambiguous_question("help", has_sku=False, has_drive_context=False) is True


def test_three_word_imperative_flagged():
    assert is_ambiguous_question("please help me", has_sku=False, has_drive_context=False) is True


def test_motor_tuning_question_family_not_flagged():
    # "How do I tune a FlexPro drive?" — family keyword bailout.
    assert is_ambiguous_question(
        "How do I tune a FlexPro drive?",
        has_sku=False,
        has_drive_context=False,
    ) is False


def test_analog_family_keyword_bailout():
    # "What's the current limit for analog drives?" — family keyword "analog"
    # should bail out even though it contains the "current limit" phrase.
    assert is_ambiguous_question(
        "What's the current limit for analog drives?",
        has_sku=False,
        has_drive_context=False,
    ) is False
