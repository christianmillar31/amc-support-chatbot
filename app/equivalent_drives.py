"""Equivalent-drive lookup for "what else is similar to X?" questions.

Runs as a deterministic gate BEFORE the retrieval path so Claude can't
hallucinate SKUs when asked for replacements. Uses the canonical CSV
(CM Servo Info.csv) as the sole source of truth; every candidate drive
in the response is guaranteed to exist.

The motivating failure: a user asked "if AZBH25A20-10 gets discontinued,
what's similar at the same voltage and current?" and the bot confidently
invented ``AZBH12A12`` (doesn't exist) AND misquoted AZBH6A8's current
(said 6A/10A, actual 3A/6A). This module exists to make that class of
hallucination structurally impossible for replacement-class questions.

Gate fires when:
  (a) the message matches an "equivalent/similar/replacement" intent
      pattern, AND
  (b) a valid AMC SKU in the message resolves in the drive DB.

Returns a structured answer with three buckets:
  - "Current-production, same voltage range" — ideal drop-in
  - "Current-production, similar current (different voltage)" — adapter
    scenarios
  - "Current-production, higher-rated upsize at same voltage" — oversize
    option when no exact match exists.

Non-current-production drives (Reserved, Discontinued) are listed only
when the user's own drive is non-current-production, for reference.
"""
from __future__ import annotations

import re

from app.drive_lookup import lookup_drive, _DRIVE_DB, _load_csv
from app.sku_matcher import candidate_sku_tokens
from app.support_catalog import normalize_lookup_sku


# Intent: "if this gets discontinued, what replaces it?", "what's equivalent
# to X?", "alternative for Y", "similar drive to Z", "upgrade path for X".
# We keep this narrow to avoid false-firing on retrofit questions (which
# have their own dedicated gate) and coverage-state questions.
_EQUIV_INTENT = re.compile(
    r"\b("
    r"similar\s+(drive|amc|part|model|option)|"
    r"equivalent\s+(drive|amc|part|model|option|to)|"
    r"(what|any)\s+(other|else)\s+\w*\s*(drive|option|amc|model)|"
    r"anything\s+similar|any\s+alternatives?|any\s+equivalent|"
    r"(if|when|once)\s+(this|that|\w+)\s+(gets?|is|becomes?|goes?)\s+discontinued|"
    r"(current\s+production|active)\s+(drive|alternative|replacement|equivalent)|"
    r"(drop.?in|same|matching)\s+(replacement|alternative)|"
    r"comparable\s+(drive|amc|part|model)|"
    r"upgrade\s+(path|option|to)|"
    r"replace\s+(this|my|the)\s+(drive|\w+)\s+with|"
    r"what'?s?\s+(like|similar\s+to)|"
    r"do\s+you\s+have\s+(anything|any\s+\w+|something)\s+(like|similar|that|with)"
    r")\b",
    re.IGNORECASE,
)


def detect_equivalent_query(message: str) -> bool:
    """Does the user's message look like an equivalent/replacement request?"""
    return bool(_EQUIV_INTENT.search(message or ""))


def resolve_reference_drive(
    message: str,
    drive_context: dict | None = None,
) -> dict | None:
    """Resolve the "reference drive" the user wants an equivalent for.

    Priority:
      1. Explicit drive_context (UI preselect).
      2. SKU token in the message.
    Returns a drive dict (from lookup_drive) or None.
    """
    if drive_context:
        for key in ("canonical_sku", "datasheet_sku", "requested_sku"):
            sku = drive_context.get(key)
            if sku:
                hit = lookup_drive(sku)
                if hit:
                    return hit
    for raw in candidate_sku_tokens(message or ""):
        hit = lookup_drive(raw)
        if hit:
            return hit
        normalized = normalize_lookup_sku(raw)
        if normalized and normalized != raw:
            hit = lookup_drive(normalized)
            if hit:
                return hit
    return None


def _parse_voltage_range(dc_range: str) -> tuple[float, float] | None:
    """From strings like '40 - 175' or '20-80 VDC', return (40.0, 175.0)."""
    if not dc_range:
        return None
    m = re.search(r"(\d+)\s*[-–]\s*(\d+)", dc_range.replace("–", "-"))
    if not m:
        return None
    try:
        return float(m.group(1)), float(m.group(2))
    except ValueError:
        return None


