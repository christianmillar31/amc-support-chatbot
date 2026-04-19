from __future__ import annotations
"""
Drive lookup engine — powered by CM Servo Info.csv.

Maps any AMC drive part number (SKU) to:
  - Drive family (FlexPro, DigiFlex Performance, AxCent, Classic)
  - Form factor (Panel Mount, PCB Mount, Vehicle Mount, Machine Embedded, Development Board)
  - Network communication type (EtherCAT, CANopen, Modbus RTU|RS-485/232, etc.)
  - Correct communication manual filename
  - Correct hardware installation manual filename
"""

import csv
import re
from app.config import BASE_DIR
from app.support_catalog import (
    get_support_catalog_row,
    load_support_catalog,
    normalize_lookup_sku,
    resolve_datasheet_sku,
)


# ---------------------------------------------------------------------------
# CSV → lookup table
# ---------------------------------------------------------------------------

# Column indices in CM Servo Info.csv
COL_TITLE = 0       # Product name
COL_SKU = 3         # Part number / SKU
COL_DC_RANGE = 7    # e.g. "20 - 80" (may be Excel-corrupted like "Oct-36")
COL_AC_RANGE = 8    # e.g. "90 - 264"
COL_STATUS = 9      # Active / Reserved / Discontinued
COL_FAMILY = 10     # FlexPro, DigiFlex Performance, AxCent, Classic
COL_FORM = 11       # Panel Mount, PCB Mount, Vehicle Mount, Machine Embedded, Development Board
COL_SERIES = 12     # e.g. "AZ_10V_Brushless"
COL_CURRENT_CONT = 13  # Continuous current (A)
COL_CURRENT_PEAK = 14  # Peak current (A)
COL_CONTROL_CMD = 19   # e.g. "±10 V Analog", "PWM and Direction"
COL_OPERATING_MODE = 21  # e.g. "Current|Duty Cycle (Open Loop)|Velocity"
COL_MOTOR_TYPE = 22    # e.g. "Three Phase (Brushless)|Single Phase (Brushed)"
COL_NETWORK = 25    # EtherCAT, CANopen, Modbus RTU|RS-485/232, etc.
COL_DC_VDC = 51     # pipe-separated canonical DC list, e.g. "24|36|48|60|72"

# In-memory lookup: sku_upper -> {family, form_factor, network, title, plus spec fields}
_DRIVE_DB: dict[str, dict] = {}
def _normalize_family(raw: str) -> str:
    """Strip status suffixes like '(Discontinued)', '(Reserved)'."""
    raw = raw.strip()
    for suffix in ["(Discontinued)", "(Reserved)"]:
        raw = raw.replace(suffix, "").strip()
    return raw

def _infer_network_from_sku(sku: str, family: str) -> str:
    """Infer network protocol from SKU naming convention when CSV field is empty.

    DigiFlex Performance naming:
      DP"CAN"  = CANopen        DP"R" = Serial (RS-485/232)
      DP"EAN"  = EtherCAT       DP"M" = Modbus RTU
      DP"P"    = POWERLINK      DZ"X" prefix = Extended environment
      DVC      = CANopen (Vehicle)

    FlexPro naming:
      -EM  = EtherCAT    -IPM = Ethernet/IP
      -RM  = Serial       -CM  = CANopen

    AxCent: Always analog/PWM (no network)
    """
    s = sku.upper()

    if family == "FlexPro":
        if s.endswith("-EM") or "-EM" in s:
            return "EtherCAT"
        elif s.endswith("-IPM") or "-IPM" in s:
            return "Ethernet/IP"
        elif s.endswith("-RM") or "-RM" in s:
            return "RS-485/232"
        elif s.endswith("-CM") or "-CM" in s:
            return "CANopen"

    elif "DigiFlex" in family:
        # Check for specific protocol indicators in the SKU prefix
        if s.startswith("DVC"):
            return "CANopen"
        # After the family prefix (DP/DZ/DV/DX), check the next letters
        # Remove family prefix to get the protocol section
        prefix = ""
        for p in ["DZXC", "DZX", "DZC", "DZS", "DZE", "DZP", "DZM", "DZ",
                   "DPC", "DPE", "DPM", "DPP", "DPQ", "DPR", "DP",
                   "DVC", "DV", "DX"]:
            if s.startswith(p):
                prefix = p
                break

        if "CAN" in prefix or prefix in ("DPC", "DZXC", "DZC", "DVC"):
            return "CANopen"
        elif "EAN" in s[:8] or prefix in ("DPE", "DZE"):
            return "EtherCAT"
        elif prefix in ("DPR", "DZR") or "RAL" in s[:8] or "RAN" in s[:8] or "RAH" in s[:8]:
            return "Modbus RTU|RS-485/232"
        elif prefix in ("DPM", "DZM"):
            return "Modbus RTU|RS-485/232"
        elif prefix in ("DPP", "DZP"):
            return "POWERLINK"
        elif prefix in ("DPS", "DZS"):
            return "Modbus RTU|RS-485/232"
        elif prefix in ("DPQ",):
            return "SynqNet"

    elif family == "AxCent":
        return "None (analog/PWM)"

    elif family == "Classic":
        return "None (analog)"

    return ""


