"""
core/summary.py
================

Mapping + Grouping + CandidateProfile ko recruiter-facing, evidence-backed
`CandidateSummary` mein convert karta hai.

    CandidateProfile + JobDescription + List[Mapping] + CandidateGroup
                            |
                       summary.py
                            |
                     CandidateSummary
                (fit_summary, strengths, gaps,
                 key_evidence, validation_areas)

Core rule (sabse important):

    Summary mein koi naya fact nahi aana chahiye jo CandidateProfile,
    JobDescription, ya Mapping.evidence se supported na ho.

Source hierarchy jo hallucination rokti hai:

    1. Mapping.status        (kya decide hua)
    2. Requirement.priority  (kitna important tha)
    3. Mapping.evidence      (kyun decide hua)
    4. CandidateProfile      (context ke liye)
    5. CandidateGroup        (overall bucket, already grouping.py se decided)

Hybrid design:
    - strengths / gaps / validation_areas / key_evidence -> fully
      DETERMINISTIC, seedha Mapping data se derive hote hain.
    - fit_summary -> deterministic base sentence ban sakta hai; optionally
      LLM (`candidate_summary.txt`) sirf WORDING polish karta hai — usko
      naye facts nahi, sirf already-computed structured facts diye jaate
      hain (raw resume phir se nahi bheja jaata).

Is file mein jaan-bujh kar NAHI hai:
    - PDF/resume/JD parsing          -> ingestion.py / extraction.py
    - Requirement-level matching      -> mapping.py
    - Group assignment                 -> grouping.py
    - Interview questions               -> interview.py
    - Candidate score/rank/hire-decision (koi bhi number jaisa "87%")
    - Database                          -> db/
    - Streamlit UI                      -> pages/
"""

from __future__ import annotations

import logging

from pydantic import BaseModel

from llm.client import LLMError, generate_from_prompt_file
from models.schemas import (
    CandidateGroup,
    CandidateProfile,
    CandidateSummary,
    Evidence,
    JobDescription,
    Mapping,
    MappingStatus,
    Requirement,
    RequirementPriority,
)

logger = logging.getLogger("hireflow.summary")

# Key evidence list ko dashboard-friendly rakhne ke liye cap.
MAX_KEY_EVIDENCE_ITEMS = 8


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class SummaryError(Exception):
    """Base class for summary.py errors."""


class InvalidSummaryInputError(SummaryError):
    """Candidate/job/mappings data summary banane ke liye insufficient hai."""


# ---------------------------------------------------------------------------
# Internal LLM-output model (sirf fit_summary wording polish ke liye)
# ---------------------------------------------------------------------------

class _LLMFitSummary(BaseModel):
    fit_summary: str


# ---------------------------------------------------------------------------
# Step 0 — validation + requirement lookup helper
# ---------------------------------------------------------------------------

def validate_summary_input(
    candidate: CandidateProfile, job: JobDescription, mappings: list[Mapping]
) -> None:
    if candidate is None:
        raise InvalidSummaryInputError("candidate is required to build a summary.")
    if job is None or not job.requirements:
        raise InvalidSummaryInputError("job with at least one requirement is required.")
    if not mappings:
        raise InvalidSummaryInputError("No mappings provided for this candidate.")

    mismatched = [m for m in mappings if m.candidate_id != candidate.candidate_id]
    if mismatched:
        raise InvalidSummaryInputError(
            f"{len(mismatched)} mapping(s) belong to a different candidate_id "
            f"than '{candidate.candidate_id}'."
        )


def _sort_key(requirement: "Requirement | None") -> tuple:
    """MUST_HAVE requirements pehle, phir title se stable order."""
    if requirement is None:
        return (2, "")
    priority_rank = 0 if requirement.priority == RequirementPriority.MUST_HAVE else 1
    return (priority_rank, requirement.title)


# ---------------------------------------------------------------------------
# Step 1 — strengths: role-relative, MET requirements
# ---------------------------------------------------------------------------

def extract_strengths(mappings: list, job: JobDescription) -> list:
    """
    Strengths sirf wahi hain jo JD requirement ke against MET hue —
    resume mein koi bhi skill ho, agar wo requirement se relevant nahi
    (ya requirement khud MET nahi hui), strength mein nahi aata.
    """
    met_mappings = [m for m in mappings if m.status == MappingStatus.MET]
    met_mappings.sort(key=lambda m: _sort_key(job.requirement_by_id(m.requirement_id)))

    strengths = []
    for m in met_mappings:
        requirement = job.requirement_by_id(m.requirement_id)
        if requirement is None:
            continue
        strengths.append(requirement.title)
    return strengths


# ---------------------------------------------------------------------------
# Step 2 — gaps: MISSING/PARTIAL, must-have prioritized
# ---------------------------------------------------------------------------

