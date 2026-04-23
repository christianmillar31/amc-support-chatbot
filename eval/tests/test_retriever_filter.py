"""Regression tests for retriever doc_type_filter normalization and
chat._classify_query_type — both locked in after the resolver-question bug
hunt showed the agent path emits comma-separated / list doc_types and the
classifier was substring-matching "rma" inside "tRANSFoRMAtion".
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.retriever import _normalize_doc_type_filter


# ---------- _normalize_doc_type_filter ----------

def test_none_returns_none():
    assert _normalize_doc_type_filter(None) is None


def test_empty_string_returns_none():
    assert _normalize_doc_type_filter("") is None
    assert _normalize_doc_type_filter("   ") is None


def test_single_value_string():
    assert _normalize_doc_type_filter("datasheet") == {"datasheet"}


def test_comma_separated_string():
    assert _normalize_doc_type_filter("datasheet,hw,app_note") == {"datasheet", "hw", "app_note"}


def test_comma_separated_with_spaces():
    assert _normalize_doc_type_filter("datasheet, hw , app_note") == {"datasheet", "hw", "app_note"}


def test_list_input():
    assert _normalize_doc_type_filter(["hw", "comm"]) == {"hw", "comm"}


def test_set_input():
    assert _normalize_doc_type_filter({"hw", "comm"}) == {"hw", "comm"}


def test_tuple_input():
    assert _normalize_doc_type_filter(("hw", "comm")) == {"hw", "comm"}


def test_list_with_empties_filtered():
    assert _normalize_doc_type_filter(["hw", "", "  ", "comm"]) == {"hw", "comm"}


# ---------- _classify_query_type (regression: rma false-trigger on "transformation") ----------

def test_resolver_transformation_does_not_route_to_rma():
    """
    Regression: bare 'rma' substring matched 'tRANSFoRMAtion' and mis-routed
    the resolver question to the RMA bucket, which returned only 4 unrelated
    chunks and caused the agent to hallucinate a 'RESRAT' parameter.
    """
    from app.chat import _classify_query_type
    msg = "I am working with a DigiFlex resolver drive with a transformation ratio of .28"
    assert _classify_query_type(msg) != "rma"


def test_real_rma_question_still_routes():
    from app.chat import _classify_query_type
    assert _classify_query_type("How do I file an RMA?") == "rma"
    assert _classify_query_type("I need an RMA number for my drive.") == "rma"


def test_beyond_repair_still_routes_to_rma():
    from app.chat import _classify_query_type
    assert _classify_query_type("My drive is beyond repair, how do I return it?") == "rma"


def test_compliance_still_routes():
    from app.chat import _classify_query_type
    assert _classify_query_type("Is the FE060-25-EM UL-certified?") == "compliance"


def test_spec_still_routes_to_datasheet():
    from app.chat import _classify_query_type
    assert _classify_query_type("What is the peak current on 12A8?") == "datasheet"


def test_marketing_still_routes():
    from app.chat import _classify_query_type
    assert _classify_query_type("Give me the industry flyer for medical applications") == "marketing"
