"""
HireFlow - app.py  (Home / Dashboard)
=====================================

Role of this file: dashboard shell + navigation ONLY.

    app.py  ->  db/database.py  ->  SQLite

Deliberately NOT here: PDF parsing, LLM calls, mapping/grouping logic,
interview evaluation, raw SQL. Those live in core/*, llm/*, db/*.

Every number on this page comes from the database (no hard-coded demo data).
A failing section degrades gracefully instead of blanking the whole page.

Styling lives in ui/theme.py, shared with every page under pages/. This file
only adds the handful of dashboard-only visuals theme.py doesn't already
provide (the group-distribution donut, the activity feed and the attention
list, the job cards) — built with the same colour tokens as everything else.

Run:  streamlit run app.py
"""

from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import streamlit as st

# set_page_config must be the first Streamlit call in the script.
st.set_page_config(
    page_title="HireFlow",
    page_icon="💼",
    layout="wide",
    initial_sidebar_state="expanded",
)

# --------------------------------------------------------------------------
# Imports from the project (fail with a readable message, not a blank page)
# --------------------------------------------------------------------------
try:
    from db.database import (
        DatabaseError,
        get_audit_records,
        get_candidate_group,
        get_candidates,
        get_interview_answers,
        get_interview_questions,
        get_interview_reports_for_candidate,
        get_jobs,
        get_mappings,
        init_db,
    )
    from models.schemas import MappingStatus, RequirementPriority
    from ui.theme import (
        ACTION_LABELS,
        APP_VERSION,
        GROUP_META,
        collapse,
        esc,
        inject_css,
        nav_button,
        plural,
        render_sidebar,
        steps_html,
        time_ago,
    )
except ImportError as exc:  # pragma: no cover - environment problem
    st.error(
        f"Project modules could not be imported: `{exc}`.\n\n"
        "Run the app from the project root (`streamlit run app.py`) and make sure "
        "`db/database.py`, `models/schemas.py` and `ui/theme.py` exist."
    )
    st.stop()

try:  # optional: load API keys from .env for the "AI key" status chip
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # noqa: BLE001
    pass

ROOT = Path(__file__).resolve().parent

# Colour for each audit action's activity dot. Labels themselves come from
# ui.theme.ACTION_LABELS so every page describes the same action the same way.
ACTION_DOT = {
    "EXTRACT_JD": "#8A641C",
    "EXTRACT_RESUME": "#2B6B3F",
    "MAP_REQUIREMENT": "#EA8624",
    "GROUP_CANDIDATE": "#2B4217",
    "SUMMARIZE_CANDIDATE": "#B88716",
    "GENERATE_QUESTION": "#C86B13",
    "GENERATE_FOLLOWUP": "#9A5415",
    "MAP_INTERVIEW_NOTES": "#6F684F",
    "GENERATE_REPORT": "#2B6B3F",
    "POOL_QUERY": "#9A927A",
}