def extract_gaps(mappings: list, job: JobDescription) -> list:
    """
    Gaps mein MISSING aur PARTIAL dono aate hain. Wording status ke
    hisaab se differ karti hai — "no evidence found" MISSING ke liye,
    "partially evidenced" PARTIAL ke liye. "No evidence" != "evidence of
    absence" — hum kabhi ye nahi bolte ki candidate ke paas skill nahi
    hai, sirf ye ki provided profile mein evidence nahi mila.
    """
    gap_mappings = [
        m for m in mappings if m.status in (MappingStatus.MISSING, MappingStatus.PARTIAL)
    ]
    gap_mappings.sort(key=lambda m: _sort_key(job.requirement_by_id(m.requirement_id)))

    gaps = []
    for m in gap_mappings:
        requirement = job.requirement_by_id(m.requirement_id)
        if requirement is None:
            continue
        if m.status == MappingStatus.MISSING:
            gaps.append(f"No explicit evidence of {requirement.title} was found in the profile.")
        else:  # PARTIAL
            gaps.append(
                m.reason
                or f"{requirement.title} is partially evidenced but not fully confirmed."
            )
    return gaps


# ---------------------------------------------------------------------------
# Step 3 — validation areas: UNCLEAR + PARTIAL (needs confirmation)
# ---------------------------------------------------------------------------

def extract_validation_areas(mappings: list, job: JobDescription) -> list:
    """
    UNCLEAR ka matlab hai ambiguous evidence — interview mein confirm
    karna chahiye. PARTIAL bhi validation-worthy hai (evidence hai but
    incomplete). Ye list seedha `interview.py` ke liye bridge banegi.
    """
    validation_mappings = [
        m for m in mappings if m.status in (MappingStatus.UNCLEAR, MappingStatus.PARTIAL)
    ]
    validation_mappings.sort(key=lambda m: _sort_key(job.requirement_by_id(m.requirement_id)))

    areas = []
    for m in validation_mappings:
        requirement = job.requirement_by_id(m.requirement_id)
        if requirement is None:
            continue
        areas.append(f"Confirm {requirement.title}: {m.reason}".rstrip(": "))
    return areas


# ---------------------------------------------------------------------------
# Step 4 — key evidence: explainability ("Why?" button) ka data source
# ---------------------------------------------------------------------------

def select_key_evidence(mappings: list, job: JobDescription) -> list:
    """
    MET/PARTIAL mappings ke evidence ko collect karta hai — ye vahi
    Evidence objects hain jo Mapping mein already attached the, koi naya
    evidence yahan invent nahi hota. Duplicate text dedupe + count cap.
    """
    relevant_mappings = [
        m for m in mappings if m.status in (MappingStatus.MET, MappingStatus.PARTIAL)
    ]
    relevant_mappings.sort(key=lambda m: _sort_key(job.requirement_by_id(m.requirement_id)))

    seen_texts = set()
    key_evidence = []

    for m in relevant_mappings:
        for ev in m.evidence:
            key = ev.text.strip().lower()
            if key and key not in seen_texts:
                seen_texts.add(key)
                key_evidence.append(ev)
            if len(key_evidence) >= MAX_KEY_EVIDENCE_ITEMS:
                return key_evidence

    return key_evidence


# ---------------------------------------------------------------------------
# Step 5 — fit_summary: deterministic base, optional LLM wording polish
# ---------------------------------------------------------------------------

def _deterministic_fit_summary(
    candidate: CandidateProfile,
    job: JobDescription,
    group: CandidateGroup,
    strengths: list,
    gaps: list,
    validation_areas: list,
) -> str:
    """
    Purely template-based fallback — koi LLM call nahi. Ye hamesha
    available rehta hai, chahe LLM down ho ya use_llm=False ho.
    """
    must_haves = job.must_haves

    sentences = []

    if must_haves:
        sentences.append(
            f"The profile shows direct evidence for {len(strengths)} relevant "
            f"requirement(s) out of {len(job.requirements)} evaluated, including "
            f"{len(must_haves)} must-have requirement(s)."
        )
    else:
        sentences.append(
            f"The profile shows direct evidence for {len(strengths)} of "
            f"{len(job.requirements)} evaluated requirements."
        )

    if strengths:
        shown = ", ".join(strengths[:5])
        more = "" if len(strengths) <= 5 else f", and {len(strengths) - 5} more"
        sentences.append(f"Evidenced strengths include {shown}{more}.")

    if gaps:
        shown = ", ".join(g.split(":")[0] if ":" in g else g for g in gaps[:3])
        sentences.append(f"Gaps or partial evidence were found for: {shown}.")

    if validation_areas:
        sentences.append(
            f"{len(validation_areas)} area(s) have ambiguous or incomplete evidence "
            f"and may benefit from interview confirmation."
        )

    return " ".join(sentences)


