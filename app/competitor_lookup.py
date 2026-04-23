"""Competitor-drive detection and AMC cross-reference.

When a user asks for an AMC replacement for a competitor's drive (Elmo,
Kollmorgen, Copley, Yaskawa, Beckhoff, etc.), the typo gate today treats
the competitor's SKU as an unknown AMC part and refuses with
"I couldn't find X in the AMC product database." That's technically
correct but useless for a support engineer helping a customer migrate.

This module detects competitor brand mentions, tries to parse the common
"continuous-current / voltage" SKU shorthand (Elmo uses exactly that
format — e.g. ``18/400`` = 18 A continuous, 400 VDC bus), and finds AMC
drives with comparable specs in CM Servo Info.csv.

Runs as a deterministic gate BEFORE the typo gate in support_core so
competitor mentions never hit the "unknown AMC SKU" refusal.
"""
from __future__ import annotations

import re

from app.drive_lookup import _DRIVE_DB, _load_csv


# Known competitor brands. Order matters a bit — more-specific multi-word
# names first so "Kollmorgen Servostar" matches before bare "Servostar".
# Match against `msg.lower()` with word boundaries.
_COMPETITOR_BRANDS: tuple[tuple[str, str], ...] = (
    # (canonical_display_name, regex)
    ("Elmo Motion Control",      r"\belmo(\s+motion\s+controls?)?\b"),
    ("Kollmorgen",               r"\bkollmorgen\b"),
    ("Servostar",                r"\bservostar\b"),
    ("Copley Controls",          r"\bcopley(\s+controls?)?\b"),
    ("Yaskawa",                  r"\byaskawa\b"),
    ("Beckhoff",                 r"\bbeckhoff\b"),
    ("ABB",                      r"\babb\s+(servo|drive|motion)\b"),
    ("Parker Hannifin",          r"\bparker\s+(compax|aries|pssd|hannifin)\b|\bcompax3?\b"),
    ("Bosch Rexroth",            r"\bbosch\s+rexroth\b|\brexroth\b"),
    ("Siemens Sinamics",         r"\bsiemens\b|\bsinamics\b"),
    ("Mitsubishi",               r"\bmitsubishi\s+(servo|mr-?j)\b|\bmr-?j\d+\b"),
    ("Delta Tau",                r"\bdelta\s+tau\b|\bpowerpmac\b"),
    ("Panasonic Minas",          r"\bpanasonic\s+(servo|minas)\b|\bminas\b"),
    ("Omron",                    r"\bomron\s+(servo|r88|g5)\b|\br88\w*\b"),
    ("Sanyo Denki",              r"\bsanyo\s+denki\b"),
    ("Rockwell / Allen-Bradley", r"\ballen.?bradley\b|\bkinetix\b|\bab\s+servo\b"),
    ("Schneider Electric",       r"\bschneider\s+(electric|lexium)\b|\blexium\b"),
    ("Lenze",                    r"\blenze\s+(servo|8400|9400|i700)\b"),
    ("SEW Eurodrive",            r"\bsew\s+(eurodrive|movi)\b|\bmovidrive\b"),
    # Also match the product-line names so users who say "my EPOS4 drive"
    # get routed correctly without needing to mention the parent brand.
    ("Ingenia Motion Control",   r"\bingenia\b|\beverest\s+(net|xcr|s)\b|\bcapitan\s+(net|xcr)\b|\bsummit\s+net\b|\bnemesis\b"),
    ("Nanotec",                  r"\bnanotec\b|\bnanotech\b|\bn5-\w+\b|\bc5-?e\b|\bcl3-?e\b"),
    ("Maxon Motor",              r"\bmaxon\s+(motor|drive|servo|esc|epos|maxpos)\b|\bescon\b|\bepos\s*\d?\b|\bmaxpos\b"),
)

_COMPETITOR_PATTERNS = tuple(
    (name, re.compile(pattern, re.IGNORECASE)) for name, pattern in _COMPETITOR_BRANDS
)


