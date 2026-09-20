"""
core/interview.py
==================

HireFlow ka "interview-question layer".

    CandidateProfile + JobDescription + Mapping[]
                        |
                   interview.py
                        |
                   InterviewQuestion[]   (prioritized, evidence-grounded)
                        |
                   Interviewer asks candidate
                        |
                   InterviewAnswer / Notes
                        |
                   evaluation.py

Core question:

    **"Kya poochna chahiye?"**

`evaluation.py` alag question answer karta hai:

    **"Candidate ne jo bola usse kya establish hua?"**

Isliye `interview.py` kabhi answer ka final judgment nahi karta — wo sirf
kya poochna hai decide/generate karta hai, aur (agar evaluation.py se
already ek suggested follow-up mil chuka ho) usse ek proper
`InterviewQuestion` object mein wrap karta hai.

Design principle — mapping-status-driven, prioritized question generation:

    Requirement + Mapping status
        |
        +-- MISSING (must-have)  -> highest priority, direct validation Q
        +-- UNCLEAR               -> clarification Q
        +-- PARTIAL               -> depth/scope Q
        +-- MET                   -> optional depth/verification Q (lowest)
        |
        v
    llm/client.py -> LLM (question is naturally language-based, so LLM
                      IS used here — unlike grouping.py)
        |
        v
    InterviewQuestion (validated before being handed to the interviewer)

Is file mein jaan-bujh kar NAHI hai:
    - PDF/resume parsing                        -> ingestion.py
    - Resume/JD extraction                      -> extraction.py
    - Requirement matching (resume-side)        -> mapping.py
    - Candidate grouping / summary                -> grouping.py / summary.py
    - Interview ANSWER evaluation/judgment       -> evaluation.py
    - Final hire/reject decision, scoring         -> nowhere
    - Direct Groq SDK                             -> llm/client.py
    - Database / Streamlit UI                     -> db/ , pages/
"""

from __future__ import annotations

import logging
from typing import Optional

from pydantic import BaseModel, Field

from llm.client import LLMError, LLMValidationError, generate_from_prompt_file
from models.schemas import (
    CandidateProfile,
    Difficulty,
    InterviewAnswer,
    InterviewQuestion,
    JobDescription,
    Mapping,
    MappingStatus,
    Requirement,
    RequirementPriority,
)

logger = logging.getLogger("hireflow.interview")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class InterviewError(Exception):
    """Base class for interview.py errors."""


class InvalidInterviewInputError(InterviewError):
    """Candidate/job/mapping data question generation ke liye insufficient hai."""


class QuestionGenerationError(InterviewError):
    """Ek specific requirement ke liye question generate nahi ho paaya."""


class InvalidQuestionError(InterviewError):
    """LLM ne malformed/unusable question diya (empty, too short, etc.)."""


# ---------------------------------------------------------------------------
# Internal LLM-output models (NOT schemas.py business entities — sirf
# is file ke internal parsing contracts)
# ---------------------------------------------------------------------------

class _LLMQuestionResult(BaseModel):
    question: str
    reason: str = ""
    what_to_validate: str = ""
    difficulty: Difficulty = Difficulty.MEDIUM
    expected_evidence: str = ""


class _LLMFollowUpResult(BaseModel):
    follow_up_needed: bool = False
    question: str = ""
    reason: str = ""
    what_to_validate: str = ""
    expected_evidence: str = ""


# ---------------------------------------------------------------------------
# Priority ordering — MISSING/UNCLEAR/PARTIAL/MET x MUST_HAVE/NICE_TO_HAVE
# ---------------------------------------------------------------------------

_STATUS_PRIORITY_RANK: dict[MappingStatus, int] = {
    MappingStatus.MISSING: 0,
    MappingStatus.UNCLEAR: 1,
    MappingStatus.PARTIAL: 2,
    MappingStatus.MET: 3,
}

