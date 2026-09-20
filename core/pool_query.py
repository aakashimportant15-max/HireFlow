"""
core/pool_query.py
===================

HireFlow ka "natural-language candidate search layer".

    Recruiter ka plain-English sawaal
                |
           pool_query.py
                |
           PoolQuery (whitelisted, validated)
                |
           db/database.py
                |
           Candidate records
                |
           Recruiter-friendly answer

Design principle — **LLM interprets, database retrieves**:

    Recruiter question
        |
        +-- LLM (llm/client.py) -> raw structured intent (filters/sort/fields)
        |
        +-- DETERMINISTIC whitelist validation (fields, operators, enum
        |   values, limit) -- LLM output kabhi bhi database tak seedha
        |   nahi jaata
        |
        v
    PoolQuery (schemas.py)  ->  db/database.py  ->  candidate records

LLM kabhi SQL nahi likhta, aur kabhi ye decide nahi karta ki konsa candidate
match karta hai — wo sirf "recruiter ka matlab kya hai" batata hai. Actual
filtering hamesha deterministic database layer mein hoti hai.

Is file mein jaan-bujh kar NAHI hai:
    - Resume/JD parsing, extraction, mapping    -> ingestion/extraction/mapping.py
    - Interview evaluation                      -> evaluation.py
    - Raw SQL generation/execution              -> db/database.py (sirf wahi)
    - Groq SDK directly                         -> llm/client.py
    - Streamlit UI                               -> pages/
    - Subjective "best candidate" ranking logic  -> kahin nahi (undefined hai)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from pydantic import BaseModel, Field

from llm.client import LLMError, LLMValidationError, generate_from_prompt_file
from models.schemas import FilterOperator, PoolFilter, PoolQuery, PoolSort, SortDirection

logger = logging.getLogger("hireflow.pool_query")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class PoolQueryError(Exception):
    """Base class for pool_query.py errors."""


class InvalidPoolQuestionError(PoolQueryError):
    """Recruiter ka question khaali/invalid hai."""


class PoolQueryParsingError(PoolQueryError):
    """LLM se natural language -> PoolQuery convert nahi ho paaya."""


class AmbiguousPoolQueryError(PoolQueryError):
    """
    Question sirf subjective terms par based hai ('best', 'top', 'good')
    aur koi defined, whitelisted criterion nahi mila. System fake precision
    invent nahi karega -- recruiter se clarification maangta hai.
    """


class PoolQueryValidationError(PoolQueryError):
    """
    PoolQuery mein whitelist ke bahar field/operator/value mila.
    Security-critical: is error ko kabhi silently ignore mat karo.
    """

    def __init__(self, message: str, issues: list[str]):
        super().__init__(message)
        self.issues = issues


class PoolQueryExecutionError(PoolQueryError):
    """db/database.py se query execute karte waqt fail hua."""


# ---------------------------------------------------------------------------
# Whitelist — single source of truth for allowed fields/operators/values.
# NOTHING outside this whitelist reaches db/database.py.
# ---------------------------------------------------------------------------

class _FieldType:
    TEXT = "text"
    NUMBER = "number"
    ENUM = "enum"


@dataclass(frozen=True)
class _FieldSpec:
    field_type: str
    operators: frozenset[FilterOperator]
    enum_values: Optional[frozenset[str]] = None


ALLOWED_FIELDS: dict[str, _FieldSpec] = {
    "candidate_id": _FieldSpec(
        _FieldType.TEXT, frozenset({FilterOperator.EQ, FilterOperator.NEQ, FilterOperator.IN})
    ),
    "name": _FieldSpec(
        _FieldType.TEXT,
        frozenset({FilterOperator.EQ, FilterOperator.NEQ, FilterOperator.CONTAINS}),
    ),
    "location": _FieldSpec(
        _FieldType.TEXT,
        frozenset({FilterOperator.EQ, FilterOperator.NEQ, FilterOperator.CONTAINS}),
    ),
    "skills": _FieldSpec(
        _FieldType.TEXT, frozenset({FilterOperator.CONTAINS, FilterOperator.IN})
    ),
    "years_of_experience": _FieldSpec(
        _FieldType.NUMBER,
        frozenset(
            {
                FilterOperator.EQ,
                FilterOperator.NEQ,
                FilterOperator.GT,
                FilterOperator.GTE,
                FilterOperator.LT,
                FilterOperator.LTE,
            }
        ),
    ),
    "education": _FieldSpec(_FieldType.TEXT, frozenset({FilterOperator.CONTAINS})),
    "certifications": _FieldSpec(_FieldType.TEXT, frozenset({FilterOperator.CONTAINS})),
    "group": _FieldSpec(
        _FieldType.ENUM,
        frozenset({FilterOperator.EQ, FilterOperator.NEQ, FilterOperator.IN}),
        enum_values=frozenset(
            {"STRONG_MATCH", "PARTIAL_MATCH", "NEEDS_VALIDATION", "WEAK_MATCH"}
        ),
    ),
    "mapping_status": _FieldSpec(
        _FieldType.ENUM,
        frozenset({FilterOperator.EQ, FilterOperator.NEQ, FilterOperator.IN}),
        enum_values=frozenset({"MET", "PARTIAL", "UNCLEAR", "MISSING"}),
    ),
    "requirement": _FieldSpec(
        _FieldType.TEXT, frozenset({FilterOperator.EQ, FilterOperator.CONTAINS})
    ),
    "validation_areas": _FieldSpec(_FieldType.TEXT, frozenset({FilterOperator.CONTAINS})),
}

ALLOWED_SORT_FIELDS: frozenset[str] = frozenset({"years_of_experience", "name"})

# requested_fields ke liye thoda wider whitelist (display-only, koi
# filtering/security risk nahi, isliye kuch extra profile fields bhi allowed)
ALLOWED_REQUESTED_FIELDS: frozenset[str] = frozenset(ALLOWED_FIELDS) | {
    "experience",
    "projects",
    "email",
    "phone",
}

DEFAULT_REQUESTED_FIELDS: list[str] = ["name", "skills", "location"]
DEFAULT_LIMIT = 20
MAX_LIMIT = 100

# Question mein ye words ho aur koi concrete filter na nikla ho, to system
# fake precision invent karne ke bajaye clarification maangega.
_SUBJECTIVE_TERMS = frozenset(
    {"best", "top", "strongest", "greatest", "good", "great", "ideal", "perfect"}
)


# ---------------------------------------------------------------------------
# Internal LLM-output model (NOT a schemas.py business entity — sirf
# parse_pool_question() ka internal parsing contract). natural_language_question
# jaanbujh kar isme nahi hai; wo caller khud set karta hai (LLM se echo
# karwana unnecessary risk hai).
# ---------------------------------------------------------------------------

class _LLMFilterItem(BaseModel):
    field: str
    operator: FilterOperator = FilterOperator.CONTAINS
    value: Any


class _LLMSortItem(BaseModel):
    field: str
    direction: SortDirection = SortDirection.DESC


class _LLMPoolQueryResult(BaseModel):
    filters: list[_LLMFilterItem] = Field(default_factory=list)
    sort: Optional[_LLMSortItem] = None
    requested_fields: list[str] = Field(default_factory=list)
    limit: Optional[int] = None


# ---------------------------------------------------------------------------
# Result container for the high-level orchestrator
# ---------------------------------------------------------------------------

@dataclass
class PoolQueryResult:
    query: Optional[PoolQuery]
    records: list[dict[str, Any]] = field(default_factory=list)
    message: str = ""


# ---------------------------------------------------------------------------
# Phase A — Step 1: natural language -> raw structured intent (LLM)
# ---------------------------------------------------------------------------

def _call_llm_for_intent(question: str) -> _LLMPoolQueryResult:
    try:
        return generate_from_prompt_file(
            "pool_query",
            schema=_LLMPoolQueryResult,
            operation="pool_query_parsing",
            question=question,
        )
    except LLMValidationError as exc:
        logger.error("Pool query LLM output failed validation | question=%s", question)
        raise PoolQueryParsingError(
            "Could not understand this search request: LLM returned an invalid response."
        ) from exc
    except LLMError as exc:
        logger.error("Pool query LLM call failed | question=%s", question)
        raise PoolQueryParsingError(f"Could not understand this search request: {exc}") from exc


def parse_pool_question(question: str) -> PoolQuery:
    """
    Recruiter ke natural-language question ko structured `PoolQuery` mein
    convert karta hai.

    Flow:
        question -> LLM (intent only, no SQL, whitelist-only vocabulary)
                 -> raw filters/sort/requested_fields/limit
                 -> PoolQuery (natural_language_question caller-set, LLM
                    echo par depend nahi karta)

    Raises:
        InvalidPoolQuestionError -- question khaali hai
        PoolQueryParsingError    -- LLM call/parsing fail hui
        AmbiguousPoolQueryError  -- sirf subjective terms, koi concrete
                                     filter nahi mila (e.g. "best candidate")
    """
    if not question or not question.strip():
        raise InvalidPoolQuestionError("Search question cannot be empty.")

    result = _call_llm_for_intent(question)

    if not result.filters and not result.sort:
        lowered = question.lower()
        if any(term in lowered for term in _SUBJECTIVE_TERMS):
            raise AmbiguousPoolQueryError(
                "This question relies on a subjective term (e.g. 'best', 'top') "
                "without a defined ranking criterion. Please specify concrete "
                "criteria — e.g. a skill, years of experience, location, or "
                "candidate group."
            )

    limit = result.limit if result.limit is not None else DEFAULT_LIMIT
    limit = max(1, min(limit, MAX_LIMIT))

    try:
        return PoolQuery(
            natural_language_question=question,
            filters=[
                PoolFilter(field=f.field, operator=f.operator, value=f.value)
                for f in result.filters
            ],
            sort=PoolSort(field=result.sort.field, direction=result.sort.direction)
            if result.sort
            else None,
            requested_fields=result.requested_fields,
            limit=limit,
        )
    except Exception as exc:  # pydantic ValidationError etc.
        logger.error("Pool query construction failed | question=%s", question)
        raise PoolQueryParsingError(
            f"Could not build a valid search query from this question: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# Phase A — Step 2: DETERMINISTIC whitelist validation (security boundary)
# ---------------------------------------------------------------------------

def validate_pool_query(query: PoolQuery) -> PoolQuery:
    """
    `PoolQuery` ko whitelist ke against validate karta hai. Ye is file ka
    security boundary hai — koi bhi field/operator/enum-value/sort-field
    jo `ALLOWED_FIELDS` / `ALLOWED_SORT_FIELDS` mein nahi hai, wo
    db/database.py tak kabhi nahi pahunchega.

    Raises:
        PoolQueryValidationError -- ek ya zyada filters/sort whitelist
                                     violate karte hain (saare issues
                                     `.issues` mein collect hote hain)
    """
    issues: list[str] = []

    for f in query.filters:
        spec = ALLOWED_FIELDS.get(f.field)
        if spec is None:
            issues.append(f"Unsupported field: '{f.field}'")
            continue
        if f.operator not in spec.operators:
            issues.append(f"Unsupported operator '{f.operator.value}' for field '{f.field}'")
            continue
        if spec.field_type == _FieldType.NUMBER:
            try:
                float(f.value)
            except (TypeError, ValueError):
                issues.append(f"Field '{f.field}' expects a number, got: {f.value!r}")
        if spec.field_type == _FieldType.ENUM and spec.enum_values is not None:
            values = f.value if isinstance(f.value, list) else [f.value]
            for v in values:
                if str(v).strip().upper() not in spec.enum_values:
                    issues.append(
                        f"Unsupported value '{v}' for field '{f.field}'. "
                        f"Allowed: {sorted(spec.enum_values)}"
                    )

    if query.sort is not None and query.sort.field not in ALLOWED_SORT_FIELDS:
        issues.append(f"Unsupported sort field: '{query.sort.field}'")

    if issues:
        logger.error("Pool query failed whitelist validation | issues=%s", issues)
        raise PoolQueryValidationError(
            "This search could not be safely translated into a supported query.",
            issues=issues,
        )

    # requested_fields: display-only, so unknown ones are dropped (not
    # rejected) rather than failing the whole query.
    clean_requested = [f for f in query.requested_fields if f in ALLOWED_REQUESTED_FIELDS]
    dropped = set(query.requested_fields) - set(clean_requested)
    if dropped:
        logger.warning("Dropping unsupported requested_fields: %s", dropped)

    query.requested_fields = clean_requested or DEFAULT_REQUESTED_FIELDS
    query.limit = max(1, min(query.limit, MAX_LIMIT))
    return query


# ---------------------------------------------------------------------------
# Phase B — validated PoolQuery -> database
# ---------------------------------------------------------------------------
#
# NOTE: db/database.py is the ONLY place that builds/executes actual SQL.
# It is expected to expose:
#
#     def query_candidates(query: PoolQuery) -> list[dict[str, Any]]:
#         """PoolQuery ko safe, parameterized SQL mein translate karke
#         candidate records (dicts, requested_fields ke hisaab se) return
#         karta hai."""
#
# Agar db/database.py abhi implement nahi hui, execute_pool_query() ek
# clear PoolQueryExecutionError raise karega (silently empty list nahi
# dega, jo "no candidates" se confuse ho sakta hai).