def _voltage_overlap_fraction(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Return how much of range A is covered by range B, in [0, 1]."""
    lo = max(a[0], b[0])
    hi = min(a[1], b[1])
    if hi <= lo:
        return 0.0
    a_len = a[1] - a[0]
    if a_len <= 0:
        return 0.0
    return (hi - lo) / a_len


def _parse_modes(operating_mode: str) -> set[str]:
    """Split 'Current|Hall Velocity|Velocity' -> {'Current', 'Hall Velocity', ...}."""
    if not operating_mode:
        return set()
    return {p.strip() for p in operating_mode.split("|") if p.strip()}


def _is_active(status: str) -> bool:
    s = (status or "").lower()
    return "active" in s and "discontin" not in s and "reserved" not in s


def find_equivalents(
    reference: dict,
    *,
    current_tolerance: float = 0.30,
    voltage_overlap_min: float = 0.50,
    prefer_modes: set[str] | None = None,
    max_per_bucket: int = 5,
) -> dict:
    """Search _DRIVE_DB for drives that could replace the reference.

    Returns a dict with three buckets (each a list of drive dicts):
      - same_voltage_similar_current: ideal drop-in replacements
      - same_current_different_voltage: current match, voltage adjusted
      - same_voltage_upsize: higher-current at same voltage (oversize)

    All candidates are filtered to current-production status UNLESS the
    reference itself is already non-current-production, in which case
    Reserved drives are acceptable too (Discontinued is always excluded —
    retrofit_lookup handles those).
    """
    _load_csv()

    ref_sku = reference["sku"]
    ref_cc = _num(reference.get("current_continuous_a"))
    ref_cp = _num(reference.get("current_peak_a"))
    ref_v = _parse_voltage_range(reference.get("dc_supply_range", ""))
    ref_modes = _parse_modes(reference.get("operating_mode", ""))
    ref_is_active = _is_active(reference.get("status", ""))

    # If the user didn't specify modes to preserve, preserve the reference's
    # full mode set (intersected with "Hall Velocity" if the user asked about
    # that feature — caller can pass prefer_modes).
    required_modes = prefer_modes or ref_modes or set()

    buckets = {
        "same_voltage_similar_current": [],
        "same_current_different_voltage": [],
        "same_voltage_upsize": [],
    }

    for sku, d in _DRIVE_DB.items():
        if sku == ref_sku or sku == reference.get("canonical_sku"):
            continue
        status = (d.get("status") or "").strip()
        # Always exclude Discontinued (retrofit handles those).
        if "discontin" in status.lower():
            continue
        # Exclude Reserved unless reference is itself non-active.
        if not ref_is_active:
            pass  # reserved OK
        else:
            if "reserved" in status.lower():
                continue

        cand_cc = _num(d.get("current_continuous_a"))
        cand_cp = _num(d.get("current_peak_a"))
        cand_v = _parse_voltage_range(d.get("dc_supply_range", ""))
        cand_modes = _parse_modes(d.get("operating_mode", ""))

        if ref_v is None or cand_v is None:
            continue

        # Operating-mode gate: if any required_modes, the candidate must
        # support ALL of them (a "Hall Velocity" source requires candidates
        # with Hall Velocity).
        if required_modes and not required_modes.issubset(cand_modes):
            continue

        # Skip candidates with no spec info
        if cand_cc is None or cand_cp is None:
            continue

        v_overlap = _voltage_overlap_fraction(ref_v, cand_v)

        # Current closeness
        cc_ratio = cand_cc / ref_cc if ref_cc else float("inf")
        cp_ratio = cand_cp / ref_cp if ref_cp else float("inf")
        cc_close = ref_cc and (1 - current_tolerance) <= cc_ratio <= (1 + current_tolerance)
        cp_close = ref_cp and (1 - current_tolerance) <= cp_ratio <= (1 + current_tolerance)

        entry = _serialize(d, v_overlap=v_overlap, cc_ratio=cc_ratio, cp_ratio=cp_ratio)

        # Bucket 1: same voltage range + close current
        if v_overlap >= voltage_overlap_min and (cc_close or cp_close):
            buckets["same_voltage_similar_current"].append(entry)
            continue

        # Bucket 3: same voltage, higher current (oversize option)
        if v_overlap >= voltage_overlap_min and (cc_ratio > 1 + current_tolerance):
            buckets["same_voltage_upsize"].append(entry)
            continue

        # Bucket 2: current match but different voltage
        if (cc_close or cp_close) and v_overlap < voltage_overlap_min:
            # Require some voltage neighborhood — skip wildly mismatched voltages
            # (e.g. 400V drive when user has 48V). Candidate's max voltage
            # should be within 3x of reference's max.
            if cand_v[1] < ref_v[1] * 3 and cand_v[1] * 3 > ref_v[1]:
                buckets["same_current_different_voltage"].append(entry)
            continue

    # Rank each bucket by closeness
    def _rank_similar(e):
        v_cost = 1.0 - e["_v_overlap"]
        c_cost = abs((e["_cc_ratio"] or 1) - 1)
        return v_cost + c_cost

    def _rank_current(e):
        return abs((e["_cc_ratio"] or 1) - 1)

    def _rank_upsize(e):
        # Prefer smaller upsize
        return (e["_cc_ratio"] or 99) - 1

    buckets["same_voltage_similar_current"].sort(key=_rank_similar)
    buckets["same_current_different_voltage"].sort(key=_rank_current)
    buckets["same_voltage_upsize"].sort(key=_rank_upsize)

    for k in buckets:
        buckets[k] = buckets[k][:max_per_bucket]

    return buckets


def _num(raw) -> float | None:
    try:
        s = str(raw or "").strip()
        if not s:
            return None
        return float(s)
    except (ValueError, TypeError):
        return None


def _serialize(d: dict, *, v_overlap: float, cc_ratio: float | None, cp_ratio: float | None) -> dict:
    return {
        "sku": d["sku"],
        "family": d.get("family", ""),
        "form_factor": d.get("form_factor", ""),
        "network": d.get("network", ""),
        "status": d.get("status", ""),
        "current_continuous_a": d.get("current_continuous_a", ""),
        "current_peak_a": d.get("current_peak_a", ""),
        "dc_supply_range": d.get("dc_supply_range", ""),
        "operating_mode": d.get("operating_mode", ""),
        "_v_overlap": v_overlap,
        "_cc_ratio": cc_ratio,
        "_cp_ratio": cp_ratio,
    }


def _fmt_candidate(c: dict) -> str:
    peak = c.get("current_peak_a") or "?"
    cont = c.get("current_continuous_a") or "?"
    return (
        f"- **{c['sku']}** — {cont} A continuous / {peak} A peak, "
        f"DC {c.get('dc_supply_range', '?')} V, "
        f"{c.get('network') or 'analog/PWM'}, "
        f"{c['family']} {c['form_factor']}, "
        f"status: {c['status']}"
    )


def format_equivalent_answer(
    reference: dict,
    buckets: dict,
    user_mentioned_feature: str | None = None,
) -> str:
    """Compose a response listing the real alternatives from CSV."""
    ref_sku = reference["sku"]
    ref_cc = reference.get("current_continuous_a") or "?"
    ref_cp = reference.get("current_peak_a") or "?"
    ref_v = reference.get("dc_supply_range") or "?"
    ref_family = reference.get("family") or ""
    ref_status = reference.get("status") or ""
    ref_modes = reference.get("operating_mode") or ""

    lines = [
        f"**Equivalent/alternative drives for {ref_sku}**",
        "",
        f"Your drive: {ref_cc} A continuous / {ref_cp} A peak, DC {ref_v} V, "
        f"{ref_family} family, status: **{ref_status}**, modes: {ref_modes}.",
        "",
    ]

    same_v = buckets.get("same_voltage_similar_current") or []
    same_i = buckets.get("same_current_different_voltage") or []
    upsize = buckets.get("same_voltage_upsize") or []

    if not (same_v or same_i or upsize):
        lines.append(
            "**No current-production AMC drive in the CSV matches both the "
            "voltage range and the current level with the same operating "
            "modes.** If you can relax one constraint (lower voltage OR "
            "higher current OR different control mode), let me know which "
            "is flexible and I'll re-search. Otherwise the recommendation "
            "is to contact AMC support — a similar discontinued unit may "
            "still be available or a custom-option variant may fit."
        )
        return "\n".join(lines)

    if same_v:
        lines.append(f"**Same voltage range ({ref_v} V), similar current level — ideal drop-in candidates:**")
        lines.append("")
        for c in same_v:
            lines.append(_fmt_candidate(c))
        lines.append("")

    if upsize and not same_v:
        # Only recommend upsize prominently when no exact match exists
        lines.append(f"**Same voltage range ({ref_v} V), higher current (upsize option):**")
        lines.append("")
        for c in upsize:
            lines.append(_fmt_candidate(c))
        lines.append("")
        lines.append(
            "_Note: these drives can deliver more current than you need. "
            "Upsizing is safe; you'll pay for headroom you may not use._"
        )
        lines.append("")
    elif upsize and same_v:
        # Show upsize secondarily
        lines.append(f"**Same voltage range, higher current (upsize if you want headroom):**")
        lines.append("")
        for c in upsize[:3]:
            lines.append(_fmt_candidate(c))
        lines.append("")

    if same_i:
        lines.append(f"**Current match ({ref_cc} A / {ref_cp} A), different voltage range — requires power-supply change:**")
        lines.append("")
        for c in same_i:
            lines.append(_fmt_candidate(c))
        lines.append("")

    lines.append(
        "All candidates above come directly from AMC's canonical product "
        "database (CM Servo Info.csv). Specs are verbatim — no estimates, "
        "no inferred values from SKU naming. If none of these fit, ask "
        "about FlexPro or DigiFlex families, which support Hall feedback "
        "with broader voltage options but use a digital control interface."
    )
    return "\n".join(lines)
