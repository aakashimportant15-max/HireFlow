"""
core/ingestion.py
==================

HireFlow ka document entry gate.

    Resume / JD file
          |
    core/ingestion.py
          |
    raw text + basic metadata
          |
    core/extraction.py  (LLM yahan lagta hai, ingestion mein NAHI)
          |
    CandidateProfile / JobDescription

Mental model:

    ingestion.py   -> "FILE MEIN KYA LIKHA HAI (raw)?"
    extraction.py  -> "US TEXT KA MATLAB KYA HAI?"

Is file mein jaan-bujh kar NAHI hai:
    - Groq / koi bhi LLM call
    - Prompts
    - Candidate/JD structured extraction (Pydantic business schemas)
    - Requirement mapping / scoring
    - Database
    - Streamlit UI

Ye sirf ek cheez karta hai: **file lo, reliably raw text nikaalo.**
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import docx  # python-docx
import pymupdf

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SUPPORTED_EXTENSIONS = {".pdf", ".docx"}

# Agar ek page mein itne se kam characters mile, to use "likely empty/scanned"
# treat karte hain. Yeh heuristic hai, exact science nahi.
MIN_CHARS_PER_PAGE_THRESHOLD = 20


# ---------------------------------------------------------------------------
# Errors — controlled, user-understandable. Koi giant traceback UI tak nahi jaata.
# ---------------------------------------------------------------------------

class IngestionError(Exception):
    """Base class — core/UI layer isi ko catch karke handle kare."""


class DocumentNotFoundError(IngestionError):
    """File path exist hi nahi karta."""


class UnsupportedFileTypeError(IngestionError):
    """Extension .pdf / .docx ke alawa kuch aur hai."""


class EmptyFileError(IngestionError):
    """File 0 bytes hai."""


class DocumentParseError(IngestionError):
    """PDF/DOCX parser hi fail ho gaya (corrupt file, etc.)."""


class EmptyDocumentError(IngestionError):
    """File parse ho gayi, but usable text nahi mila (khaali document)."""


class ScannedDocumentError(IngestionError):
    """
    PDF technically valid hai but extractable text bahut kam/zero hai —
    likely scanned/image-based. MVP mein OCR support nahi hai, isliye
    clear error diya jaata hai instead of silently LLM ko empty text bhejna.
    """


# ---------------------------------------------------------------------------
# Output model — raw document representation. Ye CandidateProfile NAHI hai.
# ---------------------------------------------------------------------------

@dataclass
class PageContent:
    """Ek page/section ka raw text — future evidence traceability ke liye."""

    page_number: int
    text: str


@dataclass
class DocumentContent:
    """
    Ingestion ka final output. Sirf raw representation — koi meaning
    attach nahi hai.
    """

    filename: str
    file_type: str  # "pdf" | "docx"
    source_path: str
    page_count: int
    pages: list[PageContent] = field(default_factory=list)
    text: str = ""  # saare pages ka combined, cleaned text

    def page_text(self, page_number: int) -> str:
        for p in self.pages:
            if p.page_number == page_number:
                return p.text
        return ""


# ---------------------------------------------------------------------------
# Text cleaning — clean enough, but information-preserving
# ---------------------------------------------------------------------------

def _clean_text(raw: str) -> str:
    """
    Halka cleaning: extra blank lines aur trailing whitespace hatao.
    Content ko change/reword NAHI karte (e.g. "3+ years" waisa hi rahega).
    """
    if not raw:
        return ""

    # Trailing whitespace per line
    lines = [line.rstrip() for line in raw.splitlines()]

    # 3+ consecutive blank lines -> max 1 blank line
    cleaned_lines: list[str] = []
    blank_run = 0
    for line in lines:
        if line.strip() == "":
            blank_run += 1
            if blank_run <= 1:
                cleaned_lines.append("")
        else:
            blank_run = 0
            cleaned_lines.append(line)

    cleaned = "\n".join(cleaned_lines).strip()
    # Multiple spaces within a line -> single space (safe, doesn't touch numbers/symbols)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned


# ---------------------------------------------------------------------------
# File-level validation
# ---------------------------------------------------------------------------

def _validate_file(path: Path) -> None:
    if not path.exists() or not path.is_file():
        raise DocumentNotFoundError(f"File not found: {path}")

    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise UnsupportedFileTypeError(
            f"Unsupported file type '{path.suffix}'. "
            f"Supported types: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )

    if path.stat().st_size == 0:
        raise EmptyFileError(f"File is empty (0 bytes): {path.name}")


# ---------------------------------------------------------------------------
# PDF extraction
# ---------------------------------------------------------------------------

def _extract_pdf(path: Path) -> tuple[list[PageContent], int]:
    try:
        doc = pymupdf.open(path)
    except Exception as exc:  # noqa: BLE001
        raise DocumentParseError(f"Could not open PDF '{path.name}': {exc}") from exc

    pages: list[PageContent] = []
    try:
        for i, page in enumerate(doc, start=1):
            raw_page_text = page.get_text("text")
            pages.append(PageContent(page_number=i, text=_clean_text(raw_page_text)))
    except Exception as exc:  # noqa: BLE001
        raise DocumentParseError(f"Failed reading pages from '{path.name}': {exc}") from exc
    finally:
        doc.close()

    return pages, len(pages)


# ---------------------------------------------------------------------------
# DOCX extraction
# ---------------------------------------------------------------------------

def _extract_docx(path: Path) -> tuple[list[PageContent], int]:
    try:
        document = docx.Document(str(path))
    except Exception as exc:  # noqa: BLE001
        raise DocumentParseError(f"Could not open DOCX '{path.name}': {exc}") from exc

    try:
        paragraphs = [p.text for p in document.paragraphs]
    except Exception as exc:  # noqa: BLE001
        raise DocumentParseError(f"Failed reading content from '{path.name}': {exc}") from exc

    raw_text = "\n".join(paragraphs)
    cleaned = _clean_text(raw_text)

    # DOCX mein native "pages" ka concept file-format level par nahi hota,
    # isliye poora document ek single logical "page" ki tarah treat karte hain.
    pages = [PageContent(page_number=1, text=cleaned)]
    return pages, 1


# ---------------------------------------------------------------------------
# Scanned / empty detection
# ---------------------------------------------------------------------------

def _check_extractable_quality(pages: list[PageContent], file_type: str) -> None:
    combined = "\n".join(p.text for p in pages).strip()

    if not combined:
        if file_type == "pdf":
            raise ScannedDocumentError(
                "This PDF has no extractable text — it may be a scanned/image-based "
                "PDF. Please upload a text-based PDF or DOCX. (OCR is not supported yet.)"
            )
        raise EmptyDocumentError("Document contains no extractable text.")

    if file_type == "pdf" and pages:
        avg_chars_per_page = len(combined) / len(pages)
        if avg_chars_per_page < MIN_CHARS_PER_PAGE_THRESHOLD:
            raise ScannedDocumentError(
                "This PDF appears to contain little to no extractable text per page "
                "— it may be scanned/image-based. Please upload a text-based PDF or "
                "DOCX. (OCR is not supported yet.)"
            )


# ---------------------------------------------------------------------------
# PUBLIC API — single document in, DocumentContent out
# ---------------------------------------------------------------------------

def ingest_document(file_path: str | Path) -> DocumentContent:
    """
    Ek single document (PDF ya DOCX) ko raw text mein convert karta hai.

    Higher-level code (jaise resume batch processing) is function ko
    loop mein call karega — folder management ya multi-file logic
    ingestion.py ka kaam nahi hai.

    Raises:
        DocumentNotFoundError, UnsupportedFileTypeError, EmptyFileError,
        DocumentParseError, EmptyDocumentError, ScannedDocumentError
    """
    path = Path(file_path)
    _validate_file(path)

    file_type = path.suffix.lower().lstrip(".")

    if file_type == "pdf":
        pages, page_count = _extract_pdf(path)
    elif file_type == "docx":
        pages, page_count = _extract_docx(path)
    else:
        # _validate_file already guards this, but keep it explicit/safe.
        raise UnsupportedFileTypeError(f"Unsupported file type '{path.suffix}'")

    _check_extractable_quality(pages, file_type)

    combined_text = "\n\n".join(p.text for p in pages if p.text).strip()

    return DocumentContent(
        filename=path.name,
        file_type=file_type,
        source_path=str(path),
        page_count=page_count,
        pages=pages,
        text=combined_text,
    )


def ingest_documents(
    file_paths: list[str | Path],
) -> tuple[list[DocumentContent], dict[str, str]]:
    """
    Convenience helper: multiple documents ko ingest karta hai.

    Ek file fail ho jaaye to poora batch crash nahi hota — us file ka
    error capture hoke saath return hota hai, taaki UI selectively
    dikha sake ki kaunsi file fail hui aur kyun.

    Returns:
        (successful_documents, {file_path: error_message})
    """
    results: list[DocumentContent] = []
    errors: dict[str, str] = {}

    for fp in file_paths:
        try:
            results.append(ingest_document(fp))
        except IngestionError as exc:
            errors[str(fp)] = str(exc)

    return results, errors