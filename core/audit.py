"""
core/audit.py
=============

HireFlow ka "traceability / accountability layer".

    mapping.py / grouping.py / summary.py / evaluation.py / pool_query.py / ...
                        |
                   audit.py
                        |
                  AuditRecord (append-only)
                        |
                   db/database.py
                        |
                   hireflow.db (audit_records table)

Har important business/AI insight ke saath ye batana ki:

    "WHAT HAPPENED?"   -> action, entity_type, entity_id
    "WHY?"             -> output
    "SOURCE?"          -> source
    "EVIDENCE?"        -> evidence
    "WHEN?"            -> timestamp
    "WHICH MODEL?"     -> model (None agar deterministic operation thi)

Design principles:
    - APPEND-ONLY: ek audit record kabhi update/delete nahi hota. Result
      badal jaaye (resume UNCLEAR -> interview MET) to naya record banta
      hai, purana as-is rehta hai — poori history preserve hoti hai.
    - DETERMINISTIC: audit.py khud kabhi LLM call nahi karta. Ye sirf
      record karta hai ki kya hua — agar audit khud AI-generated ho,
      traceability hi weak ho jaayegi.
    - NON-FRAGILE: audit persistence fail ho jaaye to core pipeline
      (mapping/grouping/...) crash nahi honi chahiye — lekin failure
      silently gayab bhi nahi honi chahiye, ek visible warning zaroor
      log hoti hai.
    - PRIVACY-SAFE: secrets (API keys/tokens/passwords) kabhi audit mein
      nahi jaate; evidence text bhi length-capped hai (poora resume
      baar-baar store nahi hota).

Is file mein jaan-bujh kar NAHI hai:
    - PDF parsing, resume/JD extraction        -> ingestion.py / extraction.py
    - Requirement matching                     -> mapping.py
    - Candidate grouping / summary              -> grouping.py / summary.py
    - Interview evaluation                      -> evaluation.py
    - Natural-language query parsing            -> pool_query.py
    - Direct Groq SDK / any LLM call             -> nowhere in this file
    - Raw SQL                                    -> db/database.py (sirf wahi)
    - Streamlit UI                               -> pages/
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional, Union

from models.schemas import AuditAction, AuditRecord, Evidence, EvidenceSource

logger = logging.getLogger("hireflow.audit")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class AuditError(Exception):
    """Base class for audit.py errors."""


class InvalidAuditInputError(AuditError):
    """Audit record banane ke liye required data missing/malformed hai."""


class AuditPersistenceError(AuditError):
    """AuditRecord ban gaya, lekin db/database.py mein save nahi ho paaya."""


# ---------------------------------------------------------------------------
# Privacy / hygiene helpers
# ---------------------------------------------------------------------------

# Ye keys (case-insensitive, partial match) kabhi audit output mein nahi
# jaani chahiye, chahe caller galti se pass bhi kar de.
_SENSITIVE_KEY_MARKERS = (
    "api_key",
    "apikey",
    "token",
    "password",
    "secret",
    "authorization",
    "auth_header",
)

# Ek evidence snippet ki max length — poora resume/JD baar-baar audit mein
# store nahi hota, sirf traceable excerpt.
_MAX_EVIDENCE_TEXT_LENGTH = 500


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in _SENSITIVE_KEY_MARKERS)


def _sanitize_output_value(value: Any) -> Any:
    """Dict/list ke andar se sensitive-looking keys recursively redact karta hai."""
    if isinstance(value, dict):
        return {
            k: ("[REDACTED]" if _is_sensitive_key(k) else _sanitize_output_value(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_output_value(v) for v in value]
    return value


def _serialize_output(output: Union[str, dict, list, None]) -> Optional[str]:
    """
    `AuditRecord.output` schema ke according ek string hai. Caller dict/list
    (e.g. {"status": "MET", "confidence": 0.94}) bhi de sakta hai — usko
    sanitize karke deterministic JSON string mein convert kar dete hain.
    """
    if output is None:
        return None
    if isinstance(output, str):
        return output
    sanitized = _sanitize_output_value(output)
    return json.dumps(sanitized, sort_keys=True, default=str)


def _truncate_evidence(evidence: list[Evidence]) -> list[Evidence]:
    truncated: list[Evidence] = []
    for item in evidence:
        text = item.text
        if len(text) > _MAX_EVIDENCE_TEXT_LENGTH:
            text = text[: _MAX_EVIDENCE_TEXT_LENGTH].rstrip() + " …[truncated]"
        truncated.append(item.model_copy(update={"text": text}))
    return truncated


# ---------------------------------------------------------------------------
# Step 1 — build an AuditRecord (pure construction, no side effects)
# ---------------------------------------------------------------------------

def create_audit_record(
    action: AuditAction,
    entity_type: str,
    output: Union[str, dict, list, None] = None,
    *,
    entity_id: Optional[str] = None,
    candidate_id: Optional[str] = None,
    job_id: Optional[str] = None,
    source: Optional[str] = None,
    evidence: Optional[list[Evidence]] = None,
    model: Optional[str] = None,
) -> AuditRecord:
    """
    Ek `AuditRecord` banata hai — koi persistence nahi, sirf validated,
    privacy-safe object construction.

    Args:
        action:      kya operation hua (schemas.AuditAction, e.g. MAP_REQUIREMENT)
        entity_type: kis type ke object par (e.g. "mapping", "candidate_summary")
        output:      system ne kya result diya (str ya dict/list — dict/list
                     ko sanitize karke JSON string mein store kiya jaata hai)
        entity_id:   specific entity ka id (e.g. requirement_id, report_id)
        candidate_id / job_id: relevant candidate/job, agar applicable ho
        source:      evidence kaha se aayi (e.g. "resume", "interview_notes")
        evidence:    supporting Evidence list (source-derived, fabricated nahi)
        model:       LLM ka naam agar AI-generated hai; deterministic
                     operation ke liye None (e.g. grouping.py)

    Raises:
        InvalidAuditInputError -- action ya entity_type missing/invalid hai
    """
    if action is None:
        raise InvalidAuditInputError("action is required to create an audit record.")
    if not entity_type or not entity_type.strip():
        raise InvalidAuditInputError("entity_type is required to create an audit record.")

    try:
        return AuditRecord(
            action=action,
            entity_type=entity_type.strip(),
            entity_id=entity_id,
            candidate_id=candidate_id,
            job_id=job_id,
            source=source,
            evidence=_truncate_evidence(evidence or []),
            output=_serialize_output(output),
            model=model,
        )
    except Exception as exc:  # pydantic ValidationError etc.
        raise InvalidAuditInputError(f"Could not build a valid audit record: {exc}") from exc


# ---------------------------------------------------------------------------
# Step 2 — persistence (audit.py never talks SQL directly)
# ---------------------------------------------------------------------------
#
# NOTE: db/database.py is expected to expose:
#
#     def save_audit_record(record: AuditRecord) -> None: ...
#     def get_audit_records(
#         candidate_id: str | None = None,
#         job_id: str | None = None,
#         entity_type: str | None = None,
#         entity_id: str | None = None,
#         action: AuditAction | None = None,
#     ) -> list[dict]: ...
#
# Audit records append-only hain — database.py mein koi "update_audit" /
# "delete_audit" function honi hi nahi chahiye.

try:
    from db.database import (
        get_audit_records as _db_get_audit_records,
        save_audit_record as _db_save_audit_record,
    )
except ImportError:  # db/database.py abhi is environment mein nahi hai
    _db_save_audit_record = None  # type: ignore[assignment]
    _db_get_audit_records = None  # type: ignore[assignment]


def save_audit(record: AuditRecord) -> AuditRecord:
    """
    `AuditRecord` ko `db/database.py` ke through persist karta hai.
    Append-only: existing record kabhi mutate/overwrite nahi hota, ye
    function hamesha ek naya row insert karta hai.
    """
    if _db_save_audit_record is None:
        raise AuditPersistenceError(
            "Audit database is not available (db.database.save_audit_record not found)."
        )
    try:
        _db_save_audit_record(record)
    except Exception as exc:  # noqa: BLE001 — database-layer errors ko safe message mein wrap karo
        logger.error(
            "Audit persistence failed | audit_id=%s | action=%s | error=%s",
            record.audit_id,
            record.action.value,
            type(exc).__name__,
        )
        raise AuditPersistenceError(f"Could not save audit record: {exc}") from exc
    return record


# ---------------------------------------------------------------------------
# PUBLIC API — high-level convenience: build + save in one call
# ---------------------------------------------------------------------------

def log_action(
    action: AuditAction,
    entity_type: str,
    output: Union[str, dict, list, None] = None,
    *,
    entity_id: Optional[str] = None,
    candidate_id: Optional[str] = None,
    job_id: Optional[str] = None,
    source: Optional[str] = None,
    evidence: Optional[list[Evidence]] = None,
    model: Optional[str] = None,
    raise_on_failure: bool = False,
) -> AuditRecord:
    """
    Ek call mein audit record banata aur save karta hai — mapping.py,
    grouping.py, summary.py, evaluation.py, pool_query.py wagera is single
    function se apna provenance log karenge.

    Persistence failure se core pipeline crash NAHI hoti (per design
    principle: audit shouldn't make the pipeline fragile) — lekin har
    failure par ek visible warning log hoti hai, aur `raise_on_failure=True`
    diya jaaye to caller explicitly failure propagate karwa sakta hai
    (e.g. compliance-critical flows ke liye).

    Returns:
        AuditRecord — record hamesha return hota hai (persist ho ya na ho),
        taaki caller ke paas kam se kam in-memory provenance rahe.

    Raises:
        InvalidAuditInputError -- record hi invalid tha (hamesha raise hoti
                                   hai, ye caller ka bug hai)
        AuditPersistenceError  -- sirf agar raise_on_failure=True ho
    """
    record = create_audit_record(
        action,
        entity_type,
        output,
        entity_id=entity_id,
        candidate_id=candidate_id,
        job_id=job_id,
        source=source,
        evidence=evidence,
        model=model,
    )

    try:
        save_audit(record)
    except AuditPersistenceError as exc:
        logger.warning(
            "Audit record created but NOT persisted | audit_id=%s | action=%s | reason=%s",
            record.audit_id,
            record.action.value,
            exc,
        )
        if raise_on_failure:
            raise

    return record


# ---------------------------------------------------------------------------
# PUBLIC API — retrieval / provenance
# ---------------------------------------------------------------------------

def _rows_to_records(rows: list[dict]) -> list[AuditRecord]:
    records: list[AuditRecord] = []
    for row in rows:
        try:
            records.append(AuditRecord.model_validate(row))
        except Exception:  # noqa: BLE001 — corrupt row shouldn't break the whole history
            logger.error("Skipping malformed audit row: %s", row.get("audit_id", "<unknown>"))
    return sorted(records, key=lambda r: r.timestamp)


def get_audit_history(candidate_id: str) -> list[AuditRecord]:
    """
    Candidate ki poori journey — sabse purane event se latest tak
    (e.g. resume_ingested -> requirement_mapped -> candidate_grouped ->
    interview_evaluated). Ye "Why?" button ke liye backbone hai.
    """
    if not candidate_id:
        raise InvalidAuditInputError("candidate_id is required to fetch audit history.")
    if _db_get_audit_records is None:
        raise AuditPersistenceError(
            "Audit database is not available (db.database.get_audit_records not found)."
        )
    rows = _db_get_audit_records(candidate_id=candidate_id)
    return _rows_to_records(rows)


def get_entity_audit(entity_type: str, entity_id: str) -> list[AuditRecord]:
    """
    Ek specific entity (e.g. ek Mapping, ek InterviewReport) ki poori
    history, chronological order mein. Yahi wo trail hai jo "AWS pehle
    UNCLEAR tha, phir interview ke baad MET hua" jaisi story dikhata hai —
    kyunki purane records overwrite nahi hote, sirf naye append hote hain.
    """
    if not entity_type or not entity_id:
        raise InvalidAuditInputError("entity_type and entity_id are required.")
    if _db_get_audit_records is None:
        raise AuditPersistenceError(
            "Audit database is not available (db.database.get_audit_records not found)."
        )
    rows = _db_get_audit_records(entity_type=entity_type, entity_id=entity_id)
    return _rows_to_records(rows)


def get_candidate_provenance(
    candidate_id: str,
    *,
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
) -> list[AuditRecord]:
    """
    "Why?" button ka direct backend: candidate ke liye poora ya (agar
    entity_type/entity_id diya ho) ek specific insight ka provenance trail
    — action, source, evidence, model aur timestamp sab chronological
    order mein, taaki recruiter ko "AI ne kya bola" nahi, "kis evidence ke
    basis par ye conclusion aaya" dikhaya ja sake.
    """
    if not candidate_id:
        raise InvalidAuditInputError("candidate_id is required.")
    if _db_get_audit_records is None:
        raise AuditPersistenceError(
            "Audit database is not available (db.database.get_audit_records not found)."
        )
    rows = _db_get_audit_records(
        candidate_id=candidate_id, entity_type=entity_type, entity_id=entity_id
    )
    return _rows_to_records(rows)


# ---------------------------------------------------------------------------
# PUBLIC API — thin, action-specific convenience wrappers
# ---------------------------------------------------------------------------
#
# Ye sirf sugar hain: har caller module (extraction.py, mapping.py, ...) ko
# baar-baar sahi AuditAction/entity_type yaad rakhne ki zarurat nahi.
# Sab log_action() par hi delegate karte hain — koi extra logic nahi.

def log_resume_extraction(
    candidate_id: str, *, source: str, evidence: list[Evidence], output: Union[str, dict],
    model: Optional[str], job_id: Optional[str] = None, raise_on_failure: bool = False,
) -> AuditRecord:
    return log_action(
        AuditAction.EXTRACT_RESUME, "candidate", output,
        entity_id=candidate_id, candidate_id=candidate_id, job_id=job_id,
        source=source, evidence=evidence, model=model, raise_on_failure=raise_on_failure,
    )


def log_jd_extraction(
    job_id: str, *, source: str, evidence: list[Evidence], output: Union[str, dict],
    model: Optional[str], raise_on_failure: bool = False,
) -> AuditRecord:
    return log_action(
        AuditAction.EXTRACT_JD, "job", output,
        entity_id=job_id, job_id=job_id,
        source=source, evidence=evidence, model=model, raise_on_failure=raise_on_failure,
    )


def log_mapping(
    requirement_id: str, candidate_id: str, *, job_id: Optional[str],
    source: str, evidence: list[Evidence], output: Union[str, dict],
    model: Optional[str], raise_on_failure: bool = False,
) -> AuditRecord:
    return log_action(
        AuditAction.MAP_REQUIREMENT, "mapping", output,
        entity_id=requirement_id, candidate_id=candidate_id, job_id=job_id,
        source=source, evidence=evidence, model=model, raise_on_failure=raise_on_failure,
    )


def log_grouping(
    candidate_id: str, *, evidence: list[Evidence], output: Union[str, dict],
    job_id: Optional[str] = None, raise_on_failure: bool = False,
) -> AuditRecord:
    # Grouping deterministic/rule-based hai -> model hamesha None.
    return log_action(
        AuditAction.GROUP_CANDIDATE, "candidate", output,
        entity_id=candidate_id, candidate_id=candidate_id, job_id=job_id,
        source="mapping", evidence=evidence, model=None, raise_on_failure=raise_on_failure,
    )


def log_summary(
    candidate_id: str, *, evidence: list[Evidence], output: Union[str, dict],
    model: Optional[str], job_id: Optional[str] = None, raise_on_failure: bool = False,
) -> AuditRecord:
    return log_action(
        AuditAction.SUMMARIZE_CANDIDATE, "candidate_summary", output,
        entity_id=candidate_id, candidate_id=candidate_id, job_id=job_id,
        source="mapping", evidence=evidence, model=model, raise_on_failure=raise_on_failure,
    )


def log_interview_question(
    question_id: str, candidate_id: Optional[str], *, output: Union[str, dict],
    model: Optional[str], job_id: Optional[str] = None,
    is_follow_up: bool = False, raise_on_failure: bool = False,
) -> AuditRecord:
    action = AuditAction.GENERATE_FOLLOWUP if is_follow_up else AuditAction.GENERATE_QUESTION
    return log_action(
        action, "interview_question", output,
        entity_id=question_id, candidate_id=candidate_id, job_id=job_id,
        source="mapping", evidence=[], model=model, raise_on_failure=raise_on_failure,
    )


def log_interview_notes_mapped(
    requirement_id: str, candidate_id: str, *, evidence: list[Evidence],
    output: Union[str, dict], model: Optional[str], job_id: Optional[str] = None,
    raise_on_failure: bool = False,
) -> AuditRecord:
    return log_action(
        AuditAction.MAP_INTERVIEW_NOTES, "requirement_finding", output,
        entity_id=requirement_id, candidate_id=candidate_id, job_id=job_id,
        source="interview_notes", evidence=evidence, model=model,
        raise_on_failure=raise_on_failure,
    )


def log_interview_report_generated(
    report_id: str, candidate_id: str, *, evidence: list[Evidence],
    output: Union[str, dict], job_id: Optional[str] = None, raise_on_failure: bool = False,
) -> AuditRecord:
    # Final report assembly deterministic hai (evaluation.py mein LLM sirf
    # per-answer extraction ke liye use hota hai, report synthesis nahi) ->
    # model=None.
    return log_action(
        AuditAction.GENERATE_REPORT, "interview_report", output,
        entity_id=report_id, candidate_id=candidate_id, job_id=job_id,
        source="interview_notes", evidence=evidence, model=None,
        raise_on_failure=raise_on_failure,
    )


def log_pool_query(
    output: Union[str, dict], *, source: str = "database", raise_on_failure: bool = False,
) -> AuditRecord:
    return log_action(
        AuditAction.POOL_QUERY, "pool_query", output,
        source=source, evidence=[], model=None, raise_on_failure=raise_on_failure,
    )