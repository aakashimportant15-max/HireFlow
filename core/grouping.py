"""
core/grouping.py
=================

Mapping ke baad candidates ko meaningful, EXPLAINABLE screening groups
mein organize karta hai.

    List[Mapping]  (per requirement: MET / PARTIAL / UNCLEAR / MISSING)
            |
      grouping.py
            |
      CandidateGroup  (STRONG_MATCH / PARTIAL_MATCH / NEEDS_VALIDATION / WEAK_MATCH)

Core principle — DETERMINISTIC, RULE-BASED, NO LLM:
    Mapping already structured hai (status + priority per requirement),
    isliye grouping sirf counting aur transparent thresholds se decide
    hoti hai. Ye recruiter ko clearly explain karne layak hona chahiye:
    "candidate ko yeh group kyun mila."

Priority-aware: MUST_HAVE requirements ka weight NICE_TO_HAVE se zyada
hota hai. Ek NICE_TO_HAVE missing hona candidate ko weak nahi banata.

Confidence scores ko blindly sum/average karke score nahi banaya jaata —
grouping candidate ranking algorithm NAHI hai, evidence-based screening
bucket hai.

Is file mein jaan-bujh kar NAHI hai:
    - Groq/LLM call             -> mapping.py already resolve kar chuka
    - Prompt content              -> llm/prompts/*.txt
    - PDF/resume/JD parsing        -> ingestion.py / extraction.py
    - Requirement-level matching    -> mapping.py
    - Interview questions            -> interview.py
    - Candidate summary text          -> summary.py
    - Database                        -> db/
    - Streamlit UI                    -> pages/
"""

from __future__ import annotations

from dataclasses import dataclass, field

from models.schemas import (
    CandidateGroup,
    CandidateProfile,
    Mapping,
    MappingStatus,
    Requirement,
    RequirementPriority,
)

# ---------------------------------------------------------------------------
# Tunable thresholds — sab ek jagah, taaki baad mein testing se tune kar sako
# bina rules ke logic ko chhede.
# ---------------------------------------------------------------------------

# Agar MUST_HAVE requirements ka itna fraction MISSING hai -> WEAK_MATCH
WEAK_MATCH_MISSING_RATIO = 0.6

# Agar MUST_HAVE requirements ka itna fraction UNCLEAR hai (aur missing
# dominant nahi hai) -> NEEDS_VALIDATION
NEEDS_VALIDATION_UNCLEAR_RATIO = 0.5

# Agar MUST_HAVE requirements ka itna fraction MET hai aur koi major
# unresolved gap nahi hai -> STRONG_MATCH
STRONG_MATCH_MET_RATIO = 0.9


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class GroupingError(Exception):
    """Base class for grouping.py errors."""


class InvalidGroupingInputError(GroupingError):
    """Candidate/mappings/requirements data grouping ke liye insufficient hai."""


# ---------------------------------------------------------------------------
# Internal summary — grouping decision isi par based hoti hai
# ---------------------------------------------------------------------------

@dataclass
class MappingSummary:
    """
    Ek candidate ke mappings ka aggregated, priority-aware breakdown.
    Ye recruiter-explainable grouping decisions ka base hai.
    """

    total: int = 0
    met: int = 0
    partial: int = 0
    unclear: int = 0
    missing: int = 0

    must_have_total: int = 0
    must_have_met: int = 0
    must_have_partial: int = 0
    must_have_unclear: int = 0
    must_have_missing: int = 0

    nice_to_have_total: int = 0
    nice_to_have_met: int = 0
    nice_to_have_partial: int = 0
    nice_to_have_unclear: int = 0
    nice_to_have_missing: int = 0

    unresolved_must_haves: list[str] = field(default_factory=list)  # requirement titles


# ---------------------------------------------------------------------------
# Step 1 — validate input
# ---------------------------------------------------------------------------

def validate_grouping_input(
    candidate: CandidateProfile,
    mappings: list[Mapping],
    requirements: list[Requirement],
) -> None:
    if candidate is None:
        raise InvalidGroupingInputError("candidate is required for grouping.")
    if not requirements:
        raise InvalidGroupingInputError("No requirements provided to group against.")
    if not mappings:
        raise InvalidGroupingInputError("No mappings provided for this candidate.")

    mismatched = [m for m in mappings if m.candidate_id != candidate.candidate_id]
    if mismatched:
        raise InvalidGroupingInputError(
            f"{len(mismatched)} mapping(s) belong to a different candidate_id "
            f"than '{candidate.candidate_id}'."
        )


# ---------------------------------------------------------------------------
# Step 2 — aggregate mappings (priority-aware)
# ---------------------------------------------------------------------------