# Dashboard-only visuals: the group-distribution donut, activity feed,
# attention list and job cards. Everything else on this page (hero, KPI strip,
# cards, welcome steps, buttons) reuses classes already defined in ui/theme.py.
DASH_CSS = """
.hf-dist { display: flex; align-items: center; gap: 1.6rem; flex-wrap: wrap; }
.hf-donut { width: 140px; height: 140px; border-radius: 50%; position: relative; flex-shrink: 0; }
.hf-donut-hole { position: absolute; inset: 20px; background: var(--hf-surface); border-radius: 50%;
  display: flex; flex-direction: column; align-items: center; justify-content: center; }
.hf-donut-hole b { font-size: 1.55rem; color: var(--hf-ink); line-height: 1; font-variant-numeric: tabular-nums; }
.hf-donut-hole span { font-size: 0.68rem; color: var(--hf-muted); }
.hf-legend { flex: 1; min-width: 200px; }
.hf-lg-row { margin-bottom: 0.7rem; }
.hf-lg-top { display: flex; justify-content: space-between; font-size: 0.85rem; color: var(--hf-ink-2); font-weight: 600; }
.hf-lg-bar { height: 6px; background: var(--hf-line-2); border-radius: 99px; margin-top: 0.3rem; overflow: hidden; }
.hf-lg-bar > div { height: 100%; border-radius: 99px; }

.hf-act { display: flex; gap: 0.7rem; padding: 0.55rem 0; border-bottom: 1px solid var(--hf-line-2); }
.hf-act:last-child { border-bottom: none; }
.hf-act-dot { width: 9px; height: 9px; border-radius: 50%; margin-top: 0.42rem; flex-shrink: 0; }
.hf-act-main { font-size: 0.88rem; font-weight: 600; color: var(--hf-ink); }
.hf-act-meta { font-size: 0.78rem; color: var(--hf-muted); }
.hf-badge { display: inline-block; background: var(--hf-primary-soft); color: var(--hf-primary-dark); border-radius: 99px;
  font-size: 0.7rem; padding: 0.05rem 0.5rem; margin-left: 0.4rem; font-weight: 700; }

.hf-attn { display: flex; align-items: center; gap: 0.8rem; padding: 0.7rem 0.85rem; border-radius: 10px;
  margin-bottom: 0.55rem; font-size: 0.88rem; color: var(--hf-ink); border: 1px solid transparent; }
.hf-attn .n { font-weight: 700; font-size: 1.05rem; min-width: 1.6rem; text-align: center; }
.hf-attn.amber { background: #FFF3D2; border-color: #E8C76A; }
.hf-attn.amber .n { color: #7A5A13; }
.hf-attn.red { background: #FBECEA; border-color: #E3B2AA; }
.hf-attn.red .n { color: #8A3028; }
.hf-attn.blue { background: #EDF6E8; border-color: #B9D5A8; }
.hf-attn.blue .n { color: #2B6B3F; }

.hf-jobs { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 1rem; }
.hf-job { background: var(--hf-surface); border: 1px solid var(--hf-line); border-radius: 12px; padding: 1.15rem 1.25rem;
  box-shadow: 0 1px 2px rgba(25,48,15,0.04); transition: box-shadow 0.15s ease; }
.hf-job:hover { box-shadow: 0 4px 14px rgba(25,48,15,0.07); }
.hf-job-t { font-size: 1.0rem; font-weight: 700; color: var(--hf-ink); }
.hf-job-m { font-size: 0.8rem; color: var(--hf-muted); margin-bottom: 0.85rem; }
.hf-job-stats { display: flex; gap: 1.2rem; margin: 0.85rem 0 0.1rem 0; }
.hf-job-stats div b { display: block; font-size: 1.15rem; color: var(--hf-ink); }
.hf-job-stats div span { font-size: 0.7rem; color: var(--hf-muted); }
.hf-prog-l { display: flex; justify-content: space-between; font-size: 0.78rem; color: var(--hf-ink-2); font-weight: 600; }

/* Candidate distribution / Recent activity sit side by side and rarely have
   equal amounts of content. Stretch both cards to the row's tallest one, and
   let Recent activity's own row list scroll internally so it never pushes the
   card (and the whitespace below its shorter neighbour) taller than it needs
   to be. */
[data-testid="stHorizontalBlock"] > [data-testid="column"] { display: flex; }
[data-testid="stHorizontalBlock"] > [data-testid="column"] > div { display: flex; flex-direction: column; width: 100%; }
[data-testid="stHorizontalBlock"] [data-testid="stMarkdown"],
[data-testid="stHorizontalBlock"] [data-testid="stMarkdownContainer"] { display: flex; flex: 1 1 auto; width: 100%; }
[data-testid="stHorizontalBlock"] .hf-card {
  flex: 1 1 auto; display: flex; flex-direction: column; height: 100%; box-sizing: border-box;
}
[data-testid="stHorizontalBlock"] .hf-card .hf-dist { flex: 1 1 auto; }
[data-testid="stHorizontalBlock"] .hf-card:has(.hf-dist) { justify-content: center; }

.hf-act-list { flex: 1 1 auto; min-height: 0; max-height: 360px; overflow-y: auto; margin-top: 0.3rem; padding-right: 0.35rem; }
.hf-act-list::-webkit-scrollbar { width: 6px; }
.hf-act-list::-webkit-scrollbar-track { background: transparent; }
.hf-act-list::-webkit-scrollbar-thumb { background: #D8CBAF; border-radius: 99px; }
.hf-act-list::-webkit-scrollbar-thumb:hover { background: var(--hf-primary); }
.hf-act-list { scrollbar-width: thin; scrollbar-color: #D8CBAF transparent; }
"""


