"""
HireFlow - pages/4_Interview_Report.py
======================================
Evidence-backed interview evaluation workspace.

    saved interview answers  ->  core.evaluation  ->  InterviewReport  ->  database  ->  this page

This page only: loads stored reports, triggers (re)generation in core.evaluation,
and lays the findings out with their evidence. It shows resume evidence and
interview evidence side by side and never overwrites the original resume
mapping. It never calls an LLM directly, writes SQL, scores candidates or
recommends hire / reject: no overall score, no final recommendation.

Reading order on the page:
    header -> overview -> summary -> evidence comparison (resume vs interview)
    -> requirement findings (expandable traceability) -> strengths / gaps / validation
    -> interview notes -> audit trail
"""

from __future__ import annotations

import inspect
import logging
from datetime import datetime
from pathlib import Path

import streamlit as st

st.set_page_config(
    page_title="Interview Report · HireFlow",
    page_icon="📋",
    layout="wide",
    initial_sidebar_state="expanded",
)

try:
    import core.evaluation as evaluation_core
    import db.database as dbm
    from db.database import DatabaseError, get_candidates, get_jobs, init_db
    from ui.theme import (
        ACTION_LABELS,
        STATUS_META,
        STATUS_RAIL,
        esc,
        inject_css,
        nav_button,
        page_header,
        plural,
        render_sidebar,
        status_counts_html,
    )
    from ui.theme import status_pill as _theme_status_pill
except ImportError as exc:  # pragma: no cover - environment problem
    st.error(
        f"Project modules could not be imported: `{exc}`.\n\n"
        "Run the app from the project root: `streamlit run app.py`."
    )
    st.stop()

logger = logging.getLogger("hireflow.interview_report")

ROOT = Path(__file__).resolve().parent.parent
PAGES = {
    "candidates": "pages/2_Candidates.py",
    "interview": "pages/3_Interview_Prep.py",
}

STATUS_ORDER = ["MET", "PARTIAL", "UNCLEAR", "MISSING"]

# ---- core / db entry points (first name that exists wins; adjust here if yours differ) ----
GENERATE_REPORT_FNS = (
    "generate_report", "generate_interview_report", "evaluate_interview",
    "build_report", "create_report", "evaluate",
)
SAVE_REPORT_FNS = ("save_interview_report", "add_interview_report", "insert_interview_report", "save_report")

