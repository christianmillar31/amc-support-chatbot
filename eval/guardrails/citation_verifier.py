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

# Match "[Source: filename.pdf, Page X]" where filename MUST end in .pdf
# (avoids capturing prose like "AMC replacement database" as a filename).
# Handles multiple comma/and-separated filenames in a single [Source: ...] block.
_CITATION_PATTERN = re.compile(
    r"([A-Za-z0-9_\-]+\.pdf)(?:\s*,?\s*(?:Page|p\.?)\s*(\d+))?",
    re.IGNORECASE,
)

# Detects the opening of a [Source: ...] block, used as a scoping filter
_SOURCE_BLOCK = re.compile(r"\[Source:[^\]]*\]", re.IGNORECASE)


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
    """
    Parse all [Source: ...] citations from text.

    Only considers content inside [Source: ...] blocks and only extracts strings
    that look like actual .pdf filenames. This prevents false positives from
    prose like "AMC replacement database" or "search results".
    """
    if not text:
        return []
    result = []
    # Scope to [Source: ...] blocks so we don't match .pdf mentions in body text
    for block in _SOURCE_BLOCK.findall(text):
        for filename, page_str in _CITATION_PATTERN.findall(block):
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
