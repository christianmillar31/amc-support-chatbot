from __future__ import annotations
"""Complex-troubleshooting helpers.

The bot is good at answering well-scoped questions. Multi-step deep-manual
troubleshooting — the kind of thing a smart engineer would call AMC about — is
harder, because the bot can't actually reproduce the setup or see a scope trace.

This module helps in three narrow ways:

1. Detect "the user has already tried things" / "nothing has worked" language.
2. Match the symptom against a small library of patterns that almost always
   require AMC tech support (encoder index missing, Phase Detect fails on an
   encoder-only motor, CAN silent after enable, over-voltage during regen, etc.).
3. Build a structured "here's what to tell AMC tech support" summary so the
   support engineer on the other end of the phone gets a clean handoff.
"""

import re
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Escalation cues
# ---------------------------------------------------------------------------

_ESCALATION_CUES = [
    # "I already tried…" language
    "already tried", "i've tried", "i have tried", "tried everything",
    "nothing works", "still doesn't work", "still fails", "still failing",
    "keeps throwing", "keeps failing", "keeps faulting", "keeps resetting",
    "tried both", "tried all", "tried several",
    # "Went through the steps" language
    "went through the", "followed the procedure", "followed the steps",
    "step 1 and step 2", "step by step",
    # Explicit "stuck" indicators
    "i'm stuck", "i am stuck", "out of ideas", "out of options",
    "losing my mind", "at my wit's end", "pulling my hair",
    # Reviewer-review language
    "reviewed the manual", "read the manual", "checked the datasheet",
]

_FAULT_CUES = [
    "overvoltage", "over-voltage", "over voltage",
    "overcurrent", "over-current", "over current",
    "over temperature", "over-temp", "overtemp",
    "ground fault",
    "encoder fault", "encoder error",
    "commutation fault", "commutation error",
    "phase detect fail", "phase detect failed",
    "unable to commutate",
    "regen fault", "regen over-voltage",
    "can silent", "canopen silent",
    "ethercat drop", "ethercat timeout",
    "rs-485 timeout", "modbus timeout",
]


@dataclass
class EscalationCues:
    already_tried: list[str] = field(default_factory=list)
    fault_cues: list[str] = field(default_factory=list)

    @property
    def should_escalate(self) -> bool:
        # Escalate when the user has tried things and mentioned a specific fault,
        # or when a high-value fault cue is present on its own.
        return bool(self.already_tried and self.fault_cues) or bool(
            set(self.fault_cues)
            & {
                "phase detect fail",
                "phase detect failed",
                "unable to commutate",
                "regen fault",
                "regen over-voltage",
                "can silent",
                "canopen silent",
                "ethercat timeout",
                "ethercat drop",
            }
        )


def detect_escalation_cues(message: str) -> EscalationCues:
    text = (message or "").lower()
    already_tried = [c for c in _ESCALATION_CUES if c in text]
    fault_cues = [c for c in _FAULT_CUES if c in text]
    return EscalationCues(already_tried=already_tried, fault_cues=fault_cues)


# ---------------------------------------------------------------------------
# Known escalation patterns
# ---------------------------------------------------------------------------

# Very small starter library. Each entry: (keyword set that triggers it,
# short triage guidance). Designed to grow over time from real support cases.
@dataclass
class EscalationPattern:
    name: str
    keywords: tuple[str, ...]
    diagnosis_hint: str
    data_to_collect: tuple[str, ...]