def _load_csv():
    """Load the CSV into memory once."""
    global _DRIVE_DB
    if _DRIVE_DB:
        return

    csv_path = BASE_DIR / "CM Servo Info.csv"
    if not csv_path.exists():
        print(f"WARNING: Drive database not found at {csv_path}")
        return

    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)  # skip header
        for row in reader:
            if len(row) <= COL_NETWORK:
                continue
            sku = row[COL_SKU].strip()
            family = row[COL_FAMILY].strip()
            form_factor = row[COL_FORM].strip()
            network = row[COL_NETWORK].strip()
            title = row[COL_TITLE].strip()

            if not sku or not family:
                continue

            norm_family = _normalize_family(family)

            # Infer network from SKU if CSV field is empty
            if not network:
                network = _infer_network_from_sku(sku, norm_family)

            def _c(i: int) -> str:
                return row[i].strip() if i < len(row) else ""

            _DRIVE_DB[sku.upper()] = {
                "sku": sku,
                "title": title,
                "family": norm_family,
                "form_factor": form_factor,
                "network": network,
                "status": _c(COL_STATUS),
                "product_series": _c(COL_SERIES),
                "current_continuous_a": _c(COL_CURRENT_CONT),
                "current_peak_a": _c(COL_CURRENT_PEAK),
                "dc_supply_range": _clean_dc_range(_c(COL_DC_RANGE), _c(COL_DC_VDC)),
                "ac_supply_range": _c(COL_AC_RANGE),
                "control_command": _c(COL_CONTROL_CMD),
                "operating_mode": _c(COL_OPERATING_MODE),
                "motor_type": _c(COL_MOTOR_TYPE),
            }

    print(f"Loaded {len(_DRIVE_DB)} drives from CSV.")


def _clean_dc_range(raw_range: str, raw_vdc_list: str) -> str:
    """The CSV 'DC Supply Voltage Range' column can be Excel-corrupted.

    Examples of corruption: '10-36' becomes 'Oct-36', '10-80' becomes 'Oct-80'.
    Prefer the raw range when it doesn't match those patterns, otherwise
    reconstruct from the pipe-separated canonical list (e.g. '10|12|24|36' -> '10 - 36').
    """
    bad_patterns = ("Oct-", "Nov-", "Sep-", "Dec-", "Jan-", "Feb-", "Mar-", "Apr-", "May-", "Jun-", "Jul-", "Aug-")
    if raw_range and not raw_range.startswith(bad_patterns):
        return raw_range
    if raw_vdc_list:
        nums = [int(x) for x in raw_vdc_list.split("|") if x.strip().isdigit()]
        if nums:
            return f"{min(nums)} - {max(nums)}"
    return raw_range

# ---------------------------------------------------------------------------
# Comm manual routing
# ---------------------------------------------------------------------------

# FlexPro comm manuals (keyed by network type)
_FP_COMM_MAP = {
    "EtherCAT": "AMC_CommManual_FP_EtherCAT.pdf",
    "Ethernet/IP": "AMC_CommManual_EthernetIP_FP.pdf",
    "RS-485/232": "AMC_CommManual_FP_Serial.pdf",
    "CANopen": "AMC_CommManual_FP_CANopen.pdf",
}

