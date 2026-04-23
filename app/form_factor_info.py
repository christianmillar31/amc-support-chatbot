"""Authoritative form-factor descriptions for AMC servo drives.

The CSV labels drives with a short ``form_factor`` bucket (PCB Mount,
Machine Embedded, Panel Mount, Development Board, Vehicle Mount). The
labels alone don't convey what the customer actually receives, so
answer synthesis frequently gets it wrong — most common failures in
the field:

  - calling an FM drive "Panel Mount" (it's Machine Embedded: a compact
    card with pre-built connectors, drops into an OEM chassis)
  - calling an FE drive "compact with connectors" (it's PCB Mount: a
    bare PCB with solder pads; the customer has to build or buy a
    Development Board / interface card to get signals in and out)
  - not distinguishing Panel Mount (bigger box, wired enclosure) from
    Machine Embedded (compact card)

This module holds the canonical form-factor descriptions, grounded in
what AMC's own brochures and HW manuals actually say:

  - "Machine Embedded - FM060-10-CM/RM" / "PCB Mount - FE100-50-EM w/
    Development Board"                 (AMC_Brochure_Products-and-
    Capabilities.pdf p.9)
  - "FM Machine Embedded Assemblies"   (AMC_HWManual_FlexPro_PCB.pdf
    p.39)
  - "FlexPro development board assembly" (FD series pairs with FE for
    prototyping) (AMC_HWManual_FlexPro_PCB.pdf p.27)

The text below is phrased to bind Claude via rule 15 (verbatim from
canonical facts) so cross-drive / "what's X's form factor?" questions
answer consistently with what a customer actually sees in the box.
"""
from __future__ import annotations


# Raw CSV form-factor label -> authoritative description
FORM_FACTOR_DESCRIPTIONS: dict[str, str] = {
    "Machine Embedded": (
        "Compact PCB card with pre-built connectors, designed to be embedded "
        "into an OEM machine chassis. Ready-to-use connector interface — "
        "no customer-built interface card required. Most FlexPro FM-series "
        "and FXM-series drives are Machine Embedded."
    ),
    "PCB Mount": (
        "Bare PCB with solder pads / header pins for signal and power "
        "connections. NO pre-built connectors. The customer is expected "
        "to build their own connector/interface card (or use AMC's "
        "Development Board for prototyping and evaluation). Most FlexPro "
        "FE-series, FXE-series, and DigiFlex Performance PCB-mount drives "
        "are PCB Mount. Compact form factor intended for tight OEM "
        "integration where the customer controls the interface layout."
    ),
    "Panel Mount": (
        "Full-enclosure drive with mounting ears for installation onto a "
        "panel or cabinet backplate. Pluggable screw-terminal or D-sub "
        "connectors. Larger footprint than Machine Embedded / PCB Mount, "
        "but easier to wire up and service. Typical for AxCent AB-series, "
        "DigiFlex Performance Panel-mount drives, and the FlexPro FMP "
        "sub-series (FMP = FlexPro Machine Panel). NOTE: FM-series drives "
        "without the 'P' suffix are Machine Embedded, not Panel Mount."
    ),
    "Development Board": (
        "Evaluation / prototyping platform. Not a production drive — an "
        "interface board that pairs with PCB-mount drives (typically the "
        "FlexPro FE-series) to expose all signals via standard connectors "
        "and I/O headers. Used during design-in before the OEM builds "
        "their own target interface board. FlexPro FD-series are "
        "Development Boards."
    ),
    "Vehicle Mount": (
        "Ruggedized servo drive purpose-built for mobile / electric "
        "vehicle applications. Hardened against vibration, shock, and "
        "wide temperature range. AMC M/V-series vehicle mount motor "
        "controllers fall in this bucket, as do the DigiFlex Performance "
        "Vehicle-mount (DPV/DVC) drives."
    ),
}


def describe_form_factor(form_factor: str) -> str:
    """Return the AMC-authoritative description for a form-factor label.

    Unknown / empty labels return an empty string so the caller can
    skip emitting the line.
    """
    if not form_factor:
        return ""
    return FORM_FACTOR_DESCRIPTIONS.get(form_factor.strip(), "")