try:
    from db.database import query_candidates as _db_query_candidates
except ImportError:  # db/database.py abhi is environment mein nahi hai
    _db_query_candidates = None  # type: ignore[assignment]


def execute_pool_query(query: PoolQuery) -> list[dict[str, Any]]:
    """
    Validated `PoolQuery` ko `db/database.py` ke through execute karta hai.
    Raw SQL yahan kabhi nahi banta/chalta — sirf structured query pass hoti hai.
    """
    if _db_query_candidates is None:
        raise PoolQueryExecutionError(
            "Candidate database is not available (db.database.query_candidates not found)."
        )
    try:
        return _db_query_candidates(query)
    except PoolQueryError:
        raise
    except Exception as exc:  # noqa: BLE001 — database-layer errors ko safe message mein wrap karo
        logger.error("Pool query execution failed | error=%s", type(exc).__name__)
        raise PoolQueryExecutionError(f"Could not retrieve candidates: {exc}") from exc


# ---------------------------------------------------------------------------
# Phase C — records -> recruiter-friendly text (deterministic, no LLM —
# candidate data yahan se guzarte hue kabhi reinterpret/hallucinate nahi hoti)
# ---------------------------------------------------------------------------

def format_pool_results(query: PoolQuery, records: list[dict[str, Any]]) -> str:
    """
    DB records ko ek clean, recruiter-friendly text summary mein convert
    karta hai. LLM yahan involve nahi hota — jo data DB se aaya wahi
    dikhaya jaata hai, kuch invent nahi hota.
    """
    if not records:
        return "No candidates matched the specified filters."

    fields = query.requested_fields or DEFAULT_REQUESTED_FIELDS
    lines = [f"{len(records)} candidate(s) found.", ""]

    for i, record in enumerate(records, start=1):
        name = record.get("name", "Unknown")
        lines.append(f"{i}. {name}")
        for field_name in fields:
            if field_name == "name":
                continue
            value = record.get(field_name)
            if value is None or value == "":
                continue
            if isinstance(value, list):
                value = ", ".join(str(v) for v in value)
            lines.append(f"   {field_name}: {value}")
        lines.append("")

    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# PUBLIC API — full orchestration