# DigiFlex comm manuals
_DF_COMM_MAP = {
    "EtherCAT": "AMC_CommManual_EtherCAT.pdf",
    "CANopen": "AMC_CommManual_CANopen.pdf",
    "Modbus RTU|RS-485/232": None,  # ambiguous — could be Modbus or RS485
    "RS-485/232": "AMC_CommManual_RS485.pdf",
    "Ethernet POWERLINK|Ethernet|Modbus TCP": None,  # no specific manual
    "DxM Technology": None,
    "EtherCAT|DxM Technology": "AMC_CommManual_EtherCAT.pdf",
}


def _get_comm_manual(family: str, network: str) -> dict:
    """Return {manual, protocol, ambiguous} for a given family + network."""
    if family == "FlexPro":
        # FlexPro networks are clean single values
        manual = _FP_COMM_MAP.get(network)
        return {"manual": manual, "protocol": network, "ambiguous": False}

    elif family == "DigiFlex Performance":
        # Check exact match first
        if network in _DF_COMM_MAP:
            manual = _DF_COMM_MAP[network]
            if network == "Modbus RTU|RS-485/232":
                return {
                    "manual": None,
                    "protocol": "Serial (RS-485) or Modbus RTU",
                    "ambiguous": True,
                    "options": {
                        "Serial/RS-485": "AMC_CommManual_RS485.pdf",
                        "Modbus RTU": "AMC_CommManual_Modbus.pdf",
                    },
                }
            return {"manual": manual, "protocol": network, "ambiguous": False}
        # Partial match
        if "EtherCAT" in network:
            return {"manual": "AMC_CommManual_EtherCAT.pdf", "protocol": "EtherCAT", "ambiguous": False}
        if "CANopen" in network:
            return {"manual": "AMC_CommManual_CANopen.pdf", "protocol": "CANopen", "ambiguous": False}
        return {"manual": None, "protocol": network or "Unknown", "ambiguous": False}

    elif family == "AxCent":
        # AxCent drives have "None" for network — no comm manual
        return {"manual": None, "protocol": "None (analog/PWM)", "ambiguous": False}

    elif family == "Classic":
        return {"manual": None, "protocol": "None (analog)", "ambiguous": False}

    return {"manual": None, "protocol": network or "Unknown", "ambiguous": False}


# ---------------------------------------------------------------------------
# HW install manual routing
# ---------------------------------------------------------------------------

# Effective form factor: Machine Embedded and Development Board → PCB Mount
def _effective_form(form_factor: str) -> str:
    if form_factor in ("Machine Embedded", "Development Board"):
        return "PCB Mount"
    return form_factor


# HW manual lookup: (family, effective_form_factor, network_key) -> filename
# DigiFlex PCB has network-specific HW manuals; others don't
_HW_MAP = {
    # FlexPro — all form factors use PCB manual
    ("FlexPro", "PCB Mount"): "AMC_HWManual_FlexPro_PCB.pdf",
    ("FlexPro", "Panel Mount"): "AMC_HWManual_FlexPro_PCB.pdf",

    # AxCent
    ("AxCent", "PCB Mount"): "AMC_HWManual_AxCent_PCB.pdf",
    ("AxCent", "Panel Mount"): "AMC_HWManual_AxCent_Panel.pdf",
    ("AxCent", "Vehicle Mount"): "AMC_HWManual_AxCent_Vehicle.pdf",

    # DigiFlex Panel (network-specific)
    ("DigiFlex Performance", "Panel Mount", "CANopen"): "AMC_HWManual_DigiFlex_Panel_CANopen.pdf",
    ("DigiFlex Performance", "Panel Mount", "EtherCAT"): "AMC_HWManual_DigiFlex_Panel_EtherCAT.pdf",
    ("DigiFlex Performance", "Panel Mount", "Modbus RTU|RS-485/232"): "AMC_HWManual_DigiFlex_Panel_RS485-ModbusRTU.pdf",
    ("DigiFlex Performance", "Panel Mount", "None"): "AMC_HWManual_DigiFlex_Panel_CANopen.pdf",  # default

    # DigiFlex PCB (network-specific)
    ("DigiFlex Performance", "PCB Mount", "CANopen"): "AMC_HWManual_DigiFlex_PCB_CANopen.pdf",
    ("DigiFlex Performance", "PCB Mount", "Modbus RTU|RS-485/232"): "AMC_HWManual_DigiFlex_PCB_RS485-ModbusRTU.pdf",
    ("DigiFlex Performance", "PCB Mount", "DxM Technology"): "AMC_HWManual_DigiFlex_PCB_XEnv.pdf",
    ("DigiFlex Performance", "PCB Mount", "EtherCAT|DxM Technology"): "AMC_HWManual_DigiFlex_PCB_XEnv.pdf",
    ("DigiFlex Performance", "PCB Mount", "Ethernet POWERLINK|Ethernet|Modbus TCP"): "AMC_HWManual_DigiFlex_PCB_XEnv.pdf",
    ("DigiFlex Performance", "PCB Mount", "None"): "AMC_HWManual_DigiFlex_PCB_CANopen.pdf",  # default

    # DigiFlex Vehicle
    ("DigiFlex Performance", "Vehicle Mount"): "AMC_HWManual_DigiFlex_Vehicle.pdf",

    # Classic / Analog
    ("Classic", "Panel Mount"): "AMC_HWManual_AnalogDrives.pdf",
}


