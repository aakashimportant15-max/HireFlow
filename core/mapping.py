"""
core/mapping.py
================

HireFlow ka "requirement-to-evidence bridge".

    JobDescription.requirements  +  CandidateProfile
                        |
                   mapping.py
                        |
                   List[Mapping]   (status: MET / PARTIAL / MISSING / UNCLEAR)

Extraction batata hai: "candidate ke resume mein kya hai?"
Mapping batata hai:    "JD ki har requirement ke against us information
                         ka factual status kya hai?"

Design principle — evidence-first, hybrid matching:

    Requirement
        |
        +-- exact / normalized match  (fast, deterministic, no LLM call)
        |
        +-- experience/years check    (deterministic, arithmetic)
        |
        +-- ambiguous / semantic case -> llm/client.py -> LLM reasoning
        |
        v
    Mapping

Is file mein jaan-bujh kar NAHI hai:
    - PDF/DOCX parsing              -> ingestion.py
    - Resume/JD extraction          -> extraction.py
    - Groq SDK directly             -> llm/client.py
    - Overall candidate score/rank/hire-decision -> grouping.py / summary.py
    - Database                      -> db/
    - Streamlit UI                  -> pages/
"""

from __future__ import annotations

import logging
import re

from pydantic import BaseModel, Field

from llm.client import LLMError, LLMValidationError, generate_from_prompt_file
from models.schemas import (
    CandidateProfile,
    Evidence,
    EvidenceSource,
    Mapping,
    MappingStatus,
    Requirement,
    RequirementCategory,
)

logger = logging.getLogger("hireflow.mapping")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class MappingError(Exception):
    """Base class for mapping.py errors."""


class InvalidMappingInputError(MappingError):
    """Candidate ya JD data mapping ke liye insufficient/malformed hai."""


class RequirementMappingError(MappingError):
    """Ek specific requirement ka mapping fail hua (e.g. LLM call fail)."""


# ---------------------------------------------------------------------------
# Internal LLM-output model (NOT a schemas.py business entity — sirf
# resolve_ambiguous_match() ka internal parsing contract)
# ---------------------------------------------------------------------------

class _LLMEvidenceItem(BaseModel):
    text: str
    location: str = "profile"


class _LLMMappingResult(BaseModel):
    status: MappingStatus
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    evidence: list[_LLMEvidenceItem] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

# Chhoti si synonym table — common wording variations jo exact-match ko
# unnecessarily MISSING/UNCLEAR mein daal dete hain.
_SYNONYMS: dict[str, str] = {
    "postgres": "postgresql",
    "js": "javascript",
    "ts": "typescript",
    "k8s": "kubernetes",
    "aws cloud": "aws",
    "amazon web services": "aws",
    "reactjs": "react",
    "react.js": "react",
    "nodejs": "node",
    "node.js": "node",
}

# Small, explicit requirement aliases. These bridge common JD/resume wording
# without turning mapping into fuzzy ranking. Every match still comes from
# text actually present in the candidate profile.
_REQUIREMENT_ALIASES: dict[str, set[str]] = {
    "erp / accounting software": {
        "erp", "erp system", "erp systems", "accounting software",
        "oracle", "sap", "sage", "quickbooks",
    },
    "accounting experience": {
        "accounting", "cost accounting", "general ledger",
        "accounts payable", "accounts receivable", "financial controller",
        "controller", "accounting analyst", "financial analyst",
    },
    "general ledger & month-end close": {
        "general ledger", "month end close", "month-end close",
        "month end closing", "month-end closing",
    },
    "financial reporting": {
        "financial reporting", "financial reports", "financial statements",
    },
    "sox / compliance": {
        "sox", "sox compliance", "internal controls",
        "financial compliance", "audit support",
    },
    "advanced excel / bi": {
        "excel", "power bi", "business intelligence", "bi reporting",
    },
    "payroll / fixed assets": {
        "payroll", "payroll processing", "payroll software",
        "fixed assets", "fixed asset management",
    },
}


def _normalize(text: str) -> str:
    """Normalize text for deterministic matching."""
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9+.\s-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return _SYNONYMS.get(text, text)


