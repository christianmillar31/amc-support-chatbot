"""Deterministic short-circuit for direct-spec questions and impossible-combo refusals.

Runs between the retrofit gate and the FAQ gate in
``support_core.stream_support_request``. Two purposes:

1. ``try_spec_answer`` — when the user asks for a canonical spec field on a
   known SKU (continuous/peak current, DC or AC supply range, motor type, form
   factor, communication interface, or the name of the comm manual), assemble
   the answer from ``CM Servo Info.csv`` with zero model cost.
2. ``detect_impossible_combo`` — when the user's question mentions a protocol
   that the resolved SKU's family/variant cannot support (AxCent + any
   fieldbus, Classic-analog + any fieldbus, or a specific FlexPro/DigiFlex
   variant + a different protocol, e.g. POWERLINK on an EtherCAT variant),
   return a deterministic refusal that names what the drive DOES support.

Both functions are pure lookups: no embedding model, no cross-encoder, no LLM.
Failures fall through gracefully (return ``None``) so the FAQ and single-shot
gates stay in charge of anything this module can't confidently answer.
"""

from __future__ import annotations

import re

from app.drive_lookup import lookup_drive
from app.sku_matcher import candidate_sku_tokens
from app.support_catalog import normalize_lookup_sku


# -- spec-intent regexes -----------------------------------------------------

# Use a bounded gap between the adjective and "current" so questions like
# "continuous and peak current ratings" still route both intents (the
# continuous-current regex was previously only adjacent-only, which dropped
# half the answer for combined-spec questions).
_CONTINUOUS_CURRENT = re.compile(
    r"\b(continuous|nominal|rated|rms)\b[^.?!\n]{0,40}?\bcurrent\b",
    re.IGNORECASE,
)
_PEAK_CURRENT = re.compile(
    r"\b(peak|max(?:imum)?)\b[^.?!\n]{0,40}?\bcurrent\b",
    re.IGNORECASE,
)
_DC_SUPPLY = re.compile(
    r"\b(dc\s+(supply|bus|input)\s*voltage|dc\s+voltage|dc\s+range|dc\s+input|bus\s+voltage)\b",
    re.IGNORECASE,
)
_AC_SUPPLY = re.compile(
    r"\b(ac\s+(supply|input)\s*voltage|ac\s+voltage|ac\s+range|ac\s+input)\b",
    re.IGNORECASE,
)
_SUPPLY_GENERIC = re.compile(
    r"\b(supply\s+voltage|input\s+voltage|operating\s+voltage|voltage\s+range)\b",
    re.IGNORECASE,
)
_PROTOCOL_Q = re.compile(
    r"\b(what\s+(protocol|network|fieldbus|interface|comm(unication)?)|"
    r"which\s+(protocol|network|fieldbus)|"
    r"(is|does)\s+(it|the\s+\w+)\s+(support|use)\s+"
    r"(ethercat|canopen|ethernet/?\s*ip|powerlink|modbus|rs-?\s*485|serial))\b",
    re.IGNORECASE,
)
_COMM_MANUAL_Q = re.compile(
    r"\b(communication\s+manual|comm\s+manual|which\s+manual|what\s+manual)\b",
    re.IGNORECASE,
)
_FORM_FACTOR_Q = re.compile(
    r"\b(form\s*factor|panel\s*mount|pcb\s*mount|vehicle\s*mount|mounting\s+style)\b",
    re.IGNORECASE,
)
_MOTOR_TYPE_Q = re.compile(
    r"\b(motor\s+type|what\s+motors|three[-\s]?phase\s+motor|single[-\s]?phase\s+motor|brushless\s+support|brushed\s+support)\b",
    re.IGNORECASE,
)


# -- protocol-mismatch patterns ---------------------------------------------

