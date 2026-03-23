import json
import re
import pickle
import fitz  # PyMuPDF
from sklearn.feature_extraction.text import TfidfVectorizer
from pathlib import Path

from app.config import PDF_DIR, INDEX_DIR, CHUNK_SIZE, CHUNK_OVERLAP


def extract_text_with_headings(pdf_path: Path) -> list[dict]:
    """
    Extract text page-by-page from a PDF, detecting section headings and tables.
    Returns list of {text, source, page, heading, type}.
    """
    doc = fitz.open(pdf_path)
    pages = []
    h1_heading = ""  # Major section (≥16pt or allcaps bold)
    h2_heading = ""  # Sub-section (≥14pt or bold)
    heading_set_page = -1  # Track when heading was last set

    for page_num in range(len(doc)):
        page = doc[page_num]
        blocks = page.get_text("dict")["blocks"]

        page_text = ""
        page_has_heading = False

        for block in blocks:
            if "lines" not in block:
                continue

            for line in block["lines"]:
                line_text = ""
                is_bold = False
                max_size = 0
                for span in line["spans"]:
                    line_text += span["text"]
                    max_size = max(max_size, span["size"])
                    if "bold" in span["font"].lower() or "Bold" in span["font"]:
                        is_bold = True

                line_text = line_text.strip()
                if not line_text:
                    continue

                # Detect headings with hierarchy
                is_any_heading = (
                    (is_bold or max_size >= 14)
                    and len(line_text) < 120
                    and not line_text.startswith("0x")
                    and not re.match(r'^\d', line_text)
                    and not re.search(r'0x[0-9A-Fa-f]{2,}', line_text)
                )
                if is_any_heading:
                    page_has_heading = True
                    heading_set_page = page_num
                    if max_size >= 16 or (is_bold and line_text == line_text.upper() and len(line_text) > 3):
                        # Major heading (H1)
                        h1_heading = line_text
                        h2_heading = ""  # Reset sub-heading under new major section
                    else:
                        # Sub-heading (H2)
                        h2_heading = line_text

                page_text += line_text + "\n"

        # If no heading found on this page and we're >2 pages from last heading, reset
        if not page_has_heading and (page_num - heading_set_page) > 2:
            h1_heading = ""
            h2_heading = ""

        # Build hierarchical heading string
        if h1_heading and h2_heading:
            combined_heading = f"{h1_heading} > {h2_heading}"
        elif h1_heading:
            combined_heading = h1_heading
        elif h2_heading:
            combined_heading = h2_heading
        else:
            combined_heading = ""

        if page_text.strip():
            pages.append({
                "text": page_text,
                "source": pdf_path.name,
                "page": page_num + 1,
                "heading": combined_heading,
            })

    doc.close()
    return pages


def smart_chunk_text(text: str, heading: str = "",
                     chunk_size: int = CHUNK_SIZE,
                     overlap: int = CHUNK_OVERLAP) -> list[str]:
    """
    Split text into chunks at natural boundaries (paragraphs, then sentences).
    Prepends section heading to each chunk for context.
    Filters out tiny/useless fragments.
    """
    # Split on double-newlines (paragraphs) first
    paragraphs = re.split(r'\n\s*\n', text)

    chunks = []
    current_chunk = ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        # If adding this paragraph would exceed chunk_size, finalize current chunk
        if current_chunk and len(current_chunk) + len(para) + 1 > chunk_size:
            chunks.append(current_chunk.strip())
            # Keep overlap: take the last `overlap` characters as the start of next chunk
            if len(current_chunk) > overlap:
                # Try to break at a sentence boundary within the overlap zone
                overlap_text = current_chunk[-overlap:]
                sentence_break = overlap_text.find('. ')
                if sentence_break > 0:
                    current_chunk = overlap_text[sentence_break + 2:]
                else:
                    current_chunk = overlap_text
            else:
                current_chunk = ""

        if current_chunk:
            current_chunk += "\n\n" + para
        else:
            current_chunk = para

    # Don't forget the last chunk
    if current_chunk.strip():
        chunks.append(current_chunk.strip())

    # If a paragraph itself is bigger than chunk_size, split it at sentence boundaries
    final_chunks = []
    for chunk in chunks:
        if len(chunk) <= chunk_size * 1.2:  # Allow 20% overflow to avoid bad splits
            final_chunks.append(chunk)
        else:
            # Split long chunk at sentence boundaries
            sentences = re.split(r'(?<=[.!?])\s+', chunk)
            sub_chunk = ""
            for sent in sentences:
                if sub_chunk and len(sub_chunk) + len(sent) + 1 > chunk_size:
                    final_chunks.append(sub_chunk.strip())
                    sub_chunk = sent
                else:
                    sub_chunk = (sub_chunk + " " + sent).strip() if sub_chunk else sent
            if sub_chunk.strip():
                final_chunks.append(sub_chunk.strip())

    # Prepend heading and filter out tiny/useless chunks
    result = []
    for chunk in final_chunks:
        if len(chunk) < 40:
            continue  # Skip tiny fragments
        if heading:
            chunk = f"[Section: {heading}]\n{chunk}"
        result.append(chunk)

    return result


def get_all_pdfs() -> list[Path]:
    """Find all PDF files in the configured directory."""
    return sorted(PDF_DIR.glob("*.pdf"))


def build_index():
    """Parse all PDFs, chunk, and build a TF-IDF index."""
    pdfs = get_all_pdfs()
    print(f"Found {len(pdfs)} PDFs to ingest.")

    all_chunks = []
    all_metadatas = []

    for pdf_path in pdfs:
        print(f"  Processing: {pdf_path.name}")
        pages = extract_text_with_headings(pdf_path)

        for page_data in pages:
            heading = page_data.get("heading", "")
            chunks = smart_chunk_text(page_data["text"], heading=heading)
            for chunk in chunks:
                all_chunks.append(chunk)
                all_metadatas.append({
                    "source": page_data["source"],
                    "page": page_data["page"],
                    "heading": heading,
                })

    print(f"Building TF-IDF index for {len(all_chunks)} chunks...")
    vectorizer = TfidfVectorizer(
        max_features=30000,
        stop_words="english",
        ngram_range=(1, 3),  # Up to trigrams for technical terms
        sublinear_tf=True,
        min_df=1,
        max_df=0.85,  # Ignore terms that appear in >85% of chunks (boilerplate)
    )
    tfidf_matrix = vectorizer.fit_transform(all_chunks)

    # Save to disk
    INDEX_DIR.mkdir(exist_ok=True)
    with open(INDEX_DIR / "vectorizer.pkl", "wb") as f:
        pickle.dump(vectorizer, f)
    with open(INDEX_DIR / "tfidf_matrix.pkl", "wb") as f:
        pickle.dump(tfidf_matrix, f)
    with open(INDEX_DIR / "chunks.json", "w", encoding="utf-8") as f:
        json.dump({"chunks": all_chunks, "metadatas": all_metadatas}, f)

    print(f"Ingestion complete. {len(all_chunks)} chunks indexed.")
    return len(all_chunks)


def is_indexed() -> bool:
    """Check if the index files already exist."""
    return (INDEX_DIR / "vectorizer.pkl").exists() and (INDEX_DIR / "chunks.json").exists()
