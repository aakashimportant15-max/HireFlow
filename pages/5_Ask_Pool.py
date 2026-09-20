"""
HireFlow - pages/5_Ask_Pool.py
==============================
Natural-language candidate pool search.

    question -> core.pool_query (LLM -> validated PoolQuery) -> database -> results + evidence

This page only: takes the recruiter's question, hands it to core.pool_query,
shows how the question was interpreted, lists the matching candidates and the
evidence behind them. It never generates or runs SQL, calls an LLM directly,
ranks candidates with an invented score or makes hiring recommendations.

"Why this result" is built only from stored data: skills that literally equal a
value in the interpreted query are highlighted, and stored requirement evidence
whose requirement title contains a query term is listed first.
"""

from __future__ import annotations

import inspect
import logging
from pathlib import Path

import streamlit as st

st.set_page_config(
    page_title="Ask Pool · HireFlow",
    page_icon="🔎",
    layout="wide",
    initial_sidebar_state="expanded",
)

try:
    import core.pool_query as pool_core
    import db.database as dbm
    from db.database import DatabaseError, get_candidate_group, get_candidates, get_jobs, get_mappings, init_db
    from ui.theme import (
        STATUS_META,
        STATUS_RAIL,
        esc,
        evidence_quote,
        group_pill,
        inject_css,
        nav_button,
        page_header,
        plural,
        render_sidebar,
        time_ago,
    )
    from ui.theme import status_pill as _theme_status_pill
except ImportError as exc:  # pragma: no cover - environment problem
    st.error(
        f"Project modules could not be imported: `{exc}`.\n\n"
        "Run the app from the project root: `streamlit run app.py`."
    )
    st.stop()

logger = logging.getLogger("hireflow.ask_pool")

ROOT = Path(__file__).resolve().parent.parent
CANDIDATES_PAGE = "pages/2_Candidates.py"

SUGGESTIONS = [
    "Who has Python and FastAPI experience?",
    "Show candidates with 3+ years of backend experience.",
    "Which candidates need AWS validation?",
    "Which candidates have unclear requirements?",
    "Show 5 candidates with Docker experience.",
    "Show the most experienced Python candidates.",
]

OP_SYMBOL = {"EQ": "=", "NEQ": "≠", "GT": ">", "GTE": "≥", "LT": "<", "LTE": "≤", "CONTAINS": "contains", "IN": "in"}
STATUS_ORDER_MAP = {"MET": 0, "PARTIAL": 1, "UNCLEAR": 2, "MISSING": 3}

# ---- core / db entry points (first name that exists wins; adjust here if yours differ) ----
ASK_FNS = ("ask_pool", "answer_question", "answer_pool_question", "process_question", "run_question")
PARSE_FNS = (
    "parse_question", "question_to_query", "build_pool_query", "interpret_question",
    "parse_pool_query", "generate_pool_query", "natural_language_to_query",
)
EXECUTE_FNS = ("execute_pool_query", "run_pool_query", "execute_query", "run_query", "apply_query", "query_pool")

EXTRA_CSS = """
.hf-ask-lead { font-size: 0.86rem; color: var(--hf-muted); margin: 0.9rem 0 0.4rem 0; }
.hf-interp { background: var(--hf-ai-soft); border: 1px solid #d9d7f8; border-radius: 12px; padding: 0.9rem 1.1rem; margin: 1rem 0 0.4rem 0; }
.hf-interp-h { font-size: 0.9rem; font-weight: 600; color: var(--hf-ai); margin-bottom: 0.4rem; }
.hf-interp-r { display: flex; align-items: baseline; gap: 0.6rem; flex-wrap: wrap; padding: 0.15rem 0; }
.hf-interp-k { font-size: 0.8rem; font-weight: 600; color: var(--hf-muted); min-width: 5.2rem; }
.hf-tag { display: inline-block; background: #ffffff; border: 1px solid #d9d7f8; color: #322e9c; border-radius: 8px;
  padding: 0.15rem 0.6rem; font-size: 0.82rem; font-weight: 500; margin: 0.15rem 0.3rem 0.15rem 0; }
.hf-tag .f { font-weight: 600; }
.hf-tag .o { color: var(--hf-faint); padding: 0 0.15rem; }
.hf-asked { font-size: 0.86rem; color: var(--hf-muted); margin-top: 1rem; }
.hf-asked b { color: var(--hf-ink); font-weight: 600; }
.hf-skill.hit { background: var(--hf-primary-soft); border-color: #b9ccf7; color: var(--hf-primary); font-weight: 600; }
.hf-matchline { font-size: 0.82rem; color: var(--hf-muted); margin: 0.1rem 0 0.2rem 0; }
.hf-why-h { font-size: 0.86rem; font-weight: 600; color: var(--hf-ink-2); margin: 0.9rem 0 0.4rem 0; }
.hf-why-row { display: flex; align-items: center; gap: 0.6rem; margin-top: 0.7rem; font-weight: 600; color: var(--hf-ink); font-size: 0.9rem; }
.hf-evrow { border-left: 4px solid var(--hf-line); padding-left: 0.8rem; margin-bottom: 0.4rem; }
.hf-hist { display: flex; justify-content: space-between; gap: 1rem; padding: 0.5rem 0; border-bottom: 1px solid var(--hf-line-2); font-size: 0.88rem; color: var(--hf-ink-2); }
.hf-hist:last-child { border-bottom: none; }
.hf-hist span { color: var(--hf-muted); white-space: nowrap; font-size: 0.8rem; }
"""