def _get_hw_manual(family: str, form_factor: str, network: str) -> str | None:
    """Return the HW installation manual filename for a given drive."""
    eff_form = _effective_form(form_factor)

    # Try network-specific key first (DigiFlex needs this)
    key3 = (family, eff_form, network)
    if key3 in _HW_MAP:
        return _HW_MAP[key3]

    # Try family + form factor only
    key2 = (family, eff_form)
    if key2 in _HW_MAP:
        return _HW_MAP[key2]

    # DigiFlex Panel fallback — try matching partial network
    if family == "DigiFlex Performance" and eff_form == "Panel Mount":
        if "EtherCAT" in network:
            return "AMC_HWManual_DigiFlex_Panel_EtherCAT.pdf"
        if "CANopen" in network:
            return "AMC_HWManual_DigiFlex_Panel_CANopen.pdf"
        if "Modbus" in network or "RS-485" in network:
            return "AMC_HWManual_DigiFlex_Panel_RS485-ModbusRTU.pdf"

    # DigiFlex PCB fallback
    if family == "DigiFlex Performance" and eff_form == "PCB Mount":
        if "CANopen" in network:
            return "AMC_HWManual_DigiFlex_PCB_CANopen.pdf"
        if "Modbus" in network or "RS-485" in network:
            return "AMC_HWManual_DigiFlex_PCB_RS485-ModbusRTU.pdf"
        # Everything else (EtherCAT, POWERLINK, DxM, etc.)
        return "AMC_HWManual_DigiFlex_PCB_XEnv.pdf"

    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def lookup_drive(part_number: str) -> dict | None:
    """
    Look up a drive by part number / SKU.
    Returns full info dict or None if not found.

    Only exact matches are allowed. Fuzzy substring matching was removed
    because it allowed fabricated SKUs like "AB25A20-10" to falsely match
    the real drive "25A20" (since "25A20" is a substring of "AB25A20-10").
    If the user has a typo, they must fix it — we won't guess.
    """
    _load_csv()
    load_support_catalog()
    requested = part_number.strip().upper()

    # Exact match only
    if requested in _DRIVE_DB:
        drive = _DRIVE_DB[requested]
        return _build_result(drive, requested_sku=requested, match_strategy="exact")

    normalized = normalize_lookup_sku(requested)
    if normalized != requested and normalized in _DRIVE_DB:
        drive = _DRIVE_DB[normalized]
        return _build_result(drive, requested_sku=requested, match_strategy="normalized_variant")

    return None


def _build_result(drive: dict, requested_sku: str | None = None, match_strategy: str = "exact") -> dict:
    """Build the full result dict from a drive DB entry."""
    family = drive["family"]
    form_factor = drive["form_factor"]
    network = drive["network"]
    requested_sku = requested_sku or drive["sku"]

    comm_info = _get_comm_manual(family, network)
    hw_manual = _get_hw_manual(family, form_factor, network)
    support = get_support_catalog_row(drive["sku"])

    return {
        "sku": drive["sku"],
        "requested_sku": requested_sku,
        "canonical_sku": drive["sku"],
        "normalized_sku": support.get("normalized_sku") or normalize_lookup_sku(drive["sku"]),
        "datasheet_sku": resolve_datasheet_sku(drive["sku"]),
        "alias_resolved": requested_sku != drive["sku"],
        "match_strategy": match_strategy,
        "title": drive["title"],
        "family": family,
        "form_factor": form_factor,
        "network": network,
        "comm_manual": comm_info.get("manual"),
        "comm_protocol": comm_info.get("protocol"),
        "comm_ambiguous": comm_info.get("ambiguous", False),
        "comm_options": comm_info.get("options"),
        "hw_manual": hw_manual,
        "site_status": support.get("site_status"),
        "site_category": support.get("category"),
        "support_bucket": support.get("support_bucket"),
        "recommended_next_action": support.get("recommended_next_action"),
        "site_url": support.get("url"),
    }


