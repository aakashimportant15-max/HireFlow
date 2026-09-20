"""
HireFlow - core/pipeline.py
===========================
Thin orchestrator used by pages/1_Upload_Screen.py.

    ingest -> extract -> save -> map -> group -> summarize -> audit

It contains NO extraction / mapping / grouping logic of its own. It only calls
your core modules, saves their output through db/database.py and writes the
audit trail in ONE place.

>>> IMPORTANT: the core modules' function names/signatures are declared in
>>> ADAPTERS below plus the small `_stage_*` helpers. If your modules use other
>>> names or argument orders, change them HERE (one line each), nowhere else.

Assumed contracts (edit if yours differ):
    ingest(filename: str, data: bytes)                       -> str
    extract_job(text: str, source_file: str | None)          -> JobDescription
    extract_candidate(text: str, source_file: str | None)    -> CandidateProfile
    map(candidate: CandidateProfile, job: JobDescription)    -> list[Mapping]
    group(candidate, job, mappings)                          -> CandidateGroup (adapter wraps group_candidates)
    summarize(candidate, job, mappings, group)                -> CandidateSummary

Robustness rules:
  * One bad resume never stops the batch (per-candidate try/except).
  * Duplicate resumes (same text) are skipped, not re-inserted.
  * Mappings pointing at unknown requirement ids are dropped (FK safety).
  * Audit failures are counted and reported, never fatal.
"""

from __future__ import annotations

import hashlib
import importlib
import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from db.database import (
    get_candidates,
    save_audit_record,
    save_candidate,
    save_group,
    save_job,
    save_mapping,
    save_summary,
)
from models.schemas import (
    AuditAction,
    AuditRecord,
    CandidateGroup,
    CandidateProfile,
    Evidence,
    JobDescription,
    Mapping,
)

logger = logging.getLogger("hireflow.pipeline")

# ---- edit these if your core modules use different names ------------------
ADAPTERS: dict[str, tuple[str, str]] = {
    "ingest": ("core.ingestion", "ingest_document"),
    "extract_job": ("core.extraction", "extract_job_description"),
    "extract_candidate": ("core.extraction", "extract_resume"),
    "map": ("core.mapping", "map_candidate_to_job"),
    "group": ("core.grouping", "group_candidates"),
    "summarize": ("core.summary", "build_candidate_summary"),
}
MODEL_NAME: Optional[str] = None  # e.g. "llama-3.3-70b" -> stored in audit records
# ---------------------------------------------------------------------------


class PipelineError(Exception):
    """Recruiter-safe problem (message can be shown as-is)."""


class PipelineConfigError(PipelineError):
    """A core module/function the pipeline expects is missing (developer-facing)."""


@dataclass
class UploadedDoc:
    name: str
    data: bytes


@dataclass
class CandidateOutcome:
    file: str
    name: str = ""
    candidate_id: Optional[str] = None
    status: str = "failed"  # processed | partial | skipped | failed
    reason: str = ""
    group: Optional[str] = None
    n_mappings: int = 0


@dataclass
class PipelineResult:
    job_id: str
    job_title: str
    n_requirements: int
    outcomes: list[CandidateOutcome] = field(default_factory=list)
    n_mappings: int = 0
    n_groups: int = 0
    n_summaries: int = 0
    audit_failures: int = 0

    @property
    def n_processed(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "processed")

    @property
    def n_problems(self) -> int:
        return sum(1 for o in self.outcomes if o.status != "processed")


ProgressFn = Callable[[str, float], None]


# --------------------------------------------------------------------------
# Adapter plumbing
# --------------------------------------------------------------------------
def _load(step: str) -> Callable[..., Any]:
    module_name, fn_name = ADAPTERS[step]
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise PipelineConfigError(
            f"Cannot import '{module_name}' for step '{step}': {exc}. "
            f"Check ADAPTERS in core/pipeline.py."
        ) from exc
    fn = getattr(module, fn_name, None)
    if not callable(fn):
        raise PipelineConfigError(
            f"'{module_name}.{fn_name}' was not found (step '{step}'). "
            f"Update ADAPTERS in core/pipeline.py to your real function name."
        )
    return fn


def _stage_ingest(name: str, data: bytes) -> str:
    """
    core.ingestion.ingest_document() reads from a file path and returns a
    DocumentContent object (text in `.text`) — it does not accept raw bytes.
    So: write the uploaded bytes to a temp file (keeping the original
    extension, since ingestion.py dispatches on suffix), call
    ingest_document() on that path, return `.text`, then clean up.
    The original filename (`name`) is still used as `source` everywhere
    else in the pipeline (audit logs, job.source_file, profile.resume_source).
    """
    suffix = Path(name).suffix  # preserves .pdf / .docx for ingestion's dispatch
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        document = _load("ingest")(tmp_path)
        return document.text
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            logger.warning("Could not remove temp ingestion file: %s", tmp_path)


