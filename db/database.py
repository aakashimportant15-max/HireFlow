"""
HireFlow - db/database.py
==========================

Persistence layer. Poore application mein **sirf ye file** SQL likhti hai.

    core/*.py  ---->  database.py  ---->  SQLite (hireflow.db)

Is file mein jaan-bujh kar NAHI hai:
    - Business logic (matching/grouping/scoring rules)   -> core/mapping.py, core/grouping.py
    - LLM / Groq calls, prompts                           -> client.py
    - Streamlit UI                                        -> pages/
    - Raw SQL kahin aur (core modules ye function call karte hain, SQL nahi likhte)

Design principles (README se):
    - Sab functions parameterized queries use karte hain (no string-formatted SQL).
    - Foreign keys ON hain -> invalid mapping/child row silently accept nahi hoti.
    - Multi-table saves (candidate + skills + experience + education + projects,
      mapping + evidence, etc.) transactions ke andar hain -> partial writes nahi.
    - `audit_records` APPEND-ONLY hai: sirf INSERT + SELECT functions hain,
      koi update_audit_record / delete_audit_record jaan-bujh kar nahi banaya.
    - `candidate_groups` bhi history-preserving hai (overwrite nahi, naya row).
    - Ye file khud kabhi crash-propagate nahi karti chupke se: DB-level errors
      `DatabaseError` (ya subclasses) ke roop mein raise hote hain taaki caller
      (jaise audit.py) explicitly handle kar sake.

Contract jo baaki core/*.py expect karte hain (mat todna):
    audit.py  -> save_audit_record(record: AuditRecord) -> None
                 get_audit_records(candidate_id=None, job_id=None,
                                    entity_type=None, entity_id=None,
                                    action=None) -> list[dict]
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, Optional, Union

from models.schemas import (
    AuditAction,
    AuditRecord,
    CandidateGroup,
    CandidateProfile,
    CandidateSummary,
    Difficulty,
    EducationItem,
    Evidence,
    EvidenceSource,
    ExperienceItem,
    InterviewAnswer,
    InterviewQuestion,
    InterviewReport,
    JobDescription,
    Mapping,
    MappingStatus,
    PoolQuery,
    ProjectItem,
    Requirement,
    RequirementCategory,
    RequirementFinding,
    RequirementPriority,
)

logger = logging.getLogger("hireflow.database")

__all__ = [
    "DatabaseError",
    "NotFoundError",
    "init_db",
    "get_connection",
    # Jobs / requirements
    "save_job",
    "get_job",
    "get_jobs",
    # Candidates
    "save_candidate",
    "get_candidate",
    "get_candidates",
    "update_candidate",
    # Mapping
    "save_mapping",
    "get_mappings",
    # Grouping
    "save_group",
    "get_candidate_group",
    "get_group_history",
    # Summary
    "save_summary",
    "get_candidate_summary",
    # Interview
    "save_interview_question",
    "get_interview_questions",
    "save_interview_answer",
    "update_interview_answer",
    "get_interview_answers",
    # Evaluation / reports
    "save_interview_report",
    "get_interview_report",
    "get_interview_reports_for_candidate",
    # Audit (append-only)
    "save_audit_record",
    "get_audit_records",
    # Pool query (safe, whitelisted filtering)
    "query_candidates",
    "query_candidate_pool",
]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class DatabaseError(Exception):
    """Base class for db/database.py errors."""


class NotFoundError(DatabaseError):
    """Requested row does not exist."""


class IntegrityErrorWrapped(DatabaseError):
    """Foreign key / uniqueness / constraint violation."""


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------

_DB_PATH = os.environ.get(
    "HIREFLOW_DB_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "hireflow.db"),
)

# sqlite3 connections are not thread-safe to share across threads; Streamlit
# can run callbacks on different threads, so we keep one connection per
# thread rather than one global connection.
_local = threading.local()


def _raw_connection() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(_DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        _local.conn = conn
    return conn


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    """
    Context manager exposing the raw connection for read-only access
    (SELECTs). For writes, prefer `_transaction()` below so rollback is
    automatic on error.
    """
    conn = _raw_connection()
    try:
        yield conn
    except sqlite3.Error as exc:
        raise DatabaseError(str(exc)) from exc


@contextmanager
def _transaction() -> Iterator[sqlite3.Cursor]:
    """
    BEGIN ... COMMIT / ROLLBACK wrapper. Use for any write that touches
    more than one table (or even one, for consistency) so a failure never
    leaves half-written rows.
    """
    conn = _raw_connection()
    cur = conn.cursor()
    try:
        cur.execute("BEGIN;")
        yield cur
        conn.commit()
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        raise IntegrityErrorWrapped(str(exc)) from exc
    except sqlite3.Error as exc:
        conn.rollback()
        raise DatabaseError(str(exc)) from exc
    except Exception:
        conn.rollback()
        raise


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id      TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    company     TEXT,
    location    TEXT,
    raw_text    TEXT,
    source_file TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS requirements (
    requirement_id  TEXT PRIMARY KEY,
    job_id          TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    title           TEXT NOT NULL,
    description     TEXT DEFAULT '',
    category        TEXT NOT NULL,
    priority        TEXT NOT NULL,
    years_required  REAL
);
CREATE INDEX IF NOT EXISTS idx_requirements_job ON requirements(job_id);

CREATE TABLE IF NOT EXISTS candidates (
    candidate_id   TEXT PRIMARY KEY,
    name           TEXT NOT NULL DEFAULT 'Unknown',
    email          TEXT,
    phone          TEXT,
    location       TEXT,
    certifications TEXT DEFAULT '[]',   -- JSON list[str]
    resume_source  TEXT,
    raw_text       TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS candidate_skills (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    skill        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_skills_candidate ON candidate_skills(candidate_id);
CREATE INDEX IF NOT EXISTS idx_skills_skill ON candidate_skills(skill);

CREATE TABLE IF NOT EXISTS candidate_experience (
    experience_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id    TEXT NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    title           TEXT NOT NULL,
    company         TEXT,
    start_date      TEXT,
    end_date        TEXT,
    duration_years  REAL,
    description     TEXT DEFAULT '',
    technologies    TEXT DEFAULT '[]'   -- JSON list[str]
);
CREATE INDEX IF NOT EXISTS idx_experience_candidate ON candidate_experience(candidate_id);

CREATE TABLE IF NOT EXISTS candidate_education (
    education_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id   TEXT NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    degree         TEXT NOT NULL,
    field_of_study TEXT,
    institution    TEXT,
    year           TEXT
);
CREATE INDEX IF NOT EXISTS idx_education_candidate ON candidate_education(candidate_id);

CREATE TABLE IF NOT EXISTS candidate_projects (
    project_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id  TEXT NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    description   TEXT DEFAULT '',
    technologies  TEXT DEFAULT '[]',  -- JSON list[str]
    outcome       TEXT,
    url           TEXT
);
CREATE INDEX IF NOT EXISTS idx_projects_candidate ON candidate_projects(candidate_id);

CREATE TABLE IF NOT EXISTS mappings (
    mapping_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    requirement_id TEXT NOT NULL REFERENCES requirements(requirement_id) ON DELETE CASCADE,
    candidate_id   TEXT NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    status         TEXT NOT NULL,
    confidence     REAL NOT NULL DEFAULT 0.5,
    reason         TEXT DEFAULT '',
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mappings_candidate ON mappings(candidate_id);
CREATE INDEX IF NOT EXISTS idx_mappings_requirement ON mappings(requirement_id);
CREATE INDEX IF NOT EXISTS idx_mappings_pair ON mappings(candidate_id, requirement_id);

CREATE TABLE IF NOT EXISTS mapping_evidence (
    evidence_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    mapping_id   INTEGER NOT NULL REFERENCES mappings(mapping_id) ON DELETE CASCADE,
    text         TEXT NOT NULL,
    source       TEXT NOT NULL,
    location     TEXT,
    source_id    TEXT
);
CREATE INDEX IF NOT EXISTS idx_mapping_evidence_mapping ON mapping_evidence(mapping_id);

CREATE TABLE IF NOT EXISTS candidate_groups (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    job_id       TEXT REFERENCES jobs(job_id) ON DELETE CASCADE,
    grp          TEXT NOT NULL,   -- 'group' is a SQL keyword, avoid it as a column name
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_groups_candidate_job ON candidate_groups(candidate_id, job_id);

CREATE TABLE IF NOT EXISTS candidate_summaries (
    summary_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id     TEXT NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    job_id           TEXT REFERENCES jobs(job_id) ON DELETE CASCADE,
    candidate_name   TEXT DEFAULT 'Unknown',
    grp              TEXT,
    fit_summary      TEXT DEFAULT '',
    strengths        TEXT DEFAULT '[]',        -- JSON list[str]
    gaps             TEXT DEFAULT '[]',        -- JSON list[str]
    validation_areas TEXT DEFAULT '[]',        -- JSON list[str]
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_summaries_candidate_job ON candidate_summaries(candidate_id, job_id);

CREATE TABLE IF NOT EXISTS summary_evidence (
    evidence_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    summary_id   INTEGER NOT NULL REFERENCES candidate_summaries(summary_id) ON DELETE CASCADE,
    text         TEXT NOT NULL,
    source       TEXT NOT NULL,
    location     TEXT,
    source_id    TEXT
);
CREATE INDEX IF NOT EXISTS idx_summary_evidence_summary ON summary_evidence(summary_id);

CREATE TABLE IF NOT EXISTS interview_questions (
    question_id        TEXT PRIMARY KEY,
    candidate_id        TEXT REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    job_id              TEXT REFERENCES jobs(job_id) ON DELETE CASCADE,
    requirement_id      TEXT REFERENCES requirements(requirement_id) ON DELETE SET NULL,
    question            TEXT NOT NULL,
    reason              TEXT DEFAULT '',
    what_to_validate    TEXT DEFAULT '',
    difficulty          TEXT NOT NULL DEFAULT 'MEDIUM',
    expected_evidence   TEXT DEFAULT '',
    is_follow_up        INTEGER NOT NULL DEFAULT 0,
    parent_question_id  TEXT REFERENCES interview_questions(question_id) ON DELETE SET NULL,
    created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_questions_candidate ON interview_questions(candidate_id);
CREATE INDEX IF NOT EXISTS idx_questions_job ON interview_questions(job_id);

CREATE TABLE IF NOT EXISTS interview_answers (
    answer_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id          TEXT REFERENCES interview_questions(question_id) ON DELETE SET NULL,
    candidate_id         TEXT NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    requirement_id       TEXT REFERENCES requirements(requirement_id) ON DELETE SET NULL,
    question             TEXT NOT NULL,
    answer_notes         TEXT DEFAULT '',
    unanswered_points    TEXT DEFAULT '[]',   -- JSON list[str]
    follow_up_needed     INTEGER NOT NULL DEFAULT 0,
    follow_up_question   TEXT,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_answers_candidate ON interview_answers(candidate_id);
CREATE INDEX IF NOT EXISTS idx_answers_question ON interview_answers(question_id);

CREATE TABLE IF NOT EXISTS interview_answer_evidence (
    evidence_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    answer_id    INTEGER NOT NULL REFERENCES interview_answers(answer_id) ON DELETE CASCADE,
    text         TEXT NOT NULL,
    source       TEXT NOT NULL,
    location     TEXT,
    source_id    TEXT
);
CREATE INDEX IF NOT EXISTS idx_answer_evidence_answer ON interview_answer_evidence(answer_id);

CREATE TABLE IF NOT EXISTS interview_reports (
    report_id         TEXT PRIMARY KEY,
    candidate_id      TEXT NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    job_id            TEXT REFERENCES jobs(job_id) ON DELETE CASCADE,
    candidate_name    TEXT DEFAULT 'Unknown',
    interviewer       TEXT,
    summary           TEXT DEFAULT '',
    strengths         TEXT DEFAULT '[]',          -- JSON list[str]
    gaps              TEXT DEFAULT '[]',          -- JSON list[str]
    unanswered_areas  TEXT DEFAULT '[]',          -- JSON list[str]
    answer_ids        TEXT DEFAULT '[]',          -- JSON list[int] -> interview_answers.answer_id
    generated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reports_candidate_job ON interview_reports(candidate_id, job_id);

CREATE TABLE IF NOT EXISTS requirement_findings (
    finding_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id          TEXT NOT NULL REFERENCES interview_reports(report_id) ON DELETE CASCADE,
    requirement_id     TEXT NOT NULL,
    requirement_title  TEXT DEFAULT '',
    status             TEXT NOT NULL,
    summary            TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_findings_report ON requirement_findings(report_id);

CREATE TABLE IF NOT EXISTS finding_evidence (
    evidence_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    finding_id   INTEGER NOT NULL REFERENCES requirement_findings(finding_id) ON DELETE CASCADE,
    text         TEXT NOT NULL,
    source       TEXT NOT NULL,
    location     TEXT,
    source_id    TEXT
);
CREATE INDEX IF NOT EXISTS idx_finding_evidence_finding ON finding_evidence(finding_id);

-- Append-only trust layer. No UPDATE/DELETE helpers exist for this table
-- on purpose -- see module docstring.
CREATE TABLE IF NOT EXISTS audit_records (
    audit_id     TEXT PRIMARY KEY,
    action       TEXT NOT NULL,
    entity_type  TEXT NOT NULL,
    entity_id    TEXT,
    candidate_id TEXT,
    job_id       TEXT,
    source       TEXT,
    output       TEXT,
    model        TEXT,
    timestamp    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_candidate ON audit_records(candidate_id);
CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_records(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_audit_job ON audit_records(job_id);

CREATE TABLE IF NOT EXISTS audit_evidence (
    evidence_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_id     TEXT NOT NULL REFERENCES audit_records(audit_id) ON DELETE CASCADE,
    text         TEXT NOT NULL,
    source       TEXT NOT NULL,
    location     TEXT,
    source_id    TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_evidence_audit ON audit_evidence(audit_id);
"""