def search_drives(query: str) -> list[dict]:
    """
    Search for drives matching a query string.
    Searches SKU, title, family, form factor, and network fields.
    Returns up to 10 matching drives.
    """
    _load_csv()
    query_upper = query.upper()
    results = []

    for sku, drive in _DRIVE_DB.items():
        searchable = f"{sku} {drive['title'].upper()} {drive['family'].upper()} {drive['form_factor'].upper()} {drive['network'].upper()}"
        if query_upper in searchable:
            results.append(_build_result(drive))
            if len(results) >= 10:
                break

    return results


def detect_part_number(message: str) -> str | None:
    """
    Detect an AMC drive part number in a user message.
    Returns the detected part number string or None.

    Uses word-boundary matching so that fake SKUs like 'AB25A20-10' do NOT
    fuzzy-match real SKUs like '25A20' embedded within them. This is the
    same class of bug fixed in lookup_drive() and lookup_replacement().
    """
    _load_csv()
    msg_upper = message.upper()

    # Try to match known SKUs in the message with WORD BOUNDARIES
    # Sort by length descending so longer (more specific) matches win
    for sku in sorted(_DRIVE_DB.keys(), key=len, reverse=True):
        if len(sku) < 4:
            continue
        # Word-boundary match: SKU must be preceded/followed by non-alphanumeric
        # (or start/end of string). This prevents 'B25A20' matching inside 'AB25A20-10'.
        pattern = r"(?:^|[^A-Z0-9])" + re.escape(sku) + r"(?:[^A-Z0-9]|$)"
        if re.search(pattern, msg_upper):
            return sku

    # Fallback: regex patterns for AMC part numbers
    patterns = [
        r'\b(FE\d{3}-\d+-\w+)',      # FlexPro FE
        r'\b(FM\d{3}-\d+-\w+)',      # FlexPro FM
        r'\b(FD\d{3}-\d+-\w+)',      # FlexPro FD
        r'\b(FMP\d{3}-\d+-\w+)',     # FlexPro FMP
        r'\b(FX\w+-\d+-\w+)',        # FlexPro FX
        r'\b(FXM\d{3}-\d+-\w+)',     # FlexPro FXM (machine embedded)
        r'\b(DP\w+-\d+\w*)',         # DigiFlex Panel
        r'\b(DV\w+-\d+\w*)',         # DigiFlex
        r'\b(DZ\w+-\d+\w*)',         # DigiFlex PCB
        r'\b(DX\w+-\d+\w*)',         # DigiFlex
        r'\b(DVCNET-\d+-\d+)',       # DigiFlex DVC
        r'\b(AZ\w+\d+\w*)',          # AxCent
        r'\b(AZBH?\w+)',             # AxCent
    ]

    for pattern in patterns:
        match = re.search(pattern, message, re.IGNORECASE)
        if match:
            return match.group(1).upper()

    return None


# ---------------------------------------------------------------------------
# Retrofit / replacement mapping (discontinued → AxCent)
# ---------------------------------------------------------------------------

_RETROFIT_DB: list[dict] = []


def _load_retrofit_csv():
    """Load retrofit_mapping.csv into memory once."""
    global _RETROFIT_DB
    if _RETROFIT_DB:
        return

    csv_path = BASE_DIR / "retrofit_mapping.csv"
    if not csv_path.exists():
        print(f"WARNING: Retrofit mapping not found at {csv_path}")
        return

    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            _RETROFIT_DB.append({
                "discontinued": row["discontinued_model"].strip().upper(),
                "size": row["size"].strip(),
                "motor_type": row["motor_type"].strip(),
                "replacement_brushless": row["replacement_brushless"].strip(),
                "replacement_brushed_only": row["replacement_brushed_only"].strip(),
                "notes": row["notes"].strip(),
            })

    print(f"Loaded {len(_RETROFIT_DB)} retrofit mappings.")


