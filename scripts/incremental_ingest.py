#!/usr/bin/env python3
"""Incremental ingester — embed only NEW PDFs, append to existing index.

Compared to ``build_index()`` which re-embeds the entire corpus, this script:
  1. Loads the current index (chunks.json, embeddings.npy)
  2. Detects which PDFs in the repo are NOT already represented in the index
  3. Extracts chunks from the new PDFs only (uses the same routines as
     build_index, so behavior matches)
  4. Embeds only the new chunks with BGE-large
  5. Concatenates [old_embeddings | new_embeddings] in source order
  6. Rebuilds BM25 (cheap) over the combined chunk list
  7. Writes atomically via temp files + rename

Why incremental: on a memory-constrained machine, re-embedding 32k+ chunks
hits swap and slows to a crawl (measured: 4s -> 36s per batch as RSS drops).
Embedding ~6k new chunks instead of 32k peaks far lower and doesn't thrash.

Run:
    python scripts/incremental_ingest.py

Safe to rerun — idempotent (already-indexed PDFs are skipped).
"""
from __future__ import annotations

import json
import pickle
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from app.config import INDEX_DIR, EMBEDDING_MODEL  # noqa: E402
from app.ingest import (  # noqa: E402
    extract_text_with_headings,
    smart_chunk_text,
    _classify_doc_type,
    _extract_tables_as_markdown,
    get_all_pdfs,
)


