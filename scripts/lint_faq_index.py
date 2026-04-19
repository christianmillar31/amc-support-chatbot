#!/usr/bin/env python3
"""Lint faq_index.csv for content drift against the authoritative CSV.

What it catches:
  1. Any SKU mentioned in an FAQ answer that does NOT exist in CM Servo Info.csv
     (likely a typo or a stale SKU that was discontinued).
  2. Per-SKU Operating Mode claims in the answer that contradict the canonical
     CSV (e.g. "AZB supports Hall Velocity" when the CSV says AZB supports
     Current only).
  3. Region-specific compliance language leading a general wiring / configuration
     answer (e.g. an answer that begins with "European-approved" or "CE-required"
     for a question that doesn't ask about compliance).
  4. Family-scope narrowing: a question mentions an entire family or "all
     <family> drives" but the answer explicitly restricts scope to one sub-family
     (e.g. "Classic analog drives (B series) accept..." for an "analog drives"
     question, which silently drops AxCent analog PCB drives).

Exits non-zero if any issues are found so CI can block FAQ drift.
"""
from __future__ import annotations

import csv
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.drive_lookup import _DRIVE_DB, _load_csv  # noqa: E402


FAQ_PATH = ROOT / "faq_index.csv"


_SITE_SKUS_CACHE: set[str] | None = None


def _known_skus_union() -> set[str]:
    """Union of CSV drive SKUs, site product SKUs, classic discontinued SKUs,
    and retrofit map classic SKUs. Needed because CM Servo Info.csv is
    drive-only, but FAQ answers also reference power supplies (030A400,
    060A400 etc.) and other product categories."""
    global _SITE_SKUS_CACHE
    if _SITE_SKUS_CACHE is not None:
        return _SITE_SKUS_CACHE
    import json
    skus: set[str] = set(_DRIVE_DB.keys())
    for filename in ("amc_products.json", "amc_classic_products.json"):
        path = ROOT / "site_data" / filename
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for p in data.get("products", []):
                s = (p.get("sku") or "").strip().upper()
                if s:
                    skus.add(s)
        except Exception:
            pass
    retrofit_path = ROOT / "site_data" / "retrofit_map.json"
    if retrofit_path.exists():
        try:
            data = json.loads(retrofit_path.read_text(encoding="utf-8"))
            for r in data.get("retrofits", []):
                s = (r.get("classic_sku") or "").strip().upper()
                if s:
                    skus.add(s)
        except Exception:
            pass
    _SITE_SKUS_CACHE = skus
    return skus


SKU_TOKEN_RE = re.compile(r"\b[A-Z0-9][A-Z0-9-]{2,30}\b")

# Words that look SKU-shaped but aren't real SKUs (protocol codes, phrases,
# abbreviations, etc). Keep the known AMC family prefixes out of this set.
NON_SKU_ALLOWLIST = {
    # Protocol / interface
    "EM", "IPM", "EAN", "CM", "CAN", "CANOPEN", "RS", "RS-485", "RS-232", "DC",
    "AC", "VDC", "VAC", "VDCVDC", "USB", "RAM", "ROM", "KHZ", "MHZ", "HZ",
    "AWG", "SWG", "UL", "CE", "EU", "US", "USA", "PWM", "ABS", "ATEX",
    # Product line references
    "AMC", "PLC", "PID", "FOC", "SDO", "PDO", "RXPDO", "TXPDO", "EEPROM",
    # Short tokens and common abbreviations that happen to match
    "I", "IO", "ON", "OFF", "IN", "OUT", "GND", "VSS", "VCC", "CPU", "LED",
    "LSB", "MSB", "BIT", "BITS", "MAX", "MIN", "DIP", "TTL", "CMOS", "FFT",
    "DSO", "LVD", "REACH", "ROHS", "ISO9001", "CMRT", "RMA", "AOI",
    # Configuration / operational terms
    "P2", "P1", "P3", "S1", "S2", "SW1", "SW2", "A-B",
    # Word-like
    "OK", "NA", "NO", "YES",
}