def _requirement_terms(requirement: Requirement) -> set[str]:
    """
    Return the normalized requirement title plus normalized explicit aliases.

    Alias keys are normalized at lookup time as well, so punctuation such as
    "/", "&", and "-" in a requirement title cannot prevent an alias match.
    """
    target = _normalize(requirement.title)

    aliases_by_normalized_key = {
        _normalize(alias_key): aliases
        for alias_key, aliases in _REQUIREMENT_ALIASES.items()
    }

    aliases = aliases_by_normalized_key.get(target, set())

    return {
        target,
        *(_normalize(alias) for alias in aliases),
    }


def _text_matches_requirement(text: str, terms: set[str]) -> bool:
    """Check whether text contains an explicit requirement term."""
    normalized = _normalize(text)
    if not normalized:
        return False
    return any(
        normalized == term or term in normalized
        for term in terms
        if term
    )


# ---------------------------------------------------------------------------
# Step 1 — collect candidate evidence relevant to a requirement
# ---------------------------------------------------------------------------

def find_candidate_evidence(
    requirement: Requirement, candidate: CandidateProfile
) -> list[Evidence]:
    """
    Requirement se related candidate profile ke saare relevant text
    snippets collect karta hai (skills, experience, projects,
    certifications). Ye evidence hi baad mein exact-match / LLM
    reasoning dono ke liye base hai.
    """
    terms = _requirement_terms(requirement)
    evidence: list[Evidence] = []

    for skill in candidate.skills:
        if _text_matches_requirement(skill, terms):
            evidence.append(
                Evidence(text=skill, source=EvidenceSource.RESUME, location="skills")
            )

    for exp in candidate.experience:
        haystack = " ".join(
            filter(None, [exp.title, exp.description])
        )
        if _text_matches_requirement(haystack, terms):
            evidence.append(
                Evidence(
                    text=exp.description or exp.title,
                    source=EvidenceSource.RESUME,
                    location=f"experience: {exp.title}" + (f" at {exp.company}" if exp.company else ""),
                )
            )

    for proj in candidate.projects:
        haystack = " ".join(filter(None, [proj.name, proj.description])) + " " + " ".join(proj.technologies)
        if _text_matches_requirement(haystack, terms):
            evidence.append(
                Evidence(
                    text=proj.description or proj.name,
                    source=EvidenceSource.RESUME,
                    location=f"projects: {proj.name}",
                )
            )

    if candidate.certifications:
        for cert in candidate.certifications:
            if _text_matches_requirement(cert, terms):
                evidence.append(
                    Evidence(text=cert, source=EvidenceSource.RESUME, location="certifications")
                )

    return evidence


# ---------------------------------------------------------------------------
# Step 2a — fast deterministic exact/normalized match
# ---------------------------------------------------------------------------

def check_exact_match(requirement: Requirement, candidate: CandidateProfile) -> bool:
    """
    Simple, cheap check: requirement title candidate ke skills (normalized)
    mein directly milta hai? Agar haan, LLM call ki zarurat hi nahi.
    """
    terms = _requirement_terms(requirement)
    return any(_text_matches_requirement(skill, terms) for skill in candidate.skills)


# ---------------------------------------------------------------------------
# Step 2b — deterministic years-of-experience check
# ---------------------------------------------------------------------------

def _candidate_total_years(candidate: CandidateProfile, requirement: Requirement) -> float | None:
    """
    Requirement se relevant experience entries ke 'years' field ko sum
    karta hai. Agar koi bhi relevant entry mein years nahi diya (None),
    caller ko signal milta hai ki duration UNCLEAR hai.
    """
    terms = _requirement_terms(requirement)
    relevant_entries = [
        exp
        for exp in candidate.experience
        if _text_matches_requirement(
            " ".join(filter(None, [exp.title, exp.description])),
            terms,
        )
    ]

    if not relevant_entries:
        return None

    if any(exp.duration_years is None for exp in relevant_entries):
        return None

    return sum(exp.duration_years or 0.0 for exp in relevant_entries)