# ---------------------------------------------------------------------------

def answer_pool_question(question: str) -> PoolQueryResult:
    """
    End-to-end: recruiter ka natural-language question -> parse -> validate
    -> execute -> format.

        question
           |
        parse_pool_question()      (LLM: intent nikaalta hai)
           |
        validate_pool_query()      (deterministic whitelist check)
           |
        execute_pool_query()       (db/database.py: safe retrieval)
           |
        format_pool_results()      (deterministic text, no LLM)

    Koi bhi expected error (empty question, ambiguous question, unsupported
    field/operator, DB unavailable) yahan crash nahi karti — recruiter ko
    ek clear, honest message milta hai instead of silently wrong results.
    """
    try:
        query = parse_pool_question(question)
        query = validate_pool_query(query)
    except InvalidPoolQuestionError as exc:
        return PoolQueryResult(query=None, records=[], message=str(exc))
    except AmbiguousPoolQueryError as exc:
        return PoolQueryResult(query=None, records=[], message=str(exc))
    except PoolQueryParsingError as exc:
        return PoolQueryResult(query=None, records=[], message=str(exc))
    except PoolQueryValidationError as exc:
        detail = "; ".join(exc.issues)
        return PoolQueryResult(
            query=None,
            records=[],
            message=f"This search could not be processed safely: {detail}",
        )

    try:
        records = execute_pool_query(query)
    except PoolQueryExecutionError as exc:
        return PoolQueryResult(query=query, records=[], message=str(exc))

    message = format_pool_results(query, records)
    return PoolQueryResult(query=query, records=records, message=message)