"""Safety-first gate for hazard-language questions.

When a support engineer or end customer says something has exploded,
caught fire, smoked, sparked, or shocked someone, the bot's first
response must be safety guidance — NOT "which drive is this?" and
NOT a troubleshooting walk-through. If someone is standing next to
smoking hardware, the highest-value thing we can do in the first
sentence is:

  1. Tell them to remove power and stay clear.
  2. Give them a way to reach a human at AMC (phone + hours).
  3. Then — and only then — ask the qualifying questions needed to
     start an RMA / root-cause discussion.

Runs as the FIRST deterministic gate in `stream_support_request`, BEFORE
the typo / retrofit / spec / FAQ / ambiguity gates. Its output is
deterministic, cites no manual pages (the safety steps are universal,
not drive-specific), and asks for the drive SKU at the BOTTOM of the
response so follow-up questions can still be routed normally once the
user replies.

Keywords here deliberately lean permissive. A false positive (safety
steps appearing on a non-hazard question) is annoying but harmless; a
false negative (bot missing a "it caught fire" message and asking
"which drive?" like everything is fine) is the failure mode we can't
afford during the competition demo.
"""
from __future__ import annotations

import re


# Hazard language. Match as whole-word / word-boundary so we don't
# trigger on e.g. "firewall" or "smoked glass".
_HAZARD_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bexplod(?:e|ed|ing|es)\b", re.IGNORECASE),
    re.compile(r"\bexplosion\b", re.IGNORECASE),
    re.compile(r"\bcaught\s+fire\b", re.IGNORECASE),
    re.compile(r"\bon\s+fire\b", re.IGNORECASE),
    re.compile(r"\bburst\s+into\s+flames?\b", re.IGNORECASE),
    re.compile(r"\bflames?\b", re.IGNORECASE),
    re.compile(r"\bsmok(?:e|ed|ing|es)\b", re.IGNORECASE),
    re.compile(r"\bsmoke\s+coming\b", re.IGNORECASE),
    re.compile(r"\bsmell(?:s|ed|ing)?\s+(?:like\s+)?(?:burn|smoke|electrical|ozone)", re.IGNORECASE),
    re.compile(r"\bburning\s+smell\b", re.IGNORECASE),
    re.compile(r"\bburn(?:ed|t|ing)?\s+(?:up|out|through|my|the\s+drive|the\s+board)\b", re.IGNORECASE),
    re.compile(r"\bfried\s+(?:the|my|a)\s+(?:drive|board|unit|amplifier)\b", re.IGNORECASE),
    re.compile(r"\bspark(?:ed|ing|s)\b", re.IGNORECASE),
    re.compile(r"\barc(?:ed|ing)\b", re.IGNORECASE),
    re.compile(r"\barc\s+flash\b", re.IGNORECASE),
    re.compile(r"\bshock(?:ed|ing)\b", re.IGNORECASE),
    re.compile(r"\belectrocut(?:ed|ion)\b", re.IGNORECASE),
    re.compile(r"\bshort(?:ed|ing)?\s+out\b", re.IGNORECASE),
    re.compile(r"\bcapacitor\s+(?:blew|exploded|popped|burst|ruptured)\b", re.IGNORECASE),
    re.compile(r"\bblew\s+(?:up|a\s+cap|a\s+fuse|the\s+drive|the\s+board|the\s+fet|the\s+mosfet)\b", re.IGNORECASE),
    re.compile(r"\bmelt(?:ed|ing)?\b", re.IGNORECASE),
    re.compile(r"\bpop(?:ped|ping)?\s+(?:and|,|\s)", re.IGNORECASE),
    re.compile(r"\bbang(?:ed|ing)?\b", re.IGNORECASE),
    re.compile(r"\binjur(?:y|ed|ies)\b", re.IGNORECASE),
    re.compile(r"\bhurt\s+(?:someone|a\s+person|myself|my\s+tech|the\s+operator)", re.IGNORECASE),
)


# AMC support contact — sourced from site_data/amc_glossary.json (RMA entry).
# Business hours are Pacific Time, weekdays only.
_AMC_SUPPORT_PHONE = "805-389-1935"
_AMC_SUPPORT_HOURS = "Monday–Friday, 8:00 AM – 5:00 PM PST"
_AMC_SUPPORT_URL = "https://www.a-m-c.com/support/"


SAFETY_FIRST_RESPONSE = f"""**⚠ Safety first — before anything else:**

1. **Remove power immediately.** Open the main disconnect / e-stop for the drive's DC bus and AC supply. Do NOT rely on the drive's enable input — physically open the upstream breaker or disconnect.
2. **Stay clear of the unit.** A drive that has smoked, sparked, or ruptured may have damaged DC-bus capacitors. Those can hold lethal voltage for several minutes even after the supply is off. Don't touch the drive, its terminals, or the motor leads until the bus has bled down (typically 5+ minutes; longer if no bleed resistor).
3. **Ventilate the area.** Burned electronics release toxic smoke — especially from electrolytic capacitors and potting compound. Get people out of the immediate area until the smoke clears.
4. **Make sure no one is injured.** If anyone was shocked, hit by fragments, or inhaled a significant amount of smoke, treat that as the priority and call emergency services first.
5. **Do NOT re-apply power** to investigate. A drive that has already failed will typically fail worse on the second power-up — sometimes violently. Leave it off until it's been inspected.

---

**Contact AMC directly — a live person can help faster than I can here:**

- **Phone:** {_AMC_SUPPORT_PHONE}
- **Hours:** {_AMC_SUPPORT_HOURS}
- **Online:** {_AMC_SUPPORT_URL}

If this happened during business hours, calling is the fastest path — AMC can start an RMA and talk you through what to preserve (the drive, the motor wiring, any fault-log data) for failure analysis.

---

**Once the area is safe and power is off**, I can help you work through what likely went wrong and what to collect for the RMA. To do that, please share:

- The **drive part number** (e.g. `AZBH10A4`, `FE060-25-EM`, `DPCANIE-030A400`)
- What the system was doing at the moment of failure (commanded move, idle, powering up, regen decel, etc.)
- Any fault codes that were displayed before or at the moment of the event
- The DC bus voltage and motor model if you have them handy

With that, I can narrow down the likely failure mode (shorted output stage, bus over-voltage on regen, reverse-polarity wiring, capacitor failure, etc.) and tell you exactly what AMC will want in the RMA package."""


def is_safety_critical(message: str) -> bool:
    """Return True if the message contains hazard language.

    Errs permissive: a false positive (extra safety steps on a benign
    question) is annoying. A false negative (bot chirpily asking
    "which drive?" when something is on fire) is the failure mode we
    can't afford.
    """
    if not message:
        return False
    for pattern in _HAZARD_PATTERNS:
        if pattern.search(message):
            return True
    return False


__all__ = [
    "is_safety_critical",
    "SAFETY_FIRST_RESPONSE",
]
