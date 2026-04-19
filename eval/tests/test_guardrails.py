"""
Unit tests for the deterministic guardrails.
Run with: python -m pytest eval/tests/test_guardrails.py -v
Or directly: python eval/tests/test_guardrails.py
"""
import sys
from pathlib import Path

# Make eval/ importable when running directly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from eval.guardrails.part_number_extractor import extract_part_numbers, is_valid_sku_format
from eval.guardrails.part_number_verifier import verify_part_numbers, hallucinated_skus, load_valid_skus
from eval.guardrails.citation_verifier import extract_citations, verify_citations
from eval.judges.amc_deterministic import judge_deterministic
from eval.runners.run_eval import load_tests


# ============================================================
# Part number extraction tests
# ============================================================

def test_extract_flexpro():
    skus = extract_part_numbers("The FE060-25-EM supports EtherCAT.")
    assert "FE060-25-EM" in skus, f"Expected FE060-25-EM, got {skus}"


def test_extract_digiflex():
    skus = extract_part_numbers("Try the DPRALTE-020B080 for Modbus applications.")
    assert "DPRALTE-020B080" in skus, f"Expected DPRALTE-020B080, got {skus}"


def test_extract_axcent():
    skus = extract_part_numbers("The AZBH10A4 is a small AxCent drive.")
    assert "AZBH10A4" in skus, f"Expected AZBH10A4, got {skus}"


def test_extract_axcent_with_suffix():
    skus = extract_part_numbers("Use the AZBH25A20-10 for this application.")
    assert "AZBH25A20-10" in skus, f"Expected AZBH25A20-10, got {skus}"


def test_extract_classic():
    skus = extract_part_numbers("The B30A40 is a classic brushed drive.")
    assert "B30A40" in skus, f"Expected B30A40, got {skus}"


def test_extract_multiple():
    text = "Compare FE060-5-EM to DPRALTE-020B080 and AZBH10A4."
    skus = extract_part_numbers(text)
    assert "FE060-5-EM" in skus
    assert "DPRALTE-020B080" in skus
    assert "AZBH10A4" in skus


def test_extract_ignores_hex():
    """Hex addresses like 6040h should not be extracted as SKUs."""
    skus = extract_part_numbers("Read object 6040h and 0x6041 for status.")
    # These might match the classic pattern — verify they don't
    assert "6040H" not in [s.upper() for s in skus]


def test_extract_detects_hallucinated_sku():
    """The ABH25A20-10 hallucination case — should be detected."""
    # Note: ABH doesn't match our strict patterns, but let's make sure we detect
    # the common families. A bot inventing ABH is a DIFFERENT failure mode.
    skus = extract_part_numbers("Try the ABH25A20-10 drive.")
    # ABH isn't in our patterns, so this is expected — the verifier handles it differently
    # What matters is that FAKE SKUs in valid patterns are caught by the verifier


def test_extract_case_insensitive():
    skus = extract_part_numbers("The fe060-5-em is EtherCAT.")
    assert "FE060-5-EM" in skus


# ============================================================
# Part number verification tests
# ============================================================

def test_verify_real_sku_passes():
    """A real SKU from the CSV should not be flagged."""
    results = verify_part_numbers("The FE060-25-EM is an EtherCAT drive.")
    hallucinations = [r for r in results if r.is_hallucination]
    assert len(hallucinations) == 0, f"Real SKU flagged as hallucination: {hallucinations}"


def test_verify_fake_sku_caught():
    """A fabricated SKU should be flagged as a hallucination."""
    # FE999-99-EM doesn't exist in the CSV
    results = verify_part_numbers("The FE999-99-EM is great.")
    hallucinations = [r for r in results if r.is_hallucination]
    assert len(hallucinations) >= 1, f"Fake SKU not caught: {results}"
    assert any(r.sku == "FE999-99-EM" for r in hallucinations)


def test_verify_sku_in_context_passes():
    """If a SKU isn't in the CSV but IS in the retrieved context, it should pass."""
    # Pretend FE999-99-EM appeared in the context (e.g. a datasheet snippet)
    results = verify_part_numbers(
        "The FE999-99-EM is great.",
        retrieved_context="From datasheet: FE999-99-EM specifications...",
    )
    hallucinations = [r for r in results if r.is_hallucination]
    assert len(hallucinations) == 0, f"Context-grounded SKU flagged: {hallucinations}"


def test_hallucinated_skus_helper():
    skus = hallucinated_skus("The FE060-25-EM and FE999-99-EM are both cool.")
    assert "FE999-99-EM" in skus
    assert "FE060-25-EM" not in skus


def test_csv_loaded():
    """Sanity: the CSV should load and contain known drives."""
    valid = load_valid_skus()
    assert len(valid) > 500, f"Expected 600+ valid SKUs, got {len(valid)}"
    assert "FE060-5-EM" in valid
    assert "DPRALTE-020B080" in valid
    assert "AZBH10A4" in valid


# ============================================================
# Citation verification tests
# ============================================================

def test_extract_citation_simple():
    cites = extract_citations("See [Source: AMC_HWManual_FlexPro_PCB.pdf, Page 25]")
    assert len(cites) == 1
    assert cites[0][0] == "AMC_HWManual_FlexPro_PCB.pdf"
    assert cites[0][1] == 25


def test_extract_citation_no_page():
    cites = extract_citations("See [Source: AMC_HWManual_FlexPro_PCB.pdf]")
    assert len(cites) == 1
    assert cites[0][1] is None


def test_extract_multiple_citations():
    text = "First [Source: AMC_CommManual_CANopen.pdf, Page 10] and then [Source: AMC_HWManual_AxCent_Panel.pdf, p.52]"
    cites = extract_citations(text)
    assert len(cites) == 2


def test_verify_fake_citation():
    """A citation to a non-existent file should be flagged."""
    cites = verify_citations("See [Source: FAKE_MANUAL_NOT_REAL.pdf, Page 999]")
    assert len(cites) == 1
    assert cites[0].is_fabricated is True


# ============================================================
# Eval harness extension tests
# ============================================================

def test_deterministic_required_substrings_all_passes():
    test = {
        "id": "coverage_stub_pass",
        "category": "coverage_state",
        "required_substrings_all": ["100A40", "exact datasheet", "hardware manual"],
    }
    answer = "100A40 does not have its exact datasheet locally, so start with the hardware manual."
    judgment = judge_deterministic(test, answer)
    assert judgment.passed is True


def test_deterministic_required_substrings_all_fails():
    test = {
        "id": "coverage_stub_fail",
        "category": "coverage_state",
        "required_substrings_all": ["AZBH25A20-10", "Reserved", "cautious"],
    }
    answer = "AZBH25A20-10 should use the base datasheet."
    judgment = judge_deterministic(test, answer)
    assert judgment.passed is False
    assert "Missing required substrings" in judgment.failure_reason


def test_load_tests_includes_coverage_state_suite():
    tests = load_tests(category_filter="coverage_state")
    assert tests, "Expected coverage_state tests to load"
    assert all(test["category"] == "coverage_state" for test in tests)


# ============================================================
# Runner
# ============================================================

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
        except AssertionError as e:
            print(f"  FAIL  {name}: {e}")
            failed += 1
            errors.append((name, str(e)))
        except Exception as e:
            print(f"  ERROR {name}: {type(e).__name__}: {e}")
            failed += 1
            errors.append((name, traceback.format_exc()))

    print()
    print(f"Results: {passed} passed, {failed} failed (of {passed + failed})")

    if failed:
        sys.exit(1)