def detect_competitor(message: str) -> dict | None:
    """Return {brand, matched_text} if a known competitor brand is mentioned, else None."""
    if not message:
        return None
    for brand, pat in _COMPETITOR_PATTERNS:
        m = pat.search(message)
        if m:
            return {"brand": brand, "matched_text": m.group(0)}
    return None


# Elmo SOLO/Gold/Whistle series uses ``XX/YYY`` or ``XX-YYY`` where
# XX = continuous current (A) and YYY = DC bus voltage (V). Examples:
# "18/400", "5/100", "30/230", "60/1700". We accept both / and -.
_ELMO_SPEC_RE = re.compile(
    r"(?<![A-Za-z0-9])(\d{1,3})\s*[/\-]\s*(\d{2,4})(?![A-Za-z0-9])"
)


def parse_competitor_specs(message: str, brand: str) -> dict | None:
    """Extract (continuous_a, voltage_dc) from competitor SKU shorthand.

    Currently only Elmo-style ``A/V`` pairs are parsed. Returns None if
    the brand doesn't have a known shorthand or the pattern isn't present.
    """
    if not message:
        return None

    is_elmo_like = brand.lower().startswith(("elmo", "copley"))
    if not is_elmo_like:
        # Other brands have messier SKU conventions; we ask the user for specs
        # instead of guessing.
        return None

    for m in _ELMO_SPEC_RE.finditer(message):
        a, v = int(m.group(1)), int(m.group(2))
        # Sanity-check: servo-drive continuous current is typically 1-200 A,
        # DC bus 12-800 V. Reject obvious false positives like "1/2" or
        # timestamps.
        if 1 <= a <= 200 and 12 <= v <= 1000:
            return {
                "continuous_a": a,
                "voltage_dc": v,
                "raw": m.group(0),
            }
    return None


def find_amc_matches(
    continuous_a: float,
    voltage_dc: float,
    *,
    current_tolerance: float = 0.35,
    voltage_margin: float = 1.15,
    max_results: int = 6,
) -> list[dict]:
    """Return AMC drives whose specs are comparable to the target continuous
    current and DC bus voltage.

    Matching rules (loose by design — customer-migration discussions want
    options, not exact matches):
      - continuous current within ``current_tolerance`` (default ±35%)
      - DC supply upper bound >= voltage_dc × (1/voltage_margin)
        (i.e. the AMC drive can handle at least ~87% of the competitor's
        bus voltage — most AMC drives derate a bit below peak competitors)
    """
    _load_csv()
    lo = continuous_a * (1 - current_tolerance)
    hi = continuous_a * (1 + current_tolerance)
    v_min_needed = voltage_dc / voltage_margin

    candidates: list[dict] = []
    for sku, drive in _DRIVE_DB.items():
        cc_str = (drive.get("current_continuous_a") or "").strip()
        if not cc_str:
            continue
        try:
            cc = float(cc_str)
        except ValueError:
            continue
        if not (lo <= cc <= hi):
            continue

        dc_range = (drive.get("dc_supply_range") or "").replace("–", "-").strip()
        v_max = _parse_upper_voltage(dc_range)
        if v_max is None or v_max < v_min_needed:
            continue

        # Skip discontinued / reserved drives — customers migrating usually
        # want current-production options.
        status = (drive.get("status") or "").lower()
        if "discontin" in status:
            continue

        candidates.append({
            "sku": drive["sku"],
            "title": drive.get("title", ""),
            "family": drive.get("family", ""),
            "form_factor": drive.get("form_factor", ""),
            "network": drive.get("network", ""),
            "current_continuous_a": cc,
            "current_peak_a": drive.get("current_peak_a", ""),
            "dc_supply_range": dc_range,
            "status": drive.get("status", ""),
        })

    # Sort by closeness to target continuous current (best match first),
    # then prefer higher peak current (headroom), then current families.
    def _rank(d):
        current_err = abs(d["current_continuous_a"] - continuous_a)
        family_pref = {"FlexPro": 0, "DigiFlex Performance": 1}.get(d["family"], 2)
        return (current_err, family_pref)

    candidates.sort(key=_rank)
    return candidates[:max_results]