# --------------------------------------------------------------------------
# Tolerant helpers
# --------------------------------------------------------------------------
class WiringError(RuntimeError):
    """A core/db function this page needs is missing or has an unexpected signature."""


_ALIASES = {
    "question": {"question", "text", "natural_language_question", "user_question", "query_text", "q", "prompt"},
    "query": {"query", "pool_query", "pq"},
    "candidates": {"candidates", "profiles"},
    "jobs": {"jobs"},
    "limit": {"limit", "max_results"},
}


def _get(obj: object, *names: str, default=None):
    for n in names:
        val = obj.get(n) if isinstance(obj, dict) else getattr(obj, n, None)
        if val not in (None, "", [], {}):
            return val
    return default


def _enum(value: object) -> str:
    return str(getattr(value, "value", value) or "").upper()


def _as_list(value: object) -> list:
    if value is None or value == "":
        return []
    return list(value) if isinstance(value, (list, tuple, set)) else [value]


def _resolve(names: tuple[str, ...]):
    for module in (pool_core, dbm):
        for n in names:
            fn = getattr(module, n, None)
            if callable(fn):
                return fn
    return None


def _smart_call(fn, ctx: dict):
    sig = inspect.signature(fn)
    kwargs = {}
    for name, p in sig.parameters.items():
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        if name in ctx:
            kwargs[name] = ctx[name]
            continue
        key = next((k for k, aliases in _ALIASES.items() if name in aliases and k in ctx), None)
        if key is not None:
            kwargs[name] = ctx[key]
        elif p.default is p.empty:
            raise WiringError(f"`{fn.__module__}.{fn.__name__}` needs `{name}`, which this page cannot supply.")
    return fn(**kwargs)


def _status_pill(status: str) -> str:
    if status in STATUS_META:
        return _theme_status_pill(status)
    label = status.title() or "Unknown"
    return f'<span class="hf-pill" style="background:#e2e8f0;color:#334155">{esc(label)}</span>'


def _set_question(text: str) -> None:  # on_click callback: runs before the widget is instantiated
    st.session_state["pool_q"] = text


# --------------------------------------------------------------------------
# Running a question
# --------------------------------------------------------------------------
def _normalize(res: object) -> dict:
    """Any reasonable shape -> {'query', 'items', 'message'}."""
    query, items, message = None, [], ""
    if isinstance(res, tuple) and len(res) == 2:
        query, items = res
    elif isinstance(res, list):
        items = res
    elif res is not None:
        query = _get(res, "query", "pool_query", default=None)
        items = _get(res, "results", "candidates", "rows", "items", default=[])
        message = str(_get(res, "message", "clarification", "error", "explanation", default="") or "")
    return {"query": query, "items": _as_list(items), "message": message}


def _run(question: str) -> dict:
    """Returns a normalized result dict; raises WiringError / Exception for the caller to present."""
    ask = _resolve(ASK_FNS)
    if ask is not None:
        return _normalize(_smart_call(ask, {"question": question}))

    parse, execute = _resolve(PARSE_FNS), _resolve(EXECUTE_FNS)
    if parse is None or execute is None:
        raise WiringError(
            "Could not find the pool-query entry points. Expected in `core/pool_query.py`: "
            f"one of {', '.join(ASK_FNS)} — or a parser ({', '.join(PARSE_FNS[:3])}, …) plus an "
            f"executor ({', '.join(EXECUTE_FNS[:3])}, …)."
        )
    with st.status("Understanding your question...", expanded=False) as status:
        query = _smart_call(parse, {"question": question})
        if isinstance(query, dict) and not _get(query, "filters") and _get(query, "message", "clarification"):
            status.update(label="More information needed", state="complete")
            return {"query": None, "items": [], "message": str(_get(query, "message", "clarification"))}
        status.update(label="Searching candidate pool...")
        res = _smart_call(execute, {"query": query})
        status.update(label="Results ready", state="complete")
    out = _normalize(res)
    out["query"] = out["query"] or query
    return out