def inject_dash_css() -> None:
    st.markdown(f"<style>{collapse(DASH_CSS)}</style>", unsafe_allow_html=True)


def _plural(n: int, singular: str, plural_form: str | None = None) -> str:
    return plural(n, singular, plural_form)


# --------------------------------------------------------------------------
# Data snapshot (all numbers come from db/database.py)
# --------------------------------------------------------------------------
@dataclass
class Snapshot:
    n_candidates: int = 0
    n_jobs: int = 0
    n_interviewed: int = 0
    n_prepared: int = 0
    n_reports: int = 0
    groups: dict = field(default_factory=lambda: {k: 0 for k in GROUP_META})
    activity: list = field(default_factory=list)
    attention: list = field(default_factory=list)  # (tone, count, text)
    jobs: list = field(default_factory=list)
    names: dict = field(default_factory=dict)


def _build_activity(records: list[dict], limit: int = 8) -> list[dict]:
    """Newest first; consecutive identical (action, candidate) rows collapse into one 'xN' row."""
    items: list[dict] = []
    for rec in reversed(records):
        key = (str(rec.get("action") or ""), rec.get("candidate_id"))
        if items and items[-1]["key"] == key:
            items[-1]["count"] += 1
            continue
        if len(items) >= limit:
            break
        items.append(
            {
                "key": key,
                "action": key[0],
                "cid": key[1],
                "count": 1,
                "ts": rec.get("timestamp"),
                "source": rec.get("source"),
                "entity": rec.get("entity_type"),
            }
        )
    return items


def load_snapshot() -> tuple[Snapshot, list[str]]:
    """Returns (snapshot, warnings). Raises only if the core tables can't be read at all."""
    warnings: list[str] = []
    snap = Snapshot()

    candidates = get_candidates()
    jobs = get_jobs()
    snap.n_candidates = len(candidates)
    snap.n_jobs = len(jobs)
    snap.names = {c.candidate_id: c.name for c in candidates}

    must_ids = {
        r.id for j in jobs for r in j.requirements if r.priority == RequirementPriority.MUST_HAVE
    }

    # ---- latest mapping per (candidate, requirement) ----
    try:
        mappings = get_mappings(latest_only=True)
    except Exception:  # noqa: BLE001
        mappings = []
        warnings.append("Requirement mappings could not be loaded.")

    unclear_by_cand: Counter = Counter()
    missing_must_by_cand: Counter = Counter()
    for m in mappings:
        if m.status == MappingStatus.UNCLEAR:
            unclear_by_cand[m.candidate_id] += 1
        elif m.status == MappingStatus.MISSING and m.requirement_id in must_ids:
            missing_must_by_cand[m.candidate_id] += 1

    # ---- per-candidate: group, interview data, reports ----
    followups_pending = 0
    reports_with_gaps = 0
    reports_by_job: Counter = Counter()
    problems = 0

    for c in candidates:
        cid = c.candidate_id
        try:
            grp = get_candidate_group(cid)
            snap.groups[grp.value if grp and grp.value in snap.groups else "UNGROUPED"] += 1
        except Exception:  # noqa: BLE001
            snap.groups["UNGROUPED"] += 1
            problems += 1
        try:
            if get_interview_questions(candidate_id=cid):
                snap.n_prepared += 1
        except Exception:  # noqa: BLE001
            problems += 1
        try:
            answers = get_interview_answers(candidate_id=cid)
            if answers:
                snap.n_interviewed += 1
            followups_pending += sum(1 for a in answers if a.follow_up_needed)
        except Exception:  # noqa: BLE001
            problems += 1
        try:
            reports = get_interview_reports_for_candidate(cid)
            snap.n_reports += len(reports)
            for r in reports:
                reports_by_job[r.job_id] += 1
                if r.unanswered_areas:
                    reports_with_gaps += 1
        except Exception:  # noqa: BLE001
            problems += 1

    if problems:
        warnings.append("Some interview/report details could not be loaded.")

    # ---- needs-attention (validation pointers, never hire/reject advice) ----
    if unclear_by_cand:
        n = len(unclear_by_cand)
        snap.attention.append(
            (
                "amber",
                n,
                f"{_plural(n, 'candidate')} with unclear requirements "
                f"({_plural(sum(unclear_by_cand.values()), 'requirement')} need evidence)",
            )
        )
    if missing_must_by_cand:
        n = len(missing_must_by_cand)
        snap.attention.append(
            ("red", n, f"{_plural(n, 'candidate')} missing at least one must-have requirement")
        )
    if followups_pending:
        snap.attention.append(
            ("blue", followups_pending, f"{_plural(followups_pending, 'follow-up question')} still to ask")
        )
    if reports_with_gaps:
        snap.attention.append(
            ("amber", reports_with_gaps, f"{_plural(reports_with_gaps, 'interview report')} with unanswered areas")
        )
    ungrouped = snap.groups.get("UNGROUPED", 0)
    if ungrouped and snap.n_candidates:
        snap.attention.append(("blue", ungrouped, f"{_plural(ungrouped, 'candidate')} not screened yet"))

    # ---- per-job cards ----
    for j in jobs:
        card = {
            "title": j.title,
            "company": j.company,
            "location": j.location,
            "n_req": len(j.requirements),
            "n_must": len(j.must_haves),
            "screened": 0,
            "met": 0,
            "pairs": 0,
            "prepared": 0,
            "reports": reports_by_job.get(j.job_id, 0),
        }
        try:
            jm = get_mappings(job_id=j.job_id, latest_only=True)
            card["screened"] = len({m.candidate_id for m in jm})
            card["pairs"] = len(jm)
            card["met"] = sum(1 for m in jm if m.status == MappingStatus.MET)
        except Exception:  # noqa: BLE001
            warnings.append(f"Mappings for '{j.title}' could not be loaded.")
        try:
            qs = get_interview_questions(job_id=j.job_id)
            card["prepared"] = len({q.candidate_id for q in qs if q.candidate_id})
        except Exception:  # noqa: BLE001
            pass
        snap.jobs.append(card)

    # ---- recent activity (from the audit trail) ----
    try:
        snap.activity = _build_activity(get_audit_records())
    except Exception:  # noqa: BLE001
        warnings.append("Audit trail could not be loaded.")

    return snap, warnings


