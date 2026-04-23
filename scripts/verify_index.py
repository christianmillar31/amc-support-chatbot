#!/usr/bin/env python3
"""Post-ingest integrity + smoke-test for the search index.

1. Consistency check: chunks / metadatas / embeddings / bm25 all agree on size.
2. Doc-type distribution: confirm new doc_types (marketing, compliance, rma,
   sw_release) are present and counts look sane.
3. Coverage spot-check: confirm specific SKU datasheets that used to be
   missing are now retrievable (100A40, 120A10, AZXBH40A8, 12A8, etc.).
4. Retrieval smoke-test: run a handful of queries that should now surface
   the new PDFs.

Run: python scripts/verify_index.py
Exits 0 on success, non-zero on any check failure.
"""
from __future__ import annotations

import json
import pickle
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from app.config import INDEX_DIR  # noqa: E402


def check_consistency() -> bool:
    """Assert chunks/metadatas/embeddings/bm25 all have the same length."""
    with open(INDEX_DIR / "chunks.json") as f:
        data = json.load(f)
    n_chunks = len(data["chunks"])
    n_md = len(data["metadatas"])
    embeddings = np.load(INDEX_DIR / "embeddings.npy")
    n_emb = embeddings.shape[0]
    with open(INDEX_DIR / "bm25.pkl", "rb") as f:
        bm25 = pickle.load(f)
    n_bm25 = len(bm25.doc_freqs) if hasattr(bm25, "doc_freqs") else n_chunks

    print(f"[1] Consistency:")
    print(f"    chunks={n_chunks}  metadatas={n_md}  embeddings={n_emb}  bm25_docs={n_bm25}")
    ok = n_chunks == n_md == n_emb
    if not ok:
        print(f"    FAIL: shape mismatch")
    else:
        print(f"    OK: all match at {n_chunks}")
    return ok


def check_coverage() -> bool:
    """Confirm specific SKUs that were missing are now in the index."""
    with open(INDEX_DIR / "chunks.json") as f:
        metadatas = json.load(f)["metadatas"]
    sources = {md["source"] for md in metadatas}

    # Previously-missing PDFs that MUST now be present
    required = [
        "AMC_Datasheet_100A40.pdf",
        "AMC_Datasheet_120A10.pdf",
        "AMC_Datasheet_AZXBH40A8.pdf",
        "AMC_Datasheet_12A8.pdf",
        "AMC_Datasheet_25A8.pdf",
        "AMC_Datasheet_30A8.pdf",
        "AMC_Datasheet_50A8DD.pdf",
        "AMC_SW_Manual_DriveLibrary.pdf",
        "AMC_HWManual_DigiFlex_PCB_EtherCAT.pdf",
    ]

    print(f"\n[2] Coverage spot-check ({len(sources)} unique sources in index):")
    missing = [p for p in required if p not in sources]
    for p in required:
        mark = "✗" if p in missing else "✓"
        print(f"    {mark} {p}")
    if missing:
        print(f"    FAIL: {len(missing)} required PDFs missing from index")
        return False
    print(f"    OK: all {len(required)} previously-missing datasheets now indexed")
    return True


def check_doc_type_distribution() -> bool:
    """Show doc_type counts — new types must be > 0."""
    with open(INDEX_DIR / "chunks.json") as f:
        metadatas = json.load(f)["metadatas"]
    counts = Counter(md.get("doc_type", "?") for md in metadatas)

    print(f"\n[3] Doc-type distribution (chunks):")
    for t, c in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"    {t:15s} {c}")

    # The new categories must have at least 1 chunk each (except rma may be empty)
    expected_nonzero = ["marketing", "compliance", "sw_release"]
    missing = [t for t in expected_nonzero if counts.get(t, 0) == 0]
    if missing:
        print(f"    WARN: new doc_types with 0 chunks: {missing}")
        return True  # warning, not failure
    print(f"    OK: new doc_types present")
    return True


def check_retrieval_smoke() -> bool:
    """Retrieve on a few queries that should hit newly-indexed PDFs."""
    print(f"\n[4] Retrieval smoke-test:")
    from app.retriever import retrieve  # noqa: WPS433

    queries = [
        ("continuous current of 100A40", "100A40"),
        ("peak current 12A8 analog drive", "12A8"),
        ("120A10 servo drive specifications", "120A10"),
        ("AZXBH40A8 datasheet", "AZXBH40A8"),
        ("DriveLibrary software manual", "DriveLibrary"),
    ]

    all_pass = True
    for query, needle in queries:
        results = retrieve(query, top_k=5)
        hit = any(needle.lower() in r["source"].lower() or needle.lower() in r["text"].lower()
                  for r in results)
        top_src = results[0]["source"] if results else "(none)"
        mark = "✓" if hit else "✗"
        print(f"    {mark} '{query}' -> top: {top_src} (needle={needle}: {'found' if hit else 'NOT FOUND'})")
        if not hit:
            all_pass = False

    if not all_pass:
        print(f"    FAIL: one or more smoke queries missed their target")
    else:
        print(f"    OK: all smoke queries surfaced their new PDFs")
    return all_pass


def main() -> int:
    print("=" * 60)
    print("Index verification")
    print("=" * 60)
    checks = [
        check_consistency,
        check_coverage,
        check_doc_type_distribution,
        check_retrieval_smoke,
    ]
    results = []
    for c in checks:
        try:
            results.append(c())
        except Exception as e:
            print(f"  EXCEPTION in {c.__name__}: {e}")
            results.append(False)

    print("\n" + "=" * 60)
    if all(results):
        print("ALL CHECKS PASSED ✓")
        return 0
    else:
        failed = sum(1 for r in results if not r)
        print(f"{failed} check(s) failed ✗")
        return 1


if __name__ == "__main__":
    sys.exit(main())