def init_db() -> None:
    """
    Idempotent: app startup par safe hai bar-bar call karna
    (`CREATE TABLE IF NOT EXISTS`). First run par `hireflow.db` khud
    ban jaati hai (sqlite3.connect ek naya file create kar deta hai).
    """
    conn = _raw_connection()
    with conn:
        conn.executescript(_SCHEMA)
    logger.info("HireFlow DB initialized at %s", _DB_PATH)


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dumps(value: Any) -> str:
    return json.dumps(value if value is not None else [], default=str)


def _loads(value: Optional[str]) -> list:
    if not value:
        return []
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []


def _insert_evidence(cur: sqlite3.Cursor, table: str, fk_col: str, fk_value: Any,
                      evidence: list[Evidence]) -> None:
    if not evidence:
        return
    cur.executemany(
        f"INSERT INTO {table} ({fk_col}, text, source, location, source_id) "
        f"VALUES (?, ?, ?, ?, ?)",
        [
            (fk_value, e.text, e.source.value, e.location, e.source_id)
            for e in evidence
        ],
    )


def _load_evidence(conn: sqlite3.Connection, table: str, fk_col: str, fk_value: Any) -> list[Evidence]:
    rows = conn.execute(
        f"SELECT text, source, location, source_id FROM {table} WHERE {fk_col} = ?",
        (fk_value,),
    ).fetchall()
    return [
        Evidence(text=r["text"], source=r["source"], location=r["location"], source_id=r["source_id"])
        for r in rows
    ]


