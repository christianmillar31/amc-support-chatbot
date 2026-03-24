#!/usr/bin/env python3
"""
Analyze feedback.json to identify weak spots in chatbot responses.

Usage:
    python analyze_feedback.py [--json]

Outputs:
    - Total feedback count and rating breakdown
    - Most common negative-feedback questions (potential retrieval gaps)
    - Questions suitable for a "hard questions" test set
"""

import json
import sys
from collections import Counter
from pathlib import Path

FEEDBACK_FILE = Path(__file__).parent / "feedback.json"


def load_feedback():
    if not FEEDBACK_FILE.exists():
        print(f"No feedback file found at {FEEDBACK_FILE}")
        sys.exit(1)
    with open(FEEDBACK_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def analyze(entries, output_json=False):
    total = len(entries)
    up = sum(1 for e in entries if e.get("rating") == "up")
    down = sum(1 for e in entries if e.get("rating") == "down")

    negative = [e for e in entries if e.get("rating") == "down"]
    negative_questions = [e["question"] for e in negative]
    question_counts = Counter(negative_questions)

    # Extract comments from negative feedback
    comments = [(e["question"], e.get("comment", "")) for e in negative if e.get("comment")]

    if output_json:
        result = {
            "total": total,
            "positive": up,
            "negative": down,
            "approval_rate": round(up / total * 100, 1) if total else 0,
            "top_negative_questions": question_counts.most_common(20),
            "negative_comments": comments[:50],
            "hard_questions_test_set": [e["question"] for e in negative[:50]],
        }
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    print(f"\n{'='*60}")
    print(f"AMC Support Bot — Feedback Analysis")
    print(f"{'='*60}")
    print(f"Total feedback entries: {total}")
    print(f"  Positive (thumbs up):  {up} ({up/total*100:.1f}%)" if total else "")
    print(f"  Negative (thumbs down): {down} ({down/total*100:.1f}%)" if total else "")

    if negative:
        print(f"\n{'─'*60}")
        print("Top Negative-Feedback Questions (potential retrieval gaps):")
        print(f"{'─'*60}")
        for q, count in question_counts.most_common(15):
            print(f"  [{count}x] {q[:100]}")

    if comments:
        print(f"\n{'─'*60}")
        print("User Comments on Poor Answers:")
        print(f"{'─'*60}")
        for q, c in comments[:10]:
            print(f"  Q: {q[:80]}")
            print(f"  Comment: {c[:200]}")
            print()

    if negative:
        print(f"\n{'─'*60}")
        print(f"Hard Questions Test Set ({len(negative)} questions)")
        print(f"{'─'*60}")
        print("These questions received negative feedback and should be used")
        print("to evaluate retrieval improvements:")
        for i, e in enumerate(negative[:20], 1):
            print(f"  {i}. {e['question'][:100]}")

    print()


if __name__ == "__main__":
    entries = load_feedback()
    output_json = "--json" in sys.argv
    analyze(entries, output_json=output_json)