_REQUIREMENT_PRIORITY_RANK: dict[RequirementPriority, int] = {
    RequirementPriority.MUST_HAVE: 0,
    RequirementPriority.NICE_TO_HAVE: 1,
}


# ---------------------------------------------------------------------------
# PUBLIC API — Step 1: decide WHICH requirements to interview on, in order
# ---------------------------------------------------------------------------

def select_requirements_for_interview(
    job: JobDescription,
    mappings: list[Mapping],
    *,
    include_met: bool = True,
    max_requirements: Optional[int] = None,
) -> list[tuple[Requirement, Optional[Mapping]]]:
    """
    Job requirements ko interview ke liye prioritize karta hai:

        MISSING > UNCLEAR > PARTIAL > MET       (within each: MUST_HAVE first)

    Requirement jiska mapping hi nahi mila (edge case — mapping.py se
    aana chahiye tha) ko bhi MISSING jaisa treat kiya jaata hai, taaki
    silently skip na ho.

    Args:
        job: JobDescription (requirements ka source)
        mappings: candidate ke liye map_candidate_to_job() ka output
        include_met: False diya to already-MET requirements interview
                     list se exclude ho jaayenge (kam time ho to useful)
        max_requirements: diya ho to us se zyada requirements select nahi
                     honge (priority order preserve karte hue truncate)

    Returns:
        list[(Requirement, Mapping | None)] — priority order mein
    """
    if job is None or not job.requirements:
        raise InvalidInterviewInputError("A job with requirements is needed to plan an interview.")

    mapping_by_requirement: dict[str, Mapping] = {m.requirement_id: m for m in mappings}

    pairs: list[tuple[Requirement, Optional[Mapping]]] = []
    for requirement in job.requirements:
        mapping = mapping_by_requirement.get(requirement.id)
        status = mapping.status if mapping is not None else MappingStatus.MISSING
        if not include_met and status == MappingStatus.MET:
            continue
        pairs.append((requirement, mapping))

    def _sort_key(pair: tuple[Requirement, Optional[Mapping]]) -> tuple[int, int]:
        requirement, mapping = pair
        status = mapping.status if mapping is not None else MappingStatus.MISSING
        return (
            _STATUS_PRIORITY_RANK[status],
            _REQUIREMENT_PRIORITY_RANK[requirement.priority],
        )

    pairs.sort(key=_sort_key)

    if max_requirements is not None:
        pairs = pairs[:max_requirements]

    return pairs


# ---------------------------------------------------------------------------
# PUBLIC API — Step 2: one requirement -> one targeted question (LLM)
# ---------------------------------------------------------------------------

def generate_question(
    requirement: Requirement, candidate: CandidateProfile, mapping: Optional[Mapping]
) -> InterviewQuestion:
    """
    Ek requirement ke liye ek targeted, evidence-grounded question generate
    karta hai — mapping status (MISSING/UNCLEAR/PARTIAL/MET) ke hisaab se
    question ka angle badalta hai (prompt ye rule enforce karta hai).

    Raises:
        QuestionGenerationError -- LLM call/validation fail hui
    """
    status = mapping.status if mapping is not None else MappingStatus.MISSING
    reason = mapping.reason if mapping is not None else "No resume mapping data was available."
    evidence_text = (
        "; ".join(e.text for e in mapping.evidence) if mapping and mapping.evidence else "none"
    )

    try:
        result: _LLMQuestionResult = generate_from_prompt_file(
            "gen_questions",
            schema=_LLMQuestionResult,
            operation="generate_interview_question",
            requirement_title=requirement.title,
            requirement_description=requirement.description,
            requirement_category=requirement.category.value,
            requirement_priority=requirement.priority.value,
            requirement_years_required=str(requirement.years_required or "not specified"),
            mapping_status=status.value,
            mapping_reason=reason,
            mapping_evidence=evidence_text,
            candidate_name=candidate.name,
            candidate_skills=", ".join(candidate.skills) or "none listed",
            candidate_experience="; ".join(
                f"{exp.title} at {exp.company or 'unknown'}: {exp.description or ''}"
                for exp in candidate.experience
            )
            or "none listed",
            candidate_projects="; ".join(
                f"{proj.name}: {proj.description or ''}" for proj in candidate.projects
            )
            or "none listed",
        )
    except LLMValidationError as exc:
        logger.error(
            "Question generation LLM output failed validation | requirement=%s",
            requirement.title,
        )
        raise QuestionGenerationError(
            f"Could not generate a question for '{requirement.title}': "
            "LLM returned an invalid response."
        ) from exc
    except LLMError as exc:
        logger.error("Question generation LLM call failed | requirement=%s", requirement.title)
        raise QuestionGenerationError(
            f"Could not generate a question for '{requirement.title}': {exc}"
        ) from exc

    question = InterviewQuestion(
        candidate_id=candidate.candidate_id,
        question=result.question,
        requirement_id=requirement.id,
        reason=result.reason,
        what_to_validate=result.what_to_validate,
        difficulty=result.difficulty,
        expected_evidence=result.expected_evidence,
        is_follow_up=False,
    )
    return question