# --------------------------------------------------------------------------
# Renderers
# --------------------------------------------------------------------------
def render_hero(db_ok: bool, ai_ok: bool) -> None:
    chips = (
        f'<span class="hf-chip"><span class="hf-dot" style="background:{"#2B6B3F" if db_ok else "#9B3D32"}"></span>'
        f'Database {"connected" if db_ok else "unavailable"}</span>'
        f'<span class="hf-chip"><span class="hf-dot" style="background:{"#2B6B3F" if ai_ok else "#B88716"}"></span>'
        f'AI key {"configured" if ai_ok else "not found"}</span>'
    )
    st.markdown(
        collapse(
            f"""
            <div class="hf-hero">
              <div class="hf-eyebrow">HireFlow</div>
              <div class="hf-title">Dashboard</div>
              <div class="hf-sub">Your candidate pipeline at a glance: evidence-backed screening,
              interview preparation and audit trail in one place.</div>
              <div class="hf-chips" style="margin-top:0.7rem">{chips}</div>
            </div>
            """
        ),
        unsafe_allow_html=True,
    )


def render_kpis(s: Snapshot) -> None:
    kpis = [
        ("Candidates", s.n_candidates, "in the talent pool"),
        ("Jobs", s.n_jobs, "job descriptions"),
        ("Interviews", s.n_interviewed, f"{s.n_prepared} with prepared questions"),
        ("Reports", s.n_reports, "evaluation reports"),
    ]
    body = "".join(
        f'<div class="hf-kpi"><div class="hf-kpi-val">{val}</div><div class="hf-kpi-lbl">{esc(lbl)}</div>'
        f'<div class="hf-kpi-sub">{esc(sub)}</div></div>'
        for lbl, val, sub in kpis
    )
    st.markdown(f'<div class="hf-kpis">{body}</div>', unsafe_allow_html=True)


