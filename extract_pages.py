#!/usr/bin/env python3
"""
Extracts specific pages from PDFs for FAQ verification.
Run: python extract_pages.py > faq_extract_results.txt
"""
import fitz
import sys
from pathlib import Path

PDF_DIR = Path(__file__).parent
OUT_FILE = PDF_DIR / "faq_extract_results.txt"

extractions = [
    # (pdf_filename, page_numbers_1indexed, entry_label)
    # Entry 39: DigiFlex Panel EtherCAT - feedback connector pinout
    ("AMC_HWManual_DigiFlex_Panel_EtherCAT.pdf", list(range(30, 40)), "Entry 39: DPE Feedback Connector"),
    # Entry 40: DigiFlex Panel EtherCAT - DC power/motor connections
    ("AMC_HWManual_DigiFlex_Panel_EtherCAT.pdf", list(range(35, 42)), "Entry 40: DPE Power/Motor Connector"),
    # Entry 41: DigiFlex PCB CANopen - mounting
    ("AMC_HWManual_DigiFlex_PCB_CANopen.pdf", list(range(12, 20)), "Entry 41: DZ PCB Mounting"),
    # Entry 42: DigiFlex PCB CANopen - CAN transceiver
    ("AMC_HWManual_DigiFlex_PCB_CANopen.pdf", list(range(14, 22)), "Entry 42: DZ CAN Transceiver"),
    # Entries 43-45: AxCent PCB specs, feedback, modes
    ("AMC_HWManual_AxCent_PCB.pdf", list(range(1, 20)), "Entries 43-45: AxCent PCB"),
    # Entries 46-48: Analog Drives wiring, inductance, command
    ("AMC_HWManual_AnalogDrives.pdf", list(range(8, 20)) + list(range(32, 40)), "Entries 46-48: Analog Drives"),
    # Entry 49: ACE Connect To Drive
    ("AMC_SW_Manual_ACE.pdf", list(range(22, 30)), "Entry 49: ACE Connect To Drive"),
    # Entry 50: ACE Motor Params
    ("AMC_SW_Manual_ACE.pdf", list(range(32, 40)), "Entry 50: ACE Motor Params"),
    # Entry 51: ACE Commutation Halls
    ("AMC_SW_Manual_ACE.pdf", list(range(37, 45)), "Entry 51: ACE Commutation/Halls"),
    # Entry 52: ACE Digital I/O
    ("AMC_SW_Manual_ACE.pdf", list(range(42, 50)), "Entry 52: ACE Digital I/O"),
    # Entry 53: ACE Shunt
    ("AMC_SW_Manual_ACE.pdf", list(range(52, 60)), "Entry 53: ACE Shunt Resistor"),
    # Entry 54: ACE Current loop auto-tune
    ("AMC_SW_Manual_ACE.pdf", list(range(65, 73)), "Entry 54: ACE Current Loop Auto-Tune"),
    # Entry 55: ACE Manual current loop
    ("AMC_SW_Manual_ACE.pdf", list(range(67, 75)), "Entry 55: ACE Manual Current Loop Tuning"),
    # Entry 56: ACE AutoCommutation
    ("AMC_SW_Manual_ACE.pdf", list(range(72, 80)), "Entry 56: ACE AutoCommutation"),
    # Entry 57: ACE Phase Detect fail
    ("AMC_SW_Manual_ACE.pdf", list(range(77, 85)), "Entry 57: ACE Phase Detect Fail"),
    # Entry 58: ACE Position loop
    ("AMC_SW_Manual_ACE.pdf", list(range(82, 90)), "Entry 58: ACE Position Loop"),
    # Entry 59: ACE Homing
    ("AMC_SW_Manual_ACE.pdf", list(range(87, 95)), "Entry 59: ACE Homing"),
    # Entry 60: ACE Motion Indexes
    ("AMC_SW_Manual_ACE.pdf", list(range(92, 100)), "Entry 60: ACE Motion Indexes"),
    # Entry 61: ACE PVT
    ("AMC_SW_Manual_ACE.pdf", list(range(97, 105)), "Entry 61: ACE PVT"),
    # Entry 62: ACE Oscilloscope
    ("AMC_SW_Manual_ACE.pdf", list(range(102, 110)), "Entry 62: ACE Oscilloscope"),
    # Entry 63: ACE Motion Engine
    ("AMC_SW_Manual_ACE.pdf", list(range(107, 115)), "Entry 63: ACE Motion Engine"),
    # Entry 64: ACE Firmware Download
    ("AMC_SW_Manual_ACE.pdf", list(range(97, 105)), "Entry 64: ACE Firmware Download"),
    # Entry 65: DriveWare Connect
    ("AMC_SW_Manual_DriveWare.pdf", list(range(12, 20)), "Entry 65: DriveWare Connect"),
    # Entry 66: DriveWare hardware info
    ("AMC_SW_Manual_DriveWare.pdf", list(range(17, 25)), "Entry 66: DriveWare Hardware Info"),
    # Entry 67: DriveWare Motor Params
    ("AMC_SW_Manual_DriveWare.pdf", list(range(22, 30)), "Entry 67: DriveWare Motor Params"),
    # Entry 68: DriveWare Velocity Feedback
    ("AMC_SW_Manual_DriveWare.pdf", list(range(27, 35)), "Entry 68: DriveWare Velocity Feedback"),
    # Entry 69: DriveWare AutoCommutation
    ("AMC_SW_Manual_DriveWare.pdf", list(range(37, 45)), "Entry 69: DriveWare AutoCommutation"),
    # Entry 70: DriveWare Digital I/O
    ("AMC_SW_Manual_DriveWare.pdf", list(range(42, 50)), "Entry 70: DriveWare Digital I/O"),
    # Entry 71: DriveWare Shunt Resistance
    ("AMC_SW_Manual_DriveWare.pdf", list(range(47, 55)), "Entry 71: DriveWare Shunt"),
    # Entry 72: DriveWare Current Loop Tuning
    ("AMC_SW_Manual_DriveWare.pdf", list(range(62, 70)), "Entry 72: DriveWare Current Loop"),
    # Entry 73: DriveWare Velocity Loop Tuning
    ("AMC_SW_Manual_DriveWare.pdf", list(range(72, 80)), "Entry 73: DriveWare Velocity Loop"),
    # Entry 74: DriveWare Commutation
    ("AMC_SW_Manual_DriveWare.pdf", list(range(67, 75)), "Entry 74: DriveWare Commutation"),
    # Entry 75: DriveWare Homing
    ("AMC_SW_Manual_DriveWare.pdf", list(range(77, 85)), "Entry 75: DriveWare Homing"),
    # Entry 76: DriveWare Jog
    ("AMC_SW_Manual_DriveWare.pdf", list(range(82, 90)), "Entry 76: DriveWare Jog"),
    # Entry 77: DriveWare Oscilloscope
    ("AMC_SW_Manual_DriveWare.pdf", list(range(87, 95)), "Entry 77: DriveWare Oscilloscope"),
    # Entry 78: DriveWare Firmware Download
    ("AMC_SW_Manual_DriveWare.pdf", list(range(85, 93)), "Entry 78: DriveWare Firmware Download"),
    # Entries 79-80: EtherCAT State Machine / Operation Enabled
    ("AMC_CommManual_FP_EtherCAT.pdf", list(range(18, 26)), "Entries 79-80: EtherCAT State Machine"),
]

results = []
for pdf_name, pages, label in extractions:
    pdf_path = PDF_DIR / pdf_name
    if not pdf_path.exists():
        results.append(f"\nMISSING: {pdf_name}\n")
        continue

    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    results.append(f"\n{'='*60}")
    results.append(f"{label}")
    results.append(f"Source: {pdf_name} (total pages: {total_pages})")
    results.append(f"{'='*60}")

    seen_pages = set()
    for pg in pages:
        if pg in seen_pages or pg < 1 or pg > total_pages:
            continue
        seen_pages.add(pg)
        page = doc[pg - 1]  # 0-indexed
        text = page.get_text()
        results.append(f"\n--- Page {pg} ---")
        results.append(text[:2500])  # Limit per page

    doc.close()

results.append("\n\nDONE")

with open(OUT_FILE, "w", encoding="utf-8") as f:
    f.write("\n".join(results))

print(f"Written to {OUT_FILE}")