EXTRA_CSS = """
.hf-hdr { display: flex; justify-content: space-between; gap: 1rem; flex-wrap: wrap; align-items: flex-start; }
.hf-hdr-name { font-size: 1.6rem; font-weight: 600; color: var(--hf-ink); letter-spacing: -0.01em; }
.hf-hdr-meta { font-size: 0.86rem; color: var(--hf-muted); margin-top: 0.15rem; line-height: 1.6; }
.hf-lbl { font-size: 0.8rem; font-weight: 600; color: var(--hf-muted); margin: 0.2rem 0 0.4rem 0; }
.hf-note { font-size: 0.82rem; color: var(--hf-muted); }
.hf-cov-row { display: flex; align-items: center; gap: 0.8rem; flex-wrap: wrap; margin-bottom: 0.2rem; }
.hf-cov-row .k { font-size: 0.82rem; font-weight: 600; color: var(--hf-muted); min-width: 6.5rem; }
.hf-cov-row .hf-cov { margin: 0.25rem 0; }

/* comparison table */
.hf-cmp td, .hf-cmp th { white-space: nowrap; }
.hf-cmp td:first-child { white-space: normal; }
.hf-arrow { color: var(--hf-faint); padding: 0 0.15rem; }
.hf-delta { display: inline-block; border-radius: 99px; padding: 0.12rem 0.6rem; font-size: 0.75rem; font-weight: 600; }
.hf-delta.same { background: var(--hf-line-2); color: var(--hf-muted); }
.hf-delta.chg { background: var(--hf-ai-soft); color: var(--hf-ai); }
.hf-delta.na { background: transparent; color: var(--hf-faint); }

/* finding card: the rail is the interview status */
.hf-find { background: var(--hf-surface); border: 1px solid var(--hf-line); border-left: 4px solid var(--hf-line);
  border-radius: 10px; padding: 0.85rem 1.05rem; margin: 0.7rem 0 0.15rem 0; }
.hf-find-top { display: flex; justify-content: space-between; gap: 0.6rem; align-items: center; flex-wrap: wrap; }
.hf-find-name { font-weight: 600; color: var(--hf-ink); font-size: 0.98rem; }
.hf-find-sum { font-size: 0.88rem; color: var(--hf-ink-2); margin-top: 0.35rem; line-height: 1.5; }
.hf-chain { display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; font-size: 0.82rem; color: var(--hf-muted); margin-top: 0.55rem; }

/* two evidence sources, visibly different */
.hf-src { display: flex; align-items: center; gap: 0.5rem; font-size: 0.86rem; font-weight: 600; color: var(--hf-ink);
  padding-bottom: 0.4rem; margin-bottom: 0.5rem; border-bottom: 2px solid var(--hf-line); }
.hf-src.resume { border-bottom-color: #94a3b8; }
.hf-src.interview { border-bottom-color: var(--hf-ai); }
.hf-src .tag { font-size: 0.74rem; font-weight: 500; color: var(--hf-muted); }
.hf-ev { font-size: 0.87rem; color: var(--hf-ink-2); background: var(--hf-paper); border-left: 3px solid #94a3b8;
  padding: 0.5rem 0.75rem; border-radius: 0 8px 8px 0; margin: 0.4rem 0; line-height: 1.5; }
.hf-ev.interview { border-left-color: var(--hf-ai); background: #f8f8fe; }
.hf-ev.none { border-left-color: var(--hf-line); color: var(--hf-muted); font-style: normal; }
.hf-ev-meta { font-size: 0.76rem; color: var(--hf-muted); margin-top: 0.25rem; }
.hf-qa { border-top: 1px solid var(--hf-line-2); padding: 0.6rem 0; font-size: 0.88rem; color: var(--hf-ink-2); line-height: 1.5; }
.hf-qa b { color: var(--hf-ink); font-weight: 600; }

/* strengths / gaps / validation */
.hf-trio { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 1rem; }
.hf-box { background: var(--hf-surface); border: 1px solid var(--hf-line); border-top: 3px solid var(--hf-line); border-radius: 10px; padding: 0.9rem 1.05rem; }
.hf-box.str { border-top-color: #16a34a; } .hf-box.gap { border-top-color: #d97706; } .hf-box.val { border-top-color: #2563eb; }
.hf-box h4 { margin: 0 0 0.4rem 0; padding: 0; font-size: 0.95rem; font-weight: 600; color: var(--hf-ink); }
.hf-box ul { margin: 0; padding-left: 1.05rem; color: var(--hf-ink-2); font-size: 0.88rem; line-height: 1.5; }
.hf-box li { margin-bottom: 0.25rem; }
.hf-box .none { color: var(--hf-muted); font-size: 0.86rem; }
"""


# --------------------------------------------------------------------------
# Tolerant helpers
# --------------------------------------------------------------------------
class WiringError(RuntimeError):
    """A core/db function this page needs is missing or has an unexpected signature."""