# --------------------------------------------------------------------------
# Query terms (used only to highlight literal matches in stored data)
# --------------------------------------------------------------------------
def _query_terms(query: object) -> list[str]:
    """Lower-cased string values from the interpreted filters. Numbers/booleans are ignored."""
    terms: list[str] = []
    if query is None:
        return terms
    for f in _as_list(_get(query, "filters", default=[])):
        val = _get(f, "value", default=None)
        vals = val if isinstance(val, (list, tuple, set)) else [val]
        for v in vals:
            if isinstance(v, str) and v.strip():
                terms.append(v.strip().lower())
    return terms


# --------------------------------------------------------------------------
# Renderers
# --------------------------------------------------------------------------
def _render_interpretation(query: object) -> None:
    if query is None:
        return
    filters, sorts = [], []
    for f in _as_list(_get(query, "filters", default=[])):
        field = str(_get(f, "field", default="")).replace("_", " ")
        op = OP_SYMBOL.get(_enum(_get(f, "operator", default="")), str(_get(f, "operator", default="")))
        val = _get(f, "value", default="")
        if isinstance(val, (list, tuple)):
            val = ", ".join(str(v) for v in val)
        filters.append(f'<span class="hf-tag"><span class="f">{esc(field)}</span><span class="o">{esc(op)}</span>{esc(val)}</span>')
    for s in _as_list(_get(query, "sort", default=None)):
        direction = "descending" if _enum(_get(s, "direction", default="DESC")) == "DESC" else "ascending"
        sorts.append(f'<span class="hf-tag">{esc(str(_get(s, "field", default="")).replace("_", " "))} · {direction}</span>')
    limit = _get(query, "limit", default=None)

    rows = ""
    if filters:
        rows += f'<div class="hf-interp-r"><span class="hf-interp-k">Filters</span><span>{"".join(filters)}</span></div>'
    if sorts:
        rows += f'<div class="hf-interp-r"><span class="hf-interp-k">Order</span><span>{"".join(sorts)}</span></div>'
    if limit:
        rows += f'<div class="hf-interp-r"><span class="hf-interp-k">Limit</span><span><span class="hf-tag">up to {esc(limit)} candidates</span></span></div>'
    if not rows:
        return
    st.markdown(
        '<div class="hf-interp"><div class="hf-interp-h">How HireFlow read your question</div>' + rows + "</div>",
        unsafe_allow_html=True,
    )


def _profile(item: object, by_id: dict) -> object:
    cid = _get(item, "candidate_id", "id")
    return by_id.get(cid, item)


def _evidence_rows(m: object, req_titles: dict) -> str:
    s = _enum(_get(m, "status", default=""))
    title = req_titles.get(_get(m, "requirement_id"), "Requirement")
    evidence = _as_list(_get(m, "evidence", default=[]))
    body = ""
    for e in evidence[:2]:
        text = e if isinstance(e, str) else _get(e, "text", default="")
        src = "" if isinstance(e, str) else _enum(_get(e, "source", default="")).replace("_", " ").title()
        where = "" if isinstance(e, str) else _get(e, "location", default="")
        body += evidence_quote(text, src, str(where) if where else "")
    if not evidence:
        body = '<div class="hf-quote">No supporting evidence found in the available profile.</div>'
    return (
        f'<div class="hf-evrow" style="border-left-color:{STATUS_RAIL.get(s, "#cbd5e1")}">'
        f'<div class="hf-why-row">{_status_pill(s)} {esc(title)}</div>{body}</div>'
    )


def _render_why(cid: str, maps_by_cand: dict, req_titles: dict, query: object) -> None:
    maps = maps_by_cand.get(cid, [])
    if not maps:
        st.markdown('<div class="hf-muted">No requirement evidence is stored for this candidate yet.</div>', unsafe_allow_html=True)
        return
    ordered = sorted(maps, key=lambda m: STATUS_ORDER_MAP.get(_enum(_get(m, "status", default="")), 9))
    terms = _query_terms(query)

    def related(m: object) -> bool:
        title = str(req_titles.get(_get(m, "requirement_id"), "")).lower()
        return bool(title) and any(t in title for t in terms)

    rel = [m for m in ordered if terms and related(m)]
    rest = [m for m in ordered if m not in rel]
    html_ = ""
    if rel:
        html_ += '<div class="hf-why-h">Stored evidence for what you asked</div>' + "".join(_evidence_rows(m, req_titles) for m in rel)
        if rest:
            html_ += '<div class="hf-why-h">Other stored requirement evidence</div>' + "".join(_evidence_rows(m, req_titles) for m in rest)
    else:
        html_ += '<div class="hf-why-h">Requirement evidence stored for this candidate</div>' + "".join(_evidence_rows(m, req_titles) for m in rest)
    st.markdown(html_, unsafe_allow_html=True)