# ===========================================================================
# 1. Jobs + Requirements
# ===========================================================================

def save_job(job: JobDescription) -> None:
    """
    Upsert a JobDescription and replace its requirements. Requirements are
    fully replaced (delete + reinsert) so edits in an existing JD don't
    leave orphaned rows.
    """
    with _transaction() as cur:
        cur.execute(
            """
            INSERT INTO jobs (job_id, title, company, location, raw_text, source_file, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                title=excluded.title, company=excluded.company, location=excluded.location,
                raw_text=excluded.raw_text, source_file=excluded.source_file
            """,
            (job.job_id, job.title, job.company, job.location, job.raw_text,
             job.source_file, _now_iso()),
        )
        cur.execute("DELETE FROM requirements WHERE job_id = ?", (job.job_id,))
        if job.requirements:
            cur.executemany(
                """
                INSERT INTO requirements
                    (requirement_id, job_id, title, description, category, priority, years_required)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (r.id, job.job_id, r.title, r.description, r.category.value,
                     r.priority.value, r.years_required)
                    for r in job.requirements
                ],
            )


def _row_to_requirement(row: sqlite3.Row) -> Requirement:
    return Requirement(
        id=row["requirement_id"],
        title=row["title"],
        description=row["description"] or "",
        category=row["category"],
        priority=row["priority"],
        years_required=row["years_required"],
    )


def get_job(job_id: str) -> JobDescription:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"Job '{job_id}' not found.")
        req_rows = conn.execute(
            "SELECT * FROM requirements WHERE job_id = ?", (job_id,)
        ).fetchall()
    return JobDescription(
        job_id=row["job_id"],
        title=row["title"],
        company=row["company"],
        location=row["location"],
        raw_text=row["raw_text"],
        source_file=row["source_file"],
        requirements=[_row_to_requirement(r) for r in req_rows],
    )


def get_jobs() -> list[JobDescription]:
    with get_connection() as conn:
        job_ids = [r["job_id"] for r in conn.execute("SELECT job_id FROM jobs ORDER BY created_at DESC")]
    return [get_job(j) for j in job_ids]


# ===========================================================================
# 2. Candidates (+ skills / experience / education / projects)
# ===========================================================================

def save_candidate(profile: CandidateProfile) -> None:
    """
    Upsert a CandidateProfile. Child rows (skills/experience/education/
    projects) are fully replaced inside one transaction -> either the whole
    candidate is saved consistently, or nothing changes.
    """
    with _transaction() as cur:
        existing = cur.execute(
            "SELECT created_at FROM candidates WHERE candidate_id = ?", (profile.candidate_id,)
        ).fetchone()
        now = _now_iso()
        created_at = existing["created_at"] if existing else now

        cur.execute(
            """
            INSERT INTO candidates
                (candidate_id, name, email, phone, location, certifications,
                 resume_source, raw_text, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(candidate_id) DO UPDATE SET
                name=excluded.name, email=excluded.email, phone=excluded.phone,
                location=excluded.location, certifications=excluded.certifications,
                resume_source=excluded.resume_source, raw_text=excluded.raw_text,
                updated_at=excluded.updated_at
            """,
            (profile.candidate_id, profile.name, profile.email, profile.phone,
             profile.location, _dumps(profile.certifications), profile.resume_source,
             profile.raw_text, created_at, now),
        )

        for table in ("candidate_skills", "candidate_experience", "candidate_education", "candidate_projects"):
            cur.execute(f"DELETE FROM {table} WHERE candidate_id = ?", (profile.candidate_id,))

        if profile.skills:
            cur.executemany(
                "INSERT INTO candidate_skills (candidate_id, skill) VALUES (?, ?)",
                [(profile.candidate_id, s) for s in profile.skills],
            )

        if profile.experience:
            cur.executemany(
                """
                INSERT INTO candidate_experience
                    (candidate_id, title, company, start_date, end_date,
                     duration_years, description, technologies)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (profile.candidate_id, e.title, e.company, e.start_date, e.end_date,
                     e.duration_years, e.description, _dumps(e.technologies))
                    for e in profile.experience
                ],
            )

        if profile.education:
            cur.executemany(
                """
                INSERT INTO candidate_education
                    (candidate_id, degree, field_of_study, institution, year)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (profile.candidate_id, ed.degree, ed.field_of_study, ed.institution, ed.year)
                    for ed in profile.education
                ],
            )

        if profile.projects:
            cur.executemany(
                """
                INSERT INTO candidate_projects
                    (candidate_id, name, description, technologies, outcome, url)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (profile.candidate_id, p.name, p.description, _dumps(p.technologies),
                     p.outcome, p.url)
                    for p in profile.projects
                ],
            )