def lookup_replacement(part_number: str) -> dict | None:
    """
    Look up the AxCent replacement for a discontinued analog drive.

    Returns a dict with replacement info, or None if no mapping exists.
    The part_number is matched against the base model (revision letters
    and ordering suffixes like -INV, -QD, -QDI are stripped).
    """
    _load_retrofit_csv()
    pn = part_number.strip().upper()

    # Strip revision letter (single letter at end before any dash suffix)
    # e.g. "12A8J-INV" → base "12A8", or "30A8K" → "30A8"
    # First remove ordering suffixes
    for suffix in ["-INV", "-QD", "-QDI", "-ANP"]:
        pn = pn.replace(suffix, "")

    # Try exact match first
    for entry in _RETROFIT_DB:
        if entry["discontinued"] == pn:
            return _format_retrofit_result(entry)

    # Try stripping trailing revision letter (single alpha char)
    if len(pn) > 3 and pn[-1].isalpha() and pn[-2].isdigit():
        base = pn[:-1]
        for entry in _RETROFIT_DB:
            if entry["discontinued"] == base:
                return _format_retrofit_result(entry)

    # No match. Do NOT do fuzzy substring matching — that was allowing
    # fabricated SKUs like "ABH25A20-10" to match "25A20" and return a bogus result.
    # Only exact matches (and stripped revision letters) are allowed.
    return None


def _format_retrofit_result(entry: dict) -> dict:
    """Format a retrofit mapping entry into a user-friendly result."""
    replacements = []
    if entry["replacement_brushless"]:
        if entry["motor_type"].startswith("Brushed"):
            replacements.append({
                "model": entry["replacement_brushless"],
                "modes": "Current, Voltage (brushless/brushed capable)",
            })
        elif entry["motor_type"].startswith("Brushless"):
            replacements.append({
                "model": entry["replacement_brushless"],
                "modes": "Current, Duty Cycle, Encoder Velocity" if "BE" in entry["discontinued"] or "BX" in entry["discontinued"] else "Current, Duty Cycle",
            })
    if entry["replacement_brushed_only"]:
        replacements.append({
            "model": entry["replacement_brushed_only"],
            "modes": "IR Compensation, Tachometer Velocity (brushed only)",
        })

    return {
        "discontinued_model": entry["discontinued"],
        "size": entry["size"],
        "motor_type": entry["motor_type"],
        "replacements": replacements,
        "notes": entry["notes"] if entry["notes"] else None,
    }


def get_all_replacements() -> list[dict]:
    """Return all retrofit mappings for reference."""
    _load_retrofit_csv()
    return [_format_retrofit_result(e) for e in _RETROFIT_DB]


def _friendly_network(network: str) -> str:
    """Convert raw CSV network values to user-friendly display labels."""
    if not network:
        return "Analog"
    mapping = {
        "Modbus RTU|RS-485/232": "Serial / Modbus",
        "RS-485/232": "Serial",
        "Ethernet POWERLINK|Ethernet|Modbus TCP": "POWERLINK",
        "EtherCAT|DxM Technology": "EtherCAT",
        "DxM Technology": "DxM",
        "None (analog/PWM)": "Analog/PWM",
        "None (analog)": "Analog",
    }
    return mapping.get(network, network)


# ---------------------------------------------------------------------------
# Canonical spec injection (authoritative facts from CSV)
# ---------------------------------------------------------------------------

def _format_spec_lines(drive: dict) -> list[str]:
    """Return a stable bulleted block of authoritative facts for one drive row."""
    sku = drive.get("sku", "")
    lines = [f"- SKU: {sku}"]

    def _add(label: str, value: str) -> None:
        value = (value or "").strip()
        if value:
            lines.append(f"- {label}: {value.replace('|', ', ')}")

    _add("Family", drive.get("family", ""))
    _add("Product Series", drive.get("product_series", ""))
    _add("Form Factor", drive.get("form_factor", ""))
    _add("Product Status", drive.get("status", ""))
    _add("Current Continuous (A)", drive.get("current_continuous_a", ""))
    _add("Current Peak (A)", drive.get("current_peak_a", ""))
    _add("DC Supply Voltage (VDC)", drive.get("dc_supply_range", ""))
    _add("AC Supply Voltage (VAC)", drive.get("ac_supply_range", ""))
    _add("Network Communication", drive.get("network", ""))
    _add("Control/Command", drive.get("control_command", ""))
    _add("Operating Mode", drive.get("operating_mode", ""))
    _add("Motor Type", drive.get("motor_type", ""))
    return lines