def _render_results(res: dict, candidates: list, groups: dict, maps_by_cand: dict, req_titles: dict) -> None:
    items, query, message = res["items"], res["query"], res["message"]
    _render_interpretation(query)

    if not items:
        if message:
            st.info(message)
        else:
            st.markdown(
                '<div class="hf-card" style="margin-top:0.8rem"><div class="hf-card-title">No matching candidates</div>'
                '<div class="hf-card-sub">Try removing one filter, lowering an experience requirement, '
                "or searching one skill at a time.</div></div>",
                unsafe_allow_html=True,
            )
        return

    if message:
        st.caption(message)
    st.markdown(
        f'<div class="hf-section">{plural(len(items), "candidate")} found</div>', unsafe_allow_html=True
    )
    by_id = {str(_get(c, "candidate_id", "id")): c for c in candidates}
    open_ids: set = st.session_state["pool_why_open"]
    terms = _query_terms(query)

    for item in items:
        c = _profile(item, {**by_id})
        cid = str(_get(item, "candidate_id", "id", default=_get(c, "candidate_id", "id", default="")))
        c = by_id.get(cid, c)
        group = groups.get(cid, "UNGROUPED")
        skills = [str(s) for s in _as_list(_get(c, "skills", default=[]))]
        years = _get(c, "total_experience_years", "experience_years", default="")
        meta = " · ".join(
            p for p in (
                str(_get(c, "location")) if _get(c, "location") else "",
                f"{years} years experience" if years else "",
            ) if p
        )
        hits = [s for s in skills if s.lower() in terms]
        # matched skills first so the reason for the match is visible without opening anything
        shown = hits + [s for s in skills if s not in hits]
        chips = "".join(
            f'<span class="hf-skill{" hit" if s in hits else ""}">{esc(s)}</span>' for s in shown[:8]
        )
        match_line = (
            f'<div class="hf-matchline">Skills matching your question: {esc(", ".join(hits))}</div>' if hits else ""
        )
        with st.container(border=True, key=f"pool_card_{cid}"):
            st.markdown(
                f'<div class="hf-cand-top"><div><div class="hf-cand-name">{esc(_get(c, "name", default="Unnamed candidate"))}</div>'
                f'<div class="hf-cand-meta">{esc(meta)}</div></div>{group_pill(group)}</div>'
                f'<div class="hf-skills">{chips}</div>{match_line}',
                unsafe_allow_html=True,
            )
            b1, b2, _ = st.columns([1, 1, 2])
            with b1:
                if st.button("View candidate", key=f"pool_view_{cid}", use_container_width=True):
                    st.session_state["selected_candidate_id"] = cid
                    st.session_state["open_candidate_id"] = cid
                    if (ROOT / CANDIDATES_PAGE).exists():
                        st.switch_page(CANDIDATES_PAGE)
                    else:
                        st.warning("The Candidates page is not available yet.")
            with b2:
                is_open = cid in open_ids
                if st.button("Hide evidence" if is_open else "Why this result?", key=f"pool_why_{cid}", use_container_width=True):
                    open_ids.symmetric_difference_update({cid})
                    st.rerun()
            if cid in open_ids:
                _render_why(cid, maps_by_cand, req_titles, query)