def update_candidate(candidate_id: str, **fields: Any) -> None:
    """
    Partial update of top-level candidate fields only (name/email/phone/
    location/resume_source/raw_text/certifications). For skills/experience/
    education/projects, call save_candidate() with the full profile instead
    -- those are always replaced as a set.
    """
    allowed = {"name", "email", "phone", "location", "resume_source", "raw_text", "certifications"}
    unknown = set(fields) - allowed
    if unknown:
        raise DatabaseError(f"update_candidate: unsupported field(s) {unknown}")
    if not fields:
        return
    if "certifications" in fields:
        fields["certifications"] = _dumps(fields["certifications"])
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    with _transaction() as cur:
        cur.execute(
            f"UPDATE candidates SET {set_clause}, updated_at = ? WHERE candidate_id = ?",
            (*fields.values(), _now_iso(), candidate_id),
        )
        if cur.rowcount == 0:
            raise NotFoundError(f"Candidate '{candidate_id}' not found.")


def _build_candidate(conn: sqlite3.Connection, row: sqlite3.Row) -> CandidateProfile:
    cid = row["candidate_id"]
    skills = [r["skill"] for r in conn.execute(
        "SELECT skill FROM candidate_skills WHERE candidate_id = ?", (cid,))]
    experience = [
        ExperienceItem(
            title=r["title"], company=r["company"], start_date=r["start_date"],
            end_date=r["end_date"], duration_years=r["duration_years"],
            description=r["description"] or "", technologies=_loads(r["technologies"]),
        )
        for r in conn.execute("SELECT * FROM candidate_experience WHERE candidate_id = ?", (cid,))
    ]
    education = [
        EducationItem(degree=r["degree"], field_of_study=r["field_of_study"],
                       institution=r["institution"], year=r["year"])
        for r in conn.execute("SELECT * FROM candidate_education WHERE candidate_id = ?", (cid,))
    ]
    projects = [
        ProjectItem(name=r["name"], description=r["description"] or "",
                    technologies=_loads(r["technologies"]), outcome=r["outcome"], url=r["url"])
        for r in conn.execute("SELECT * FROM candidate_projects WHERE candidate_id = ?", (cid,))
    ]
    return CandidateProfile(
        candidate_id=cid, name=row["name"], email=row["email"], phone=row["phone"],
        location=row["location"], skills=skills, experience=experience, education=education,
        projects=projects, certifications=_loads(row["certifications"]),
        resume_source=row["resume_source"], raw_text=row["raw_text"],
    )


def get_candidate(candidate_id: str) -> CandidateProfile:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM candidates WHERE candidate_id = ?", (candidate_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"Candidate '{candidate_id}' not found.")
        return _build_candidate(conn, row)


def get_candidates() -> list[CandidateProfile]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM candidates ORDER BY created_at DESC").fetchall()
        return [_build_candidate(conn, r) for r in rows]


# ===========================================================================
# 3. Mapping
# ===========================================================================