_ALIASES = {
    "candidate": {"candidate", "profile", "candidate_profile", "cand"},
    "candidate_id": {"candidate_id", "cid"},
    "job": {"job", "job_description", "jd"},
    "job_id": {"job_id", "jid"},
    "requirements": {"requirements", "reqs"},
    "mappings": {"mappings", "mapping"},
    "questions": {"questions", "interview_questions"},
    "answers": {"answers", "interview_answers"},
    "report": {"report", "interview_report"},
    "interviewer": {"interviewer", "interviewer_name"},
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


def _resolve(module: object, names: tuple[str, ...]):
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


def _status(o: object) -> str:
    return _enum(_get(o, "status", default=""))


def _cand_id(c: object) -> str:
    return str(_get(c, "candidate_id", "id", default=""))


def _pill(text: str, bg: str, fg: str) -> str:
    return f'<span class="hf-pill" style="background:{bg};color:{fg}">{esc(text)}</span>'


def _status_pill(status: str) -> str:
    if status in STATUS_META:
        return _theme_status_pill(status)
    return _pill("Not assessed", "#e2e8f0", "#334155")


def _fmt_date(value: object) -> str:
    if isinstance(value, datetime):
        return value.strftime("%d %b %Y")
    try:
        return datetime.fromisoformat(str(value)).strftime("%d %b %Y")
    except (TypeError, ValueError):
        return str(value or "")


def _go(page_key: str) -> None:
    target = PAGES[page_key]
    if not (ROOT / target).exists():
        st.warning("That page is not available yet.")
        return
    try:
        st.switch_page(target)
    except Exception:  # noqa: BLE001
        st.warning("That page is not available yet.")


# --------------------------------------------------------------------------
# Data access
# --------------------------------------------------------------------------
def _db_list(fn_name: str, ctx: dict) -> list:
    fn = getattr(dbm, fn_name, None)
    if fn is None:
        raise WiringError(f"`db.database.{fn_name}` was not found.")
    return list(_smart_call(fn, ctx) or [])


def _load_reports(cid: str, job_id: object) -> list:
    reports = _db_list("get_interview_reports_for_candidate", {"candidate_id": cid})
    if job_id is not None:
        reports = [r for r in reports if _get(r, "job_id") in (None, job_id)]
    return sorted(reports, key=lambda r: str(_get(r, "generated_at", default="")), reverse=True)


def _resume_mappings(cid: str, job) -> dict:
    """Latest RESUME mapping per requirement. Read-only: never replaced by interview findings."""
    req_ids = {_get(r, "id") for r in _get(job, "requirements", default=[])}
    maps = _db_list("get_mappings", {"job_id": _get(job, "job_id", "id"), "latest_only": True})
    return {_get(m, "requirement_id"): m for m in maps if _get(m, "candidate_id") == cid and _get(m, "requirement_id") in req_ids}


def _audit_for(report_id: str) -> list:
    fn = getattr(dbm, "get_audit_records", None)
    if fn is None:
        return []
    try:
        recs = list(fn() or [])
    except Exception:  # noqa: BLE001
        return []
    return [r for r in recs if _get(r, "entity_id") == report_id]


# --------------------------------------------------------------------------
# Generation
# --------------------------------------------------------------------------
def _generate(cand, job, cid: str, job_id) -> None:
    fn = _resolve(evaluation_core, GENERATE_REPORT_FNS)
    if fn is None:
        st.error(f"`core/evaluation.py` has none of: {', '.join(GENERATE_REPORT_FNS)}.")
        return
    try:
        with st.spinner("Evaluating interview evidence..."):
            answers = _db_list("get_interview_answers", {"candidate_id": cid})
            if not answers:
                st.warning("No interview answers have been saved yet. Complete the interview first.")
                return
            questions = _db_list("get_interview_questions", {"candidate_id": cid, "job_id": job_id})
            maps = list(_resume_mappings(cid, job).values())
            before = {_get(r, "report_id") for r in _load_reports(cid, None)}
            result = _smart_call(fn, {
                "candidate": cand, "candidate_id": cid, "job": job, "job_id": job_id,
                "requirements": _get(job, "requirements", default=[]), "answers": answers,
                "questions": questions, "mappings": maps,
                "interviewer": st.session_state.get("ip_interviewer") or None,
            })
            after = {_get(r, "report_id") for r in _load_reports(cid, None)}
            rid = _get(result, "report_id") if result is not None and not isinstance(result, (list, tuple, str)) else None
            if result is not None and rid and rid not in after:  # generator returned a report but did not store it
                save = _resolve(dbm, SAVE_REPORT_FNS)
                if save is None:
                    raise WiringError(f"`db/database.py` has none of: {', '.join(SAVE_REPORT_FNS)}.")
                _smart_call(save, {"report": result, "candidate_id": cid, "job_id": job_id})
                after = {_get(r, "report_id") for r in _load_reports(cid, None)}
            if rid:
                st.session_state["current_report_id"] = rid
            elif after - before:
                st.session_state["current_report_id"] = next(iter(after - before))
    except WiringError as exc:
        st.error(str(exc))
        return
    except DatabaseError:
        logger.exception("DB error while generating report")
        st.error("⚠ Unable to save the report. Please retry.")
        return
    except Exception:  # noqa: BLE001
        logger.exception("Report generation failed")
        st.error("⚠ Unable to generate the report right now. Your saved interview notes are unaffected.")
        return
    st.rerun()


# --------------------------------------------------------------------------
# Renderers
# --------------------------------------------------------------------------
def _evidence_html(evidence: list, empty: str, kind: str) -> str:
    """Quoted evidence with provenance. `kind` is 'resume' or 'interview' (drives the colour of the rail)."""
    if not evidence:
        return f'<div class="hf-ev none">{esc(empty)}</div>'
    out = ""
    for e in evidence:
        text = e if isinstance(e, str) else _get(e, "text", default="")
        src = "" if isinstance(e, str) else _enum(_get(e, "source", default="")).replace("_", " ").title()
        where = "" if isinstance(e, str) else _get(e, "location", default="")
        meta = " · ".join(p for p in (f"Source: {src}" if src else "", f"Location: {where}" if where else "") if p)
        out += (
            f'<div class="hf-ev {kind}">“{esc(text)}”'
            + (f'<div class="hf-ev-meta">{esc(meta)}</div>' if meta else "")
            + "</div>"
        )
    return out


def _list_box(cls: str, title: str, items: list[str], empty: str) -> str:
    body = "<ul>" + "".join(f"<li>{esc(i)}</li>" for i in items) + "</ul>" if items else f'<div class="none">{esc(empty)}</div>'
    return f'<div class="hf-box {cls}"><h4>{esc(title)}</h4>{body}</div>'


def _render_header(report, cand, job) -> None:
    st.markdown(
        f'<div class="hf-card"><div class="hf-hdr"><div>'
        f'<div class="hf-hdr-name">{esc(_get(report, "candidate_name", default=_get(cand, "name", default="Candidate")))}</div>'
        f'<div class="hf-hdr-meta">{esc(_get(job, "title", default=""))}</div></div>'
        f'<div class="hf-hdr-meta" style="text-align:right">'
        f'Interviewer: {esc(_get(report, "interviewer", default="Not recorded"))}<br>'
        f'Date: {esc(_fmt_date(_get(report, "generated_at")))}<br>'
        f'Report ID: {esc(_get(report, "report_id", default=""))}</div></div>'
        '<div class="hf-card-sub" style="margin:0.7rem 0 0 0">Findings are pointers for human review. '
        "They are not a hiring recommendation.</div></div>",
        unsafe_allow_html=True,
    )


def _render_overview(report, questions: list) -> None:
    follow_ups = sum(1 for q in questions if _get(q, "is_follow_up", default=False))
    asked = len(questions) - follow_ups
    if not questions:  # no stored questions: fall back to the report's own answers
        answers = _as_list(_get(report, "answers", default=[]))
        asked, follow_ups = len(answers), sum(1 for a in answers if _get(a, "follow_up_needed", default=False))
    kpis = [
        ("Questions asked", asked),
        ("Follow-ups", follow_ups),
        ("Requirements assessed", len(_as_list(_get(report, "findings", default=[])))),
        ("Unanswered areas", len(_as_list(_get(report, "unanswered_areas", default=[])))),
    ]
    st.markdown(
        '<div class="hf-kpis">'
        + "".join(
            f'<div class="hf-kpi"><div class="hf-kpi-val">{val}</div><div class="hf-kpi-lbl">{esc(lbl)}</div></div>'
            for lbl, val in kpis
        )
        + "</div>",
        unsafe_allow_html=True,
    )


def _render_comparison(report, resume: dict, reqs: dict) -> None:
    findings = _as_list(_get(report, "findings", default=[]))
    st.markdown('<div class="hf-section">Resume vs interview evidence</div>', unsafe_allow_html=True)
    if not findings:
        st.markdown('<div class="hf-muted">No requirement findings were produced for this interview.</div>', unsafe_allow_html=True)
        return

    resume_counts = {s: sum(1 for f in findings if resume.get(_get(f, "requirement_id")) and _status(resume[_get(f, "requirement_id")]) == s) for s in STATUS_ORDER}
    interview_counts = {s: sum(1 for f in findings if _status(f) == s) for s in STATUS_ORDER}
    st.markdown(
        '<div class="hf-card">'
        f'<div class="hf-cov-row"><span class="k">Resume screening</span>{status_counts_html(resume_counts)}</div>'
        f'<div class="hf-cov-row"><span class="k">Interview</span>{status_counts_html(interview_counts)}</div></div>',
        unsafe_allow_html=True,
    )

    rows = ""
    for f in findings:
        rid = _get(f, "requirement_id")
        title = _get(f, "requirement_title", default=reqs.get(rid, "Requirement"))
        r = resume.get(rid)
        s = _status(f)
        r_status = _status(r) if r else ""
        resume_cell = _status_pill(r_status) if r_status else '<span class="hf-muted">Not screened</span>'
        if not r_status:
            delta = '<span class="hf-delta na">n/a</span>'
        elif r_status == s:
            delta = '<span class="hf-delta same">Same</span>'
        else:
            delta = '<span class="hf-delta chg">Changed</span>'
        rows += (
            f"<tr><td><b>{esc(title)}</b></td><td>{resume_cell}</td>"
            f'<td><span class="hf-arrow">→</span>{_status_pill(s)}</td><td>{delta}</td></tr>'
        )
    st.markdown(
        '<div class="hf-card" style="margin-top:0.7rem"><table class="hf-table hf-cmp"><thead><tr>'
        "<th>Requirement</th><th>Resume</th><th>Interview</th><th>Status change</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
        '<div class="hf-note" style="margin-top:0.7rem">Shows how the evidence moved between sources. '
        "Both statuses are kept, and the resume mapping is never overwritten. It is not a decision.</div></div>",
        unsafe_allow_html=True,
    )


def _render_findings(report, resume: dict, reqs: dict) -> None:
    findings = _as_list(_get(report, "findings", default=[]))
    answers = _as_list(_get(report, "answers", default=[]))
    st.markdown('<div class="hf-section">Requirement findings</div>', unsafe_allow_html=True)
    if not findings:
        st.markdown('<div class="hf-muted">No requirement findings were produced for this interview.</div>', unsafe_allow_html=True)
        return
    st.markdown(
        '<div class="hf-note" style="margin:-0.4rem 0 0.4rem 0">Open a requirement to trace its evidence back to the '
        "resume and to the interview notes.</div>",
        unsafe_allow_html=True,
    )

    for f in findings:
        rid = _get(f, "requirement_id")
        title = _get(f, "requirement_title", default=reqs.get(rid, "Requirement"))
        s = _status(f)
        r_map = resume.get(rid)
        r_status = _status(r_map) if r_map else ""

        chain = ""
        if r_status:
            chain = (
                '<div class="hf-chain"><span>Resume</span>' + _status_pill(r_status)
                + '<span class="hf-arrow">→</span><span>Interview</span>' + _status_pill(s) + "</div>"
            )
        st.markdown(
            f'<div class="hf-find" style="border-left-color:{STATUS_RAIL.get(s, "#cbd5e1")}">'
            f'<div class="hf-find-top"><span class="hf-find-name">{esc(title)}</span>{_status_pill(s)}</div>'
            + (f'<div class="hf-find-sum">{esc(_get(f, "summary"))}</div>' if _get(f, "summary") else "")
            + chain + "</div>",
            unsafe_allow_html=True,
        )
        with st.expander("Evidence & traceability", expanded=False):
            col_a, col_b = st.columns(2, gap="medium")
            with col_a:
                st.markdown(
                    '<div class="hf-src resume">From the resume <span class="tag">stored screening evidence</span></div>',
                    unsafe_allow_html=True,
                )
                st.markdown(
                    (_status_pill(r_status) if r_status else '<span class="hf-muted">Not screened</span>')
                    + _evidence_html(
                        _as_list(_get(r_map, "evidence", default=[])) if r_map else [],
                        "No supporting evidence found in the available profile.",
                        "resume",
                    ),
                    unsafe_allow_html=True,
                )
            with col_b:
                st.markdown(
                    '<div class="hf-src interview">From the interview <span class="tag">interviewer notes</span></div>',
                    unsafe_allow_html=True,
                )
                st.markdown(
                    _status_pill(s)
                    + _evidence_html(
                        _as_list(_get(f, "evidence", default=[])),
                        "No interview evidence recorded for this requirement.",
                        "interview",
                    ),
                    unsafe_allow_html=True,
                )
            if r_status and r_status != s:
                st.markdown(
                    f'<div class="hf-note">Resume screening: {esc(_status_label(r_status))} → interview: {esc(_status_label(s))}. '
                    "Both are kept; the resume mapping is not overwritten.</div>",
                    unsafe_allow_html=True,
                )
            elif r_status:
                st.markdown('<div class="hf-note">Resume screening and interview statuses agree.</div>', unsafe_allow_html=True)

            related = [a for a in answers if _get(a, "requirement_id") == rid]
            if related:
                st.markdown('<div class="hf-lbl" style="margin-top:0.8rem">Question and answer notes</div>', unsafe_allow_html=True)
                for a in related:
                    body = (
                        f'<div class="hf-qa"><b>Q:</b> {esc(_get(a, "question", default=""))}<br>'
                        f'<b>Notes:</b> {esc(_get(a, "answer_notes", default="—"))}'
                    )
                    pts = _as_list(_get(a, "unanswered_points", default=[]))
                    if pts:
                        body += "<br><b>Not covered:</b> " + esc("; ".join(str(p) for p in pts))
                    st.markdown(body + "</div>", unsafe_allow_html=True)


def _status_label(status: str) -> str:
    return STATUS_META.get(status, (status.title(),))[0]


def _render_lists(report) -> None:
    findings = _as_list(_get(report, "findings", default=[]))
    unanswered = [str(x) for x in _as_list(_get(report, "unanswered_areas", default=[]))]
    st.markdown('<div class="hf-section">Strengths, gaps and validation</div>', unsafe_allow_html=True)
    validate = [
        str(_get(f, "requirement_title", default=_get(f, "requirement_id", default="")))
        for f in findings if _status(f) in ("UNCLEAR", "PARTIAL", "MISSING")
    ] + [f"{u} (not covered)" for u in unanswered]
    st.markdown(
        '<div class="hf-trio">'
        + _list_box("str", "Interview strengths", [str(x) for x in _as_list(_get(report, "strengths", default=[]))], "No strengths recorded.")
        + _list_box("gap", "Gaps and evidence limitations", [str(x) for x in _as_list(_get(report, "gaps", default=[]))], "No gaps recorded.")
        + _list_box("val", "Validation areas", validate, "No open validation areas.")
        + "</div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="hf-box" style="margin-top:1rem"><h4>Unanswered areas</h4>'
        + ("<ul>" + "".join(f"<li>{esc(u)}</li>" for u in unanswered) + "</ul>" if unanswered else '<div class="none">Every requirement was covered.</div>')
        + "</div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="hf-note" style="margin-top:0.6rem">A gap means the available evidence is incomplete or partial. It is not a rejection.</div>',
        unsafe_allow_html=True,
    )


def _render_notes(report) -> None:
    answers = _as_list(_get(report, "answers", default=[]))
    if not answers:
        return
    with st.expander(f"Interview notes ({plural(len(answers), 'answer')})"):
        for i, a in enumerate(answers, start=1):
            st.markdown(f"**Q{i}. {_get(a, 'question', default='')}**")
            st.write(_get(a, "answer_notes", default="No notes recorded."))
            if _get(a, "follow_up_question"):
                st.caption(f"Follow-up suggested: {_get(a, 'follow_up_question')}")


def _render_audit(report) -> None:
    rid = str(_get(report, "report_id", default=""))
    recs = _audit_for(rid)
    with st.expander("Audit trail"):
        if not recs:
            st.markdown('<div class="hf-muted">No audit entry found for this report.</div>', unsafe_allow_html=True)
            return
        rows = ""
        for r in recs:
            action = str(_get(r, "action", default=""))
            label = ACTION_LABELS.get(action, action.replace("_", " ").capitalize())
            rows += (
                f"<tr><td><b>{esc(label)}</b><div class='hf-muted'>{esc(action)}</div></td>"
                f"<td>{esc(_get(r, 'entity_type', default=''))}<div class='hf-muted'>{esc(rid)}</div></td>"
                f"<td>{esc(_get(r, 'source', default='n/a'))}</td><td>{esc(_get(r, 'model', default='n/a'))}</td>"
                f"<td>{esc(_fmt_date(_get(r, 'timestamp')))}</td></tr>"
            )
        st.markdown(
            '<table class="hf-table"><thead><tr><th>Action</th><th>Record</th><th>Source</th><th>Model</th><th>Date</th></tr></thead>'
            f"<tbody>{rows}</tbody></table>",
            unsafe_allow_html=True,
        )


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main() -> None:
    inject_css()
    st.markdown(f"<style>{EXTRA_CSS}</style>", unsafe_allow_html=True)
    for k, v in {"selected_candidate_id": None, "selected_job_id": None, "current_report_id": None}.items():
        st.session_state.setdefault(k, v)

    db_ok = True
    try:
        init_db()
    except Exception as exc:  # noqa: BLE001
        db_ok, db_error = False, str(exc)
    render_sidebar(db_ok)

    page_header(
        "Interview report",
        "Evidence from the interview, kept next to the original resume evidence so every finding can be traced.",
        eyebrow="Report",
    )
    if not db_ok:
        logger.error("DB unavailable: %s", db_error)
        st.error("⚠ Unable to load interview data. Please check the database connection.")
        if st.button("Retry", key="retry_db"):
            st.rerun()
        return

    try:
        with st.spinner("Loading interview reports..."):
            candidates, jobs = get_candidates(), get_jobs()
    except (DatabaseError, Exception):  # noqa: BLE001
        logger.exception("Failed to load candidates/jobs")
        st.error("⚠ Unable to load interview data.")
        if st.button("Retry", key="retry_load"):
            st.rerun()
        return

    if not candidates or not jobs:
        st.info("No candidates or jobs yet. Upload a job description and resumes first.")
        nav_button("Upload job & resumes", "upload", primary=True, key_suffix="empty")
        return

    # ---- selectors ----
    cand_ids = [_cand_id(c) for c in candidates]
    job_ids = [_get(j, "job_id", "id") for j in jobs]
    sel_c = st.session_state["selected_candidate_id"]
    sel_j = st.session_state["selected_job_id"]

    s1, s2, s3 = st.columns(3, gap="medium")
    with s1:
        c_pick = st.selectbox(
            "Candidate", range(len(candidates)), index=cand_ids.index(sel_c) if sel_c in cand_ids else None,
            format_func=lambda i: str(_get(candidates[i], "name", default="Unnamed candidate")),
            placeholder="Select a candidate", key=f"rp_cand_{sel_c}",
        )
    if c_pick is None:
        st.info("Select a candidate to see their interview reports.")
        return
    cand = candidates[c_pick]
    cid = _cand_id(cand)
    st.session_state["selected_candidate_id"] = cid

    with s2:
        j_options = list(range(len(jobs)))
        j_pick = st.selectbox(
            "Job", j_options, index=job_ids.index(sel_j) if sel_j in job_ids else 0,
            format_func=lambda i: str(_get(jobs[i], "title", default="Untitled job")),
            key=f"rp_job_{cid}_{sel_j}",
        )
    job = jobs[j_pick]
    job_id = _get(job, "job_id", "id")
    st.session_state["selected_job_id"] = job_id

    try:
        reports = _load_reports(cid, job_id)
    except WiringError as exc:
        st.error(str(exc))
        return
    except (DatabaseError, Exception):  # noqa: BLE001
        logger.exception("Failed to load reports")
        st.error("⚠ Unable to load interview data.")
        return

    reqs = {_get(r, "id"): str(_get(r, "title", default="Requirement")) for r in _get(job, "requirements", default=[])}

    if not reports:
        st.markdown(
            '<div class="hf-card"><div class="hf-card-title">No interview report yet</div>'
            '<div class="hf-card-sub">Once the interview answers are saved, generate a structured report from them. '
            "It will place the interview evidence next to the resume evidence for each requirement.</div></div>",
            unsafe_allow_html=True,
        )
        st.write("")
        c1, c2 = st.columns(2)
        with c1:
            if st.button("Generate interview report", type="primary", use_container_width=True, key="gen_first"):
                _generate(cand, job, cid, job_id)
        with c2:
            if st.button("Go to interview prep", use_container_width=True, key="to_prep"):
                _go("interview")
        return

    ids = [str(_get(r, "report_id", default=i)) for i, r in enumerate(reports)]
    cur = st.session_state.get("current_report_id")
    with s3:
        r_pick = st.selectbox(
            "Interview", range(len(reports)), index=ids.index(cur) if cur in ids else 0,
            format_func=lambda i: f"{ids[i]} · {_fmt_date(_get(reports[i], 'generated_at'))}",
            key=f"rp_pick_{cid}_{job_id}_{cur}",
        )
    report = reports[r_pick]
    st.session_state["current_report_id"] = ids[r_pick]

    try:
        resume = _resume_mappings(cid, job)
        questions = _db_list("get_interview_questions", {"candidate_id": cid, "job_id": job_id})
    except Exception:  # noqa: BLE001
        logger.exception("Failed to load supporting data")
        resume, questions = {}, []
        st.warning("Some supporting evidence could not be loaded.")

    _render_header(report, cand, job)
    _render_overview(report, questions)

    st.markdown('<div class="hf-section">Interview summary</div>', unsafe_allow_html=True)
    summary = _get(report, "summary", default="")
    if summary:
        st.write(summary)
    else:
        st.markdown('<div class="hf-muted">No summary was recorded for this report.</div>', unsafe_allow_html=True)

    _render_comparison(report, resume, reqs)
    _render_findings(report, resume, reqs)
    _render_lists(report)
    _render_notes(report)
    _render_audit(report)

    st.write("")
    a1, a2, a3 = st.columns(3)
    with a1:
        if st.button("Refresh evaluation", use_container_width=True, key="refresh_eval"):
            _generate(cand, job, cid, job_id)
    with a2:
        if st.button("View candidate", use_container_width=True, key="view_cand"):
            st.session_state["open_candidate_id"] = cid
            _go("candidates")
    with a3:
        if st.button("Prepare follow-up", use_container_width=True, key="prep_followup"):
            _go("interview")

    st.markdown(
        '<div class="hf-footer">Evidence-backed evaluation. Human hiring decisions stay with the recruiter.</div>',
        unsafe_allow_html=True,
    )


main()