"""
HireFlow - pages/3_Interview_Prep.py
====================================
Targeted interview preparation + interview session.

    Candidate + Job + screening mappings  ->  core.interview  ->  InterviewQuestion[]
        ->  interview session (interviewer notes)  ->  InterviewAnswer  ->  database

This page only: selects context, shows what needs validating, triggers question /
follow-up generation in core.interview, runs the interview UI and saves the
interviewer's notes. It never calls an LLM directly, evaluates answers, scores
candidates, writes SQL or makes hiring decisions (evaluation lives in
core.evaluation / 4_Interview_Report.py).

Two modes, both driven by st.session_state["ip_mode"]:
    prep       validation workspace: what to validate, and the questions built from it
    interview  distraction-free session: one question, notes, progress rail
"""

from __future__ import annotations

import inspect
import logging
from collections import defaultdict
from pathlib import Path

import streamlit as st

st.set_page_config(
    page_title="Interview Prep · HireFlow",
    page_icon="🎤",
    layout="wide",
    initial_sidebar_state="expanded",
)

try:
    import core.interview as interview_core
    import db.database as dbm
    from db.database import (
        DatabaseError,
        get_candidate_group,
        get_candidates,
        get_jobs,
        get_mappings,
        init_db,
    )
    from models.schemas import InterviewAnswer, InterviewQuestion
    from ui.theme import (
        STATUS_META,
        STATUS_RAIL,
        esc,
        group_pill,
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

logger = logging.getLogger("hireflow.interview_prep")

ROOT = Path(__file__).resolve().parent.parent
PAGES = {
    "candidates": "pages/2_Candidates.py",
    "report": "pages/4_Interview_Report.py",
}

STATUS_ORDER = ["MISSING", "UNCLEAR", "PARTIAL", "MET"]
DIFF_STYLE = {"EASY": ("#dcfce7", "#166534"), "MEDIUM": ("#fef3c7", "#92400e"), "HARD": ("#fee2e2", "#991b1b")}

# ---- core / db entry points (first name that exists wins; adjust here if yours differ) ----
GEN_QUESTION_FNS = (
    "generate_questions", "generate_interview_questions", "create_interview_questions",
    "generate_initial_questions", "build_questions",
)
FOLLOWUP_FNS = (
    "generate_followup", "generate_follow_up", "generate_followup_question",
    "generate_follow_up_question", "suggest_followup",
)
SAVE_QUESTION_FNS = (
    "save_interview_questions", "save_questions", "add_interview_questions",
    "insert_interview_questions", "save_interview_question", "add_interview_question",
)
SAVE_ANSWER_FNS = (
    "save_interview_answer", "add_interview_answer", "insert_interview_answer",
    "save_interview_answers", "save_answer",
)

EXTRA_CSS = """
.hf-ctx-k { font-size: 0.78rem; font-weight: 600; color: var(--hf-muted); }
.hf-ctx-job { font-size: 1.02rem; font-weight: 600; color: var(--hf-ink); margin-bottom: 0.4rem; }
.hf-focus-lead { font-size: 0.88rem; color: var(--hf-muted); margin: -0.2rem 0 0.4rem 0; }
.hf-focus-h { font-size: 0.9rem; font-weight: 600; color: var(--hf-ink-2); margin: 1.1rem 0 0.45rem 0; }
.hf-focus-row { background: var(--hf-surface); border: 1px solid var(--hf-line); border-left: 4px solid var(--hf-line);
  border-radius: 10px; padding: 0.7rem 0.95rem; margin-bottom: 0.45rem; }
.hf-focus-top { display: flex; align-items: center; gap: 0.6rem; flex-wrap: wrap; font-size: 0.93rem; color: var(--hf-ink); }
.hf-focus-top b { font-weight: 600; }
.hf-focus-r { font-size: 0.83rem; color: var(--hf-muted); margin-top: 0.3rem; line-height: 1.45; }
.hf-q { background: var(--hf-surface); border: 1px solid var(--hf-line); border-left: 4px solid var(--hf-line);
  border-radius: 12px; padding: 1rem 1.2rem; margin-bottom: 0.8rem; }
.hf-q-head { display: flex; justify-content: space-between; align-items: center; gap: 0.6rem; flex-wrap: wrap; }
.hf-q-n { font-size: 0.82rem; font-weight: 600; color: var(--hf-primary); }
.hf-q-t { font-size: 1.05rem; font-weight: 600; color: var(--hf-ink); margin: 0.35rem 0 0.2rem 0; line-height: 1.4; }
.hf-q-req { font-size: 0.84rem; color: var(--hf-muted); display: flex; gap: 0.5rem; align-items: center; flex-wrap: wrap; }
.hf-q-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 0.8rem 1.6rem;
  margin-top: 0.85rem; padding-top: 0.8rem; border-top: 1px solid var(--hf-line-2); }
.hf-q-l { font-size: 0.78rem; font-weight: 600; color: var(--hf-muted); margin-bottom: 0.15rem; }
.hf-q-v { font-size: 0.88rem; color: var(--hf-ink-2); line-height: 1.5; }
.hf-q-fu { margin: 0.7rem 0 0 0.2rem; padding-left: 0.9rem; border-left: 2px solid #fcd9a6; font-size: 0.86rem; color: var(--hf-ink-2); }
.hf-state { display: inline-block; border-radius: 99px; padding: 0.1rem 0.55rem; font-size: 0.74rem; font-weight: 600; }
.hf-state.done { background: #dcfce7; color: #166534; }
.hf-state.todo { background: var(--hf-line-2); color: var(--hf-muted); }
.hf-state.flag { background: #fef3c7; color: #92400e; }
.hf-big-q { font-size: 1.4rem; font-weight: 600; color: var(--hf-ink); line-height: 1.4; margin: 0.7rem 0 1rem 0; letter-spacing: -0.005em; }
.hf-exp { background: var(--hf-paper); border: 1px solid var(--hf-line); border-radius: 8px; padding: 0.6rem 0.8rem; }
.hf-followup { background: #fffbeb; border: 1px solid #fde68a; border-left: 4px solid #d97706; border-radius: 10px;
  padding: 0.9rem 1rem; margin: 0.8rem 0; color: #78350f; font-size: 0.95rem; }
.hf-side { background: var(--hf-surface); border: 1px solid var(--hf-line); border-radius: 12px; padding: 1rem 1.05rem; }
.hf-side-h { font-size: 0.86rem; font-weight: 600; color: var(--hf-ink); margin-bottom: 0.5rem; }
.hf-side-row { display: flex; gap: 0.55rem; align-items: flex-start; padding: 0.32rem 0; font-size: 0.85rem; color: var(--hf-muted); }
.hf-side-row .m { width: 1.1rem; flex-shrink: 0; text-align: center; font-weight: 700; }
.hf-side-row.done { color: var(--hf-ink-2); } .hf-side-row.done .m { color: #16a34a; }
.hf-side-row.now { color: var(--hf-ink); font-weight: 600; } .hf-side-row.now .m { color: var(--hf-primary); }
.hf-side-row.skip .m { color: var(--hf-faint); }
"""


# --------------------------------------------------------------------------
# Tolerant helpers (records may be pydantic models, dataclasses or dicts)
# --------------------------------------------------------------------------
class WiringError(RuntimeError):
    """A core/db function this page needs is missing or has an unexpected signature."""


_ALIASES = {
    "candidate": {"candidate", "profile", "candidate_profile", "cand"},
    "candidate_id": {"candidate_id", "cid"},
    "job": {"job", "job_description", "jd"},
    "job_id": {"job_id", "jid"},
    "requirements": {"requirements", "reqs"},
    "mappings": {"mappings", "mapping", "findings"},
    "questions": {"questions", "interview_questions"},
    "question": {"question", "parent_question", "parent", "base_question"},
    "answer": {"answer", "interview_answer"},
    "answers": {"answers", "interview_answers"},
    "notes": {"notes", "answer_notes", "interviewer_notes"},
    "n": {"n", "num_questions", "n_questions", "count", "max_questions", "limit"},
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
    """Call `fn` passing only the arguments it accepts, matched by name / alias."""
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


def _status(m: object) -> str:
    return _enum(_get(m, "status", default=""))


def _cand_id(c: object) -> str:
    return str(_get(c, "candidate_id", "id", default=""))


def _pill(text: str, bg: str, fg: str) -> str:
    return f'<span class="hf-pill" style="background:{bg};color:{fg}">{esc(text)}</span>'


def _status_pill(status: str) -> str:
    if status in STATUS_META:
        return _theme_status_pill(status)
    return _pill("Not screened", "#e2e8f0", "#334155")


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
# Data access (db/database.py only)
# --------------------------------------------------------------------------
def _db_list(fn_name: str, ctx: dict) -> list:
    fn = getattr(dbm, fn_name, None)
    if fn is None:
        raise WiringError(f"`db.database.{fn_name}` was not found.")
    return list(_smart_call(fn, ctx) or [])


def _load_questions(cid: str, job_id: object) -> list:
    qs = _db_list("get_interview_questions", {"candidate_id": cid, "job_id": job_id})
    return [q for q in qs if _get(q, "candidate_id") in (None, cid)]


def _load_answers(cid: str) -> list:
    return _db_list("get_interview_answers", {"candidate_id": cid})


def _load_mappings(cid: str, job) -> list:
    req_ids = {_get(r, "id") for r in _get(job, "requirements", default=[])}
    maps = _db_list("get_mappings", {"job_id": _get(job, "job_id", "id"), "latest_only": True})
    return [m for m in maps if _get(m, "candidate_id") == cid and _get(m, "requirement_id") in req_ids]


def _persist(fn_names: tuple[str, ...], items: list, singular: str, plural_key: str, base_ctx: dict) -> bool:
    """Save through whichever DB function exists. Handles list-style and single-item functions."""
    fn = _resolve(dbm, fn_names)
    if fn is None or not items:
        return False
    params = inspect.signature(fn).parameters
    takes_list = any(name in _ALIASES[plural_key] for name in params)
    if takes_list:
        _smart_call(fn, {**base_ctx, plural_key: items})
    else:
        for it in items:
            _smart_call(fn, {**base_ctx, singular: it})
    return True


# --------------------------------------------------------------------------
# Question helpers
# --------------------------------------------------------------------------
def _as_questions(res: object, cid: str) -> list:
    if isinstance(res, dict):
        res = res.get("questions", [])
    out = []
    for q in _as_list(res):
        if isinstance(q, str):
            q = InterviewQuestion(question=q)
        elif isinstance(q, dict):
            q = InterviewQuestion(**q)
        if not _get(q, "candidate_id"):
            try:
                q.candidate_id = cid
            except Exception:  # noqa: BLE001
                pass
        out.append(q)
    return out


def _build_queue(questions: list) -> list:
    """Base questions in order, each followed by its saved follow-ups."""
    base = [q for q in questions if not _get(q, "is_follow_up", default=False)]
    kids: dict = defaultdict(list)
    for q in questions:
        if _get(q, "is_follow_up", default=False):
            kids[_get(q, "parent_question_id")].append(q)
    queue = []
    for q in base:
        queue.append(q)
        queue.extend(kids.get(_get(q, "question_id"), []))
    return queue or questions


def _sort_reqs(reqs: list) -> list:
    return sorted(reqs, key=lambda r: 0 if _enum(_get(r, "priority", default="")) == "MUST_HAVE" else 1)


def _focus_buckets(reqs: list, maps: list) -> dict[str, list]:
    by_req = {_get(m, "requirement_id"): m for m in maps}
    buckets: dict[str, list] = {"high": [], "validate": [], "verify": [], "unscreened": []}
    for r in _sort_reqs(reqs):
        s = _status(by_req[_get(r, "id")]) if _get(r, "id") in by_req else ""
        key = {"MISSING": "high", "UNCLEAR": "high", "PARTIAL": "validate", "MET": "verify"}.get(s, "unscreened")
        buckets[key].append((r, s))
    return buckets


# --------------------------------------------------------------------------
# Prep-mode renderers
# --------------------------------------------------------------------------
def _render_context(c: object, group: str, job: object) -> None:
    skills = [str(s) for s in _as_list(_get(c, "skills", default=[]))]
    projects = [str(_get(p, "name", "title", default=p)) for p in _as_list(_get(c, "projects", default=[]))]
    years = _get(c, "total_experience_years", default="")
    meta = " · ".join(
        p for p in (
            str(_get(c, "location")) if _get(c, "location") else "",
            f"{years} years experience" if years else "",
        ) if p
    )
    job_meta = " · ".join(p for p in (_get(job, "company", default=""), _get(job, "location", default="")) if p)
    chips = "".join(f'<span class="hf-skill">{esc(s)}</span>' for s in skills[:12])
    proj = ""
    if projects:
        proj = f'<div class="hf-cand-meta">Projects: {esc(" · ".join(projects[:4]))}</div>'
    st.markdown(
        '<div class="hf-card"><div class="hf-cand-top"><div>'
        '<div class="hf-ctx-k">Candidate</div>'
        f'<div class="hf-cand-name">{esc(_get(c, "name", default="Unnamed candidate"))}</div>'
        f'<div class="hf-cand-meta">{esc(meta)}</div></div>'
        '<div style="text-align:right"><div class="hf-ctx-k">Interviewing for</div>'
        f'<div class="hf-ctx-job">{esc(_get(job, "title", default="Untitled job"))}</div>'
        f'{group_pill(group)}'
        + (f'<div class="hf-cand-meta">{esc(job_meta)}</div>' if job_meta else "")
        + f'</div></div><div class="hf-skills">{chips}</div>{proj}</div>',
        unsafe_allow_html=True,
    )


def _render_focus(reqs: list, maps: list) -> None:
    st.markdown('<div class="hf-section">What to validate</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="hf-focus-lead">Based on resume screening evidence. Requirements with missing, '
        "unclear or partial evidence come first.</div>",
        unsafe_allow_html=True,
    )
    counts = {s: sum(1 for m in maps if _status(m) == s) for s in STATUS_ORDER}
    st.markdown(status_counts_html(counts), unsafe_allow_html=True)

    titles = {
        "high": "Missing or unclear in the resume",
        "validate": "Partly evidenced",
        "verify": "Evidence found in the resume",
        "unscreened": "Not screened yet",
    }
    by_req = {_get(m, "requirement_id"): m for m in maps}
    buckets = _focus_buckets(reqs, maps)
    html_ = ""
    for key, items in buckets.items():
        if not items:
            continue
        html_ += f'<div class="hf-focus-h">{esc(titles[key])}</div>'
        for r, s in items:
            prio = _enum(_get(r, "priority", default="")).replace("_", " ").title()
            reason = _get(by_req.get(_get(r, "id")), "reason", default="") if by_req.get(_get(r, "id")) else ""
            rail = STATUS_RAIL.get(s, "#cbd5e1")
            html_ += (
                f'<div class="hf-focus-row" style="border-left-color:{rail}"><div class="hf-focus-top">'
                f'{_status_pill(s)}<b>{esc(_get(r, "title", default="Requirement"))}</b>'
                f'<span class="hf-muted">{esc(prio)}</span></div>'
                + (f'<div class="hf-focus-r">{esc(reason)}</div>' if reason else "")
                + "</div>"
            )
    st.markdown(html_, unsafe_allow_html=True)


def _state_chip(answer: object | None) -> str:
    if answer is None:
        return '<span class="hf-state todo">Not asked yet</span>'
    chip = '<span class="hf-state done">Notes saved</span>'
    if _get(answer, "follow_up_needed", default=False):
        chip += ' <span class="hf-state flag">Follow-up flagged</span>'
    return chip


def _render_question_card(i: int, q: object, req_titles: dict, req_status: dict, answers_by_q: dict, kids: dict) -> None:
    rid = _get(q, "requirement_id")
    rtitle = req_titles.get(rid, "")
    rstatus = req_status.get(rid, "")
    diff = _enum(_get(q, "difficulty", default=""))
    bg, fg = DIFF_STYLE.get(diff, ("#e2e8f0", "#334155"))
    qid = _get(q, "question_id")

    fields = ""
    for label, val in (
        ("Why this question", _get(q, "reason", default="")),
        ("What to validate", _get(q, "what_to_validate", default="")),
        ("Expected evidence", _get(q, "expected_evidence", default="")),
    ):
        if val:
            fields += f'<div><div class="hf-q-l">{esc(label)}</div><div class="hf-q-v">{esc(val)}</div></div>'
    grid = f'<div class="hf-q-grid">{fields}</div>' if fields else ""

    fu_html = ""
    for fu in kids.get(qid, []):
        fu_html += (
            f'<div class="hf-q-fu"><span class="hf-q-l">Follow-up</span><div class="hf-q-v">{esc(_get(fu, "question", default=""))}</div>'
            f'<div style="margin-top:0.25rem">{_state_chip(answers_by_q.get(_get(fu, "question_id")))}</div></div>'
        )

    req_line = ""
    if rtitle:
        req_line = f'<div class="hf-q-req"><span>Requirement: <b>{esc(rtitle)}</b></span>{_status_pill(rstatus) if rstatus else ""}</div>'

    st.markdown(
        f'<div class="hf-q" style="border-left-color:{STATUS_RAIL.get(rstatus, "#cbd5e1")}">'
        f'<div class="hf-q-head"><span class="hf-q-n">Question {i}</span>'
        f'<span>{_state_chip(answers_by_q.get(qid))} {_pill(diff.title(), bg, fg) if diff else ""}</span></div>'
        f'<div class="hf-q-t">{esc(_get(q, "question", default=""))}</div>{req_line}{grid}{fu_html}</div>',
        unsafe_allow_html=True,
    )


# --------------------------------------------------------------------------
# Actions
# --------------------------------------------------------------------------
def _generate(cand, job, maps, cid: str, job_id) -> None:
    gen = _resolve(interview_core, GEN_QUESTION_FNS)
    if gen is None:
        st.error(f"`core/interview.py` has none of: {', '.join(GEN_QUESTION_FNS)}.")
        return
    ctx = {
        "candidate": cand, "candidate_id": cid, "job": job, "job_id": job_id,
        "requirements": _get(job, "requirements", default=[]), "mappings": maps,
    }
    try:
        with st.spinner("Generating targeted interview questions..."):
            before = {_get(q, "question_id") for q in _load_questions(cid, job_id)}
            result = _smart_call(gen, ctx)
            generated = _as_questions(result, cid)
            after_db = _load_questions(cid, job_id)
            persisted = {_get(q, "question_id") for q in after_db} - before
            if not persisted and generated:  # generator did not persist: save through the DB layer
                _persist(SAVE_QUESTION_FNS, generated, "question", "questions", {"candidate_id": cid, "job_id": job_id})
                after_db = _load_questions(cid, job_id)
            if not after_db and generated:
                st.session_state["ip_unsaved_questions"] = generated  # last resort, shown but not stored
    except WiringError as exc:
        st.error(str(exc))
        return
    except DatabaseError:
        logger.exception("DB error while generating questions")
        st.error("⚠ Unable to load interview data.")
        return
    except Exception:  # noqa: BLE001
        logger.exception("Question generation failed")
        st.error("⚠ Unable to generate questions. The candidate profile is available, "
                 "but AI question generation is temporarily unavailable.")
        return
    st.rerun()


def _suggest_followup(cand, job, cid, job_id, q, draft_answer, notes) -> object | None:
    fn = _resolve(interview_core, FOLLOWUP_FNS)
    if fn is None:
        return None
    res = _smart_call(fn, {
        "candidate": cand, "candidate_id": cid, "job": job, "job_id": job_id,
        "question": q, "answer": draft_answer, "notes": notes,
        "requirements": _get(job, "requirements", default=[]),
    })
    if not res:
        return None
    if isinstance(res, InterviewAnswer):
        res = _get(res, "follow_up_question", default=None)
    if isinstance(res, dict):
        res = InterviewQuestion(**res) if "question" in res else None
    if isinstance(res, str):
        res = InterviewQuestion(question=res)
    if res is None:
        return None
    res.is_follow_up = True
    res.parent_question_id = _get(q, "question_id")
    res.candidate_id = _get(res, "candidate_id") or cid
    if not _get(res, "requirement_id"):
        res.requirement_id = _get(q, "requirement_id")
    return res


def _save_answer(cand, job, cid, job_id, q, notes: str, unanswered: str, flag: bool) -> bool:
    points = [ln.strip("-• \t") for ln in unanswered.splitlines() if ln.strip("-• \t")]
    answer = InterviewAnswer(
        question_id=_get(q, "question_id"),
        question=_get(q, "question", default=""),
        answer_notes=notes.strip(),
        requirement_id=_get(q, "requirement_id"),
        unanswered_points=points,
        follow_up_needed=flag,
    )
    followup = None
    try:
        followup = _suggest_followup(cand, job, cid, job_id, q, answer, notes.strip())
    except WiringError as exc:
        st.warning(f"Follow-up suggestion skipped: {exc}")
    except Exception:  # noqa: BLE001
        logger.exception("Follow-up generation failed")
        st.warning("Follow-up suggestion is unavailable right now. Your answer is still being saved.")
    if followup is not None:
        answer.follow_up_needed = True
        answer.follow_up_question = _get(followup, "question", default=None)
    try:
        ok = _persist(SAVE_ANSWER_FNS, [answer], "answer", "answers", {"candidate_id": cid, "job_id": job_id})
    except WiringError as exc:
        st.error(str(exc))
        return False
    except Exception:  # noqa: BLE001
        logger.exception("Saving answer failed")
        st.error("⚠ The answer could not be saved. Please try again.")
        return False
    if not ok:
        st.error(f"`db/database.py` has none of: {', '.join(SAVE_ANSWER_FNS)}.")
        return False
    st.session_state["ip_followup"] = {"qid": _get(q, "question_id"), "question": followup} if followup else None
    return True


def _accept_followup(cid, job_id) -> None:
    fu = st.session_state.get("ip_followup")
    if not fu:
        return
    q = fu["question"]
    try:
        _persist(SAVE_QUESTION_FNS, [q], "question", "questions", {"candidate_id": cid, "job_id": job_id})
    except Exception:  # noqa: BLE001
        logger.exception("Saving follow-up question failed")
        st.warning("The follow-up question could not be stored, but you can still ask it.")
    pos = st.session_state["ip_pos"]
    st.session_state["ip_queue"].insert(pos + 1, q)
    st.session_state["ip_pos"] = pos + 1
    st.session_state["ip_followup"] = None


# --------------------------------------------------------------------------
# Interview mode
# --------------------------------------------------------------------------
def _progress_rail(queue: list, pos: int, req_titles: dict, answered_ids: set) -> None:
    rows = ""
    for i, q in enumerate(queue):
        is_fu = _get(q, "is_follow_up", default=False)
        label = req_titles.get(_get(q, "requirement_id"), "") or str(_get(q, "question", default=""))[:42]
        if is_fu:
            label = f"Follow-up: {label}"
        if i == pos:
            cls, mark = "now", "→"
        elif _get(q, "question_id") in answered_ids:
            cls, mark = "done", "✓"
        elif i < pos:
            cls, mark = "skip", "–"
        else:
            cls, mark = "", "○"
        rows += f'<div class="hf-side-row {cls}"><span class="m">{mark}</span><span>{esc(label)}</span></div>'
    st.markdown(f'<div class="hf-side"><div class="hf-side-h">Interview progress</div>{rows}</div>', unsafe_allow_html=True)


def _render_interview(cand, job, cid, job_id, req_titles: dict, focus_reqs: list) -> None:
    queue: list = st.session_state["ip_queue"]
    pos: int = st.session_state["ip_pos"]
    name = _get(cand, "name", default="Candidate")
    req_status = {_get(r, "id"): s for r, s in focus_reqs}

    top_l, top_r = st.columns([3, 1])
    with top_l:
        st.markdown(
            f'<div class="hf-card-title">Interview · {esc(name)}</div>'
            f'<div class="hf-card-sub">{esc(_get(job, "title", default=""))}</div>',
            unsafe_allow_html=True,
        )
    with top_r:
        if st.button("← Back to preparation", key="ip_back", use_container_width=True):
            st.session_state["ip_mode"] = "prep"
            st.session_state["ip_followup"] = None
            st.rerun()

    if pos >= len(queue):
        _render_done(cid, queue, focus_reqs, req_titles)
        return

    q = queue[pos]
    qid = _get(q, "question_id", default=f"pos{pos}")
    st.progress(pos / max(len(queue), 1), text=f"Question {pos + 1} of {len(queue)}")

    try:
        answered_ids = {_get(a, "question_id") for a in _load_answers(cid)}
    except Exception:  # noqa: BLE001
        answered_ids = set()

    main_col, side_col = st.columns([3, 1.15], gap="large")
    with side_col:
        _progress_rail(queue, pos, req_titles, answered_ids)

    with main_col:
        pending = st.session_state.get("ip_followup")
        if pending:
            fq = pending["question"]
            st.markdown(
                '<div class="hf-followup"><b>Follow-up suggested</b><br>'
                f'{esc(_get(fq, "question", default=""))}</div>',
                unsafe_allow_html=True,
            )
            c1, c2 = st.columns(2)
            with c1:
                if st.button("Ask this follow-up now", type="primary", use_container_width=True, key="fu_yes"):
                    _accept_followup(cid, job_id)
                    st.rerun()
            with c2:
                if st.button("Skip and continue", use_container_width=True, key="fu_no"):
                    st.session_state["ip_followup"] = None
                    st.session_state["ip_pos"] = pos + 1
                    st.rerun()
            return

        tag = "Follow-up" if _get(q, "is_follow_up", default=False) else "Question"
        rid = _get(q, "requirement_id")
        rtitle = req_titles.get(rid, "")
        rstatus = req_status.get(rid, "")
        head = f'<span class="hf-q-n">{tag}</span>'
        if rtitle:
            head += f' <span class="hf-q-req">Requirement: <b>{esc(rtitle)}</b> {_status_pill(rstatus) if rstatus else ""}</span>'
        exp = _get(q, "expected_evidence")
        st.markdown(
            f'<div class="hf-card" style="border-left:4px solid {STATUS_RAIL.get(rstatus, "#cbd5e1")}">{head}'
            f'<div class="hf-big-q">{esc(_get(q, "question", default=""))}</div>'
            + (f'<div class="hf-exp"><div class="hf-q-l">Expected evidence</div><div class="hf-q-v">{esc(exp)}</div></div>' if exp else "")
            + "</div>",
            unsafe_allow_html=True,
        )
        why, validate = _get(q, "reason", default=""), _get(q, "what_to_validate", default="")
        if why or validate:
            with st.expander("Why this question"):
                if why:
                    st.markdown(f'<div class="hf-q-l">Why this question</div><div class="hf-q-v">{esc(why)}</div>', unsafe_allow_html=True)
                if validate:
                    st.markdown(f'<div class="hf-q-l" style="margin-top:0.6rem">What to validate</div><div class="hf-q-v">{esc(validate)}</div>', unsafe_allow_html=True)

        st.text_input("Interviewer (optional)", key="ip_interviewer", placeholder="Your name")
        notes = st.text_area("Interviewer notes", key=f"ip_notes_{qid}", height=160,
                             placeholder="Type what the candidate said. Notes are saved as written; nothing is scored here.")
        unanswered = st.text_area("Points not covered (optional, one per line)", key=f"ip_unans_{qid}", height=80)
        flag = st.checkbox("Flag for follow-up", key=f"ip_flag_{qid}")

        b1, b2 = st.columns([2, 1])
        with b1:
            if st.button("Save answer & continue", type="primary", use_container_width=True, key=f"ip_save_{qid}"):
                if not notes.strip():
                    st.warning("Add some notes first, or skip this question.")
                elif _save_answer(cand, job, cid, job_id, q, notes, unanswered, flag):
                    if not st.session_state.get("ip_followup"):
                        st.session_state["ip_pos"] = pos + 1
                    st.rerun()
        with b2:
            if st.button("Skip question", use_container_width=True, key=f"ip_skip_{qid}"):
                st.session_state["ip_pos"] = pos + 1
                st.rerun()


def _render_done(cid, queue: list, focus_reqs: list, req_titles: dict) -> None:
    try:
        answers = _load_answers(cid)
    except Exception:  # noqa: BLE001
        logger.exception("Could not load answers")
        answers = []
    discussed = {_get(a, "requirement_id") for a in answers if _get(a, "requirement_id")}
    follow_ups = sum(1 for q in queue if _get(q, "is_follow_up", default=False))
    open_reqs = [_get(r, "title", default="") for r, _s in focus_reqs if _get(r, "id") not in discussed]
    st.markdown('<div class="hf-banner ok">✓ Interview completed</div>', unsafe_allow_html=True)
    kpis = [
        ("Questions", len(queue) - follow_ups),
        ("Follow-ups", follow_ups),
        ("Requirements discussed", len(discussed)),
        ("Areas not covered", len(open_reqs)),
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
    if open_reqs:
        st.markdown(
            '<div class="hf-muted">No answers recorded yet for: ' + esc(", ".join(open_reqs)) + "</div>",
            unsafe_allow_html=True,
        )
    st.write("")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("Generate interview report", type="primary", use_container_width=True, key="go_report"):
            _go("report")
    with c2:
        if st.button("Review questions", use_container_width=True, key="review_q"):
            st.session_state["ip_mode"] = "prep"
            st.rerun()


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main() -> None:
    inject_css()
    st.markdown(f"<style>{EXTRA_CSS}</style>", unsafe_allow_html=True)
    for k, v in {
        "selected_candidate_id": None, "selected_job_id": None, "ip_mode": "prep", "ip_queue": [],
        "ip_pos": 0, "ip_followup": None, "ip_sig": None, "ip_unsaved_questions": [],
    }.items():
        st.session_state.setdefault(k, v)

    db_ok = True
    try:
        init_db()
    except Exception as exc:  # noqa: BLE001
        db_ok, db_error = False, str(exc)
    render_sidebar(db_ok)

    in_session = st.session_state["ip_mode"] in ("interview", "done") and bool(st.session_state["ip_queue"])
    page_header(
        "Interview" if in_session else "Interview preparation",
        "Notes are recorded as written. Nothing is scored here; evaluation happens in the report."
        if in_session
        else "See what the resume leaves open, then ask questions built from those gaps. Notes are recorded, not scored.",
        eyebrow="Interview",
    )
    if not db_ok:
        logger.error("DB unavailable: %s", db_error)
        st.error("⚠ Unable to load interview data. Please check the database connection.")
        if st.button("Retry", key="retry_db"):
            st.rerun()
        return

    try:
        with st.spinner("Loading interview workspace..."):
            candidates = get_candidates()
            jobs = get_jobs()
    except (DatabaseError, Exception):  # noqa: BLE001
        logger.exception("Failed to load candidates/jobs")
        st.error("⚠ Unable to load interview data.")
        if st.button("Retry", key="retry_load"):
            st.rerun()
        return

    if not jobs:
        st.info("No job descriptions available. Upload a job description first.")
        nav_button("Upload job & resumes", "upload", primary=True, key_suffix="nojob")
        return
    if not candidates:
        st.info("No candidates available yet. Process some resumes first.")
        nav_button("Upload job & resumes", "upload", primary=True, key_suffix="nocand")
        return

    # ---- selectors (index comes from session so links from other pages preselect) ----
    cand_ids = [_cand_id(c) for c in candidates]
    sel_c = st.session_state["selected_candidate_id"]
    c_idx = cand_ids.index(sel_c) if sel_c in cand_ids else None
    job_ids = [_get(j, "job_id", "id") for j in jobs]

    s1, s2 = st.columns(2, gap="medium")
    with s1:
        c_pick = st.selectbox(
            "Candidate", range(len(candidates)), index=c_idx,
            format_func=lambda i: str(_get(candidates[i], "name", default="Unnamed candidate")),
            placeholder="Select a candidate", key=f"ip_cand_pick_{sel_c}",
        )
    if c_pick is None:
        st.info("Select a candidate to prepare an interview.")
        return
    cand = candidates[c_pick]
    cid = _cand_id(cand)
    st.session_state["selected_candidate_id"] = cid

    sel_j = st.session_state["selected_job_id"]
    if sel_j not in job_ids:
        # default: the job this candidate has the most screening evidence for
        try:
            all_maps = [m for m in _db_list("get_mappings", {"latest_only": True}) if _get(m, "candidate_id") == cid]
        except Exception:  # noqa: BLE001
            all_maps = []
        req_to_job = {_get(r, "id"): _get(j, "job_id", "id") for j in jobs for r in _get(j, "requirements", default=[])}
        tally: dict = defaultdict(int)
        for m in all_maps:
            tally[req_to_job.get(_get(m, "requirement_id"))] += 1
        sel_j = max((j for j in job_ids), key=lambda j: tally.get(j, 0))
    with s2:
        j_pick = st.selectbox(
            "Job", range(len(jobs)), index=job_ids.index(sel_j),
            format_func=lambda i: str(_get(jobs[i], "title", default="Untitled job")),
            key=f"ip_job_pick_{cid}_{sel_j}",
        )
    job = jobs[j_pick]
    job_id = _get(job, "job_id", "id")
    st.session_state["selected_job_id"] = job_id

    sig = (cid, job_id)
    if st.session_state["ip_sig"] != sig:
        st.session_state.update(ip_sig=sig, ip_mode="prep", ip_queue=[], ip_pos=0, ip_followup=None, ip_unsaved_questions=[])

    reqs = _get(job, "requirements", default=[])
    req_titles = {_get(r, "id"): str(_get(r, "title", default="Requirement")) for r in reqs}

    try:
        maps = _load_mappings(cid, job)
        questions = _load_questions(cid, job_id)
        try:
            group = _enum(get_candidate_group(cid)) or "UNGROUPED"
        except Exception:  # noqa: BLE001
            group = "UNGROUPED"
    except WiringError as exc:
        st.error(str(exc))
        return
    except (DatabaseError, Exception):  # noqa: BLE001
        logger.exception("Failed to load interview data")
        st.error("⚠ Unable to load interview data.")
        return
    if not questions:
        questions = list(st.session_state.get("ip_unsaved_questions") or [])

    focus_reqs = [item for bucket in _focus_buckets(reqs, maps).values() for item in bucket]

    # ---- interview mode ----
    if st.session_state["ip_mode"] in ("interview", "done") and st.session_state["ip_queue"]:
        _render_interview(cand, job, cid, job_id, req_titles, focus_reqs)
        return

    # ---- preparation mode ----
    _render_context(cand, group, job)
    if not maps:
        st.warning(
            "No screening evidence is available for this candidate and job yet. "
            "Process the candidate before generating targeted interview questions."
        )
        c1, c2 = st.columns(2)
        with c1:
            nav_button("Upload job & resumes", "upload", key_suffix="nomap")
        with c2:
            if st.button("← Back to candidates", key="back_cands", use_container_width=True):
                _go("candidates")
        return
    _render_focus(reqs, maps)

    st.write("")
    if not questions:
        _, mid, _ = st.columns([1, 1.4, 1])
        with mid:
            if st.button("Generate interview questions", type="primary", use_container_width=True, key="gen_q"):
                _generate(cand, job, maps, cid, job_id)
        st.markdown(
            '<div class="hf-muted" style="text-align:center;margin-top:0.5rem">'
            "Questions are built from this candidate's missing, unclear and partial evidence for the selected job.</div>",
            unsafe_allow_html=True,
        )
        return

    # ---- questions, with progress + follow-up state from saved answers ----
    try:
        answers = _load_answers(cid)
    except Exception:  # noqa: BLE001
        logger.exception("Could not load answers")
        answers = []
    answers_by_q = {_get(a, "question_id"): a for a in answers if _get(a, "question_id")}
    kids: dict = defaultdict(list)
    for q in questions:
        if _get(q, "is_follow_up", default=False):
            kids[_get(q, "parent_question_id")].append(q)
    base = [q for q in questions if not _get(q, "is_follow_up", default=False)]
    n_saved = sum(1 for q in questions if _get(q, "question_id") in answers_by_q)
    req_status = {_get(r, "id"): s for r, s in focus_reqs}

    st.markdown(f'<div class="hf-section">Questions ({len(base)})</div>', unsafe_allow_html=True)
    progress = (
        f"Notes saved for {n_saved} of {plural(len(questions), 'question')}."
        if n_saved
        else "No answers recorded yet."
    )
    st.markdown(f'<div class="hf-focus-lead">{esc(progress)}</div>', unsafe_allow_html=True)
    if st.session_state.get("ip_unsaved_questions") and not _load_questions(cid, job_id):
        st.warning("These questions could not be stored in the database, so they will be lost when you leave this page.")
    for i, q in enumerate(base, start=1):
        _render_question_card(i, q, req_titles, req_status, answers_by_q, kids)

    b1, b2 = st.columns([2, 1])
    with b1:
        if st.button("Start interview", type="primary", use_container_width=True, key="start_iv"):
            queue = _build_queue(questions)
            answered = {_get(a, "question_id") for a in answers}
            pos = next((i for i, q in enumerate(queue) if _get(q, "question_id") not in answered), len(queue))
            st.session_state.update(ip_queue=queue, ip_pos=pos, ip_mode="interview", ip_followup=None)
            st.rerun()
    with b2:
        if st.button("Regenerate", use_container_width=True, key="regen_q",
                     help="Creates a new set of questions. Existing saved questions are kept."):
            _generate(cand, job, maps, cid, job_id)

    st.markdown(
        '<div class="hf-footer">Interview notes are recorded, not scored. Hiring decisions stay with people.</div>',
        unsafe_allow_html=True,
    )


main()