# ---------------------------------------------------------------------------
# PUBLIC API — sanity-check a generated question before it reaches an
# interviewer (catches empty/garbage LLM output deterministically)
# ---------------------------------------------------------------------------

_MIN_QUESTION_LENGTH = 10


def validate_question(question: InterviewQuestion) -> None:
    """
    Generated question ka deterministic sanity check. LLM output kabhi
    khaali, truncated, ya placeholder-jaisa ho sakta hai — is function ka
    kaam wo interviewer tak pahunchne se pehle pakadna hai.

    Raises:
        InvalidQuestionError -- question usable nahi hai
    """
    text = (question.question or "").strip()
    if len(text) < _MIN_QUESTION_LENGTH:
        raise InvalidQuestionError(
            f"Generated question is too short/empty to be usable: {text!r}"
        )
    if not question.requirement_id and not question.is_follow_up:
        raise InvalidQuestionError("Generated question is not linked to any requirement.")


# ---------------------------------------------------------------------------
# PUBLIC API — Step 3: multiple requirements -> multiple questions
# ---------------------------------------------------------------------------

def generate_questions(
    candidate: CandidateProfile,
    job: JobDescription,
    mappings: list[Mapping],
    *,
    include_met: bool = True,
    max_questions: Optional[int] = None,
) -> list[InterviewQuestion]:
    """
    Prioritized requirement list ke liye questions generate karta hai.

    Ek requirement ka question generation fail ho jaaye (LLM error) ya
    invalid nikle (validate_question), to poora interview plan crash nahi
    hota — us requirement ko skip karke baaki continue hote hain, sirf
    warning log hoti hai.
    """
    if mappings is None:
        raise InvalidInterviewInputError(
        "mappings are required to generate interview questions."
        )

    selected = select_requirements_for_interview(
        job, mappings, include_met=include_met, max_requirements=max_questions
    )

    questions: list[InterviewQuestion] = []
    for requirement, mapping in selected:
        try:
            question = generate_question(requirement, candidate, mapping)
            validate_question(question)
        except (QuestionGenerationError, InvalidQuestionError) as exc:
            logger.warning(
                "Skipping question for requirement | requirement=%s | reason=%s",
                requirement.title,
                exc,
            )
            continue
        questions.append(question)

    return questions


# ---------------------------------------------------------------------------
# PUBLIC API — Step 4: follow-up questions
# ---------------------------------------------------------------------------