def render_distribution(s: Snapshot) -> None:
    total = sum(s.groups.values())
    if total == 0:
        inner = '<div class="hf-empty">No candidates yet. Upload resumes to see how they group.</div>'
    else:
        stops, acc = [], 0.0
        for key in GROUP_META:
            n = s.groups.get(key, 0)
            if n:
                pct = n / total * 100
                stops.append(f"{GROUP_META[key][1]} {acc:.2f}% {acc + pct:.2f}%")
                acc += pct
        rows = ""
        for key, (label, color) in GROUP_META.items():
            n = s.groups.get(key, 0)
            if key == "UNGROUPED" and n == 0:
                continue
            pct = n / total * 100
            rows += (
                f'<div class="hf-lg-row"><div class="hf-lg-top"><span>{esc(label)}</span>'
                f'<span>{n}</span></div><div class="hf-lg-bar">'
                f'<div style="width:{pct:.1f}%;background:{color}"></div></div></div>'
            )
        inner = (
            f'<div class="hf-dist"><div class="hf-donut" style="background:conic-gradient({", ".join(stops)})">'
            f'<div class="hf-donut-hole"><b>{total}</b><span>candidates</span></div></div>'
            f'<div class="hf-legend">{rows}</div></div>'
        )
    st.markdown(
        collapse(
            f"""
            <div class="hf-card">
              <div class="hf-card-title">Candidate distribution</div>
              <div class="hf-card-sub">Groups are assigned by the screening rules, not by this page.</div>
              {inner}
            </div>
            """
        ),
        unsafe_allow_html=True,
    )


def render_activity(s: Snapshot) -> None:
    if not s.activity:
        rows_html = '<div class="hf-empty">No activity recorded yet.</div>'
    else:
        rows_html = ""
        for a in s.activity:
            label = ACTION_LABELS.get(a["action"], a["action"].replace("_", " ").capitalize() or "Activity")
            color = ACTION_DOT.get(a["action"], "#9A927A")
            who = s.names.get(a["cid"]) if a["cid"] else None
            meta_parts = [p for p in (who or a.get("entity"), time_ago(a["ts"])) if p]
            badge = f'<span class="hf-badge">x{a["count"]}</span>' if a["count"] > 1 else ""
            rows_html += (
                f'<div class="hf-act"><span class="hf-act-dot" style="background:{color}"></span>'
                f'<div><div class="hf-act-main">{esc(label)}{badge}</div>'
                f'<div class="hf-act-meta">{esc(" · ".join(meta_parts))}</div></div></div>'
            )
    inner = f'<div class="hf-act-list">{rows_html}</div>'
    st.markdown(
        collapse(
            f"""
            <div class="hf-card">
              <div class="hf-card-title">Recent activity</div>
              <div class="hf-card-sub">Live from the audit trail.</div>
              {inner}
            </div>
            """
        ),
        unsafe_allow_html=True,
    )


def render_attention(s: Snapshot) -> None:
    if s.attention:
        inner = "".join(
            f'<div class="hf-attn {tone}"><span class="n">{n}</span><span>{esc(text)}</span></div>'
            for tone, n, text in s.attention
        )
    else:
        inner = '<div class="hf-clear">✓ All clear. No validation flags right now.</div>'
    st.markdown(
        collapse(
            f"""
            <div class="hf-card">
              <div class="hf-card-title">Needs attention</div>
              <div class="hf-card-sub">Where a human should validate. These are pointers, not hiring recommendations.</div>
              {inner}
            </div>
            """
        ),
        unsafe_allow_html=True,
    )


def render_quick_actions() -> None:
    with st.container(border=True, key="hf_quick"):
        st.markdown(
            collapse(
                """
                <div class="hf-card-title">Quick actions</div>
                <div class="hf-card-sub">Jump straight into a workflow.</div>
                """
            ),
            unsafe_allow_html=True,
        )
        nav_button("Upload job & resumes", "upload", primary=True, key_suffix="dash")
        nav_button("View candidates", "candidates", key_suffix="dash")
        nav_button("Prepare interview", "interview", key_suffix="dash")
        nav_button("Interview reports", "report", key_suffix="dash")
        nav_button("Ask candidate pool", "pool", key_suffix="dash")