def _stage_extract_job(text: str, source: str) -> JobDescription:
    return _load("extract_job")(text, source)


def _stage_extract_candidate(text: str, source: str) -> CandidateProfile:
    return _load("extract_candidate")(text, source)


def _stage_map(candidate: CandidateProfile, job: JobDescription) -> list[Mapping]:
    return _load("map")(candidate, job.requirements)


def _stage_group(
    candidate: CandidateProfile,
    job: JobDescription,
    mappings: list[Mapping],
) -> CandidateGroup:
    grouped = _load("group")(
        [candidate],
        {candidate.candidate_id: mappings},
        job.requirements,
    )

    return grouped[candidate.candidate_id]


def _stage_summarize(
    candidate: CandidateProfile,
    job: JobDescription,
    mappings: list[Mapping],
    group: CandidateGroup,
) -> Any:
    return _load("summarize")(candidate, job, mappings, group)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def fingerprint(text: str) -> str:
    """Whitespace/case-insensitive content hash -> duplicate detection."""
    normalized = re.sub(r"\s+", " ", (text or "").strip().lower())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class _Audit:
    def __init__(self) -> None:
        self.failures = 0

    def log(self, action: AuditAction, entity_type: str, *, entity_id: Optional[str] = None,
            candidate_id: Optional[str] = None, job_id: Optional[str] = None,
            source: Optional[str] = None, output: Optional[str] = None,
            evidence: Optional[list[Evidence]] = None) -> None:
        try:
            save_audit_record(
                AuditRecord(
                    action=action, entity_type=entity_type, entity_id=entity_id,
                    candidate_id=candidate_id, job_id=job_id, source=source,
                    output=output, evidence=evidence or [], model=MODEL_NAME,
                )
            )
        except Exception:  # noqa: BLE001 - audit must never break processing
            self.failures += 1
            logger.exception("Audit write failed (%s)", action)


def _safe_progress(cb: Optional[ProgressFn], message: str, fraction: float) -> None:
    if cb is None:
        return
    try:
        cb(message, max(0.0, min(1.0, fraction)))
    except Exception:  # noqa: BLE001 - a UI callback must not kill the pipeline
        logger.exception("Progress callback failed")