def summarize_mappings(
    mappings: list[Mapping], requirements: list[Requirement]
) -> MappingSummary:
    """
    Mappings ko requirement priority ke saath aggregate karta hai.
    Grouping decision sirf is summary par based hoti hai — raw mapping
    list par nahi.
    """
    requirement_by_id: dict[str, Requirement] = {r.id: r for r in requirements}
    summary = MappingSummary(total=len(mappings))

    status_counters = {
        MappingStatus.MET: "met",
        MappingStatus.PARTIAL: "partial",
        MappingStatus.UNCLEAR: "unclear",
        MappingStatus.MISSING: "missing",
    }

    for mapping in mappings:
        requirement = requirement_by_id.get(mapping.requirement_id)
        if requirement is None:
            # Mapping kisi unknown requirement ko refer karta hai — is
            # module ka kaam data validate karna nahi (wo mapping.py ka
            # kaam hai), so skip karte hain overall count se, but crash
            # nahi karte.
            continue

        attr = status_counters[mapping.status]
        setattr(summary, attr, getattr(summary, attr) + 1)

        if requirement.priority == RequirementPriority.MUST_HAVE:
            summary.must_have_total += 1
            setattr(
                summary, f"must_have_{attr}", getattr(summary, f"must_have_{attr}") + 1
            )
            if mapping.status in (MappingStatus.MISSING, MappingStatus.UNCLEAR, MappingStatus.PARTIAL):
                summary.unresolved_must_haves.append(requirement.title)
        else:
            summary.nice_to_have_total += 1
            setattr(
                summary,
                f"nice_to_have_{attr}",
                getattr(summary, f"nice_to_have_{attr}") + 1,
            )

    return summary


# ---------------------------------------------------------------------------
# Step 3 — classification rules (deterministic, explainable)
# ---------------------------------------------------------------------------

def _classify_from_summary(summary: MappingSummary) -> CandidateGroup:
    """
    MUST_HAVE requirements decision-critical hain. Agar JD mein koi
    MUST_HAVE requirement hi na ho (edge case), NICE_TO_HAVE counts par
    fallback karte hain taaki grouping crash na ho.
    """
    basis_total = summary.must_have_total
    basis_met = summary.must_have_met
    basis_partial = summary.must_have_partial
    basis_unclear = summary.must_have_unclear
    basis_missing = summary.must_have_missing

    if basis_total == 0:
        # Fallback: koi must-have requirement nahi thi, poori JD nice-to-have hai.
        basis_total = summary.nice_to_have_total
        basis_met = summary.nice_to_have_met
        basis_partial = summary.nice_to_have_partial
        basis_unclear = summary.nice_to_have_unclear
        basis_missing = summary.nice_to_have_missing

    if basis_total == 0:
        # Koi bhi requirement resolve hi nahi hui (sab unknown ids) —
        # ye data-quality issue hai, but hume kuch to return karna hai.
        return CandidateGroup.NEEDS_VALIDATION

    missing_ratio = basis_missing / basis_total
    unclear_ratio = basis_unclear / basis_total
    met_ratio = basis_met / basis_total

    # Rule A — majority of important requirements missing -> WEAK_MATCH
    if missing_ratio >= WEAK_MATCH_MISSING_RATIO:
        return CandidateGroup.WEAK_MATCH

    # Rule B — uncertainty dominates over hard gaps -> NEEDS_VALIDATION
    if unclear_ratio >= NEEDS_VALIDATION_UNCLEAR_RATIO:
        return CandidateGroup.NEEDS_VALIDATION

    # Rule D — important requirements mostly satisfied, no major unresolved gap
    if met_ratio >= STRONG_MATCH_MET_RATIO and basis_missing == 0:
        return CandidateGroup.STRONG_MATCH

    # Rule C — meaningful evidence exists, but real gaps remain
    if basis_partial > 0 or basis_missing > 0 or basis_met > 0:
        return CandidateGroup.PARTIAL_MATCH

    # Fallback (should rarely hit — e.g. everything UNCLEAR but below threshold)
    return CandidateGroup.NEEDS_VALIDATION


# ---------------------------------------------------------------------------
# PUBLIC API — one candidate
# ---------------------------------------------------------------------------

def classify_candidate(
    candidate: CandidateProfile,
    mappings: list[Mapping],
    requirements: list[Requirement],
) -> CandidateGroup:
    """
    Ek candidate ke Mapping results ko dekhkar screening group decide
    karta hai.

    Args:
        candidate: jiska group nikalna hai
        mappings: mapping.map_candidate_to_job(candidate, requirements) ka output
        requirements: JobDescription.requirements (priority lookup ke liye)

    Returns:
        CandidateGroup

    Raises:
        InvalidGroupingInputError -- candidate/mappings/requirements missing
            ya mismatched hain
    """
    validate_grouping_input(candidate, mappings, requirements)
    summary = summarize_mappings(mappings, requirements)
    return _classify_from_summary(summary)


# ---------------------------------------------------------------------------
# PUBLIC API — multiple candidates
# ---------------------------------------------------------------------------

def group_candidates(
    candidates: list[CandidateProfile],
    mappings_by_candidate: dict[str, list[Mapping]],
    requirements: list[Requirement],
) -> dict[str, CandidateGroup]:
    """
    Poore candidate pool ko classify karta hai. `mapping.py` ke
    `map_candidates_to_job(...)` output (`{candidate_id: List[Mapping]}`)
    ko directly yahan pass kiya ja sakta hai.

    Ek candidate ka data invalid/missing ho to poora batch crash nahi
    hota — us candidate ko `NEEDS_VALIDATION` mark karke continue hota hai
    (missing/ambiguous data khud ek "verify karo" signal hai).

    Returns:
        {candidate_id: CandidateGroup}
    """
    results: dict[str, CandidateGroup] = {}

    for candidate in candidates:
        candidate_mappings = mappings_by_candidate.get(candidate.candidate_id, [])
        try:
            results[candidate.candidate_id] = classify_candidate(
                candidate, candidate_mappings, requirements
            )
        except InvalidGroupingInputError:
            results[candidate.candidate_id] = CandidateGroup.NEEDS_VALIDATION

    return results