def generate_fit_summary(
    candidate: CandidateProfile,
    job: JobDescription,
    group: CandidateGroup,
    strengths: list,
    gaps: list,
    validation_areas: list,
    use_llm: bool = True,
) -> str:
    """
    Deterministic base summary banata hai; agar `use_llm=True` hai to
    isi structured data (raw resume NAHI) ko LLM ko dekar sirf wording
    polish karwata hai. LLM fail ho jaaye to deterministic version hi
    return hoti hai — summary generation kabhi crash nahi hoti.
    """
    base_summary = _deterministic_fit_summary(candidate, job, group, strengths, gaps, validation_areas)

    if not use_llm:
        return base_summary

    must_haves = job.must_haves
    must_have_titles = {r.title for r in must_haves}
    must_have_met_titles = [s for s in strengths if s in must_have_titles]

    try:
        result = generate_from_prompt_file(
            "candidate_summary",
            schema=_LLMFitSummary,
            operation="candidate_summary",
            candidate_name=candidate.name,
            group=group.value,
            must_have_met=str(len(must_have_met_titles)),
            must_have_total=str(len(must_haves)),
            must_have_met_titles=", ".join(must_have_met_titles) or "none",
            strengths=", ".join(strengths) or "none",
            gaps="; ".join(gaps) or "none",
            validation_areas="; ".join(validation_areas) or "none",
        )
        return result.fit_summary
    except LLMError as exc:
        # fit_summary "nice to have" polish hai — LLM fail hone par poori
        # summary generation fail nahi honi chahiye, deterministic fallback.
        logger.warning(
            "LLM fit_summary polish failed, using deterministic summary | candidate=%s | error=%s",
            candidate.candidate_id,
            exc,
        )
        return base_summary


# ---------------------------------------------------------------------------
# PUBLIC API — one candidate
# ---------------------------------------------------------------------------

def build_candidate_summary(
    candidate: CandidateProfile,
    job: JobDescription,
    mappings: list,
    group: CandidateGroup,
    use_llm: bool = True,
) -> CandidateSummary:
    """
    Ek candidate ka complete `CandidateSummary` banata hai.

    Args:
        candidate: extraction.py se aaya CandidateProfile
        job: extraction.py se aaya JobDescription
        mappings: mapping.map_candidate_to_job(candidate, job.requirements) ka output
        group: grouping.classify_candidate(...) ka output
        use_llm: True to LLM sirf fit_summary ki wording polish karta hai
                 (facts nahi badalta); False to purely deterministic sentence.

    Raises:
        InvalidSummaryInputError -- candidate/job/mappings missing ya mismatched hain
    """
    validate_summary_input(candidate, job, mappings)

    strengths = extract_strengths(mappings, job)
    gaps = extract_gaps(mappings, job)
    validation_areas = extract_validation_areas(mappings, job)
    key_evidence = select_key_evidence(mappings, job)
    fit_summary = generate_fit_summary(
        candidate, job, group, strengths, gaps, validation_areas, use_llm=use_llm
    )

    return CandidateSummary(
        candidate_id=candidate.candidate_id,
        candidate_name=candidate.name,
        group=group,
        fit_summary=fit_summary,
        strengths=strengths,
        gaps=gaps,
        key_evidence=key_evidence,
        validation_areas=validation_areas,
    )


# ---------------------------------------------------------------------------
# PUBLIC API — multiple candidates
# ---------------------------------------------------------------------------

def build_candidate_summaries(
    candidates: list,
    job: JobDescription,
    mappings_by_candidate: dict,
    groups_by_candidate: dict,
    use_llm: bool = True,
) -> dict:
    """
    Poore candidate pool ke liye summaries banata hai. `mapping.py` ke
    `map_candidates_to_job(...)` aur `grouping.py` ke `group_candidates(...)`
    outputs ko directly yahan pass kiya ja sakta hai.

    Ek candidate ka data invalid ho to poora batch crash nahi hota — us
    candidate ko minimal/empty summary milti hai (`NEEDS_VALIDATION` group
    ke saath, agar group already pata nahi), taaki UI still render ho sake.
    """
    summaries = {}

    for candidate in candidates:
        candidate_mappings = mappings_by_candidate.get(candidate.candidate_id, [])
        group = groups_by_candidate.get(candidate.candidate_id, CandidateGroup.NEEDS_VALIDATION)
        try:
            summaries[candidate.candidate_id] = build_candidate_summary(
                candidate, job, candidate_mappings, group, use_llm=use_llm
            )
        except InvalidSummaryInputError as exc:
            logger.warning(
                "Could not build summary, using minimal fallback | candidate=%s | error=%s",
                candidate.candidate_id,
                exc,
            )
            summaries[candidate.candidate_id] = CandidateSummary(
                candidate_id=candidate.candidate_id,
                candidate_name=candidate.name,
                group=group,
                fit_summary="Insufficient mapping data to generate a summary.",
            )

    return summaries