def _friendly(exc: Exception) -> str:
    """Recruiter-safe reason; technical detail goes to the log only."""
    if isinstance(exc, PipelineConfigError):
        raise exc
    if isinstance(exc, PipelineError):
        return str(exc)
    logger.exception("Candidate processing failed")
    return "Something went wrong while processing this document."


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------
def process_documents(
    jd: UploadedDoc,
    resumes: list[UploadedDoc],
    on_progress: Optional[ProgressFn] = None,
) -> PipelineResult:
    """
    Raises PipelineError (JD problems) / PipelineConfigError (bad adapter) /
    DatabaseError (JD could not be saved). Per-resume problems never raise;
    they are reported in `PipelineResult.outcomes`.
    """
    audit = _Audit()
    total_steps = 1 + max(len(resumes), 1)

    # ---- 1. Job description -------------------------------------------------
    _safe_progress(on_progress, "Reading job description...", 0.02)
    jd_text = _stage_ingest(jd.name, jd.data)
    if not (jd_text or "").strip():
        raise PipelineError("The job description contains no readable text.")

    _safe_progress(on_progress, "Extracting job requirements...", 0.05)
    job = _stage_extract_job(jd_text, jd.name)
    if not job.requirements:
        raise PipelineError("Could not identify any job requirements. Please check the uploaded JD.")
    if not job.raw_text:
        job.raw_text = jd_text
    if not job.source_file:
        job.source_file = jd.name

    save_job(job)
    audit.log(AuditAction.EXTRACT_JD, "JobDescription", entity_id=job.job_id, job_id=job.job_id,
              source=f"JD: {jd.name}", output=f"{len(job.requirements)} requirements extracted")
    _safe_progress(on_progress, f"Job saved: {job.title} ({len(job.requirements)} requirements)", 1 / total_steps)

    result = PipelineResult(job_id=job.job_id, job_title=job.title, n_requirements=len(job.requirements))
    valid_req_ids = {r.id for r in job.requirements}
    req_title = {r.id: r.title for r in job.requirements}

    # Duplicate detection is scoped to the CURRENT JOB.
    # The same resume must be allowed to run against a different JD.
    # key = (resume fingerprint, job_id)
    seen: dict[tuple[str, str], str] = {}
    try:
        for c in get_candidates():
            if not c.raw_text:
                continue

            # Existing candidates may have already been processed for this job.
            # We check the candidate's mappings through the current job's
            # requirements instead of treating the resume as globally duplicate.
            existing_mappings = []
            try:
                from db.database import get_mappings
                existing_mappings = get_mappings(
                    candidate_id=c.candidate_id,
                    job_id=job.job_id,
                    latest_only=True,
                )
            except Exception:
                logger.exception(
                    "Could not inspect existing mappings for candidate %s",
                    c.candidate_id,
                )

            if existing_mappings:
                seen[(fingerprint(c.raw_text), job.job_id)] = c.name
    except Exception:  # noqa: BLE001
        logger.exception("Could not load existing candidates for duplicate detection")

    # ---- 2. Resumes ---------------------------------------------------------
    for idx, doc in enumerate(resumes, start=1):
        base = idx / total_steps
        step = 1 / total_steps
        outcome = CandidateOutcome(file=doc.name)
        result.outcomes.append(outcome)

        def note(msg: str, part: float) -> None:
            _safe_progress(on_progress, f"[{idx}/{len(resumes)}] {doc.name}: {msg}", base + step * part)

        try:
            note("reading", 0.05)
            text = _stage_ingest(doc.name, doc.data)
            if not (text or "").strip():
                outcome.status, outcome.reason = "skipped", "No readable text found in this file."
                continue

            fp = fingerprint(text)
            duplicate_key = (fp, job.job_id)
            if duplicate_key in seen:
                outcome.status = "skipped"
                outcome.reason = (
                    f"Already processed for this job "
                    f"(same content as {seen[duplicate_key]})."
                )
                outcome.name = seen[duplicate_key]
                continue

            note("extracting profile", 0.2)
            profile = _stage_extract_candidate(text, doc.name)
            if not profile.raw_text:
                profile.raw_text = text
            if not profile.resume_source:
                profile.resume_source = doc.name
            outcome.name, outcome.candidate_id = profile.name, profile.candidate_id

            save_candidate(profile)
            # Do NOT mark the resume as globally duplicate. It is only
            # duplicate for this specific job after job-specific mappings exist.
            seen[duplicate_key] = profile.name
            audit.log(AuditAction.EXTRACT_RESUME, "CandidateProfile", entity_id=profile.candidate_id,
                      candidate_id=profile.candidate_id, job_id=job.job_id, source=f"Resume: {doc.name}",
                      output=f"{len(profile.skills)} skills, {len(profile.experience)} roles extracted")
            outcome.status = "partial"  # profile saved; flips to processed at the very end

            note("mapping requirements", 0.45)
            mappings = []
            for m in _stage_map(profile, job) or []:
                if m.requirement_id not in valid_req_ids:
                    logger.warning("Dropping mapping for unknown requirement %s", m.requirement_id)
                    continue
                m.candidate_id = profile.candidate_id
                mappings.append(m)
            if not mappings:
                raise PipelineError("No requirement mappings were produced for this candidate.")
            for m in mappings:
                save_mapping(m)
                audit.log(AuditAction.MAP_REQUIREMENT, "Mapping", entity_id=m.requirement_id,
                          candidate_id=profile.candidate_id, job_id=job.job_id,
                          source=f"Resume: {doc.name}",
                          output=f"{req_title.get(m.requirement_id, m.requirement_id)}: {m.status.value}",
                          evidence=m.evidence)
            outcome.n_mappings = len(mappings)
            result.n_mappings += len(mappings)

            note("grouping", 0.7)
            raw_group = _stage_group(profile, job, mappings)
            group = raw_group if isinstance(raw_group, CandidateGroup) else CandidateGroup(str(raw_group))
            save_group(profile.candidate_id, group, job_id=job.job_id)
            outcome.group = group.value
            result.n_groups += 1
            audit.log(AuditAction.GROUP_CANDIDATE, "CandidateGroup", entity_id=profile.candidate_id,
                      candidate_id=profile.candidate_id, job_id=job.job_id, output=group.value)

            note("writing summary", 0.85)
            summary = _stage_summarize(profile, job, mappings, group)
            summary.candidate_id = profile.candidate_id
            if not summary.candidate_name or summary.candidate_name == "Unknown":
                summary.candidate_name = profile.name
            summary.group = group
            save_summary(summary, job_id=job.job_id)
            result.n_summaries += 1
            audit.log(AuditAction.SUMMARIZE_CANDIDATE, "CandidateSummary", entity_id=profile.candidate_id,
                      candidate_id=profile.candidate_id, job_id=job.job_id, output=summary.fit_summary[:300],
                      evidence=summary.key_evidence)

            outcome.status = "processed"
            note("done", 1.0)
        except PipelineConfigError:
            raise
        except Exception as exc:  # noqa: BLE001 - isolate per-resume failures
            outcome.reason = _friendly(exc)
            if outcome.status == "partial":
                outcome.reason = f"Profile saved, but processing stopped early. {outcome.reason}"
            elif outcome.status not in ("skipped",):
                outcome.status = "failed"

    result.audit_failures = audit.failures
    _safe_progress(on_progress, "Finished", 1.0)
    return result