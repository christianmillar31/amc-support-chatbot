#!/usr/bin/env python3
"""
Build `site_data/pdf_url_map.json` — the canonical mapping from local PDF
filename to AMC-hosted web URL.

Sources of truth, in order of preference:

  1. Curated base map (hand-verified /d/?h=... hashed URLs transcribed from
     `static/index.html` AMC_DOWNLOAD_LINKS). These are the source of truth for
     manuals, app notes, product notes, software docs, and white papers.
  2. `site_data/retrofit_map.json` → 2 retrofit product notes (already carries
     site_url + local_pdf pairs).
  3. Datasheet heuristic: `AMC_Datasheet_{SKU}.pdf` → `/product/{sku}/` resolved
     through `support_catalog.resolve_datasheet_sku()` so -10 variants etc. route
     to the canonical base datasheet on the AMC site.
  4. `site_data/amc_reserved_discontinued.json` → classic analog HW manual.

Run:
    python scripts/build_pdf_url_map.py              # write the JSON
    python scripts/build_pdf_url_map.py --report     # print coverage stats
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.support_catalog import load_support_catalog, resolve_datasheet_sku  # noqa: E402


SITE_DATA = ROOT / "site_data"
BASE_URL = "https://www.a-m-c.com"


# Curated base map — mirror of static/index.html AMC_DOWNLOAD_LINKS. These URLs
# have been hand-verified to point at the real hashed /d/?h=... endpoints.
CURATED_MAP: dict[str, str] = {
    # Hardware Manuals
    "AMC_HWManual_FlexPro_PCB.pdf": "https://www.a-m-c.com/d/?h=e3578e0",
    "AMC_HWManual_AxCent_Panel.pdf": "https://www.a-m-c.com/d/?h=0c8c5da",
    "AMC_HWManual_AxCent_PCB.pdf": "https://www.a-m-c.com/d/?h=0d06fb9",
    "AMC_HWManual_AxCent_PCB_XEnv.pdf": "https://www.a-m-c.com/d/?h=66e21be",
    "AMC_HWManual_AxCent_Vehicle.pdf": "https://www.a-m-c.com/d/?h=7b18257",
    "AMC_HWManual_DigiFlex_Panel_CANopen.pdf": "https://www.a-m-c.com/d/?h=c4ee2ea",
    "AMC_HWManual_DigiFlex_Panel_EtherCAT.pdf": "https://www.a-m-c.com/d/?h=9423575",
    "AMC_HWManual_DigiFlex_Panel_ClickMove.pdf": "https://www.a-m-c.com/d/?h=3ecf435",
    "AMC_HWManual_DigiFlex_Panel_POWERLINK.pdf": "https://www.a-m-c.com/d/?h=5698335",
    "AMC_HWManual_DigiFlex_Panel_RS485-ModbusRTU.pdf": "https://www.a-m-c.com/d/?h=ec33542",
    "AMC_HWManual_DigiFlex_Vehicle.pdf": "https://www.a-m-c.com/d/?h=4504438",
    "AMC_HWManual_DigiFlex_PCB_RS485-ModbusRTU.pdf": "https://www.a-m-c.com/d/?h=413948b",
    "AMC_HWManual_DigiFlex_PCB_XEnv.pdf": "https://www.a-m-c.com/d/?h=9c58280",
    "AMC_HWManual_DigiFlex_PCB_CANopen.pdf": "https://www.a-m-c.com/d/?h=c4ee2ea",
    "AMC_HWManual_AnalogDrives.pdf": "https://www.a-m-c.com/d/?h=6e424d0",
    "AMC_HWManual_Analog_Panel.pdf": "https://www.a-m-c.com/wp-content/uploads/support/reserved/AMC_HWManual_Analog_Panel.pdf",

    # Communication Manuals
    "AMC_CommManual_CANopen.pdf": "https://www.a-m-c.com/d/?h=ff4cde0",
    "AMC_CommManual_EtherCAT.pdf": "https://www.a-m-c.com/d/?h=49e2c53",
    "AMC_CommManual_Ethernet_DP.pdf": "https://www.a-m-c.com/d/?h=723c803",
    "AMC_CommManual_Modbus.pdf": "https://www.a-m-c.com/d/?h=5972ee2",
    "AMC_CommManual_POWERLINK.pdf": "https://www.a-m-c.com/d/?h=686eb22",
    "AMC_CommManual_RS485.pdf": "https://www.a-m-c.com/d/?h=b52ae92",
    "AMC_CommManual_FP_EtherCAT.pdf": "https://www.a-m-c.com/d/?h=cc969e2",
    "AMC_CommManual_FP_CANopen.pdf": "https://www.a-m-c.com/d/?h=05027ca",
    "AMC_CommManual_EthernetIP_FP.pdf": "https://www.a-m-c.com/d/?h=c9ae73b",
    "AMC_CommManual_FP_Serial.pdf": "https://www.a-m-c.com/d/?h=5eb81c6",

    # Software
    "AMC_SW_QuickRef_ClickMove.pdf": "https://www.a-m-c.com/d/?h=335b087",
    "AMC_SW_QuickRef_DriveWare.pdf": "https://www.a-m-c.com/d/?h=bee4894",
    "AMC_SW_Manual_DriveWare.pdf": "https://www.a-m-c.com/d/?h=ee9f2a0",
    "AMC_SW_Manual_ACE.pdf": "https://www.a-m-c.com/d/?h=51d0c80",
    "AMC_SW_QuickRef_ACE.pdf": "https://www.a-m-c.com/d/?h=37134f5",
    "AMC_SW_ReleaseNotes_ACE.pdf": "https://www.a-m-c.com/d/?h=c8f66bc",

    # Product Notes
    "AMC_ProductNote_AxCent_Retrofit_Small.pdf": "https://www.a-m-c.com/d/?h=ab0a26b",
    "AMC_ProductNote_AxCent_Retrofit_Large.pdf": "https://www.a-m-c.com/d/?h=1023aaa",
    "AMC_ProductNote_Product_Identification.pdf": "https://www.a-m-c.com/d/?h=61f55c7",
    "AMC_ProductNote_FlexPro_EthernetIP_AOI.pdf": "https://www.a-m-c.com/d/?h=2fbf70a",
    "AMC_ProductNote_FlexPro_Wiring.pdf": "https://www.a-m-c.com/d/?h=d2932f5",
    "AMC_ProductNote_Heatsink.pdf": "https://www.a-m-c.com/d/?h=6066055",

    # White Papers
    "AMC_WhitePaper_EtherCAT.pdf": "https://www.a-m-c.com/d/?h=f2f42fd",
    "AMC_WhitePaper_Visual_Programming.pdf": "https://www.a-m-c.com/d/?h=59deabe",
}


def _build_app_note_map() -> dict[str, str]:
    """AppNote URLs follow a stable pattern in the curated map; build from there."""
    # AppNote IDs 000..062 (non-contiguous — some IDs like 019-022, 025, 031-033 don't exist)
    # The curated /d/?h=... hashes are authoritative; keep them verbatim from the JS table.
    return {
        "AMC_AppNote_000.pdf": "https://www.a-m-c.com/d/?h=a5c5725",
        "AMC_AppNote_001.pdf": "https://www.a-m-c.com/d/?h=a71d956",
        "AMC_AppNote_002.pdf": "https://www.a-m-c.com/d/?h=9b16c90",
        "AMC_AppNote_003.pdf": "https://www.a-m-c.com/d/?h=cc2b410",
        "AMC_AppNote_004.pdf": "https://www.a-m-c.com/d/?h=adc9a66",
        "AMC_AppNote_005.pdf": "https://www.a-m-c.com/d/?h=75cdf31",
        "AMC_AppNote_006.pdf": "https://www.a-m-c.com/d/?h=e7d3879",
        "AMC_AppNote_007.pdf": "https://www.a-m-c.com/d/?h=7d76027",
        "AMC_AppNote_008.pdf": "https://www.a-m-c.com/d/?h=3620fb9",
        "AMC_AppNote_009.pdf": "https://www.a-m-c.com/d/?h=4562392",
        "AMC_AppNote_010.pdf": "https://www.a-m-c.com/d/?h=ded5d9b",
        "AMC_AppNote_011.pdf": "https://www.a-m-c.com/d/?h=c94b6ce",
        "AMC_AppNote_012.pdf": "https://www.a-m-c.com/d/?h=7e53a03",
        "AMC_AppNote_013.pdf": "https://www.a-m-c.com/d/?h=56816b0",
        "AMC_AppNote_014.pdf": "https://www.a-m-c.com/d/?h=e7197a0",
        "AMC_AppNote_015.pdf": "https://www.a-m-c.com/d/?h=7a79994",
        "AMC_AppNote_016.pdf": "https://www.a-m-c.com/d/?h=f473e90",
        "AMC_AppNote_017.pdf": "https://www.a-m-c.com/d/?h=1d499b5",
        "AMC_AppNote_018.pdf": "https://www.a-m-c.com/d/?h=e08241e",
        "AMC_AppNote_023.pdf": "https://www.a-m-c.com/d/?h=46f6236",
        "AMC_AppNote_024.pdf": "https://www.a-m-c.com/d/?h=63f4b3b",
        "AMC_AppNote_026.pdf": "https://www.a-m-c.com/d/?h=eda8dd0",
        "AMC_AppNote_027.pdf": "https://www.a-m-c.com/d/?h=684b42e",
        "AMC_AppNote_028.pdf": "https://www.a-m-c.com/d/?h=23f397a",
        "AMC_AppNote_029.pdf": "https://www.a-m-c.com/d/?h=89cc5c9",
        "AMC_AppNote_030.pdf": "https://www.a-m-c.com/d/?h=c4e3668",
        "AMC_AppNote_034.pdf": "https://www.a-m-c.com/d/?h=9b3d140",
        "AMC_AppNote_035.pdf": "https://www.a-m-c.com/d/?h=fdfe084",
        "AMC_AppNote_036.pdf": "https://www.a-m-c.com/d/?h=2a01470",
        "AMC_AppNote_037.pdf": "https://www.a-m-c.com/d/?h=b422f5f",
        "AMC_AppNote_038.pdf": "https://www.a-m-c.com/d/?h=69acb19",
        "AMC_AppNote_039.pdf": "https://www.a-m-c.com/d/?h=75e937f",
        "AMC_AppNote_040.pdf": "https://www.a-m-c.com/d/?h=a7919bd",
        "AMC_AppNote_041.pdf": "https://www.a-m-c.com/d/?h=d75a624",
        "AMC_AppNote_042.pdf": "https://www.a-m-c.com/d/?h=a5fb646",
        "AMC_AppNote_043.pdf": "https://www.a-m-c.com/d/?h=80699bf",
        "AMC_AppNote_044.pdf": "https://www.a-m-c.com/d/?h=5093d2c",
        "AMC_AppNote_045.pdf": "https://www.a-m-c.com/d/?h=43855bd",
        "AMC_AppNote_046.pdf": "https://www.a-m-c.com/d/?h=4ff7bef",
        "AMC_AppNote_047.pdf": "https://www.a-m-c.com/d/?h=f6efb00",
        "AMC_AppNote_048.pdf": "https://www.a-m-c.com/d/?h=58e5692",
        "AMC_AppNote_049.pdf": "https://www.a-m-c.com/d/?h=08200f9",
        "AMC_AppNote_050.pdf": "https://www.a-m-c.com/d/?h=e262761",
        "AMC_AppNote_051.pdf": "https://www.a-m-c.com/d/?h=d8fa298",
        "AMC_AppNote_052.pdf": "https://www.a-m-c.com/d/?h=b092713",
        "AMC_AppNote_053.pdf": "https://www.a-m-c.com/d/?h=1d20e37",
        "AMC_AppNote_054.pdf": "https://www.a-m-c.com/d/?h=9d3cb85",
        "AMC_AppNote_055.pdf": "https://www.a-m-c.com/d/?h=fa2e235",
        "AMC_AppNote_056.pdf": "https://www.a-m-c.com/d/?h=da30cb5",
        "AMC_AppNote_057.pdf": "https://www.a-m-c.com/d/?h=3336ca1",
        "AMC_AppNote_058.pdf": "https://www.a-m-c.com/d/?h=2a64d80",
        "AMC_AppNote_059.pdf": "https://www.a-m-c.com/d/?h=195ad07",
        "AMC_AppNote_060.pdf": "https://www.a-m-c.com/d/?h=98b2fc7",
        "AMC_AppNote_061.pdf": "https://www.a-m-c.com/d/?h=3ea4810",
        "AMC_AppNote_062.pdf": "https://www.a-m-c.com/d/?h=156e745",
    }


def _datasheet_urls() -> dict[str, str]:
    """Build the AMC_Datasheet_{SKU}.pdf → product-page URL mapping for every
    datasheet that exists locally. Uses resolve_datasheet_sku so variant SKUs
    route through the canonical base SKU's product page.
    """
    out: dict[str, str] = {}
    for pdf in ROOT.glob("AMC_Datasheet_*.pdf"):
        name = pdf.name
        sku = name.removeprefix("AMC_Datasheet_").removesuffix(".pdf")
        if not sku:
            continue
        canonical = resolve_datasheet_sku(sku) or sku
        out[name] = f"{BASE_URL}/product/{canonical.lower()}/"
    return out


def _from_retrofit_map() -> dict[str, str]:
    out: dict[str, str] = {}
    retrofit_path = SITE_DATA / "retrofit_map.json"
    if not retrofit_path.exists():
        return out
    payload = json.loads(retrofit_path.read_text(encoding="utf-8"))
    for size_key in ("small_size", "large_size"):
        row = (payload.get("retrofit_document") or {}).get(size_key) or {}
        local = row.get("local_pdf")
        site_url = row.get("site_url")
        if local and site_url:
            out[local] = site_url
    return out


def build_map() -> dict:
    load_support_catalog()

    merged: dict[str, str] = {}
    merged.update(_build_app_note_map())
    merged.update(CURATED_MAP)
    merged.update(_from_retrofit_map())
    merged.update(_datasheet_urls())  # datasheets last so they win over stale entries

    return {
        "generated_by": "scripts/build_pdf_url_map.py",
        "base_url": BASE_URL,
        "count": len(merged),
        "mappings": dict(sorted(merged.items())),
    }


def count_local_pdfs() -> int:
    return sum(1 for _ in ROOT.glob("*.pdf"))


def list_unmapped(pdf_map: dict[str, str]) -> list[str]:
    mapped = set(pdf_map.keys())
    return sorted(p.name for p in ROOT.glob("*.pdf") if p.name not in mapped)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", action="store_true", help="Print coverage report only")
    parser.add_argument("--write", action="store_true", default=True, help="Write site_data/pdf_url_map.json")
    parser.add_argument("--no-write", dest="write", action="store_false")
    args = parser.parse_args()

    payload = build_map()
    mappings = payload["mappings"]
    out_path = SITE_DATA / "pdf_url_map.json"

    total_local = count_local_pdfs()
    unmapped = list_unmapped(mappings)
    mapped_count = total_local - len(unmapped)
    coverage_pct = (mapped_count / total_local * 100) if total_local else 0.0

    if args.write and not args.report:
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"Wrote {out_path} ({payload['count']} entries)")
    if args.report or args.write:
        print()
        print(f"Coverage: {mapped_count} / {total_local} local PDFs mapped ({coverage_pct:.1f}%)")
        if unmapped:
            print(f"Unmapped ({len(unmapped)}):")
            for name in unmapped[:30]:
                print(f"  - {name}")
            if len(unmapped) > 30:
                print(f"  ... and {len(unmapped) - 30} more")
        else:
            print("All local PDFs are mapped.")


if __name__ == "__main__":
    main()
