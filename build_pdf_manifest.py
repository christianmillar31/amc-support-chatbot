#!/usr/bin/env python3
"""
Build a structured manifest of local AMC PDF assets.

This treats the local PDF folder as a first-class data source and emits a
machine-readable inventory under `site_data/local_pdf_manifest.json`.
"""
from __future__ import annotations

import csv
import json
import re
import time
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PDF_DIR = ROOT
SITE_DATA_DIR = ROOT / "site_data"
DRIVE_CSV = ROOT / "CM Servo Info.csv"


DOC_TYPE_PREFIXES = [
    ("AMC_Datasheet_", "datasheet"),
    ("AMC_HWManual_", "hardware_manual"),
    ("AMC_CommManual_", "communication_manual"),
    ("AMC_AppNote_", "application_note"),
    ("AMC_SW_Manual_", "software_manual"),
    ("AMC_SW_QuickRef_", "software_quick_reference"),
    ("AMC_ProductNote_", "product_note"),
    ("AMC_WhitePaper_", "white_paper"),
    ("AMC_Compliance_", "compliance"),
]


def normalize_sku(value: str) -> str:
    token = value.strip().upper()
    token = token.replace("–", "-").replace("—", "-")
    token = re.sub(r"\s+", "", token)
    token = re.sub(r"-{2,}", "-", token)
    if token.endswith("-10"):
        token = token[:-3]
    return token


def classify_pdf(name: str) -> str:
    for prefix, doc_type in DOC_TYPE_PREFIXES:
        if name.startswith(prefix):
            return doc_type
    return "other"


def extract_sku(name: str, doc_type: str) -> str:
    stem = Path(name).stem
    if doc_type == "datasheet" and stem.startswith("AMC_Datasheet_"):
        return stem[len("AMC_Datasheet_"):]
    return ""


def guess_family(value: str) -> str:
    token = value.upper()
    if token.startswith(("FE", "FM", "FD", "FMP", "FX")):
        return "FlexPro"
    if token.startswith(("DP", "DV", "DZ", "DX", "DVC", "MC", "KC")):
        return "DigiFlex Performance"
    if token.startswith(("AZ", "AB", "B")):
        return "AxCent"
    if re.fullmatch(r"\d+[A-Z]?\d*", token):
        return "Classic"
    return ""


def load_drive_index() -> dict[str, dict[str, str]]:
    with DRIVE_CSV.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        return {
            (row.get("Sku") or "").strip().upper(): row
            for row in reader
            if (row.get("Sku") or "").strip()
        }


def build_manifest() -> dict:
    drive_index = load_drive_index()
    records: list[dict] = []
    counts_by_type: Counter[str] = Counter()
    counts_by_family: Counter[str] = Counter()
    matched_datasheets = 0

    for pdf_path in sorted(PDF_DIR.glob("*.pdf")):
        name = pdf_path.name
        doc_type = classify_pdf(name)
        counts_by_type[doc_type] += 1
        sku = extract_sku(name, doc_type)
        drive = drive_index.get(sku.upper()) if sku else None
        family = (drive or {}).get("Family", "") or guess_family(sku)
        if doc_type == "datasheet":
            counts_by_family[family or "Unknown"] += 1
            if drive:
                matched_datasheets += 1

        records.append(
            {
                "filename": name,
                "doc_type": doc_type,
                "sku": sku,
                "normalized_sku": normalize_sku(sku) if sku else "",
                "family": family,
                "status": (drive or {}).get("Status", ""),
                "network_communication": (drive or {}).get("Network Communication", ""),
                "size_bytes": pdf_path.stat().st_size,
            }
        )

    datasheet_skus = {record["sku"] for record in records if record["doc_type"] == "datasheet" and record["sku"]}
    csv_drive_skus = {
        sku for sku, row in drive_index.items()
        if (row.get("Family") or "").strip()
    }
    missing_datasheets = sorted(csv_drive_skus - datasheet_skus)

    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source_dir": str(PDF_DIR),
        "total_pdfs": len(records),
        "counts_by_type": dict(sorted(counts_by_type.items())),
        "datasheet_summary": {
            "count": counts_by_type.get("datasheet", 0),
            "matched_to_drive_csv": matched_datasheets,
            "counts_by_family": dict(sorted(counts_by_family.items())),
            "drive_csv_rows_without_local_datasheet": {
                "count": len(missing_datasheets),
                "examples": missing_datasheets[:50],
            },
        },
        "pdfs": records,
    }


def main() -> None:
    manifest = build_manifest()
    SITE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    output_path = SITE_DATA_DIR / "local_pdf_manifest.json"
    output_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Wrote {output_path}")
    print(
        f"Local PDFs: {manifest['total_pdfs']} total, "
        f"{manifest['datasheet_summary']['count']} datasheets"
    )


if __name__ == "__main__":
    main()
