"""
core/evaluation.py
===================

HireFlow ka "interview-evidence layer".

    Interview Questions + Answers/Notes  +  (optional) resume Mappings
                        |
                   evaluation.py
                        |
                   InterviewReport   (findings + strengths + gaps + unanswered_areas)

`mapping.py` batata hai: "resume mein requirement ka evidence hai?"
`evaluation.py` batata hai: "interview mein requirement ke baare mein kya
evidence mila?"

Design principle — evidence-first, hybrid evaluation (mapping.py jaisa hi):

    InterviewAnswer
        |
        +-- already structured (evidence/unanswered_points populated
        |   by interview.py / a human) -> deterministic status derivation,
        |   no LLM call
        |
        +-- only raw answer_notes present -> llm/client.py -> LLM
        |   extracts evidence/status/unanswered_points from messy text
        |
        v
    RequirementFinding (per answer) -> aggregated (per requirement) -> InterviewReport

Is file mein jaan-bujh kar NAHI hai:
    - PDF/resume parsing                       -> ingestion.py
    - Resume extraction                        -> extraction.py
    - Resume-side requirement matching         -> mapping.py
    - Candidate grouping                       -> grouping.py
    - Candidate summary from resume            -> summary.py
    - Interview question generation            -> interview.py
    - Groq SDK directly                        -> llm/client.py
    - Database / Streamlit UI                  -> db/ , pages/
    - Final hire/reject decision               -> human recruiter
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Optional

from pydantic import BaseModel, Field

from llm.client import LLMError, LLMValidationError, generate_from_prompt_file
from models.schemas import (
    CandidateProfile,
    Evidence,
    EvidenceSource,
    InterviewAnswer,
    InterviewReport,
    JobDescription,
    Mapping,
    MappingStatus,
    Requirement,
    RequirementFinding,
)

logger = logging.getLogger("hireflow.evaluation")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class EvaluationError(Exception):
    """Base class for evaluation.py errors."""


class InvalidEvaluationInputError(EvaluationError):
    """Candidate/answers data evaluation ke liye insufficient/malformed hai."""


class AnswerEvaluationError(EvaluationError):
    """Ek specific answer ka evaluation fail hua (e.g. LLM call fail)."""


# ---------------------------------------------------------------------------
# Internal LLM-output model (NOT a schemas.py business entity — sirf
# _evaluate_notes_via_llm() ka internal parsing contract)
# ---------------------------------------------------------------------------

class _LLMEvidenceItem(BaseModel):
    text: str
    location: str = "interview notes"


class _LLMAnswerEvalResult(BaseModel):
    status: MappingStatus
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str
    evidence: list[_LLMEvidenceItem] = Field(default_factory=list)
    unanswered_points: list[str] = Field(default_factory=list)
    follow_up_needed: bool = False
    follow_up_question: Optional[str] = None


# Priority order jab multiple answers/findings ek hi requirement ke liye
# combine karni ho — "best evidence wins" but gaps preserve hote hain.
_STATUS_RANK = {
    MappingStatus.MET: 3,
    MappingStatus.PARTIAL: 2,
    MappingStatus.UNCLEAR: 1,
    MappingStatus.MISSING: 0,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_requirement(
    job: Optional[JobDescription], requirement_id: Optional[str]
) -> Optional[Requirement]:
    if job is None or not requirement_id:
        return None
    return job.requirement_by_id(requirement_id)


def _requirement_title(requirement: Optional[Requirement], answer: InterviewAnswer) -> str:
    if requirement is not None:
        return requirement.title
    return answer.requirement_id or "Unassigned requirement"


# ---------------------------------------------------------------------------
# Step 1a — deterministic status derivation (answer already structured)
# ---------------------------------------------------------------------------

def _deterministic_status(answer: InterviewAnswer) -> tuple[MappingStatus, str]:
    """
    Answer mein already evidence/unanswered_points populated hai (interview.py
    ya human ne structure kar diya) — evidence-first rule se status nikaalo,
    LLM call ki zarurat nahi.
    """
    has_evidence = bool(answer.evidence)
    has_gaps = bool(answer.unanswered_points)

    if has_evidence and not has_gaps:
        return MappingStatus.MET, "Interview evidence clearly supports this requirement."
    if has_evidence and has_gaps:
        return (
            MappingStatus.PARTIAL,
            "Interview evidence partially supports this requirement; some aspects remain unaddressed.",
        )
    if not has_evidence and has_gaps:
        return (
            MappingStatus.UNCLEAR,
            "The answer touched on this requirement but did not provide clear supporting evidence.",
        )
    # no evidence, no explicit gaps, but notes exist -> ambiguous, treat as UNCLEAR
    return (
        MappingStatus.UNCLEAR,
        "The answer notes did not clearly establish this requirement.",
    )


# ---------------------------------------------------------------------------
# Step 1b — raw/messy notes -> LLM extraction
# ---------------------------------------------------------------------------

def _evaluate_notes_via_llm(
    answer: InterviewAnswer, requirement: Optional[Requirement]
) -> _LLMAnswerEvalResult:
    """
    Jab answer.evidence khaali hai aur sirf raw answer_notes text hai
    (natural, messy interviewer notes), tab LLM se evidence-first extraction
    karwate hain — invent nahi karta, sirf jo notes mein likha hai wahi
    nikaalta hai.
    """
    try:
        return generate_from_prompt_file(
            "evaluate_answer",
            schema=_LLMAnswerEvalResult,
            operation="evaluate_interview_answer",
            requirement_title=requirement.title if requirement else "Not specified",
            requirement_description=requirement.description if requirement else "",
            requirement_category=requirement.category.value if requirement else "OTHER",
            requirement_priority=requirement.priority.value if requirement else "MUST_HAVE",
            question=answer.question,
            answer_notes=answer.answer_notes or "(no notes recorded)",
        )
    except LLMValidationError as exc:
        logger.error(
            "Evaluation LLM output failed validation | question=%s", answer.question
        )
        raise AnswerEvaluationError(
            f"Could not evaluate answer to '{answer.question}': "
            "LLM returned an invalid response."
        ) from exc
    except LLMError as exc:
        logger.error("Evaluation LLM call failed | question=%s", answer.question)
        raise AnswerEvaluationError(
            f"Could not evaluate answer to '{answer.question}': {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# PUBLIC API — one answer -> (enriched answer, per-answer finding)
# ---------------------------------------------------------------------------

def evaluate_answer(
    answer: InterviewAnswer, requirement: Optional[Requirement] = None
) -> tuple[InterviewAnswer, RequirementFinding]:
    """
    Ek InterviewAnswer ko evaluate karke:
      1. enriched InterviewAnswer (evidence/unanswered_points/follow_up
         fields filled in agar pehle khaali the)
      2. us answer ka RequirementFinding

    return karta hai.

    Order of resolution (mapping.py jaisa hi — fast/deterministic pehle):
        1. Answer khaali hai (na notes, na evidence) -> MISSING, no LLM call
        2. Answer already structured (evidence/unanswered_points diye hue)
           -> deterministic status derivation, no LLM call
        3. Sirf raw answer_notes hai -> LLM se evidence-first extraction
    """
    requirement_id = answer.requirement_id or (requirement.id if requirement else "unassigned")
    title = _requirement_title(requirement, answer)

    # 1. Not answered at all
    if not answer.answer_notes.strip() and not answer.evidence:
        finding = RequirementFinding(
            requirement_id=requirement_id,
            requirement_title=title,
            status=MappingStatus.MISSING,
            summary=f"'{answer.question}' was not answered during the interview.",
            evidence=[],
        )
        return answer, finding

    # 2. Already structured -> deterministic, no LLM call
    if answer.evidence:
        status, summary = _deterministic_status(answer)
        finding = RequirementFinding(
            requirement_id=requirement_id,
            requirement_title=title,
            status=status,
            summary=summary,
            evidence=answer.evidence,
        )
        return answer, finding

    # 3. Raw notes only -> LLM extraction
    result = _evaluate_notes_via_llm(answer, requirement)

    evidence = [
        Evidence(text=item.text, source=EvidenceSource.INTERVIEW, location=item.location)
        for item in result.evidence
    ]

    enriched_answer = answer.model_copy(
        update={
            "requirement_id": requirement_id,
            "evidence": evidence,
            "unanswered_points": result.unanswered_points,
            "follow_up_needed": result.follow_up_needed,
            "follow_up_question": result.follow_up_question,
        }
    )

    finding = RequirementFinding(
        requirement_id=requirement_id,
        requirement_title=title,
        status=result.status,
        summary=result.summary,
        evidence=evidence,
    )
    return enriched_answer, finding


# ---------------------------------------------------------------------------
# PUBLIC API — combine multiple answers/findings for the SAME requirement
# ---------------------------------------------------------------------------

def aggregate_requirement_findings(
    requirement_id: str, requirement_title: str, findings: list[RequirementFinding]
) -> RequirementFinding:
    """
    Jab ek requirement ke liye multiple interview questions pooche gaye
    ho (e.g. AWS deployment + AWS monitoring + AWS cost), unke individual
    findings ko ek single requirement-level finding mein synthesize karta
    hai. Question-by-question blindly report nahi karta.

    Rule: best-evidence status "wins" (MET > PARTIAL > UNCLEAR > MISSING),
    but summary/evidence dono findings se combine hote hain — taaki
    partial/unresolved aspects lost na ho.
    """
    if not findings:
        return RequirementFinding(
            requirement_id=requirement_id,
            requirement_title=requirement_title,
            status=MappingStatus.MISSING,
            summary="This requirement was not covered during the interview.",
            evidence=[],
        )

    if len(findings) == 1:
        return findings[0]

    best = max(findings, key=lambda f: _STATUS_RANK[f.status])
    all_evidence: list[Evidence] = []
    for f in findings:
        all_evidence.extend(f.evidence)

    # Summary: strongest finding leads, weaker findings noted as caveats.
    other_summaries = [f.summary for f in findings if f is not best and f.summary]
    combined_summary = best.summary
    if other_summaries:
        combined_summary = f"{combined_summary} " + " ".join(other_summaries)

    return RequirementFinding(
        requirement_id=requirement_id,
        requirement_title=requirement_title,
        status=best.status,
        summary=combined_summary.strip(),
        evidence=all_evidence,
    )


# ---------------------------------------------------------------------------
# PUBLIC API — unanswered areas across the whole interview
# ---------------------------------------------------------------------------

def identify_unanswered_areas(
    job: Optional[JobDescription],
    findings: list[RequirementFinding],
    answers: list[InterviewAnswer],
) -> list[str]:
    """
    Teen sources se unanswered/unclear areas collect karta hai:
      1. Requirements jo JD mein hai but koi bhi answer ne cover nahi kiya
      2. Findings jinka status UNCLEAR/PARTIAL/MISSING hai
      3. Individual answers ke explicit unanswered_points
    Order preserve, duplicates clean.
    """
    seen: set[str] = set()
    areas: list[str] = []

    def _add(text: str) -> None:
        key = text.strip().lower()
        if text.strip() and key not in seen:
            seen.add(key)
            areas.append(text.strip())

    covered_requirement_ids = {f.requirement_id for f in findings}

    if job is not None:
        for req in job.requirements:
            if req.id not in covered_requirement_ids:
                _add(f"{req.title} (not covered in the interview)")

    for finding in findings:
        if finding.status in (MappingStatus.UNCLEAR, MappingStatus.PARTIAL, MappingStatus.MISSING):
            _add(finding.requirement_title)

    for answer in answers:
        for point in answer.unanswered_points:
            _add(point)

    return areas


# ---------------------------------------------------------------------------
# PUBLIC API — strengths / gaps derivation (evidence-backed only)
# ---------------------------------------------------------------------------

def derive_strengths(findings: list[RequirementFinding]) -> list[str]:
    """Sirf MET findings se — koi unsupported adjective ('great candidate') nahi."""
    strengths: list[str] = []
    for f in findings:
        if f.status == MappingStatus.MET:
            strengths.append(f.summary or f"{f.requirement_title}: supported by interview evidence.")
    return strengths


def derive_gaps(findings: list[RequirementFinding]) -> list[str]:
    """PARTIAL/UNCLEAR/MISSING findings se — evidence gap, inability ka proof nahi."""
    gaps: list[str] = []
    for f in findings:
        if f.status in (MappingStatus.PARTIAL, MappingStatus.UNCLEAR, MappingStatus.MISSING):
            gaps.append(f.summary or f"{f.requirement_title}: not clearly established by interview evidence.")
    return gaps


# ---------------------------------------------------------------------------
# PUBLIC API — final report summary (deterministic, evidence-based —
# no LLM here, so the top-line conclusion can never hallucinate beyond
# what findings already say)
# ---------------------------------------------------------------------------

def build_report_summary(
    candidate: CandidateProfile,
    findings: list[RequirementFinding],
    unanswered_areas: list[str],
) -> str:
    met = [f.requirement_title for f in findings if f.status == MappingStatus.MET]
    partial_unclear = [
        f.requirement_title for f in findings if f.status in (MappingStatus.PARTIAL, MappingStatus.UNCLEAR)
    ]
    missing = [f.requirement_title for f in findings if f.status == MappingStatus.MISSING]

    parts: list[str] = []
    if met:
        parts.append(f"The interview provided clear evidence of {', '.join(met)}.")
    if partial_unclear:
        parts.append(f"{', '.join(partial_unclear)} were discussed but not fully established.")
    if missing:
        parts.append(f"No interview evidence was found for {', '.join(missing)}.")
    if unanswered_areas:
        parts.append(f"Not yet covered: {', '.join(unanswered_areas)}.")

    if not parts:
        return f"No evaluable interview evidence was recorded for {candidate.name}."

    return " ".join(parts)


# ---------------------------------------------------------------------------
# PUBLIC API — full orchestration
# ---------------------------------------------------------------------------

def evaluate_interview(
    candidate: CandidateProfile,
    answers: list[InterviewAnswer],
    job: Optional[JobDescription] = None,
    interviewer: Optional[str] = None,
    previous_mappings: Optional[list[Mapping]] = None,  # noqa: ARG001 — audit/context only, never overwritten
) -> InterviewReport:
    """
    Poora interview-evaluation pipeline:

        InterviewAnswer[]  ->  per-answer evaluate  ->  per-requirement
        aggregate  ->  strengths / gaps / unanswered_areas  ->  InterviewReport

    Args:
        candidate: jiska interview hua
        answers: is candidate ke interview ke saare InterviewAnswer
        job: JobDescription (optional — mile to "not covered" requirements
             bhi identify ho sakti hain, aur requirement titles resolve
             ho sakte hain)
        interviewer: optional interviewer name/id
        previous_mappings: resume-stage Mapping[] — sirf reference/context
             ke liye accept kiya jaata hai (e.g. future audit trail), is
             function dwara kabhi mutate/overwrite nahi hota. Resume aur
             interview evidence dono independently preserve hote hain.

    Returns:
        InterviewReport — candidate ke liye complete evidence-backed report.

    Raises:
        InvalidEvaluationInputError -- candidate ya answers khaali/invalid hain
    """
    if candidate is None:
        raise InvalidEvaluationInputError("candidate is required for evaluation.")
    if not answers:
        raise InvalidEvaluationInputError("No interview answers provided to evaluate.")

    enriched_answers: list[InterviewAnswer] = []
    per_answer_findings: list[RequirementFinding] = []

    for answer in answers:
        requirement = _resolve_requirement(job, answer.requirement_id)
        try:
            enriched_answer, finding = evaluate_answer(answer, requirement)
        except AnswerEvaluationError as exc:
            logger.error(
                "Answer evaluation failed | question=%s | candidate=%s",
                answer.question,
                candidate.candidate_id,
            )
            # Ek answer ka evaluation fail hone se poora report crash nahi
            # hona chahiye — UNCLEAR finding ke saath continue karte hain.
            enriched_answer = answer
            finding = RequirementFinding(
                requirement_id=answer.requirement_id or "unassigned",
                requirement_title=_requirement_title(requirement, answer),
                status=MappingStatus.UNCLEAR,
                summary=f"Could not evaluate this answer due to a system error: {exc}",
                evidence=[],
            )
        enriched_answers.append(enriched_answer)
        per_answer_findings.append(finding)

    # Group findings by requirement_id -> synthesize multi-question requirements
    grouped: dict[str, list[RequirementFinding]] = defaultdict(list)
    for finding in per_answer_findings:
        grouped[finding.requirement_id].append(finding)

    findings: list[RequirementFinding] = [
        aggregate_requirement_findings(req_id, group[0].requirement_title, group)
        for req_id, group in grouped.items()
    ]

    unanswered_areas = identify_unanswered_areas(job, findings, enriched_answers)
    strengths = derive_strengths(findings)
    gaps = derive_gaps(findings)
    summary = build_report_summary(candidate, findings, unanswered_areas)

    return InterviewReport(
        candidate_id=candidate.candidate_id,
        candidate_name=candidate.name,
        job_id=job.job_id if job else None,
        interviewer=interviewer,
        answers=enriched_answers,
        findings=findings,
        strengths=strengths,
        gaps=gaps,
        unanswered_areas=unanswered_areas,
        summary=summary,
    )