def _parse_upper_voltage(dc_range: str) -> float | None:
    """From strings like '127 - 373' or '60 - 400 VDC', return 373 / 400."""
    if not dc_range:
        return None
    m = re.search(r"(\d+)\s*[-–]\s*(\d+)", dc_range)
    if m:
        try:
            return float(m.group(2))
        except ValueError:
            return None
    # Fall back: a single number
    m = re.search(r"(\d+)", dc_range)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def format_competitor_answer(
    brand: str,
    specs: dict | None,
    matches: list[dict],
    raw_message: str,
) -> str:
    """Format a response for a competitor-replacement question.

    Deliberately conservative: acknowledges the competitor, asks the user to
    confirm the actual electrical specs BEFORE making AMC recommendations.
    Even when a plausible spec shorthand is detected (e.g. Elmo "18/400"),
    we treat it as a hypothesis and ask for confirmation rather than
    asserting it. Different Elmo series (SOLO, Gold, Whistle, Platinum,
    Simpliq) and different competitors use different conventions — a
    confident-but-wrong parse leads the engineer to the wrong AMC drive.
    """
    required_specs_block = (
        "To find the best AMC replacement, I need you to confirm:\n\n"
        "1. **Continuous current** rating (A)\n"
        "2. **Peak current** rating (A)\n"
        "3. **Bus voltage** — DC supply range (V), or AC line voltage if AC-input\n\n"
        "Also helpful (optional):\n"
        "- Motor type (brushed DC / brushless / stepper / AC induction)\n"
        "- Communication protocol (analog command, EtherCAT, CANopen, Ethernet/IP, RS-232/485, etc.)\n"
        "- Form factor (panel mount / PCB mount / vehicle-rated)\n"
        "- Environmental rating (standard / extended-temp / conformal coating)"
    )

    if specs:
        # A spec hypothesis was parseable — present it as something to
        # verify, NOT assert. Optionally show tentative candidates with a
        # clear "subject to spec confirmation" caveat.
        lines = [
            f"I see you're asking about an **{brand}** drive — AMC (Advanced "
            f"Motion Controls) can likely provide a replacement once we pin "
            f"down the electrical specs.",
            "",
            f"Your question contains **`{specs['raw']}`**, which in *some* "
            f"{brand} product lines (e.g. Elmo SOLO / Gold / Whistle) would "
            f"mean **{specs['continuous_a']} A continuous at "
            f"{specs['voltage_dc']} V DC bus** — but the shorthand varies "
            f"across series, and other competitors use different conventions. "
            f"Can you confirm that reading is correct for your actual drive?",
            "",
            required_specs_block,
        ]
        if matches:
            lines.extend([
                "",
                f"**If** the `{specs['continuous_a']} A / {specs['voltage_dc']} V` "
                f"reading is right, these AMC drives would be worth a look "
                f"(subject to your confirming the specs + application):",
                "",
            ])
            for m in matches:
                peak = m.get("current_peak_a") or "?"
                lines.append(
                    f"- **{m['sku']}** ({m['family']}, {m['form_factor']}) — "
                    f"{m['current_continuous_a']:g} A continuous / {peak} A peak, "
                    f"DC {m['dc_supply_range']} V, "
                    f"{m['network'] or 'analog/PWM'}"
                )
            lines.extend([
                "",
                "These span three AMC families — **FlexPro** (newer, full "
                "fieldbus support), **DigiFlex Performance** (mature high-"
                "power platform), and **AxCent** (cost-effective analog/"
                "PWM). I'll narrow the list once you confirm the specs and "
                "target protocol.",
            ])
        return "\n".join(lines)

    # No spec shorthand parseable — just acknowledge and ask.
    return (
        f"I recognize **{brand}** as a competitor — AMC (Advanced Motion "
        f"Controls) can likely replace it, but competitor part numbers "
        f"aren't in the AMC database and the shorthand varies by series, "
        f"so I need the actual electrical specs before recommending "
        f"anything.\n\n"
        f"{required_specs_block}\n\n"
        f"With those, I can pull matching AMC options from the FlexPro, "
        f"DigiFlex Performance, and AxCent families."
    )