def save_mapping(mapping: Mapping) -> int:
    """
    Inserts a new mapping row (+ its evidence). History-preserving by
    default: mapping.py may call this again as new evidence appears
    (e.g. after an interview) without erasing the earlier row -- callers
    that want "latest mapping per pair" should use get_mappings() and take
    the most recent by created_at.
    """
    with _transaction() as cur:
        cur.execute(
            """
            INSERT INTO mappings (requirement_id, candidate_id, status, confidence, reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (mapping.requirement_id, mapping.candidate_id, mapping.status.value,
             mapping.confidence, mapping.reason, _now_iso()),
        )
        mapping_id = cur.lastrowid
        _insert_evidence(cur, "mapping_evidence", "mapping_id", mapping_id, mapping.evidence)
        return mapping_id


def get_mappings(
    *, candidate_id: Optional[str] = None, requirement_id: Optional[str] = None,
    job_id: Optional[str] = None, latest_only: bool = False,
) -> list[Mapping]:
    """
    Filter by candidate_id and/or requirement_id and/or job_id (job_id is
    resolved via requirements.job_id). `latest_only=True` keeps just the
    most recent row per (requirement_id, candidate_id) pair -- useful once
    interview results add new mapping rows on top of resume-time ones.
    """
    clauses, params = [], []
    sql = "SELECT m.* FROM mappings m"
    if job_id is not None:
        sql += " JOIN requirements req ON req.requirement_id = m.requirement_id"
        clauses.append("req.job_id = ?")
        params.append(job_id)
    if candidate_id is not None:
        clauses.append("m.candidate_id = ?")
        params.append(candidate_id)
    if requirement_id is not None:
        clauses.append("m.requirement_id = ?")
        params.append(requirement_id)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY m.created_at ASC"

    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
        results = []
        for r in rows:
            evidence = _load_evidence(conn, "mapping_evidence", "mapping_id", r["mapping_id"])
            results.append(Mapping(
                requirement_id=r["requirement_id"], candidate_id=r["candidate_id"],
                status=r["status"], confidence=r["confidence"], reason=r["reason"] or "",
                evidence=evidence,
            ))

    if latest_only:
        latest: dict[tuple, Mapping] = {}
        for m in results:  # rows are ASC by created_at, so later ones overwrite
            latest[(m.requirement_id, m.candidate_id)] = m
        results = list(latest.values())

    return results


# ===========================================================================
# 4. Grouping (history-preserving)
# ===========================================================================

def save_group(candidate_id: str, group: CandidateGroup, *, job_id: Optional[str] = None) -> None:
    with _transaction() as cur:
        cur.execute(
            "INSERT INTO candidate_groups (candidate_id, job_id, grp, created_at) VALUES (?, ?, ?, ?)",
            (candidate_id, job_id, group.value, _now_iso()),
        )


def get_candidate_group(candidate_id: str, *, job_id: Optional[str] = None) -> Optional[CandidateGroup]:
    """Most recent group assignment, or None if the candidate hasn't been grouped yet."""
    with get_connection() as conn:
        if job_id is not None:
            row = conn.execute(
                """
                SELECT grp FROM candidate_groups
                WHERE candidate_id = ? AND job_id = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (candidate_id, job_id),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT grp FROM candidate_groups
                WHERE candidate_id = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (candidate_id,),
            ).fetchone()
    return CandidateGroup(row["grp"]) if row else None


def get_group_history(candidate_id: str, *, job_id: Optional[str] = None) -> list[dict]:
    """Full chronological history, e.g. NEEDS_VALIDATION -> STRONG_MATCH, for audit/debugging."""
    sql = "SELECT grp, job_id, created_at FROM candidate_groups WHERE candidate_id = ?"
    params: list = [candidate_id]
    if job_id is not None:
        sql += " AND job_id = ?"
        params.append(job_id)
    sql += " ORDER BY created_at ASC"
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [{"group": r["grp"], "job_id": r["job_id"], "created_at": r["created_at"]} for r in rows]


# ===========================================================================
# 5. Candidate Summary
# ===========================================================================

def save_summary(summary: CandidateSummary, *, job_id: Optional[str] = None) -> int:
    with _transaction() as cur:
        cur.execute(
            """
            INSERT INTO candidate_summaries
                (candidate_id, job_id, candidate_name, grp, fit_summary,
                 strengths, gaps, validation_areas, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (summary.candidate_id, job_id, summary.candidate_name,
             summary.group.value if summary.group else None, summary.fit_summary,
             _dumps(summary.strengths), _dumps(summary.gaps),
             _dumps(summary.validation_areas), _now_iso()),
        )
        summary_id = cur.lastrowid
        _insert_evidence(cur, "summary_evidence", "summary_id", summary_id, summary.key_evidence)
        return summary_id


def get_candidate_summary(candidate_id: str, *, job_id: Optional[str] = None) -> Optional[CandidateSummary]:
    """Latest summary for the candidate (optionally scoped to a job)."""
    sql = "SELECT * FROM candidate_summaries WHERE candidate_id = ?"
    params: list = [candidate_id]
    if job_id is not None:
        sql += " AND job_id = ?"
        params.append(job_id)
    sql += " ORDER BY created_at DESC LIMIT 1"

    with get_connection() as conn:
        row = conn.execute(sql, params).fetchone()
        if row is None:
            return None
        evidence = _load_evidence(conn, "summary_evidence", "summary_id", row["summary_id"])

    return CandidateSummary(
        candidate_id=row["candidate_id"], candidate_name=row["candidate_name"],
        group=row["grp"], fit_summary=row["fit_summary"] or "",
        strengths=_loads(row["strengths"]), gaps=_loads(row["gaps"]),
        key_evidence=evidence, validation_areas=_loads(row["validation_areas"]),
    )


# ===========================================================================
# 6. Interview questions
# ===========================================================================

def save_interview_question(question: InterviewQuestion, *, job_id: Optional[str] = None) -> None:
    with _transaction() as cur:
        cur.execute(
            """
            INSERT INTO interview_questions
                (question_id, candidate_id, job_id, requirement_id, question, reason,
                 what_to_validate, difficulty, expected_evidence, is_follow_up,
                 parent_question_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(question_id) DO UPDATE SET
                question=excluded.question, reason=excluded.reason,
                what_to_validate=excluded.what_to_validate, difficulty=excluded.difficulty,
                expected_evidence=excluded.expected_evidence
            """,
            (question.question_id, question.candidate_id, job_id, question.requirement_id,
             question.question, question.reason, question.what_to_validate,
             question.difficulty.value, question.expected_evidence,
             int(question.is_follow_up), question.parent_question_id, _now_iso()),
        )


def _row_to_question(row: sqlite3.Row) -> InterviewQuestion:
    return InterviewQuestion(
        question_id=row["question_id"], candidate_id=row["candidate_id"],
        question=row["question"], requirement_id=row["requirement_id"],
        reason=row["reason"] or "", what_to_validate=row["what_to_validate"] or "",
        difficulty=row["difficulty"], expected_evidence=row["expected_evidence"] or "",
        is_follow_up=bool(row["is_follow_up"]), parent_question_id=row["parent_question_id"],
    )


def get_interview_questions(
    *, candidate_id: Optional[str] = None, job_id: Optional[str] = None,
) -> list[InterviewQuestion]:
    clauses, params = [], []
    if candidate_id is not None:
        clauses.append("candidate_id = ?")
        params.append(candidate_id)
    if job_id is not None:
        clauses.append("job_id = ?")
        params.append(job_id)
    sql = "SELECT * FROM interview_questions"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY created_at ASC"
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_question(r) for r in rows]


# ===========================================================================
# 7. Interview answers
# ===========================================================================

def save_interview_answer(answer: InterviewAnswer, candidate_id: str) -> int:
    with _transaction() as cur:
        now = _now_iso()
        cur.execute(
            """
            INSERT INTO interview_answers
                (question_id, candidate_id, requirement_id, question, answer_notes,
                 unanswered_points, follow_up_needed, follow_up_question, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (answer.question_id, candidate_id, answer.requirement_id, answer.question,
             answer.answer_notes, _dumps(answer.unanswered_points),
             int(answer.follow_up_needed), answer.follow_up_question, now, now),
        )
        answer_id = cur.lastrowid
        _insert_evidence(cur, "interview_answer_evidence", "answer_id", answer_id, answer.evidence)
        return answer_id


def update_interview_answer(answer_id: int, **fields: Any) -> None:
    """
    Partial update -- used e.g. when a follow-up gets answered later and
    `answer_notes` / `follow_up_needed` need revising in place (unlike
    mappings/summaries, a single answer row is meant to be corrected, not
    endlessly re-inserted).
    """
    allowed = {"answer_notes", "unanswered_points", "follow_up_needed", "follow_up_question"}
    unknown = set(fields) - allowed
    if unknown:
        raise DatabaseError(f"update_interview_answer: unsupported field(s) {unknown}")
    if not fields:
        return
    if "unanswered_points" in fields:
        fields["unanswered_points"] = _dumps(fields["unanswered_points"])
    if "follow_up_needed" in fields:
        fields["follow_up_needed"] = int(fields["follow_up_needed"])
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    with _transaction() as cur:
        cur.execute(
            f"UPDATE interview_answers SET {set_clause}, updated_at = ? WHERE answer_id = ?",
            (*fields.values(), _now_iso(), answer_id),
        )
        if cur.rowcount == 0:
            raise NotFoundError(f"Interview answer '{answer_id}' not found.")


def _row_to_answer(conn: sqlite3.Connection, row: sqlite3.Row) -> InterviewAnswer:
    evidence = _load_evidence(conn, "interview_answer_evidence", "answer_id", row["answer_id"])
    return InterviewAnswer(
        question_id=row["question_id"], question=row["question"],
        answer_notes=row["answer_notes"] or "", requirement_id=row["requirement_id"],
        evidence=evidence, unanswered_points=_loads(row["unanswered_points"]),
        follow_up_needed=bool(row["follow_up_needed"]), follow_up_question=row["follow_up_question"],
    )


def get_interview_answers(
    *, candidate_id: Optional[str] = None, question_id: Optional[str] = None,
) -> list[InterviewAnswer]:
    clauses, params = [], []
    if candidate_id is not None:
        clauses.append("candidate_id = ?")
        params.append(candidate_id)
    if question_id is not None:
        clauses.append("question_id = ?")
        params.append(question_id)
    sql = "SELECT * FROM interview_answers"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY created_at ASC"
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [_row_to_answer(conn, r) for r in rows]

def _get_answer_ids_by_candidate_job(
    cur: sqlite3.Cursor,
    candidate_id: str,
    job_id: Optional[str] = None,
) -> list[int]:
    if job_id is None:
        rows = cur.execute(
            """
            SELECT answer_id
            FROM interview_answers
            WHERE candidate_id = ?
            ORDER BY created_at ASC
            """,
            (candidate_id,),
        ).fetchall()
    else:
        rows = cur.execute(
            """
            SELECT ia.answer_id
            FROM interview_answers ia
            JOIN interview_questions iq
                ON ia.question_id = iq.question_id
            WHERE ia.candidate_id = ?
              AND iq.job_id = ?
            ORDER BY ia.created_at ASC
            """,
            (candidate_id, job_id),
        ).fetchall()

    return [r["answer_id"] for r in rows]
# ===========================================================================
# 8. Evaluation: interview reports + requirement findings
# ===========================================================================

def save_interview_report(report: InterviewReport, *, job_id: Optional[str] = None,
                           link_existing_answers: bool = True) -> None:
    """
    Persists the report header + its RequirementFindings (normalized,
    with evidence). `report.answers` are expected to already exist as rows
    in `interview_answers` (saved via save_interview_answer during the
    interview) -- by default this just links the candidate's existing
    answer rows by id so the answer text isn't duplicated. If the report
    carries answers that were never separately saved, pass
    `link_existing_answers=False` and save_interview_answer() them first.
    """
    with _transaction() as cur:
        resolved_job_id = job_id or report.job_id
        answer_ids = _get_answer_ids_by_candidate_job(
    cur,
    report.candidate_id,
    resolved_job_id,
) if link_existing_answers else []

        cur.execute(
            """
            INSERT INTO interview_reports
                (report_id, candidate_id, job_id, candidate_name, interviewer, summary,
                 strengths, gaps, unanswered_areas, answer_ids, generated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(report_id) DO UPDATE SET
                candidate_name=excluded.candidate_name, interviewer=excluded.interviewer,
                summary=excluded.summary, strengths=excluded.strengths, gaps=excluded.gaps,
                unanswered_areas=excluded.unanswered_areas, answer_ids=excluded.answer_ids
            """,
            (report.report_id, report.candidate_id, resolved_job_id, report.candidate_name,
             report.interviewer, report.summary, _dumps(report.strengths), _dumps(report.gaps),
             _dumps(report.unanswered_areas), _dumps(answer_ids),
             report.generated_at.isoformat()),
        )

        cur.execute("DELETE FROM requirement_findings WHERE report_id = ?", (report.report_id,))
        for finding in report.findings:
            cur.execute(
                """
                INSERT INTO requirement_findings
                    (report_id, requirement_id, requirement_title, status, summary)
                VALUES (?, ?, ?, ?, ?)
                """,
                (report.report_id, finding.requirement_id, finding.requirement_title,
                 finding.status.value, finding.summary),
            )
            finding_id = cur.lastrowid
            _insert_evidence(cur, "finding_evidence", "finding_id", finding_id, finding.evidence)


def _row_to_finding(conn: sqlite3.Connection, row: sqlite3.Row) -> RequirementFinding:
    evidence = _load_evidence(conn, "finding_evidence", "finding_id", row["finding_id"])
    return RequirementFinding(
        requirement_id=row["requirement_id"], requirement_title=row["requirement_title"] or "",
        status=row["status"], summary=row["summary"] or "", evidence=evidence,
    )


def get_interview_report(report_id: str) -> InterviewReport:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM interview_reports WHERE report_id = ?", (report_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError(f"Interview report '{report_id}' not found.")
        finding_rows = conn.execute(
            "SELECT * FROM requirement_findings WHERE report_id = ?", (report_id,)
        ).fetchall()
        findings = [_row_to_finding(conn, r) for r in finding_rows]

        answers = []
        for aid in _loads(row["answer_ids"]):
            arow = conn.execute(
                "SELECT * FROM interview_answers WHERE answer_id = ?", (aid,)
            ).fetchone()
            if arow is not None:
                answers.append(_row_to_answer(conn, arow))

    return InterviewReport(
        report_id=row["report_id"], candidate_id=row["candidate_id"], job_id=row["job_id"],
        candidate_name=row["candidate_name"], interviewer=row["interviewer"],
        answers=answers, findings=findings, strengths=_loads(row["strengths"]),
        gaps=_loads(row["gaps"]), unanswered_areas=_loads(row["unanswered_areas"]),
        summary=row["summary"] or "", generated_at=datetime.fromisoformat(row["generated_at"]),
    )


def get_interview_reports_for_candidate(candidate_id: str, *, job_id: Optional[str] = None) -> list[InterviewReport]:
    sql = "SELECT report_id FROM interview_reports WHERE candidate_id = ?"
    params: list = [candidate_id]
    if job_id is not None:
        sql += " AND job_id = ?"
        params.append(job_id)
    sql += " ORDER BY generated_at ASC"
    with get_connection() as conn:
        report_ids = [r["report_id"] for r in conn.execute(sql, params)]
    return [get_interview_report(rid) for rid in report_ids]


# ===========================================================================
# 9. Audit records -- APPEND-ONLY (matches core/audit.py's expected contract)
# ===========================================================================

def save_audit_record(record: AuditRecord) -> None:
    """
    Insert-only. There is deliberately no update/delete for audit rows --
    audit.py's `log_action()` calls this once per event and never revisits
    a past record; corrections show up as new rows.
    """
    with _transaction() as cur:
        cur.execute(
            """
            INSERT INTO audit_records
                (audit_id, action, entity_type, entity_id, candidate_id, job_id,
                 source, output, model, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (record.audit_id, record.action.value, record.entity_type, record.entity_id,
             record.candidate_id, record.job_id, record.source, record.output,
             record.model, record.timestamp.isoformat()),
        )
        _insert_evidence(cur, "audit_evidence", "audit_id", record.audit_id, record.evidence)


def get_audit_records(
    *, candidate_id: Optional[str] = None, job_id: Optional[str] = None,
    entity_type: Optional[str] = None, entity_id: Optional[str] = None,
    action: Optional[Union[AuditAction, str]] = None,
) -> list[dict]:
    """
    Returns plain dicts (not AuditRecord objects) -- audit.py re-validates
    them into AuditRecord itself (`_rows_to_records`) and skips/logs any
    row that fails validation instead of the whole query blowing up.
    """
    clauses, params = [], []
    if candidate_id is not None:
        clauses.append("candidate_id = ?")
        params.append(candidate_id)
    if job_id is not None:
        clauses.append("job_id = ?")
        params.append(job_id)
    if entity_type is not None:
        clauses.append("entity_type = ?")
        params.append(entity_type)
    if entity_id is not None:
        clauses.append("entity_id = ?")
        params.append(entity_id)
    if action is not None:
        clauses.append("action = ?")
        params.append(action.value if isinstance(action, AuditAction) else action)

    sql = "SELECT * FROM audit_records"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY timestamp ASC"

    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
        out = []
        for r in rows:
            evidence = _load_evidence(conn, "audit_evidence", "audit_id", r["audit_id"])
            out.append({
                "audit_id": r["audit_id"],
                "action": r["action"],
                "entity_type": r["entity_type"],
                "entity_id": r["entity_id"],
                "candidate_id": r["candidate_id"],
                "job_id": r["job_id"],
                "source": r["source"],
                "evidence": [e.model_dump() for e in evidence],
                "output": r["output"],
                "model": r["model"],
                "timestamp": r["timestamp"],
            })
        return out


# ===========================================================================
# 10. Pool query -- safe, whitelisted filtering (no LLM-generated SQL, ever)
# ===========================================================================
#
# pool_query.py's ALLOWED_FIELDS is the security boundary: it already
# rejects any field/operator/value outside its whitelist *before* calling
# query_candidates(). This function still independently re-checks `field`
# against its own SUPPORTED set below as defense-in-depth -- a bug in
# pool_query.py's validation should never be able to smuggle an arbitrary
# column name into SQL from here.

_POOL_COMPARISON_SQL = {"EQ": "=", "NEQ": "!=", "GT": ">", "GTE": ">=", "LT": "<", "LTE": "<="}

_POOL_SUPPORTED_FIELDS = frozenset({
    "candidate_id", "name", "location", "skills", "years_of_experience",
    "education", "certifications", "group", "mapping_status",
    "requirement", "validation_areas",
})


def _pool_simple_clause(where: list[str], params: list[Any], column: str, op: str, value: Any) -> None:
    """EQ/NEQ/GT/GTE/LT/LTE/CONTAINS/IN against a single scalar column."""
    if op == "CONTAINS":
        where.append(f"{column} LIKE ?")
        params.append(f"%{value}%")
    elif op == "IN":
        values = value if isinstance(value, list) else [value]
        where.append(f"{column} IN ({', '.join(['?'] * len(values))})")
        params.extend(values)
    else:
        sql_op = _POOL_COMPARISON_SQL.get(op)
        if sql_op is None:
            raise DatabaseError(f"query_candidates: unsupported operator '{op}' for {column}.")
        where.append(f"{column} {sql_op} ?")
        params.append(value)


def query_candidates(query: PoolQuery) -> list[dict[str, Any]]:
    """
    Translates an already whitelist-validated `PoolQuery`
    (core/pool_query.py's `validate_pool_query()` output) into safe,
    parameterized SQL. This is the ONLY function in the codebase that
    builds SQL from recruiter/LLM-originated filter values -- every value
    below is bound as a parameter, never string-formatted into the query.

    Supported filter fields (must match core/pool_query.py's ALLOWED_FIELDS):
        candidate_id, name, location  -> candidates
        skills                        -> candidate_skills (EXISTS)
        years_of_experience           -> SUM(candidate_experience.duration_years)
        education                     -> candidate_education (degree/field/institution)
        certifications                -> candidates.certifications (JSON text, LIKE)
        group                         -> candidate_groups, latest per candidate
        mapping_status                -> mappings.status (EXISTS, any requirement)
        requirement                   -> requirements.title via mappings (EXISTS)
        validation_areas              -> candidate_summaries, latest per candidate
    Supported sort fields: years_of_experience, name.

    Returns a list of dicts, one per matched candidate, projected down to
    `query.requested_fields` (core/pool_query.py's format_pool_results()
    consumes these directly -- no further DB access needed there).
    """
    where: list[str] = []
    params: list[Any] = []

    for f in query.filters:
        field = f.field
        op = f.operator.value if hasattr(f.operator, "value") else str(f.operator)
        value = f.value
        if field not in _POOL_SUPPORTED_FIELDS:
            raise DatabaseError(f"query_candidates: unsupported field '{field}'.")

        if field == "candidate_id":
            _pool_simple_clause(where, params, "c.candidate_id", op, value)
        elif field == "name":
            _pool_simple_clause(where, params, "c.name", op, value)
        elif field == "location":
            _pool_simple_clause(where, params, "c.location", op, value)
        elif field == "skills":
            values = value if isinstance(value, list) else [value]
            if op == "CONTAINS":
                sub = " OR ".join(["cs2.skill LIKE ?"] * len(values))
                params.extend(f"%{v}%" for v in values)
            else:  # IN
                sub = " OR ".join(["cs2.skill = ?"] * len(values))
                params.extend(values)
            where.append(
                f"EXISTS (SELECT 1 FROM candidate_skills cs2 "
                f"WHERE cs2.candidate_id = c.candidate_id AND ({sub}))"
            )
        elif field == "years_of_experience":
            try:
                num = float(value)
            except (TypeError, ValueError):
                raise DatabaseError(f"query_candidates: '{field}' expects a number, got {value!r}.")
            sql_op = _POOL_COMPARISON_SQL.get(op)
            if sql_op is None:
                raise DatabaseError(f"query_candidates: unsupported operator '{op}' for '{field}'.")
            where.append(
                "(SELECT COALESCE(SUM(duration_years), 0) FROM candidate_experience ce "
                f"WHERE ce.candidate_id = c.candidate_id) {sql_op} ?"
            )
            params.append(num)
        elif field == "education":
            where.append(
                "EXISTS (SELECT 1 FROM candidate_education ed WHERE ed.candidate_id = c.candidate_id "
                "AND (ed.degree LIKE ? OR ed.field_of_study LIKE ? OR ed.institution LIKE ?))"
            )
            params.extend([f"%{value}%"] * 3)
        elif field == "certifications":
            where.append("c.certifications LIKE ?")
            params.append(f"%{value}%")
        elif field == "group":
            values = [str(v).strip().upper() for v in (value if isinstance(value, list) else [value])]
            latest_group_sql = (
                "(SELECT grp FROM candidate_groups cg WHERE cg.candidate_id = c.candidate_id "
                "ORDER BY created_at DESC LIMIT 1)"
            )
            if op == "IN":
                where.append(f"{latest_group_sql} IN ({', '.join(['?'] * len(values))})")
                params.extend(values)
            else:
                sql_op = _POOL_COMPARISON_SQL.get(op)
                if sql_op is None:
                    raise DatabaseError(f"query_candidates: unsupported operator '{op}' for '{field}'.")
                where.append(f"{latest_group_sql} {sql_op} ?")
                params.append(values[0])
        elif field == "mapping_status":
            values = [str(v).strip().upper() for v in (value if isinstance(value, list) else [value])]
            if op in ("EQ", "IN"):
                where.append(
                    "EXISTS (SELECT 1 FROM mappings m WHERE m.candidate_id = c.candidate_id "
                    f"AND m.status IN ({', '.join(['?'] * len(values))}))"
                )
                params.extend(values)
            elif op == "NEQ":
                where.append(
                    "NOT EXISTS (SELECT 1 FROM mappings m WHERE m.candidate_id = c.candidate_id "
                    "AND m.status = ?)"
                )
                params.append(values[0])
            else:
                raise DatabaseError(f"query_candidates: unsupported operator '{op}' for '{field}'.")
        elif field == "requirement":
            sql_op = "LIKE" if op == "CONTAINS" else "="
            where.append(
                "EXISTS (SELECT 1 FROM mappings m JOIN requirements r "
                "ON r.requirement_id = m.requirement_id WHERE m.candidate_id = c.candidate_id "
                f"AND r.title {sql_op} ?)"
            )
            params.append(f"%{value}%" if op == "CONTAINS" else value)
        elif field == "validation_areas":
            where.append(
                "EXISTS (SELECT 1 FROM candidate_summaries cs3 "
                "WHERE cs3.candidate_id = c.candidate_id AND cs3.validation_areas LIKE ?)"
            )
            params.append(f"%{value}%")

    order_sql = "c.created_at DESC"
    if query.sort is not None:
        direction = query.sort.direction.value if hasattr(query.sort.direction, "value") else str(query.sort.direction)
        direction = "DESC" if direction.upper() == "DESC" else "ASC"
        if query.sort.field == "years_of_experience":
            order_sql = (
                "(SELECT COALESCE(SUM(duration_years), 0) FROM candidate_experience ce2 "
                f"WHERE ce2.candidate_id = c.candidate_id) {direction}"
            )
        elif query.sort.field == "name":
            order_sql = f"c.name {direction}"
        else:
            raise DatabaseError(f"query_candidates: unsupported sort field '{query.sort.field}'.")

    sql = "SELECT DISTINCT c.* FROM candidates c"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY {order_sql} LIMIT ?"
    params.append(int(query.limit) if query.limit else 20)

    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [_project_pool_fields(conn, _build_candidate(conn, r), query.requested_fields) for r in rows]


def _project_pool_fields(
    conn: sqlite3.Connection, profile: CandidateProfile, requested_fields: list[str],
) -> dict[str, Any]:
    """
    Projects a full CandidateProfile (+ derived data) down to the fields
    format_pool_results() actually needs to display. `name` is always
    included since the formatter reads it unconditionally.
    """
    fields = set(requested_fields) if requested_fields else {"name", "skills", "location"}
    record: dict[str, Any] = {"candidate_id": profile.candidate_id, "name": profile.name}

    if "location" in fields:
        record["location"] = profile.location
    if "email" in fields:
        record["email"] = profile.email
    if "phone" in fields:
        record["phone"] = profile.phone
    if "skills" in fields:
        record["skills"] = profile.skills
    if "certifications" in fields:
        record["certifications"] = profile.certifications
    if "education" in fields:
        record["education"] = [
            ed.degree + (f" ({ed.institution})" if ed.institution else "") for ed in profile.education
        ]
    if "experience" in fields:
        record["experience"] = [
            e.title + (f" at {e.company}" if e.company else "") for e in profile.experience
        ]
    if "projects" in fields:
        record["projects"] = [p.name for p in profile.projects]
    if "years_of_experience" in fields:
        record["years_of_experience"] = profile.total_experience_years
    if "group" in fields:
        grp = get_candidate_group(profile.candidate_id)
        record["group"] = grp.value if grp else None
    if "mapping_status" in fields or "requirement" in fields:
        maps = get_mappings(candidate_id=profile.candidate_id)
        titles = {
            m.requirement_id: (conn.execute(
                "SELECT title FROM requirements WHERE requirement_id = ?", (m.requirement_id,)
            ).fetchone() or {"title": m.requirement_id})["title"]
            for m in maps
        }
        if "mapping_status" in fields:
            record["mapping_status"] = [f"{titles[m.requirement_id]}: {m.status.value}" for m in maps]
        if "requirement" in fields:
            record["requirement"] = [titles[m.requirement_id] for m in maps]
    if "validation_areas" in fields:
        summ = get_candidate_summary(profile.candidate_id)
        record["validation_areas"] = summ.validation_areas if summ else []

    return record


def query_candidate_pool(
    *, skill: Optional[str] = None, min_experience_years: Optional[float] = None,
    location: Optional[str] = None, limit: int = 10,
) -> list[CandidateProfile]:
    """
    Deliberately narrow, parameterized helper for the "Ask Pool" feature --
    pool_query.py should call this (or extend it with more whitelisted
    fields) rather than ever building SQL from LLM output. `skill` and
    `location` use safe LIKE matching; `min_experience_years` sums
    candidate_experience.duration_years per candidate.
    """
    limit = max(1, min(int(limit), 100))
    sql = "SELECT DISTINCT c.* FROM candidates c"
    joins = []
    clauses, params = [], []

    if skill:
        joins.append("JOIN candidate_skills cs ON cs.candidate_id = c.candidate_id")
        clauses.append("cs.skill LIKE ?")
        params.append(f"%{skill}%")

    if min_experience_years is not None:
        joins.append(
            "JOIN (SELECT candidate_id, SUM(COALESCE(duration_years, 0)) AS total_exp "
            "FROM candidate_experience GROUP BY candidate_id) exp "
            "ON exp.candidate_id = c.candidate_id"
        )
        clauses.append("exp.total_exp >= ?")
        params.append(min_experience_years)

    if location:
        clauses.append("c.location LIKE ?")
        params.append(f"%{location}%")

    if joins:
        sql += " " + " ".join(joins)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY c.created_at DESC LIMIT ?"
    params.append(limit)

    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [_build_candidate(conn, r) for r in rows]