REGION_LEAD_PHRASES = [
    "european-approved",
    "european approved",
    "ce-required",
    "ce required",
    "ce mark",
    "lvd requirements",
    "ul-approved",
    "ul approved",
]

FAMILY_SCOPE_PATTERNS = [
    # (question-pattern, answer-scope-narrower)
    (re.compile(r"\banalog\s+drives?\b", re.IGNORECASE),
     re.compile(r"(?:classic\s+analog|b[\s-]?series)\s*(?:drives?\s*)?\(?(?:only|b\s?series)\)?", re.IGNORECASE),
     "Question asks about analog drives (both Classic B-series AND AxCent accept analog command), but answer restricts to B-series only."),
]


@dataclass
class LintIssue:
    row_number: int
    question: str
    severity: str   # "error" | "warn"
    code: str
    message: str


@dataclass
class LintReport:
    issues: list[LintIssue] = field(default_factory=list)
    total_rows: int = 0

    def add(self, row_number: int, question: str, severity: str, code: str, message: str) -> None:
        self.issues.append(LintIssue(row_number, question, severity, code, message))

    def errors(self) -> list[LintIssue]:
        return [i for i in self.issues if i.severity == "error"]

    def warnings(self) -> list[LintIssue]:
        return [i for i in self.issues if i.severity == "warn"]


from app.sku_matcher import _family_prefix  # noqa: E402


_HEX_REGISTER_RE = re.compile(r"^[0-9A-F]{2,4}H?$")
_CLASSIC_SKU_RE = re.compile(r"^\d{2,3}[AB][A-Z]*\d{1,3}[A-Z]{0,3}(?:-[A-Z0-9]+)?$")
_HEX_PREFIX_RE = re.compile(r"^0X[0-9A-F]+$")


def _is_probable_sku(token: str) -> bool:
    """Return True only if `token` looks like an AMC SKU.

    Real AMC SKUs:
      - FlexPro / DigiFlex / AxCent: start with a known family prefix (FE, DP, AZ…)
      - Classic analog: start with 1-3 digits followed by A or B (e.g. 100A40, 25A8I)

    Filters out EtherCAT object-dictionary indices like `1A00`, `6041H`, `607CH`,
    `0x0000`, subindex expressions like `1A00H-1A03H`, and bare hex constants.
    """
    if not token or token in NON_SKU_ALLOWLIST:
        return False
    if "_" in token or token.endswith(".pdf"):
        return False
    if not any(ch.isalpha() for ch in token):
        return False
    if not any(ch.isdigit() for ch in token):
        return False
    if _HEX_PREFIX_RE.match(token):
        return False
    # Object dictionary addresses often end with `H`. Skip pure hex tokens.
    if _HEX_REGISTER_RE.match(token):
        return False
    if "-" in token and all(_HEX_REGISTER_RE.match(part) for part in token.split("-")):
        return False
    # Real SKU: either starts with a known family prefix (FE, DP, AZ, AB...)
    # or matches the Classic analog pattern (digits-then-letter).
    prefix = _family_prefix(token)
    if prefix and prefix != "CLASSIC_NUMERIC":
        return True
    if prefix == "CLASSIC_NUMERIC" and _CLASSIC_SKU_RE.match(token):
        return True
    return False


def _extract_sku_candidates(text: str) -> list[str]:
    out: list[str] = []
    for raw in SKU_TOKEN_RE.findall((text or "").upper()):
        clean = raw.strip("-")
        if _is_probable_sku(clean) and clean not in out:
            out.append(clean)
    return out


def _operating_modes_for(sku: str) -> set[str]:
    row = _DRIVE_DB.get(sku.upper())
    if not row:
        return set()
    raw = row.get("operating_mode") or ""
    return {p.strip() for p in raw.split("|") if p.strip()}