def generate_followup(
    question: InterviewQuestion,
    answer_notes: str,
    *,
    evaluation_hint: Optional[InterviewAnswer] = None,
) -> Optional[InterviewQuestion]:
    """
    Ek follow-up question banata hai, do possible paths se:

    1. DETERMINISTIC (preferred, no LLM call): agar `evaluation_hint`
       diya gaya hai (evaluation.py ka `InterviewAnswer`, jisme
       `follow_up_needed` / `follow_up_question` already evidence-first
       tareeke se decide ho chuka hai), to interview.py sirf us suggestion
       ko ek proper `InterviewQuestion` object mein wrap karta hai —
       khud judgment nahi karta ("evaluation.py ka kaam" boundary respect
       hoti hai).

    2. LLM-BASED (live interview fallback, jab evaluation.py abhi chala
       nahi — e.g. interviewer real-time mein hi follow-up poochna
       chahta hai): `gen_followup.txt` prompt se decide karta hai ki
       follow-up chahiye ya nahi, aur agar chahiye to generate karta hai.

    Returns:
        InterviewQuestion (is_follow_up=True) agar follow-up zaroori hai,
        warna None.

    Raises:
        QuestionGenerationError -- (path 2 only) LLM call/validation fail hui
    """
    # Path 1 — deterministic, evaluation.py ka result reuse karo
    if evaluation_hint is not None:
        if not evaluation_hint.follow_up_needed or not evaluation_hint.follow_up_question:
            return None
        followup = InterviewQuestion(
            candidate_id=question.candidate_id,
            question=evaluation_hint.follow_up_question,
            requirement_id=question.requirement_id,
            reason="Follow-up suggested by interview evaluation based on answer evidence gaps.",
            what_to_validate=question.what_to_validate,
            difficulty=question.difficulty,
            expected_evidence=question.expected_evidence,
            is_follow_up=True,
            parent_question_id=question.question_id,
        )
        validate_question(followup)
        return followup

    # Path 2 — LLM decides live, no prior evaluation available yet
    try:
        result: _LLMFollowUpResult = generate_from_prompt_file(
            "gen_followup",
            schema=_LLMFollowUpResult,
            operation="generate_followup_question",
            requirement_title=question.requirement_id or "Not specified",
            original_question=question.question,
            answer_notes=answer_notes or "(no notes recorded)",
        )
    except LLMValidationError as exc:
        logger.error("Follow-up LLM output failed validation | question=%s", question.question)
        raise QuestionGenerationError(
            f"Could not generate a follow-up for '{question.question}': "
            "LLM returned an invalid response."
        ) from exc
    except LLMError as exc:
        logger.error("Follow-up LLM call failed | question=%s", question.question)
        raise QuestionGenerationError(
            f"Could not generate a follow-up for '{question.question}': {exc}"
        ) from exc

    if not result.follow_up_needed or not result.question.strip():
        return None

    followup = InterviewQuestion(
        candidate_id=None,
        question=result.question,
        requirement_id=question.requirement_id,
        reason=result.reason,
        what_to_validate=result.what_to_validate,
        difficulty=question.difficulty,
        expected_evidence=result.expected_evidence,
        is_follow_up=True,
        parent_question_id=question.question_id,
    )
    validate_question(followup)
    return followup


# ---------------------------------------------------------------------------
# PUBLIC API — full orchestration
# ---------------------------------------------------------------------------

def build_interview_plan(
    candidate: CandidateProfile,
    job: JobDescription,
    mappings: list[Mapping],
    *,
    include_met: bool = True,
    max_questions: Optional[int] = None,
) -> list[InterviewQuestion]:
    """
    End-to-end: candidate + job + mappings -> prioritized, validated
    interview question list, ready for an interviewer to use.

        select_requirements_for_interview()  (priority order)
                    |
             generate_questions()             (LLM, per requirement)
                    |
             validate_question()              (deterministic sanity check)
                    |
             InterviewQuestion[]

    Follow-ups is function mein NAHI hote — wo interview ke live flow ke
    hisaab se `generate_followup()` se on-demand banti hain, kyunki unka
    trigger candidate ka actual answer hai jo abhi maujood nahi.
    """
    return generate_questions(
        candidate, job, mappings, include_met=include_met, max_questions=max_questions
    )