def _load_existing_index():
    """Return (chunks, metadatas, embeddings) from disk."""
    with open(INDEX_DIR / "chunks.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    chunks = data["chunks"]
    metadatas = data["metadatas"]
    embeddings = np.load(INDEX_DIR / "embeddings.npy")
    assert len(chunks) == len(metadatas) == embeddings.shape[0], (
        f"Index shape mismatch: chunks={len(chunks)}, metadatas={len(metadatas)}, "
        f"embeddings={embeddings.shape[0]}"
    )
    return chunks, metadatas, embeddings


def _process_pdf(pdf_path: Path) -> tuple[list[str], list[dict]]:
    """Extract chunks + metadata from a single PDF (mirrors build_index logic)."""
    import fitz

    doc_type = _classify_doc_type(pdf_path.name)
    pages = extract_text_with_headings(pdf_path)

    chunks: list[str] = []
    metadatas: list[dict] = []

    # Tables first (so they appear earlier in the chunk list, matching build_index)
    try:
        doc = fitz.open(pdf_path)
        for page_num in range(len(doc)):
            page = doc[page_num]
            tables = _extract_tables_as_markdown(page)
            for table_text in tables:
                page_heading = ""
                for p in pages:
                    if p["page"] == page_num + 1:
                        page_heading = p.get("heading", "")
                        break
                chunk = (
                    f"[Section: {page_heading}]\n[TABLE]\n{table_text}"
                    if page_heading
                    else f"[TABLE]\n{table_text}"
                )
                chunks.append(chunk)
                metadatas.append({
                    "source": pdf_path.name,
                    "page": page_num + 1,
                    "heading": page_heading,
                    "doc_type": doc_type,
                })
        doc.close()
    except Exception as e:
        print(f"  Warning: table extraction failed for {pdf_path.name}: {e}")

    # Prose chunks (same routines as build_index)
    for page_data in pages:
        heading = page_data.get("heading", "")
        prose = smart_chunk_text(page_data["text"], heading=heading, source=page_data["source"])
        for c in prose:
            chunks.append(c)
            metadatas.append({
                "source": page_data["source"],
                "page": page_data["page"],
                "heading": heading,
                "doc_type": doc_type,
            })

    return chunks, metadatas


def main() -> int:
    print(f"Loading current index from {INDEX_DIR}...")
    old_chunks, old_metadatas, old_embeddings = _load_existing_index()
    print(f"  current index: {len(old_chunks)} chunks, {old_embeddings.shape[1]}-dim embeddings")

    existing_sources = {md["source"] for md in old_metadatas}

    all_pdfs = get_all_pdfs()
    new_pdfs = [p for p in all_pdfs if p.name not in existing_sources]
    print(f"  repo has {len(all_pdfs)} PDFs; {len(new_pdfs)} are NEW to the index")
    if not new_pdfs:
        print("Nothing to do — index already covers all local PDFs.")
        return 0

    # --- Extract chunks from new PDFs ---
    t0 = time.monotonic()
    new_chunks: list[str] = []
    new_metadatas: list[dict] = []
    for i, pdf in enumerate(new_pdfs, 1):
        prev_len = len(new_chunks)
        try:
            c, m = _process_pdf(pdf)
        except Exception as e:
            print(f"[{i}/{len(new_pdfs)}] FAILED {pdf.name}: {e}")
            continue
        new_chunks.extend(c)
        new_metadatas.extend(m)
        produced = len(new_chunks) - prev_len
        if i % 10 == 0 or i == len(new_pdfs) or produced == 0:
            print(f"[{i}/{len(new_pdfs)}] {pdf.name} -> +{produced} chunks "
                  f"(total new: {len(new_chunks)})")

    extract_elapsed = time.monotonic() - t0
    print(f"Extraction done: {len(new_chunks)} new chunks in {extract_elapsed:.0f}s")

    # --- Embed only the new chunks ---
    t1 = time.monotonic()
    from sentence_transformers import SentenceTransformer  # noqa: WPS433

    print(f"Loading {EMBEDDING_MODEL}...")
    # Force CPU to match how the existing embeddings.npy was computed.
    # MPS/CUDA would introduce tiny floating-point differences (~1e-5) that
    # are below the ranking noise floor but break strict reproducibility.
    # Override with INCREMENTAL_EMBED_DEVICE=mps if you explicitly want speed
    # and accept bit-non-identical embeddings.
    import os
    device = os.environ.get("INCREMENTAL_EMBED_DEVICE", "cpu")
    print(f"  using device: {device}")
    embed_model = SentenceTransformer(EMBEDDING_MODEL, device=device)

    print(f"Embedding {len(new_chunks)} new chunks (batch_size=32)...")
    new_embeddings = embed_model.encode(
        new_chunks,
        show_progress_bar=True,
        batch_size=32,        # matches build_index default — proven stable on CPU
        normalize_embeddings=False,  # match build_index (it doesn't normalize)
    )
    new_embeddings = np.asarray(new_embeddings, dtype=np.float32)
    print(f"Embedding done in {time.monotonic() - t1:.0f}s; shape={new_embeddings.shape}")

    assert new_embeddings.shape[1] == old_embeddings.shape[1], (
        f"Embedding dim mismatch: old={old_embeddings.shape[1]} vs "
        f"new={new_embeddings.shape[1]}"
    )

    # --- Combine ---
    all_chunks = old_chunks + new_chunks
    all_metadatas = old_metadatas + new_metadatas
    all_embeddings = np.vstack([old_embeddings, new_embeddings]).astype(np.float32)
    assert len(all_chunks) == all_embeddings.shape[0]
    print(f"Combined index: {len(all_chunks)} chunks")

    # --- Rebuild BM25 over combined corpus ---
    t2 = time.monotonic()
    from rank_bm25 import BM25Okapi  # noqa: WPS433

    print("Rebuilding BM25...")
    tokenized = [c.lower().split() for c in all_chunks]
    bm25 = BM25Okapi(tokenized)
    print(f"BM25 rebuilt in {time.monotonic() - t2:.0f}s")

    # --- Write atomically (temp -> rename) ---
    INDEX_DIR.mkdir(parents=True, exist_ok=True)

    chunks_tmp = INDEX_DIR / "chunks.json.tmp"
    bm25_tmp = INDEX_DIR / "bm25.pkl.tmp"
    # np.save() auto-appends .npy if the filename doesn't end with it, so the
    # tmp path has to already end in .npy or we end up writing to foo.tmp.npy
    # and the rename target silently vanishes.
    emb_tmp = INDEX_DIR / "embeddings.tmp.npy"

    with open(chunks_tmp, "w", encoding="utf-8") as f:
        json.dump({"chunks": all_chunks, "metadatas": all_metadatas}, f)
    with open(bm25_tmp, "wb") as f:
        pickle.dump(bm25, f)
    np.save(emb_tmp, all_embeddings)

    # Atomic swap
    chunks_tmp.replace(INDEX_DIR / "chunks.json")
    bm25_tmp.replace(INDEX_DIR / "bm25.pkl")
    emb_tmp.replace(INDEX_DIR / "embeddings.npy")

    total = time.monotonic() - t0
    print(f"\nDone in {total:.0f}s total.")
    print(f"  Final index: {len(all_chunks)} chunks, "
          f"{len({m['source'] for m in all_metadatas})} unique sources")
    return 0


if __name__ == "__main__":
    sys.exit(main())