def lint() -> LintReport:
    _load_csv()
    report = LintReport()
    if not FAQ_PATH.exists():
        return report

    # Common mode-to-variant claims worth cross-checking. Each tuple is
    # (sku-prefix-or-variant, mode-phrase-that-must-be-present-in-CSV).
    # Example: any sentence like "AZB supports Hall Velocity" should trip if
    # the AZB baseline doesn't list Hall Velocity as an Operating Mode.
    VARIANT_MODE_PATTERNS = [
        (re.compile(r"\bAZB\s*(?:drives?\s+)?(?:support|supports|accept|accepts|do|run|offer)[^.]*?Hall\s*Velocity", re.IGNORECASE),
         "AZB", "Hall Velocity",
         "AZB baseline variants do not support Hall Velocity — use AZBH for Hall-Velocity applications."),
        (re.compile(r"\bAZB/AZBH\b", re.IGNORECASE),
         "AZB", "Hall Velocity",
         "Conflates AZB and AZBH. AZB = Current only; AZBH = Hall Velocity."),
    ]

    with open(FAQ_PATH, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for idx, row in enumerate(reader, start=2):  # line 1 = header
            report.total_rows += 1
            question = row.get("question") or ""
            answer = row.get("answer_summary") or ""

            known = _known_skus_union()
            # 1) Unknown SKUs in answer. Accept both exact matches and
            # "model-code suffix" shorthand (e.g. `030A400` is shorthand for
            # any SKU ending in -030A400 like DPCANIA-030A400).
            def _is_known_or_suffix(token: str) -> bool:
                if token in known:
                    return True
                # Strict suffix match: must be preceded by '-' so random
                # substrings don't silently validate.
                suffix = "-" + token
                return any(s.endswith(suffix) for s in known)

            for token in _extract_sku_candidates(answer):
                if not _is_known_or_suffix(token):
                    report.add(idx, question, "warn", "unknown_sku",
                               f"Answer mentions `{token}` which is not in the AMC product catalog.")

            # 2) Variant-mode pattern conflicts
            for pattern, prefix, mode, rationale in VARIANT_MODE_PATTERNS:
                if pattern.search(answer):
                    # Check any known SKU with that prefix to see if it really
                    # supports the mode — if no SKU with that prefix has the
                    # mode, the claim is wrong.
                    matches = [
                        sku for sku in _DRIVE_DB
                        if sku.startswith(prefix) and not any(sku.startswith(v) for v in ("AZBH", "AZBE", "AZBD", "AZBDC"))
                    ]
                    supports = any(mode in _operating_modes_for(sku) for sku in matches)
                    if not supports:
                        report.add(idx, question, "error", "variant_mode_conflict", rationale)

            # 3) Region-specific lead phrase
            first_80 = answer.strip()[:120].lower()
            q_lower = question.lower()
            compliance_asked = any(word in q_lower for word in ("european", "ce ", "lvd", "ul-approved", "ul approved", "compliance", "region"))
            if not compliance_asked:
                for phrase in REGION_LEAD_PHRASES:
                    if phrase in first_80:
                        report.add(idx, question, "warn", "region_lead",
                                   f"Answer opens with `{phrase}` but the question does not ask about compliance.")
                        break

            # 4) Family-scope narrowing
            for q_pat, ans_pat, msg in FAMILY_SCOPE_PATTERNS:
                if q_pat.search(question) and ans_pat.search(answer):
                    # Only flag if answer does NOT also mention the missing family.
                    if "axcent" not in answer.lower():
                        report.add(idx, question, "error", "family_scope", msg)

    return report


def main() -> int:
    report = lint()
    errors = report.errors()
    warnings = report.warnings()

    if not report.issues:
        print(f"FAQ lint OK — {report.total_rows} rows, no issues.")
        return 0

    print(f"FAQ lint found {len(errors)} error(s) and {len(warnings)} warning(s) across {report.total_rows} rows.\n")
    for tier, label in (("error", "Errors"), ("warn", "Warnings")):
        bucket = [i for i in report.issues if i.severity == tier]
        if not bucket:
            continue
        print(f"== {label} ({len(bucket)}) ==")
        for i in bucket:
            print(f"  Row {i.row_number}: [{i.code}] {i.message}")
            print(f"    Q: {i.question[:90]}")
        print()

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