def check_experience(
    requirement: Requirement, candidate: CandidateProfile
) -> tuple[MappingStatus, float, str] | None:
    """
    Agar requirement mein `years_required` diya hai to deterministic
    duration comparison karta hai. Returns None agar ye check applicable
    nahi hai (years_required missing) — caller ko exact/semantic path
    try karna chahiye.
    """
    if requirement.years_required is None:
        return None

    candidate_years = _candidate_total_years(candidate, requirement)

    if candidate_years is None:
        # Requirement se relevant koi experience entry nahi mila, ya
        # mila to uska duration candidate ne specify nahi kiya.
        relevant = find_candidate_evidence(requirement, candidate)
        if not relevant:
            return (
                MappingStatus.MISSING,
                0.85,
                f"No evidence of {requirement.title} experience was found.",
            )
        return (
            MappingStatus.UNCLEAR,
            0.4,
            f"{requirement.title} is mentioned but the exact duration of "
            f"experience could not be determined.",
        )

    if candidate_years >= requirement.years_required:
        return (
            MappingStatus.MET,
            0.92,
            f"Candidate has approximately {candidate_years:g} years of "
            f"{requirement.title} experience, meeting the "
            f"{requirement.years_required:g}-year requirement.",
        )

    if candidate_years > 0:
        return (
            MappingStatus.PARTIAL,
            0.7,
            f"Candidate has approximately {candidate_years:g} years of "
            f"{requirement.title} experience, short of the "
            f"{requirement.years_required:g}-year requirement.",
        )

    return (
        MappingStatus.MISSING,
        0.8,
        f"No evidence of {requirement.title} experience was found.",
    )


# ---------------------------------------------------------------------------
# Step 2c — ambiguous / semantic cases go to the LLM
# ---------------------------------------------------------------------------

def resolve_ambiguous_match(
    requirement: Requirement, candidate: CandidateProfile
) -> tuple[MappingStatus, float, str, list[Evidence]]:
    """
    Deterministic checks jab confidently decide nahi kar paate (semantic
    similarity chahiye, jaise 'REST API' vs 'FastAPI backend services'),
    tab yeh function LLM se reasoning leta hai — evidence-first prompt
    ke saath, taaki hallucination na ho.
    """
    try:
        result: _LLMMappingResult = generate_from_prompt_file(
            "map_requirement",
            schema=_LLMMappingResult,
            operation="requirement_mapping",
            requirement_title=requirement.title,
            requirement_description=requirement.description,
            requirement_category=requirement.category.value,
            requirement_priority=requirement.priority.value,
            requirement_years_required=str(requirement.years_required or "not specified"),
            candidate_name=candidate.name,
            candidate_skills=", ".join(candidate.skills) or "none listed",
            candidate_experience="; ".join(
                f"{exp.title} at {exp.company or 'unknown'} "
                f"({exp.duration_years if exp.duration_years is not None else 'duration unspecified'} years): "
                f"{exp.description or ''}"
                for exp in candidate.experience
                )
            or "none listed",
            candidate_education="; ".join(
                f"{edu.degree} - {edu.institution or 'unknown'}" for edu in candidate.education
            )
            or "none listed",
            candidate_projects="; ".join(
                f"{proj.name}: {proj.description or ''} ({', '.join(proj.technologies)})"
                for proj in candidate.projects
            )
            or "none listed",
            candidate_certifications=", ".join(candidate.certifications or []) or "none listed",
        )
    except LLMValidationError as exc:
        logger.error(
            "Mapping LLM output failed validation | requirement=%s", requirement.title
        )
        raise RequirementMappingError(
            f"Could not resolve match for requirement '{requirement.title}': "
            "LLM returned an invalid response."
        ) from exc
    except LLMError as exc:
        logger.error("Mapping LLM call failed | requirement=%s", requirement.title)
        raise RequirementMappingError(
            f"Could not resolve match for requirement '{requirement.title}': {exc}"
        ) from exc

    evidence = [
        Evidence(text=item.text, source=EvidenceSource.RESUME, location=item.location)
        for item in result.evidence
    ]
    return result.status, result.confidence, result.reason, evidence


# ---------------------------------------------------------------------------
# Step 3 — assemble the final Mapping object
# ---------------------------------------------------------------------------