# message-side patterns paired with a canonical label used in refusal copy
_PROTOCOL_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bether\s*cat\b", re.IGNORECASE), "EtherCAT"),
    (re.compile(r"\bcan\s*open\b", re.IGNORECASE), "CANopen"),
    (re.compile(r"\bpowerlink\b", re.IGNORECASE), "POWERLINK"),
    (re.compile(r"\bethernet\s*/?\s*ip\b", re.IGNORECASE), "Ethernet/IP"),
    (re.compile(r"\bmodbus(\s+rtu|\s+tcp)?\b", re.IGNORECASE), "Modbus"),
    (re.compile(r"\brs\s*-?\s*485\b", re.IGNORECASE), "RS-485"),
    (re.compile(r"\brs\s*-?\s*232\b", re.IGNORECASE), "RS-232"),
)

# trigger phrases that indicate the user is trying to USE the protocol on the
# drive (vs. e.g. asking a neutral "what protocol" question, which try_spec_answer
# handles differently). we only refuse impossible-combos when one of these
# fires, to avoid false-refusing questioning-form prompts.
_USE_INTENT = re.compile(
    r"\b(set\s*up|setup|configure|configuring|use|using|enable|enabling|run|"
    r"running|connect|connecting|wire|wiring|"
    r"how\s+(do|to|can|should)|"
    r"on\s+(my|the|a|this|an)|for\s+(my|the|a|this|an)|with\s+(my|the|a|this|an)|"
    r"address\s+for|node\s+id|register\s+for|stores\s+the)\b",
    re.IGNORECASE,
)


def _asked_protocol(message: str) -> str | None:
    for pattern, label in _PROTOCOL_PATTERNS:
        if pattern.search(message):
            return label
    return None


def _drive_network_supports(drive: dict, asked: str) -> bool:
    network = (drive.get("network") or "").lower()
    family = (drive.get("family") or "").lower()
    comm_protocol = (drive.get("comm_protocol") or "").lower()
    combined = f"{network} {comm_protocol}".lower()
    if family in ("axcent", "classic"):
        return False
    asked_l = asked.lower()
    if asked_l == "ethercat":
        return "ethercat" in combined
    if asked_l == "canopen":
        return "canopen" in combined
    if asked_l == "powerlink":
        return "powerlink" in combined
    if asked_l == "ethernet/ip":
        return "ethernet/ip" in combined or "ethernetip" in combined
    if asked_l == "modbus":
        return "modbus" in combined
    if asked_l in ("rs-485", "rs-232"):
        return "rs-485" in combined or "rs-232" in combined or "serial" in combined
    return False


def resolve_drive_from_message(
    message: str,
    drive_context: dict | None = None,
) -> dict | None:
    """Resolve a drive either from explicit UI context or a SKU token in the message."""
    if drive_context:
        for key in ("canonical_sku", "datasheet_sku", "requested_sku"):
            sku = drive_context.get(key)
            if sku:
                hit = lookup_drive(sku)
                if hit:
                    return hit
    for raw in candidate_sku_tokens(message):
        hit = lookup_drive(raw)
        if hit:
            return hit
        normalized = normalize_lookup_sku(raw)
        if normalized and normalized != raw:
            hit = lookup_drive(normalized)
            if hit:
                return hit
    return None


