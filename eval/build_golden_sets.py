#!/usr/bin/env python3
"""
Build golden test sets from existing hand-verified sources.

Generates 3 JSONL files in eval/golden/:
- faq_tests.jsonl: 167 FAQ Q&A pairs
- drive_routing_tests.jsonl: 100 sampled drives from CM Servo Info.csv
- retrofit_tests.jsonl: 38 discontinued → replacement mappings

adversarial_tests.jsonl is hand-written, not generated here.

Usage: python eval/build_golden_sets.py
"""
import csv
import json
import random
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
GOLDEN = Path(__file__).resolve().parent / "golden"
GOLDEN.mkdir(parents=True, exist_ok=True)

random.seed(42)  # Deterministic sampling


def build_faq_tests() -> int:
    """One test per FAQ entry: question → expected source/page/answer keywords."""
    src = BASE / "faq_index.csv"
    out = GOLDEN / "faq_tests.jsonl"

    with open(src, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    count = 0
    with open(out, "w", encoding="utf-8") as f:
        for row in rows:
            question = row["question"].strip()
            if not question:
                continue
            answer = row["answer_summary"].strip()
            # Extract the first 6 meaningful words from answer as expected phrase
            tokens = [t for t in answer.split() if len(t) > 3]
            expected_phrase = " ".join(tokens[:6]) if tokens else ""

            test = {
                "id": f"faq_{count}",
                "category": "faq",
                "question": question,
                "expected_source": row["manual_source"].strip(),
                "expected_page": row["page"].strip(),
                "expected_section": row["section"].strip(),
                "expected_answer_contains": expected_phrase,
                "full_expected_answer": answer,
                "expected_refuse": False,
            }
            f.write(json.dumps(test, ensure_ascii=False) + "\n")
            count += 1

    return count


def build_drive_routing_tests(sample_size: int = 100) -> int:
    """Sample random drives from CM Servo Info.csv. Each test asks for routing info."""
    src = BASE / "CM Servo Info.csv"
    out = GOLDEN / "drive_routing_tests.jsonl"

    with open(src, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    # Keep only rows with a SKU and Family (skip accessories, headers, junk)
    valid = [
        r for r in rows
        if r.get("Sku", "").strip()
        and r.get("Family", "").strip()
        and "(Discontinued)" not in r.get("Family", "")
        and "(Reserved)" not in r.get("Family", "")
    ]

    sampled = random.sample(valid, min(sample_size, len(valid)))

    count = 0
    with open(out, "w", encoding="utf-8") as f:
        for row in sampled:
            sku = row["Sku"].strip()
            family = row["Family"].strip()
            network = row.get("Network Communication", "").strip()

            test = {
                "id": f"drive_{count}",
                "category": "drive_routing",
                "question": f"I need info about the {sku} drive. What family is it, and which manual should I look at?",
                "sku": sku,
                "expected_family": family,
                "expected_network": network,
                "expected_answer_contains": sku,
                "expected_refuse": False,
            }
            f.write(json.dumps(test, ensure_ascii=False) + "\n")
            count += 1

    return count


def build_retrofit_tests() -> int:
    """One test per discontinued drive → expected replacement."""
    src = BASE / "retrofit_mapping.csv"
    out = GOLDEN / "retrofit_tests.jsonl"

    if not src.exists():
        print(f"  WARNING: {src} not found, skipping retrofit tests")
        return 0

    with open(src, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    count = 0
    with open(out, "w", encoding="utf-8") as f:
        for row in rows:
            old = row.get("discontinued_model", "").strip()
            new_brushless = row.get("replacement_brushless", "").strip()
            new_brushed = row.get("replacement_brushed_only", "").strip()
            motor_type = row.get("motor_type", "").strip()

            if not old:
                continue

            # The expected replacement depends on motor type
            expected = new_brushless if new_brushless else new_brushed

            test = {
                "id": f"retrofit_{count}",
                "category": "retrofit",
                "question": f"My {old} drive is discontinued. What AxCent drive replaces it?",
                "discontinued_sku": old,
                "motor_type": motor_type,
                "expected_replacement": expected,
                "expected_answer_contains": expected,
                "expected_refuse": False,
            }
            f.write(json.dumps(test, ensure_ascii=False) + "\n")
            count += 1

    return count


def main():
    print("Building golden test sets...")
    print()

    faq_count = build_faq_tests()
    print(f"  faq_tests.jsonl              — {faq_count} tests")

    drive_count = build_drive_routing_tests(sample_size=100)
    print(f"  drive_routing_tests.jsonl    — {drive_count} tests")

    retrofit_count = build_retrofit_tests()
    print(f"  retrofit_tests.jsonl         — {retrofit_count} tests")

    # adversarial_tests.jsonl is hand-authored, not generated
    adv_path = GOLDEN / "adversarial_tests.jsonl"
    if adv_path.exists():
        adv_count = sum(1 for _ in open(adv_path, encoding="utf-8"))
        print(f"  adversarial_tests.jsonl      — {adv_count} tests (hand-written)")
    else:
        print(f"  adversarial_tests.jsonl      — NOT FOUND (hand-written, create manually)")
        adv_count = 0

    total = faq_count + drive_count + retrofit_count + adv_count
    print()
    print(f"Total golden test cases: {total}")


if __name__ == "__main__":
    main()