def build_mapping(
    requirement: Requirement,
    candidate: CandidateProfile,
    status: MappingStatus,
    confidence: float,
    reason: str,
    evidence: list[Evidence],
) -> Mapping:
    return Mapping(
        requirement_id=requirement.id,
        candidate_id=candidate.candidate_id,
        status=status,
        confidence=confidence,
        evidence=evidence,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# PUBLIC API — one requirement
# ---------------------------------------------------------------------------

def map_requirement(requirement: Requirement, candidate: CandidateProfile) -> Mapping:
    """
    Ek requirement ko candidate ke against evaluate karta hai.

    Order of resolution (fast/cheap -> slow/semantic):
        1. Exact/normalized skill match         -> MET (no LLM call)
        2. Years-of-experience deterministic check (agar years_required diya ho)
        3. Ambiguous/semantic case               -> LLM reasoning

    Raises:
        RequirementMappingError -- LLM path fail hua (deterministic paths
                                    fail nahi hoti, worst case MISSING/UNCLEAR
                                    dengi)
    """
    # 1. Fast path: exact/normalized skill match
    if check_exact_match(requirement, candidate):
        evidence = find_candidate_evidence(requirement, candidate)
        # Agar years bhi required hai, exact skill match ke baad bhi
        # duration verify karna zaroori hai.
        if requirement.years_required is not None:
            exp_result = check_experience(requirement, candidate)
            if exp_result is not None:
                status, confidence, reason = exp_result
                return build_mapping(requirement, candidate, status, confidence, reason, evidence)

        return build_mapping(
            requirement,
            candidate,
            MappingStatus.MET,
            0.95,
            f"{requirement.title} is explicitly listed in the candidate's skills.",
            evidence,
        )

    # 2. Deterministic years-of-experience check (categories where it applies)
    if requirement.category == RequirementCategory.EXPERIENCE and requirement.years_required is not None:
        exp_result = check_experience(requirement, candidate)
        if exp_result is not None:
            status, confidence, reason = exp_result
            evidence = find_candidate_evidence(requirement, candidate)
            return build_mapping(requirement, candidate, status, confidence, reason, evidence)

    # 3. No relevant evidence at all -> MISSING without spending an LLM call
    relevant_evidence = find_candidate_evidence(requirement, candidate)
    if not relevant_evidence:
        return build_mapping(
            requirement,
            candidate,
            MappingStatus.MISSING,
            0.8,
            f"No evidence of {requirement.title} was found in the candidate profile.",
            [],
        )

    # 4. Relevant-but-not-exact evidence exists -> ambiguous, let the LLM reason
    status, confidence, reason, evidence = resolve_ambiguous_match(requirement, candidate)
    return build_mapping(requirement, candidate, status, confidence, reason, evidence)


# ---------------------------------------------------------------------------
# PUBLIC API — full job description x one candidate
# ---------------------------------------------------------------------------

def map_candidate_to_job(
    candidate: CandidateProfile, requirements: list[Requirement]
) -> list[Mapping]:
    """
    Candidate ko JobDescription.requirements ki har requirement ke against
    evaluate karta hai.

    Args:
        candidate: extraction.py se aaya CandidateProfile
        requirements: JobDescription.requirements

    Returns:
        List[Mapping] — ek entry per requirement.

    Raises:
        InvalidMappingInputError -- candidate ya requirements khaali/invalid hain
    """
    if candidate is None:
        raise InvalidMappingInputError("candidate is required for mapping.")
    if not requirements:
        raise InvalidMappingInputError("No requirements provided to map against.")

    mappings: list[Mapping] = []
    for requirement in requirements:
        try:
            mappings.append(map_requirement(requirement, candidate))
        except RequirementMappingError as exc:
            logger.error(
                "Requirement mapping failed | requirement=%s | candidate=%s",
                requirement.title,
                candidate.candidate_id,
            )
            # Individual requirement failure se poora candidate mapping crash
            # nahi hona chahiye — UNCLEAR status ke saath continue karte hain,
            # taaki baaki requirements ka result mile.
            mappings.append(
                build_mapping(
                    requirement,
                    candidate,
                    MappingStatus.UNCLEAR,
                    0.0,
                    f"Could not evaluate this requirement due to a system error: {exc}",
                    [],
                )
            )

    return mappings


# ---------------------------------------------------------------------------
# PUBLIC API — multiple candidates x one job description
# ---------------------------------------------------------------------------

def map_candidates_to_job(
    candidates: list[CandidateProfile], requirements: list[Requirement]
) -> dict[str, list[Mapping]]:
    """
    Multiple candidates ko same JD requirements ke against evaluate karta hai.

    Returns:
        {candidate_id: List[Mapping]}
    """
    return {
        candidate.candidate_id: map_candidate_to_job(candidate, requirements)
        for candidate in candidates
    }