_PATTERNS: list[EscalationPattern] = [
    EscalationPattern(
        name="Encoder index (Z) missing or miswired",
        keywords=("no index", "missing index", "index pulse", "z-pulse", "phase detect fail", "phase detect failed"),
        diagnosis_hint=(
            "Phase Detect on an encoder-only motor depends on seeing the encoder's "
            "index (Z) pulse within one mechanical revolution. If the index isn't "
            "present, isn't routed to the drive, or is seen at the wrong signal level, "
            "Phase Detect will fail and commutation cannot be established."
        ),
        data_to_collect=(
            "Exact drive SKU and firmware revision",
            "Motor model and encoder model/resolution",
            "Which encoder signals are physically connected (A, /A, B, /B, Z, /Z) — and signal levels",
            "Scope capture of A, B, Z during slow manual rotation of the motor",
            "ACE/DriveWare Phase Detect error code and the Commutation Offset value it ends on",
        ),
    ),
    EscalationPattern(
        name="Regen over-voltage on decel",
        keywords=("regen", "over-voltage during decel", "bus overvoltage", "overvoltage on decel", "decel overvoltage"),
        diagnosis_hint=(
            "Over-voltage during deceleration usually means the motor's kinetic energy "
            "is pushing back into the DC bus faster than the supply or shunt can absorb. "
            "Common causes: undersized or missing external shunt resistor, incorrect "
            "shunt trigger voltage, regenerative energy exceeding supply capacity, or a "
            "bad bus capacitor."
        ),
        data_to_collect=(
            "Drive SKU, DC supply model and rating",
            "External shunt resistor value (ohms) and wattage",
            "Shunt trigger voltage configured in the drive",
            "Motor inertia, load inertia, and commanded decel rate",
            "Bus voltage scope capture during the fault",
        ),
    ),
    EscalationPattern(
        name="CAN / CANopen silent after enable",
        keywords=("can silent", "canopen silent", "can bus silent", "no sdo response", "no pdo traffic"),
        diagnosis_hint=(
            "CAN silence after enable is almost always a wiring / termination / baud-rate "
            "issue, or a node-ID collision. The drive's CAN controller goes bus-off after "
            "too many consecutive errors and stays silent until power-cycled."
        ),
        data_to_collect=(
            "Drive SKU and firmware",
            "Node ID and baud rate",
            "Termination resistors — where installed, measured value across CAN_H/CAN_L",
            "Cable length and topology (bus, star, drop-lengths)",
            "Any CAN analyzer capture showing the first error frame",
        ),
    ),
    EscalationPattern(
        name="EtherCAT drop during motion",
        keywords=("ethercat drop", "ethercat timeout", "slave lost", "eni mismatch"),
        diagnosis_hint=(
            "EtherCAT drops under load often indicate bus watchdog settings, cable "
            "quality / shielding, or Sync Manager timing that doesn't match the master's "
            "cycle time. Not a drive-internal failure in most cases."
        ),
        data_to_collect=(
            "Drive SKU and firmware",
            "Master model and cycle time",
            "ESI / ENI file version",
            "Whether drop happens at a specific point in motion",
            "Link LED behavior at the moment of drop (ideally packet capture)",
        ),
    ),
]


def match_escalation_pattern(message: str) -> EscalationPattern | None:
    text = (message or "").lower()
    for pattern in _PATTERNS:
        if any(k in text for k in pattern.keywords):
            return pattern
    return None


# ---------------------------------------------------------------------------
# Structured escalation summary
# ---------------------------------------------------------------------------


def build_escalation_summary(
    *,
    question: str,
    drive_sku: str | None,
    cues: EscalationCues,
    pattern: EscalationPattern | None,
) -> str:
    """Build a short, copy-pasteable `Call AMC Tech Support with this` block.

    Designed to be appended to an existing answer, not a replacement for it.
    """
    lines: list[str] = []
    lines.append("---")
    lines.append("### If this isn't resolved, here's a clean handoff for AMC tech support")
    lines.append("")
    if pattern:
        lines.append(f"**Likely diagnostic bucket:** {pattern.name}")
        lines.append("")
        lines.append(f"_Why this bucket:_ {pattern.diagnosis_hint}")
        lines.append("")
        lines.append("**Data to have ready when you call:**")
        for item in pattern.data_to_collect:
            lines.append(f"- {item}")
    else:
        lines.append("**Data to have ready when you call:**")
        lines.append("- Exact drive part number and firmware revision")
        lines.append("- Motor model and feedback type (encoder model/resolution or Hall only)")
        lines.append("- Software tool in use (ACE or DriveWare) and version")
        lines.append("- Wiring / mounting form factor (panel, PCB, vehicle)")
        lines.append("- Exact error code or fault name as it appears in the drive software")
        lines.append("- Scope or trace capture of the failing condition if available")

    lines.append("")
    lines.append("**Symptom (paste into your email):**")
    lines.append(f"> {question.strip()}")
    if drive_sku:
        lines.append("")
        lines.append(f"**Drive in question:** `{drive_sku}`")
    if cues.fault_cues:
        lines.append("")
        lines.append(f"**Fault cues detected in your message:** {', '.join(sorted(set(cues.fault_cues)))}")
    if cues.already_tried:
        lines.append("")
        lines.append(
            "**Notes:** user has already attempted standard troubleshooting "
            f"(\"{cues.already_tried[0]}\") — mention this so AMC doesn't start from scratch."
        )
    return "\n".join(lines)
