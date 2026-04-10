"""
Verify that [Source: filename, Page X] citations in answers refer to real files
and real page numbers.

Loads the chunk metadata from index_data/chunks.json to get:
- The list of real PDF filenames
- For each file, the set of valid page numbers
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Set

BASE = Path(__file__).resolve().parent.parent.parent
CHUNKS_PATH = BASE / "index_data" / "chunks.json"

# Match "[Source: filename, Page X]" and "[Source: filename, p.X]" and variations
# Filename is everything up to first comma or closing bracket.
_CITATION_PATTERN = re.compile(
    r"\[Source:\s*([^,\]]+)(?:,\s*(?:Page|p\.?)\s*(\d+))?[^\]]*\]",
    re.IGNORECASE,
)


@lru_cache(maxsize=1)
def load_valid_files_and_pages() -> tuple[frozenset[str], Dict[str, Set[int]]]:
    """
    Load the corpus metadata.
    Returns (set_of_filenames, dict_filename_to_page_set).
    """
    if not CHUNKS_PATH.exists():
        return frozenset(), {}

    try:
        with open(CHUNKS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return frozenset(), {}

    file_pages: Dict[str, Set[int]] = {}
    metas = data.get("metadatas", [])

    for meta in metas:
        source = meta.get("source", "").strip()
        page = meta.get("page", "")
        if not source:
            continue
        if source not in file_pages:
            file_pages[source] = set()
        try:
            file_pages[source].add(int(page))
        except (ValueError, TypeError):
            pass

    files = frozenset(file_pages.keys())
    # Convert to immutable sets per file
    return files, {k: set(v) for k, v in file_pages.items()}


@dataclass
class Citation:
    filename: str
    page: int | None
    file_exists: bool
    page_valid: bool

    @property
    def is_fabricated(self) -> bool:
        return not self.file_exists or (self.page is not None and not self.page_valid)


def extract_citations(text: str) -> List[tuple[str, int | None]]:
    """Parse all [Source: ...] citations from text."""
    if not text:
        return []
    matches = _CITATION_PATTERN.findall(text)
    result = []
    for filename, page_str in matches:
        filename = filename.strip()
        page = None
        if page_str:
            try:
                page = int(page_str)
            except ValueError:
                pass
        result.append((filename, page))
    return result


def verify_citations(answer: str) -> List[Citation]:
    """
    Check every [Source: ...] citation against the chunks.json metadata.
    Returns a list of Citation objects with verification results.
    """
    valid_files, file_pages = load_valid_files_and_pages()
    citations = extract_citations(answer)
    results = []

    for filename, page in citations:
        file_exists = filename in valid_files
        page_valid = True
        if file_exists and page is not None:
            page_valid = page in file_pages.get(filename, set())
        results.append(Citation(
            filename=filename,
            page=page,
            file_exists=file_exists,
            page_valid=page_valid,
        ))

    return results


def fabricated_citation_count(answer: str) -> int:
    """Quick scalar: how many fabricated citations in this answer?"""
    return sum(1 for c in verify_citations(answer) if c.is_fabricated)