def get_canonical_spec_block(sku: str) -> str:
    """Return an authoritative spec block for a single resolved SKU, or ''.

    Reads the raw _DRIVE_DB row so we keep every spec field, not the trimmed
    shape returned by lookup_drive/_build_result.
    """
    _load_csv()
    key = (sku or "").strip().upper()
    drive = _DRIVE_DB.get(key)
    if not drive:
        # Try normalized alias (handles -10 variants etc.)
        alias = normalize_lookup_sku(key)
        drive = _DRIVE_DB.get(alias)
        if not drive:
            return ""
    lines = _format_spec_lines(drive)
    header = (
        f"=== CANONICAL SPEC FOR {drive.get('sku', sku).upper()} "
        "(authoritative from CM Servo Info.csv; do not paraphrase numeric values) ==="
    )
    return header + "\n" + "\n".join(lines)


# Family/series match hints — keep narrow so we don't pull 600 rows into every prompt.
# Order matters: longer/more specific prefixes first.
_FAMILY_KEYWORDS = [
    # (keyword, lambda drive -> bool)
    ("axcent pcb", lambda d: d["family"] == "AxCent" and _effective_form(d["form_factor"]) == "PCB Mount"),
    ("axcent panel", lambda d: d["family"] == "AxCent" and _effective_form(d["form_factor"]) == "Panel Mount"),
    ("axcent vehicle", lambda d: d["family"] == "AxCent" and _effective_form(d["form_factor"]) == "Vehicle Mount"),
    ("azbh", lambda d: d["sku"].upper().startswith("AZBH")),
    ("azbe", lambda d: d["sku"].upper().startswith("AZBE")),
    ("azbdc", lambda d: d["sku"].upper().startswith("AZBDC")),
    ("azbd ", lambda d: d["sku"].upper().startswith("AZBD") and not d["sku"].upper().startswith("AZBDC")),
    ("azb ", lambda d: d["sku"].upper().startswith("AZB") and not d["sku"].upper().startswith(("AZBH", "AZBE", "AZBD"))),
    ("flexpro pcb", lambda d: d["family"] == "FlexPro" and _effective_form(d["form_factor"]) == "PCB Mount"),
    ("flexpro panel", lambda d: d["family"] == "FlexPro" and _effective_form(d["form_factor"]) == "Panel Mount"),
    ("digiflex panel", lambda d: d["family"] == "DigiFlex Performance" and _effective_form(d["form_factor"]) == "Panel Mount"),
    ("digiflex pcb", lambda d: d["family"] == "DigiFlex Performance" and _effective_form(d["form_factor"]) == "PCB Mount"),
    ("classic analog", lambda d: d["family"] == "Classic"),
    ("analog drive", lambda d: d["family"] in {"Classic", "AxCent"}),
    ("axcent", lambda d: d["family"] == "AxCent"),
    ("digiflex", lambda d: d["family"] == "DigiFlex Performance"),
    ("flexpro", lambda d: d["family"] == "FlexPro"),
]

# Drop less-specific keywords when a more-specific one is already present to
# avoid emitting multiple tables that mostly overlap.
_KEYWORD_SUPERSEDES = {
    "axcent pcb": {"axcent", "azb ", "azbh", "azbe", "azbd ", "azbdc"},
    "axcent panel": {"axcent"},
    "axcent vehicle": {"axcent"},
    "azbh": {"axcent", "azb "},
    "azbe": {"axcent", "azb "},
    "azbd ": {"axcent", "azb "},
    "azbdc": {"axcent", "azb "},
    "azb ": {"axcent"},
    "flexpro pcb": {"flexpro"},
    "flexpro panel": {"flexpro"},
    "digiflex panel": {"digiflex"},
    "digiflex pcb": {"digiflex"},
    "classic analog": {"analog drive"},
}


def detect_family_keywords(message: str) -> list[str]:
    """Return family/series keywords mentioned in the message, dropping less-
    specific ones when a more-specific one already covers them.
    """
    text = (message or "").lower()
    hit: list[str] = []
    for kw, _ in _FAMILY_KEYWORDS:
        if kw.strip() in text and kw not in hit:
            hit.append(kw)

    # Drop superseded keywords
    superseded: set[str] = set()
    for specific in hit:
        for less_specific in _KEYWORD_SUPERSEDES.get(specific, set()):
            superseded.add(less_specific)
    return [kw for kw in hit if kw not in superseded]