def detect_impossible_combo(message: str, drive: dict) -> dict | None:
    """Return a refusal dict if the message asks about a protocol the drive can't do.

    Only fires when the message also carries a "use intent" phrase (how do I,
    set up, configure, on the X, etc.) — neutral questions like
    "is the FE060-5-EM CANopen or EtherCAT?" are left for try_spec_answer or
    the single-shot path.
    """
    asked = _asked_protocol(message)
    if not asked:
        return None
    if _drive_network_supports(drive, asked):
        return None
    if not _USE_INTENT.search(message):
        return None

    sku = drive.get("requested_sku") or drive.get("sku")
    family = drive.get("family", "")
    network = drive.get("network") or ""
    comm_protocol = drive.get("comm_protocol") or network or "analog/PWM"

    lines = [f"**{sku}** does not support **{asked}**.", ""]
    if family == "AxCent":
        lines.append(
            f"AxCent drives (including {sku}) are **analog / PWM only** — they have "
            "no fieldbus or serial communication interface. "
            "For a networked drive, look at the FlexPro or DigiFlex Performance families."
        )
    elif family == "Classic":
        lines.append(
            f"Classic drives (including {sku}) are **analog only**. They do not "
            "support any fieldbus protocol. For fieldbus support, consider an "
            "AxCent retrofit replacement or a FlexPro/DigiFlex drive."
        )
    else:
        lines.append(
            f"This variant's communication interface is **{comm_protocol}**. "
            f"If you need {asked}, pick the matching variant of the {family} family."
        )
    lines.append("")
    lines.append(
        "Reply with a different part number, or tell me which protocol you actually "
        "need and I'll point you at the right drive."
    )

    return {
        "answer": "\n".join(lines),
        "provider_used": "impossible_combo_refusal",
        "sku": sku,
        "asked_protocol": asked,
        "actual_interface": comm_protocol,
    }


def try_spec_answer(message: str, drive: dict) -> dict | None:
    """Return a canonical spec answer dict, or None to fall through."""
    sku = drive.get("requested_sku") or drive.get("sku")
    canonical_sku = drive.get("canonical_sku") or sku
    title = drive.get("title") or ""
    network = drive.get("network") or "None"
    comm_protocol = drive.get("comm_protocol") or network

    specs: list[tuple[str, str]] = []

    if _CONTINUOUS_CURRENT.search(message):
        val = (drive.get("current_continuous_a") or "").strip()
        if val:
            specs.append(("Continuous current", f"{val} A"))

    if _PEAK_CURRENT.search(message):
        val = (drive.get("current_peak_a") or "").strip()
        if val:
            specs.append(("Peak current", f"{val} A"))

    if _DC_SUPPLY.search(message):
        val = (drive.get("dc_supply_range") or "").strip()
        if val:
            specs.append(("DC supply voltage", f"{val} VDC"))

    if _AC_SUPPLY.search(message):
        val = (drive.get("ac_supply_range") or "").strip()
        if val:
            specs.append(("AC supply voltage", f"{val} VAC"))

    # Fallback generic "supply voltage" — emit both DC and AC if present
    if not specs and _SUPPLY_GENERIC.search(message):
        dc = (drive.get("dc_supply_range") or "").strip()
        ac = (drive.get("ac_supply_range") or "").strip()
        if dc:
            specs.append(("DC supply voltage", f"{dc} VDC"))
        if ac:
            specs.append(("AC supply voltage", f"{ac} VAC"))

    if _PROTOCOL_Q.search(message):
        specs.append(("Communication interface", comm_protocol))

    if _FORM_FACTOR_Q.search(message):
        val = (drive.get("form_factor") or "").strip()
        if val:
            specs.append(("Form factor", val))

    if _MOTOR_TYPE_Q.search(message):
        val = (drive.get("motor_type") or "").strip()
        if val:
            specs.append(("Motor type", val))

    if _COMM_MANUAL_Q.search(message):
        val = (drive.get("comm_manual") or "").strip()
        if val:
            specs.append(("Communication manual", val))

    if not specs:
        return None

    header = f"**{canonical_sku}** — {title}" if title else f"**{canonical_sku}**"
    lines = [header, ""]
    for label, value in specs:
        lines.append(f"- **{label}:** {value}")
    lines.append("")
    lines.append("_Source: CM Servo Info.csv (canonical product database)._")

    site_url = drive.get("site_url") or ""
    sources = [{
        "source": "CM Servo Info.csv",
        "page": 0,
        "heading": title,
        "url": site_url,
    }]

    return {
        "answer": "\n".join(lines),
        "provider_used": "canonical_spec",
        "sources": sources,
        "sku": canonical_sku,
    }
