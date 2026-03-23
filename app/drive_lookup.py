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
from pathlib import Path
from app.config import BASE_DIR


# ---------------------------------------------------------------------------
# CSV → lookup table
# ---------------------------------------------------------------------------

# Column indices in CM Servo Info.csv
COL_TITLE = 0       # Product name
COL_SKU = 3         # Part number / SKU
COL_FAMILY = 10     # FlexPro, DigiFlex Performance, AxCent, Classic
COL_FORM = 11       # Panel Mount, PCB Mount, Vehicle Mount, Machine Embedded, Development Board
COL_NETWORK = 25    # EtherCAT, CANopen, Modbus RTU|RS-485/232, etc.

# In-memory lookup: sku_upper -> {family, form_factor, network, title}
_DRIVE_DB: dict[str, dict] = {}


def _normalize_family(raw: str) -> str:
    """Strip status suffixes like '(Discontinued)', '(Reserved)'."""
    raw = raw.strip()
    for suffix in ["(Discontinued)", "(Reserved)"]:
        raw = raw.replace(suffix, "").strip()
    return raw


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

            _DRIVE_DB[sku.upper()] = {
                "sku": sku,
                "title": title,
                "family": _normalize_family(family),
                "form_factor": form_factor,
                "network": network,
            }

    print(f"Loaded {len(_DRIVE_DB)} drives from CSV.")


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
    """
    _load_csv()
    pn = part_number.strip().upper()

    # Exact match first
    if pn in _DRIVE_DB:
        drive = _DRIVE_DB[pn]
        return _build_result(drive)

    # Fuzzy match: try finding the SKU that contains the input or vice versa
    for sku, drive in _DRIVE_DB.items():
        if pn in sku or sku in pn:
            return _build_result(drive)

    return None


def _build_result(drive: dict) -> dict:
    """Build the full result dict from a drive DB entry."""
    family = drive["family"]
    form_factor = drive["form_factor"]
    network = drive["network"]

    comm_info = _get_comm_manual(family, network)
    hw_manual = _get_hw_manual(family, form_factor, network)

    return {
        "sku": drive["sku"],
        "title": drive["title"],
        "family": family,
        "form_factor": form_factor,
        "network": network,
        "comm_manual": comm_info.get("manual"),
        "comm_protocol": comm_info.get("protocol"),
        "comm_ambiguous": comm_info.get("ambiguous", False),
        "comm_options": comm_info.get("options"),
        "hw_manual": hw_manual,
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
    """
    _load_csv()
    msg_upper = message.upper()

    # Try to match known SKUs in the message
    # Sort by length descending so longer (more specific) matches win
    for sku in sorted(_DRIVE_DB.keys(), key=len, reverse=True):
        if sku in msg_upper and len(sku) >= 4:  # minimum 4 chars to avoid false matches
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