def _render_history() -> None:
    fn = getattr(dbm, "get_audit_records", None)
    if fn is None:
        return
    try:
        recs = [r for r in (fn() or []) if _enum(_get(r, "action", default="")) == "POOL_QUERY"]
    except Exception:  # noqa: BLE001
        return
    if not recs:
        return
    rows = ""
    for r in list(reversed(recs))[:5]:
        text = str(_get(r, "output", "source", default="Pool query"))
        rows += f'<div class="hf-hist"><div>{esc(text[:110])}{"…" if len(text) > 110 else ""}</div><span>{esc(time_ago(_get(r, "timestamp")))}</span></div>'
    st.markdown(
        '<div class="hf-section">Recent queries</div>'
        f'<div class="hf-card">{rows}</div>'
        '<div class="hf-muted" style="margin-top:0.4rem">Every question is recorded in the audit trail.</div>',
        unsafe_allow_html=True,
    )


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main() -> None:
    inject_css()
    st.markdown(f"<style>{EXTRA_CSS}</style>", unsafe_allow_html=True)
    for k, v in {
        "selected_candidate_id": None, "pool_q": "", "pool_result": None, "pool_asked": "", "pool_why_open": set(),
    }.items():
        st.session_state.setdefault(k, v)

    db_ok = True
    try:
        init_db()
    except Exception as exc:  # noqa: BLE001
        db_ok, db_error = False, str(exc)
    render_sidebar(db_ok)

    page_header(
        "Ask your candidate pool",
        "Ask in plain language. HireFlow shows how it read your question, then the candidates and the stored evidence behind each match.",
        eyebrow="Discover",
    )
    if not db_ok:
        logger.error("DB unavailable: %s", db_error)
        st.error("⚠ Candidate pool is temporarily unavailable.")
        if st.button("Retry", key="retry_db"):
            st.rerun()
        return

    try:
        candidates = get_candidates()
    except (DatabaseError, Exception):  # noqa: BLE001
        logger.exception("Failed to load candidates")
        st.error("⚠ Candidate pool is temporarily unavailable.")
        if st.button("Retry", key="retry_load"):
            st.rerun()
        return
    if not candidates:
        st.info("The candidate pool is empty. Upload resumes to start asking questions.")
        nav_button("Upload job & resumes", "upload", primary=True, key_suffix="empty")
        return

    q_col, b_col = st.columns([5, 1.1], gap="small")
    with q_col:
        question = st.text_input(
            "Question", key="pool_q", label_visibility="collapsed",
            placeholder="Ask about your candidate pool, e.g. “Show candidates with Python and FastAPI experience”",
        )
    with b_col:
        ask = st.button("Ask HireFlow", type="primary", use_container_width=True, key="pool_ask")

    st.markdown('<div class="hf-ask-lead">Try asking</div>', unsafe_allow_html=True)
    cols = st.columns(3)
    for i, s in enumerate(SUGGESTIONS):
        with cols[i % 3]:
            st.button(s, key=f"sugg_{i}", on_click=_set_question, args=(s,), use_container_width=True)

    if ask:
        q = question.strip()
        if not q:
            st.warning("Type a question first.")
        else:
            st.session_state["pool_why_open"] = set()
            try:
                st.session_state["pool_result"] = _run(q)
                st.session_state["pool_asked"] = q
            except WiringError as exc:
                st.session_state["pool_result"] = None
                st.error(str(exc))
            except DatabaseError:
                logger.exception("DB error during pool query")
                st.session_state["pool_result"] = None
                st.error("⚠ Candidate pool is temporarily unavailable.")
            except ValueError:
                logger.exception("Pool query rejected")
                st.session_state["pool_result"] = None
                st.warning("⚠ I couldn't safely interpret that query. "
                           "Try: “Show Python candidates with 3+ years experience.”")
            except Exception:  # noqa: BLE001
                logger.exception("Pool query failed")
                st.session_state["pool_result"] = None
                st.error("⚠ Unable to interpret the question right now. Please try again.")

    res = st.session_state["pool_result"]
    if res is not None:
        st.markdown(
            f'<div class="hf-asked">You asked: <b>“{esc(st.session_state["pool_asked"])}”</b></div>',
            unsafe_allow_html=True,
        )
        try:
            groups: dict = {}
            for it in res["items"]:
                cid = str(_get(it, "candidate_id", "id", default=""))
                try:
                    groups[cid] = _enum(get_candidate_group(cid)) or "UNGROUPED"
                except Exception:  # noqa: BLE001
                    groups[cid] = "UNGROUPED"
            maps_by_cand: dict = {}
            for m in get_mappings(latest_only=True):
                maps_by_cand.setdefault(str(_get(m, "candidate_id", default="")), []).append(m)
            req_titles = {
                _get(r, "id"): str(_get(r, "title", default="Requirement"))
                for j in get_jobs() for r in _get(j, "requirements", default=[])
            }
        except Exception:  # noqa: BLE001
            logger.exception("Failed to load result details")
            groups, maps_by_cand, req_titles = {}, {}, {}
            st.warning("Some result details could not be loaded.")
        _render_results(res, candidates, groups, maps_by_cand, req_titles)

    _render_history()
    st.markdown(
        '<div class="hf-footer">Missing or unclear evidence means nothing was found in the available profile, '
        "not that a skill is absent. Human hiring decisions stay with the recruiter.</div>",
        unsafe_allow_html=True,
    )


main()