"""
core/extraction.py
===================

HireFlow ki intelligence layer: raw text -> structured domain objects.

    Resume.pdf --ingestion--> raw text --extraction--> CandidateProfile
    JD.pdf     --ingestion--> raw text --extraction--> JobDescription

Mental model:

    ingestion.py    "FILE MEIN KYA LIKHA HAI (raw)?"
    extraction.py   "US TEXT KA MATLAB KYA HAI (structured facts)?"
    mapping.py      "FACTS REQUIREMENTS KE AGAINST KAISE COMPARE HOTE HAIN?"

Is file mein jaan-bujh kar NAHI hai:
    - PDF/DOCX parsing            -> ingestion.py
    - Groq SDK / raw API calls    -> llm/client.py
    - Prompt content                -> llm/prompts/*.txt
    - Requirement matching/scoring  -> mapping.py
    - Candidate grouping             -> grouping.py
    - Database                       -> db/
    - Streamlit UI                   -> pages/

`extraction.py` sirf itna jaanta hai:
    "Resume ke liye CandidateProfile chahiye."
    "JD ke liye JobDescription chahiye."

Ye NAHI jaanta:
    "Candidate achha hai ya nahi."
    "Candidate shortlist hoga ya nahi."
"""

from __future__ import annotations

import logging

from llm.client import (
    LLMError,
    LLMValidationError,
    generate_from_prompt_file,
)
from models.schemas import CandidateProfile, JobDescription

logger = logging.getLogger("hireflow.extraction")


# ---------------------------------------------------------------------------
# Errors — extraction-specific, controlled
# ---------------------------------------------------------------------------

class ExtractionError(Exception):
    """Base class for extraction.py errors."""


class EmptyInputError(ExtractionError):
    """Raw text khaali hai — extraction start hi nahi hona chahiye."""


class ResumeExtractionError(ExtractionError):
    """Resume ko CandidateProfile mein convert karte waqt fail hua."""


class JobDescriptionExtractionError(ExtractionError):
    """JD ko JobDescription mein convert karte waqt fail hua."""


# ---------------------------------------------------------------------------
# Shared input guard
# ---------------------------------------------------------------------------

def _require_non_empty_text(raw_text: str, what: str) -> str:
    """
    Extraction ko LLM tak pahunchane se pehle basic guard.

    Note: "resume mein bahut kam text hai" ka detection ingestion.py
    (ScannedDocumentError) ka kaam hai. Yahan sirf ensure karte hain ki
    hume koi non-trivial text mila hai extraction shuru karne ke liye.
    """
    if raw_text is None or not raw_text.strip():
        raise EmptyInputError(f"Cannot extract from empty {what} text.")
    return raw_text.strip()


# ---------------------------------------------------------------------------
# PUBLIC API — Resume extraction
# ---------------------------------------------------------------------------

def extract_resume(raw_text: str, resume_source: str) -> CandidateProfile:
    """
    Raw resume text ko structured `CandidateProfile` mein convert karta hai.

    Args:
        raw_text: `ingestion.ingest_document(...).text` se aaya raw resume text.
        resume_source: filename/path/id — traceability ke liye (schema field
            `resume_source` mein jaata hai).

    Returns:
        CandidateProfile (missing fields honge to bhi valid object milega —
        missing data extraction failure nahi hai).

    Raises:
        EmptyInputError            -- raw_text khaali hai
        ResumeExtractionError      -- LLM call/validation fail hui
    """
    text = _require_non_empty_text(raw_text, "resume")

    try:
        profile = generate_from_prompt_file(
            "extract_resume",
            schema=CandidateProfile,
            operation="resume_extraction",
            resume_text=text,
            resume_source=resume_source,
        )
    except LLMValidationError as exc:
        logger.error("Resume extraction schema validation failed | source=%s", resume_source)
        raise ResumeExtractionError(
            f"Could not extract a valid candidate profile from '{resume_source}'. "
            "The document may be malformed or not resume-like content."
        ) from exc
    except LLMError as exc:
        logger.error("Resume extraction LLM call failed | source=%s", resume_source)
        raise ResumeExtractionError(
            f"Resume extraction failed for '{resume_source}': {exc}"
        ) from exc

    # raw_text preserve karte hain — audit/verification ke liye.
    # LLM khud raw_text nahi return karega (prompt mein nahi maanga), isliye
    # yahan explicitly attach karte hain.
    profile.raw_text = text
    return profile


# ---------------------------------------------------------------------------
# PUBLIC API — Job Description extraction
# ---------------------------------------------------------------------------

def extract_job_description(raw_text: str, jd_source: str) -> JobDescription:
    """
    Raw JD text ko structured `JobDescription` (requirements ke saath)
    mein convert karta hai.

    Args:
        raw_text: `ingestion.ingest_document(...).text` se aaya raw JD text.
        jd_source: filename/path/id — traceability ke liye.

    Returns:
        JobDescription with a list of `Requirement` objects.

    Raises:
        EmptyInputError                -- raw_text khaali hai
        JobDescriptionExtractionError  -- LLM call/validation fail hui
    """
    text = _require_non_empty_text(raw_text, "job description")

    try:
        jd = generate_from_prompt_file(
            "extract_jd",
            schema=JobDescription,
            operation="jd_extraction",
            jd_text=text,
            jd_source=jd_source,
        )
    except LLMValidationError as exc:
        logger.error("JD extraction schema validation failed | source=%s", jd_source)
        raise JobDescriptionExtractionError(
            f"Could not extract a valid job description from '{jd_source}'. "
            "The document may be malformed or not JD-like content."
        ) from exc
    except LLMError as exc:
        logger.error("JD extraction LLM call failed | source=%s", jd_source)
        raise JobDescriptionExtractionError(
            f"JD extraction failed for '{jd_source}': {exc}"
        ) from exc

    jd.raw_text = text
    return jd


# ---------------------------------------------------------------------------
# Batch convenience — mirrors ingestion.ingest_documents' error-collecting style
# ---------------------------------------------------------------------------

def extract_resumes(
    documents: list[tuple[str, str]],
) -> tuple[list[CandidateProfile], dict[str, str]]:
    """
    Multiple resumes extract karta hai.

    Args:
        documents: list of (raw_text, resume_source) tuples — typically
            ingestion.ingest_documents(...) ke output se assemble kiya jaata hai.

    Returns:
        (successful_profiles, {resume_source: error_message})
    """
    results: list[CandidateProfile] = []
    errors: dict[str, str] = {}

    for raw_text, resume_source in documents:
        try:
            results.append(extract_resume(raw_text, resume_source))
        except ExtractionError as exc:
            errors[resume_source] = str(exc)

    return results, errors