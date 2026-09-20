"""
HireFlow - models/schemas.py

Ye file sirf "data ka shape" define karti hai (Pydantic models + enums).
Isme LLM calls, PDF parsing, DB queries, UI, prompts ya scoring logic NAHI hai.

Data flow:
    JD text      -> JobDescription (Requirement list)
    Resume text  -> CandidateProfile
    JD x Profile -> Mapping (status + Evidence)        <- project ka core
    Mappings     -> CandidateSummary (+ CandidateGroup)
    Gaps         -> InterviewQuestion
    Notes        -> InterviewAnswer -> InterviewReport
    NL question  -> PoolQuery
    Har insight  -> AuditRecord                        <- trust layer

Design rules:
  * Status/priority/category free text nahi, enums hain (LLM "probably met" jaisa
    output de to bhi enum tolerant parsing usse handle kar leta hai).
  * Evidence ke bina MET/PARTIAL allowed nahi (auto-downgrade to UNCLEAR).
  * Koi "hire / reject" field nahi. Final decision hamesha human ka hai.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

__all__ = [
    # enums
    "MappingStatus",
    "RequirementPriority",
    "RequirementCategory",
    "EvidenceSource",
    "Difficulty",
    "CandidateGroup",
    "FilterOperator",
    "SortDirection",
    "AuditAction",
    # models
    "Evidence",
    "Requirement",
    "JobDescription",
    "ExperienceItem",
    "ProjectItem",
    "EducationItem",
    "CandidateProfile",
    "Mapping",
    "CandidateSummary",
    "InterviewQuestion",
    "InterviewAnswer",
    "RequirementFinding",
    "InterviewReport",
    "PoolFilter",
    "PoolSort",
    "PoolQuery",
    "AuditRecord",
]


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _dedupe_clean(items: Any) -> list[str]:
    """None / string / list -> clean, de-duplicated list[str] (order preserved)."""
    if items is None:
        return []
    if isinstance(items, str):
        items = [items]
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        text = str(item).strip()
        key = text.lower()
        if text and key not in seen:
            seen.add(key)
            out.append(text)
    return out


def _none_to_list(value: Any) -> Any:
    return [] if value is None else value


class HireFlowModel(BaseModel):
    """Common base: LLM ke extra keys ignore, strings strip."""

    model_config = ConfigDict(
        extra="ignore",
        str_strip_whitespace=True,
    )


class TolerantEnum(str, Enum):
    """
    Enum jo LLM ke messy output ko sambhalta hai:
    "met", "Must-have", "must have" -> MET / MUST_HAVE
    """

    @classmethod
    def _missing_(cls, value: object):
        if isinstance(value, str):
            normalized = value.strip().upper().replace("-", "_").replace(" ", "_")
            for member in cls:
                if member.value == normalized:
                    return member
        return None


# ----------------------------------------------------------------------------
# Enums
# ----------------------------------------------------------------------------
class MappingStatus(TolerantEnum):
    MET = "MET"
    PARTIAL = "PARTIAL"
    UNCLEAR = "UNCLEAR"
    MISSING = "MISSING"


class RequirementPriority(TolerantEnum):
    MUST_HAVE = "MUST_HAVE"
    NICE_TO_HAVE = "NICE_TO_HAVE"


class RequirementCategory(TolerantEnum):
    SKILL = "SKILL"
    EXPERIENCE = "EXPERIENCE"
    EDUCATION = "EDUCATION"
    DOMAIN = "DOMAIN"
    TOOL = "TOOL"
    SOFT_SKILL = "SOFT_SKILL"
    OTHER = "OTHER"

    @classmethod
    def _missing_(cls, value: object):
        # Unknown category par crash nahi, OTHER de do.
        found = super()._missing_(value)
        return found if found is not None else cls.OTHER


class EvidenceSource(TolerantEnum):
    RESUME = "RESUME"
    PORTFOLIO = "PORTFOLIO"
    APPLICATION_FORM = "APPLICATION_FORM"
    INTERVIEW = "INTERVIEW"
    JD = "JD"


class Difficulty(TolerantEnum):
    EASY = "EASY"
    MEDIUM = "MEDIUM"
    HARD = "HARD"


class CandidateGroup(TolerantEnum):
    STRONG_MATCH = "STRONG_MATCH"
    PARTIAL_MATCH = "PARTIAL_MATCH"
    NEEDS_VALIDATION = "NEEDS_VALIDATION"
    WEAK_MATCH = "WEAK_MATCH"


class FilterOperator(TolerantEnum):
    EQ = "EQ"
    NEQ = "NEQ"
    GT = "GT"
    GTE = "GTE"
    LT = "LT"
    LTE = "LTE"
    CONTAINS = "CONTAINS"
    IN = "IN"


class SortDirection(TolerantEnum):
    ASC = "ASC"
    DESC = "DESC"


class AuditAction(TolerantEnum):
    EXTRACT_JD = "EXTRACT_JD"
    EXTRACT_RESUME = "EXTRACT_RESUME"
    MAP_REQUIREMENT = "MAP_REQUIREMENT"
    GROUP_CANDIDATE = "GROUP_CANDIDATE"
    SUMMARIZE_CANDIDATE = "SUMMARIZE_CANDIDATE"
    GENERATE_QUESTION = "GENERATE_QUESTION"
    GENERATE_FOLLOWUP = "GENERATE_FOLLOWUP"
    MAP_INTERVIEW_NOTES = "MAP_INTERVIEW_NOTES"
    GENERATE_REPORT = "GENERATE_REPORT"
    POOL_QUERY = "POOL_QUERY"


# ----------------------------------------------------------------------------
# 1. Evidence: traceability ka base object
# ----------------------------------------------------------------------------
class Evidence(HireFlowModel):
    text: str = Field(..., min_length=1, description="Source ki exact line/snippet")
    source: EvidenceSource = EvidenceSource.RESUME
    location: Optional[str] = Field(
        default=None, description="Page number / section / question, e.g. 'Page 2, Projects'"
    )
    source_id: Optional[str] = Field(
        default=None, description="candidate_id / job_id / question_id jahan se evidence aaya"
    )


# ----------------------------------------------------------------------------
# 2. JD side
# ----------------------------------------------------------------------------
class Requirement(HireFlowModel):
    id: str = Field(default_factory=lambda: _new_id("req"))
    title: str = Field(..., min_length=1)
    description: str = ""
    category: RequirementCategory = RequirementCategory.SKILL
    priority: RequirementPriority = RequirementPriority.MUST_HAVE
    years_required: Optional[float] = Field(default=None, ge=0, le=50)


class JobDescription(HireFlowModel):
    job_id: str = Field(default_factory=lambda: _new_id("job"))
    title: str
    company: Optional[str] = None
    location: Optional[str] = None
    requirements: list[Requirement] = Field(default_factory=list)
    raw_text: Optional[str] = None
    source_file: Optional[str] = None

    _v_reqs = field_validator("requirements", mode="before")(_none_to_list)

    @model_validator(mode="after")
    def _unique_requirement_ids(self) -> "JobDescription":
        seen: set[str] = set()
        for req in self.requirements:
            if req.id in seen:
                req.id = _new_id("req")  # duplicate id (LLM bug) ko fix karo
            seen.add(req.id)
        return self

    def requirement_by_id(self, requirement_id: str) -> Optional[Requirement]:
        return next((r for r in self.requirements if r.id == requirement_id), None)

    @property
    def must_haves(self) -> list[Requirement]:
        return [r for r in self.requirements if r.priority == RequirementPriority.MUST_HAVE]


# ----------------------------------------------------------------------------
# 3. Candidate side (structured, giant strings nahi)
# ----------------------------------------------------------------------------
class ExperienceItem(HireFlowModel):
    title: str
    company: Optional[str] = None
    start_date: Optional[str] = None  # free text ("Jan 2021") kyunki resumes messy hote hain
    end_date: Optional[str] = None  # "Present" bhi ho sakta hai
    duration_years: Optional[float] = Field(default=None, ge=0, le=60)
    description: str = ""
    technologies: list[str] = Field(default_factory=list)

    _v_tech = field_validator("technologies", mode="before")(_dedupe_clean)


class ProjectItem(HireFlowModel):
    name: str
    description: str = ""
    technologies: list[str] = Field(default_factory=list)
    outcome: Optional[str] = None
    url: Optional[str] = None

    _v_tech = field_validator("technologies", mode="before")(_dedupe_clean)


class EducationItem(HireFlowModel):
    degree: str
    field_of_study: Optional[str] = None
    institution: Optional[str] = None
    year: Optional[str] = None


class CandidateProfile(HireFlowModel):
    candidate_id: str = Field(default_factory=lambda: _new_id("cand"))
    name: str = "Unknown"
    email: Optional[str] = None  # EmailStr nahi: email na ho ya galat ho to parsing fail na ho
    phone: Optional[str] = None
    location: Optional[str] = None
    skills: list[str] = Field(default_factory=list)
    experience: list[ExperienceItem] = Field(default_factory=list)
    education: list[EducationItem] = Field(default_factory=list)
    projects: list[ProjectItem] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    resume_source: Optional[str] = Field(default=None, description="File name / path")
    raw_text: Optional[str] = Field(default=None, description="Audit/evidence lookup ke liye")

    _v_skills = field_validator("skills", "certifications", mode="before")(_dedupe_clean)
    _v_lists = field_validator("experience", "education", "projects", mode="before")(_none_to_list)

    @property
    def total_experience_years(self) -> Optional[float]:
        """Sirf tab value jab kam se kam ek role mein duration_years ho."""
        durations = [e.duration_years for e in self.experience if e.duration_years is not None]
        return round(sum(durations), 1) if durations else None


# ----------------------------------------------------------------------------
# 4. Mapping: HireFlow ka core model
# ----------------------------------------------------------------------------
class Mapping(HireFlowModel):
    requirement_id: str
    candidate_id: str
    status: MappingStatus
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    evidence: list[Evidence] = Field(default_factory=list)
    reason: str = ""

    _v_evidence = field_validator("evidence", mode="before")(_none_to_list)

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp_confidence(cls, v: Any) -> Any:
        # LLM kabhi 85 (percent) ya "0.8" deta hai
        try:
            v = float(v)
        except (TypeError, ValueError):
            return 0.5
        if v > 1:
            v = v / 100
        return min(max(v, 0.0), 1.0)

    @model_validator(mode="after")
    def _evidence_required_for_positive_status(self) -> "Mapping":
        """Rule: bina evidence ke MET/PARTIAL nahi. Auto-downgrade to UNCLEAR."""
        if self.status in (MappingStatus.MET, MappingStatus.PARTIAL) and not self.evidence:
            note = f"[Auto-downgraded from {self.status.value}: no evidence provided]"
            self.reason = f"{self.reason} {note}".strip()
            self.status = MappingStatus.UNCLEAR
        return self

    @property
    def needs_validation(self) -> bool:
        return self.status in (MappingStatus.UNCLEAR, MappingStatus.PARTIAL)


# ----------------------------------------------------------------------------
# 5. Candidate summary (koi hire/no-hire field nahi)
# ----------------------------------------------------------------------------
class CandidateSummary(HireFlowModel):
    candidate_id: str
    candidate_name: str = "Unknown"
    group: Optional[CandidateGroup] = None  # grouping.py rule-based set karega
    fit_summary: str = ""
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    key_evidence: list[Evidence] = Field(default_factory=list)
    validation_areas: list[str] = Field(default_factory=list)

    _v_lists = field_validator(
        "strengths", "gaps", "key_evidence", "validation_areas", mode="before"
    )(_none_to_list)


# ----------------------------------------------------------------------------
# 6. Interview questions
# ----------------------------------------------------------------------------
class InterviewQuestion(HireFlowModel):
    question_id: str = Field(default_factory=lambda: _new_id("q"))
    candidate_id: Optional[str] = None
    question: str = Field(..., min_length=1)
    requirement_id: Optional[str] = None
    reason: str = Field(default="", description="Ye question kyu pucha ja raha hai")
    what_to_validate: str = ""
    difficulty: Difficulty = Difficulty.MEDIUM
    expected_evidence: str = Field(
        default="", description="Achhe answer mein kya sunna chahiye"
    )
    is_follow_up: bool = False
    parent_question_id: Optional[str] = None


# ----------------------------------------------------------------------------
# 7. Interview answers / notes
# ----------------------------------------------------------------------------
class InterviewAnswer(HireFlowModel):
    question_id: Optional[str] = None
    question: str
    answer_notes: str = ""
    requirement_id: Optional[str] = None  # kaun si requirement test hui
    evidence: list[Evidence] = Field(default_factory=list)
    unanswered_points: list[str] = Field(default_factory=list)
    follow_up_needed: bool = False
    follow_up_question: Optional[str] = None

    _v_lists = field_validator("evidence", "unanswered_points", mode="before")(_none_to_list)

    @model_validator(mode="after")
    def _follow_up_consistency(self) -> "InterviewAnswer":
        if self.follow_up_question and not self.follow_up_needed:
            self.follow_up_needed = True
        return self


# ----------------------------------------------------------------------------
# 8. Interview report
# ----------------------------------------------------------------------------
class RequirementFinding(HireFlowModel):
    """Interview ke baad ek requirement ka final status."""

    requirement_id: str
    requirement_title: str = ""
    status: MappingStatus
    summary: str = ""
    evidence: list[Evidence] = Field(default_factory=list)

    _v_evidence = field_validator("evidence", mode="before")(_none_to_list)

    @model_validator(mode="after")
    def _evidence_required(self) -> "RequirementFinding":
        if self.status in (MappingStatus.MET, MappingStatus.PARTIAL) and not self.evidence:
            self.status = MappingStatus.UNCLEAR
        return self


class InterviewReport(HireFlowModel):
    report_id: str = Field(default_factory=lambda: _new_id("rep"))
    candidate_id: str
    candidate_name: str = "Unknown"
    job_id: Optional[str] = None
    interviewer: Optional[str] = None
    answers: list[InterviewAnswer] = Field(default_factory=list)
    findings: list[RequirementFinding] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    unanswered_areas: list[str] = Field(
        default_factory=list, description="Requirements jo interview mein cover nahi hui"
    )
    summary: str = ""
    generated_at: datetime = Field(default_factory=_utcnow)

    _v_lists = field_validator(
        "answers", "findings", "strengths", "gaps", "unanswered_areas", mode="before"
    )(_none_to_list)

    @property
    def all_evidence(self) -> list[Evidence]:
        items: list[Evidence] = []
        for a in self.answers:
            items.extend(a.evidence)
        for f in self.findings:
            items.extend(f.evidence)
        return items


# ----------------------------------------------------------------------------
# 9. Pool query (Ask Pool page)
# ----------------------------------------------------------------------------
class PoolFilter(HireFlowModel):
    field: str = Field(..., description="e.g. 'skills', 'total_experience_years', 'location'")
    operator: FilterOperator = FilterOperator.CONTAINS
    value: Any


class PoolSort(HireFlowModel):
    field: str
    direction: SortDirection = SortDirection.DESC


class PoolQuery(HireFlowModel):
    natural_language_question: str = Field(..., min_length=1)
    filters: list[PoolFilter] = Field(default_factory=list)
    sort: Optional[PoolSort] = None
    requested_fields: list[str] = Field(default_factory=list)
    limit: int = Field(default=10, ge=1, le=100)

    _v_lists = field_validator("filters", "requested_fields", mode="before")(_none_to_list)


# ----------------------------------------------------------------------------
# 10. Audit record: trust layer
# ----------------------------------------------------------------------------
class AuditRecord(HireFlowModel):
    audit_id: str = Field(default_factory=lambda: _new_id("aud"))
    action: AuditAction
    entity_type: str = Field(..., description="e.g. 'Mapping', 'InterviewQuestion'")
    entity_id: Optional[str] = None
    candidate_id: Optional[str] = None
    job_id: Optional[str] = None
    source: Optional[str] = Field(default=None, description="e.g. 'Resume page 2'")
    evidence: list[Evidence] = Field(default_factory=list)
    output: Optional[str] = Field(default=None, description="Generated insight ka text")
    model: Optional[str] = Field(default=None, description="LLM ka naam, e.g. 'claude-sonnet-5'")
    timestamp: datetime = Field(default_factory=_utcnow)

    _v_evidence = field_validator("evidence", mode="before")(_none_to_list)