def render_jobs(s: Snapshot) -> None:
    st.markdown('<div class="hf-section">Jobs</div>', unsafe_allow_html=True)
    if not s.jobs:
        st.markdown(
            '<div class="hf-card"><div class="hf-empty">No job descriptions saved yet. '
            "Upload one to start screening.</div></div>",
            unsafe_allow_html=True,
        )
        return
    cards = ""
    for j in s.jobs:
        meta = " · ".join(p for p in (j["company"], j["location"]) if p) or "Details not specified"
        pct = (j["met"] / j["pairs"] * 100) if j["pairs"] else 0
        cards += (
            f'<div class="hf-job"><div class="hf-job-t">{esc(j["title"])}</div>'
            f'<div class="hf-job-m">{esc(meta)} · {_plural(j["n_req"], "requirement")} '
            f'({j["n_must"]} must-have)</div>'
            f'<div class="hf-prog-l"><span>Requirements met (screened)</span>'
            f'<span>{j["met"]}/{j["pairs"]}</span></div>'
            f'<div class="hf-lg-bar"><div style="width:{pct:.0f}%;background:var(--hf-primary)"></div></div>'
            f'<div class="hf-job-stats">'
            f'<div><b>{j["screened"]}</b><span>screened</span></div>'
            f'<div><b>{j["prepared"]}</b><span>interview prep</span></div>'
            f'<div><b>{j["reports"]}</b><span>reports</span></div></div></div>'
        )
    st.markdown(f'<div class="hf-jobs">{cards}</div>', unsafe_allow_html=True)


def render_welcome() -> None:
    steps = [
        ("Upload", "Job description and resumes"),
        ("Extract", "Skills, experience, projects"),
        ("Map evidence", "Each requirement gets a status and proof"),
        ("Prepare interview", "Targeted questions from the gaps"),
        ("Report", "Standardised, evidence-linked evaluation"),
    ]
    st.markdown(
        collapse(
            """
            <div class="hf-welcome">
              <h2>Welcome to HireFlow</h2>
              <p>Your recruitment workspace is ready. No candidates have been added yet.</p>
            </div>
            <div class="hf-section">How HireFlow works</div>
            """
        ),
        unsafe_allow_html=True,
    )
    st.markdown(steps_html(steps), unsafe_allow_html=True)
    st.write("")
    _, mid, _ = st.columns([1, 1, 1])
    with mid:
        nav_button("Upload job & resumes", "upload", primary=True, key_suffix="welcome")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def _ai_configured() -> bool:
    keys = ("ANTHROPIC_API_KEY", "GROQ_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY")
    return any(os.environ.get(k) for k in keys)


def _init_session_state() -> None:
    defaults = {
        "selected_candidate_id": None,
        "selected_job_id": None,
        "current_report_id": None,
        "current_question_index": 0,
        "upload_processing_status": "idle",
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def main() -> None:
    inject_css()
    inject_dash_css()
    _init_session_state()

    db_ok = True
    db_error = ""
    try:
        init_db()  # idempotent: CREATE TABLE IF NOT EXISTS
    except Exception as exc:  # noqa: BLE001
        db_ok = False
        db_error = str(exc)

    render_sidebar(db_ok)
    render_hero(db_ok, _ai_configured())

    if not db_ok:
        st.error(f"Unable to open the database: {db_error}")
        if st.button("Retry", key="retry_db"):
            st.rerun()
        return

    try:
        with st.spinner("Loading recruitment workspace..."):
            snap, warnings = load_snapshot()
    except (DatabaseError, Exception) as exc:  # noqa: BLE001
        st.error(f"Unable to load recruitment data: {exc}")
        if st.button("Retry", key="retry_load"):
            st.rerun()
        return

    for w in warnings:
        st.warning(w)

    render_kpis(snap)

    if snap.n_candidates == 0 and snap.n_jobs == 0:
        render_welcome()
    else:
        left, right = st.columns([3, 2], gap="medium")
        with left:
            render_distribution(snap)
        with right:
            render_activity(snap)

        render_jobs(snap)

        st.markdown('<div class="hf-section">Next steps</div>', unsafe_allow_html=True)
        left, right = st.columns([3, 2], gap="medium")
        with left:
            render_attention(snap)
        with right:
            render_quick_actions()

    st.markdown(
        f'<div class="hf-footer">HireFlow v{APP_VERSION} · Human hiring decisions stay with the recruiter.</div>',
        unsafe_allow_html=True,
    )


main()