def get_family_spec_table(keyword: str, limit: int = 12) -> str:
    """Return a compact table of canonical facts for drives matching a family
    keyword (e.g. "axcent pcb", "azbh", "analog drives"). Capped at `limit` rows
    to keep the token budget predictable.
    """
    _load_csv()
    predicate = None
    for kw, pred in _FAMILY_KEYWORDS:
        if kw == keyword:
            predicate = pred
            break
    if predicate is None:
        return ""

    matches: list[dict] = []
    seen: set[str] = set()
    for sku, drive in _DRIVE_DB.items():
        if sku in seen:
            continue
        try:
            if predicate(drive):
                matches.append(drive)
                seen.add(sku)
        except KeyError:
            continue

    if not matches:
        return ""

    # Prefer Active drives, then alphabetical
    matches.sort(key=lambda d: (0 if d.get("status", "").lower() == "active" else 1, d.get("sku", "")))
    rows = matches[:limit]

    header = (
        f"=== CANONICAL FAMILY TABLE — {keyword.upper()} "
        f"(showing {len(rows)} of {len(matches)} rows; authoritative from CM Servo Info.csv; "
        "do not paraphrase numeric values or invent capabilities not listed here) ==="
    )

    line_headers = ["SKU", "Family", "Form Factor", "Cont (A)", "Peak (A)", "DC Supply (VDC)", "Control", "Operating Mode", "Status"]
    lines = [" | ".join(line_headers), " | ".join(["---"] * len(line_headers))]
    for d in rows:
        lines.append(" | ".join([
            d.get("sku", ""),
            d.get("family", ""),
            d.get("form_factor", ""),
            d.get("current_continuous_a", "") or "-",
            d.get("current_peak_a", "") or "-",
            d.get("dc_supply_range", "") or "-",
            (d.get("control_command", "") or "-").replace("|", ", "),
            (d.get("operating_mode", "") or "-").replace("|", ", "),
            d.get("status", "") or "-",
        ]))
    trailing = ""
    if len(matches) > limit:
        remaining = [d.get("sku", "") for d in matches[limit:]]
        trailing = f"\n(Additional {keyword} SKUs not shown in table: {', '.join(remaining[:20])}{'...' if len(remaining) > 20 else ''})"
    return header + "\n" + "\n".join(lines) + trailing


def build_canonical_context(message: str, detected_skus: list[str] | None = None, *, family_limit: int = 12) -> str:
    """Top-level helper used by chat/support_core.

    Builds the combined canonical spec block for (a) every SKU explicitly detected
    in the message and (b) every family keyword mentioned. Empty string if nothing
    matches so callers can safely concatenate.
    """
    _load_csv()
    parts: list[str] = []
    emitted_skus: set[str] = set()
    for sku in detected_skus or []:
        block = get_canonical_spec_block(sku)
        if block:
            parts.append(block)
            # avoid re-listing this exact SKU via family-table too
            drive = lookup_drive(sku)
            if drive:
                emitted_skus.add(drive["sku"].upper())

    for kw in detect_family_keywords(message):
        table = get_family_spec_table(kw, limit=family_limit)
        if table:
            parts.append(table)

    return "\n\n".join(parts)


def get_all_drives() -> list[dict]:
    """Return all unique drives from the CSV for the frontend autocomplete dropdown."""
    _load_csv()
    load_support_catalog()
    drives = []
    seen = set()
    for sku, drive in _DRIVE_DB.items():
        if sku in seen:
            continue
        seen.add(sku)
        support = get_support_catalog_row(sku)
        drives.append({
            "sku": sku,
            "canonical_sku": sku,
            "normalized_sku": support.get("normalized_sku") or normalize_lookup_sku(sku),
            "datasheet_sku": resolve_datasheet_sku(sku),
            "title": drive["title"],
            "family": drive["family"],
            "form_factor": drive["form_factor"],
            "network": _friendly_network(drive["network"]),
            "site_category": support.get("category"),
            "site_status": support.get("site_status"),
            "support_bucket": support.get("support_bucket"),
            "recommended_next_action": support.get("recommended_next_action"),
            "site_url": support.get("url"),
        })
    # Sort by family then SKU
    drives.sort(key=lambda d: (d["family"], d["sku"]))
    return drives
