"""
HireFlow - pages/2_Candidates.py
================================
Candidate review + explainability screen.

    database  ->  candidate pool  ->  search / filter  ->  candidate card
              ->  profile  ->  requirement analysis  ->  evidence  ->  interview prep

This page only READS and DISPLAYS what earlier steps already stored. It never
parses files, calls an LLM, computes mappings/groups/summaries, evaluates
interviews or makes hiring decisions. Groups and statuses are shown as
pointers for human review, not as recommendations. There is no score,
ranking or percentage anywhere on this page: only status counts and evidence.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path

import streamlit as st

st.set_page_config(
    page_title="Candidates · HireFlow",
    page_icon="👥",
    layout="wide",
    initial_sidebar_state="expanded",
)

try:
    import db.database as dbm
    from db.database import (
        DatabaseError,
        get_candidate_group,
        get_candidates,
        get_jobs,
        get_mappings,
        init_db,
    )
    from ui.theme import (
        GROUP_META,
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
        status_counts_html,
        status_pill,
    )
except ImportError as exc:  # pragma: no cover - environment problem
    st.error(
        f"Project modules could not be imported: `{exc}`.\n\n"
        "Run the app from the project root: `streamlit run app.py`."
    )
    st.stop()

logger = logging.getLogger("hireflow.candidates_page")

ROOT = Path(__file__).resolve().parent.parent
INTERVIEW_PAGE = "pages/3_Interview_Prep.py"
PAGE_SIZE = 10
ALL_JOBS = "All jobs"
ALL_GROUPS = "All groups"
ALL_STATUS = "Any status"

STATUS_ORDER = ["MET", "PARTIAL", "UNCLEAR", "MISSING"]
GROUP_RANK = {"STRONG_MATCH": 0, "PARTIAL_MATCH": 1, "NEEDS_VALIDATION": 2, "WEAK_MATCH": 3}

EXTRA_CSS = """
.hf-ev-meta { font-size: 0.78rem; color: #5d677a; margin-top: 0.35rem; }
.hf-note { font-size: 0.83rem; color: #92400e; margin-top: 0.4rem; }
.hf-why { border: 1px solid #d8def0; background: #f7f9fe; border-radius: 12px; padding: 1rem 1.15rem; margin-top: 0.8rem; }
.hf-why-h { font-weight: 600; color: #182033; font-size: 1rem; }
.hf-why-s { font-size: 0.84rem; color: #5d677a; margin: 0.15rem 0 0.8rem 0; }
.hf-why-foot { font-size: 0.8rem; color: #5d677a; margin-top: 0.4rem; padding-top: 0.6rem; border-top: 1px solid #e2e6ee; }
.hf-snap { background: #ffffff; border: 1px solid #e2e6ee; border-radius: 12px; padding: 0.95rem 1.2rem; margin: 0.8rem 0 0.2rem 0; }
.hf-snap-h { font-size: 0.84rem; font-weight: 600; color: #5d677a; }
.hf-val { background: #ffffff; border: 1px solid #e2e6ee; border-left: 4px solid #d97706; border-radius: 10px;
  padding: 0.7rem 1rem; margin-bottom: 0.55rem; }
.hf-val b { color: #182033; font-weight: 600; }
.hf-val div { font-size: 0.86rem; color: #3b465c; margin-top: 0.15rem; }
"""


# --------------------------------------------------------------------------
# Tolerant accessors (stored records may be dataclass / pydantic / dict)
# --------------------------------------------------------------------------
def _get(obj: object, *names: str, default=None):
    """First non-empty attribute (or dict key) among `names`."""
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
    if isinstance(value, (list, tuple, set)):
        return list(value)
    if isinstance(value, str) and "," in value:
        return [v.strip() for v in value.split(",") if v.strip()]
    return [value]


def _item_text(item: object) -> str:
    """One-line text for an ExperienceItem / ProjectItem / EducationItem."""
    if isinstance(item, str):
        return item
    title = _get(item, "title", "name", "degree", default="")
    field = _get(item, "field_of_study", default="")
    if field:
        title = f"{title}, {field}" if title else field
    org = _get(item, "company", "institution", default="")
    period = _get(item, "year", default="")
    if not period:
        start, end = _get(item, "start_date", default=""), _get(item, "end_date", default="")
        if start or end:
            period = f"{start or '?'}–{end or '?'}"
        else:
            dur = _get(item, "duration_years", default="")
            period = f"{dur} yrs" if dur else ""
    head = " · ".join(str(p) for p in (title, org, period) if p)
    desc = _get(item, "description", default="")
    outcome = _get(item, "outcome", default="")
    tech = _as_list(_get(item, "technologies", default=[]))
    tail = " ".join(x for x in (str(desc), f"Outcome: {outcome}." if outcome else "",
                                f"[{', '.join(map(str, tech))}]" if tech else "") if x)
    return f"{head} — {tail}" if head and tail else (head or tail or str(item))


def _headline(c: object) -> str:
    exp = _as_list(_get(c, "experience", default=[]))
    if not exp:
        return ""
    t = _get(exp[0], "title", default="")
    co = _get(exp[0], "company", default="")
    return " at ".join(p for p in (t, co) if p)


def _status(m: object) -> str:
    return _enum(_get(m, "status", default=""))


def _cand_id(c: object) -> str:
    return str(_get(c, "candidate_id", "id", default=""))


def _skills(c: object) -> list[str]:
    return [str(s) for s in _as_list(_get(c, "skills", default=[]))]


# --------------------------------------------------------------------------
# Data loading (everything comes from db/database.py)
# --------------------------------------------------------------------------
def _load() -> dict:
    jobs = get_jobs()
    candidates = get_candidates()
    mappings = get_mappings(latest_only=True)

    req_lookup: dict = {}
    for j in jobs:
        for r in _get(j, "requirements", default=[]):
            req_lookup[_get(r, "id", "requirement_id")] = {
                "text": str(_get(r, "title", "description", default="Requirement")),
                "priority": _enum(_get(r, "priority", default="")),
                "job_id": _get(j, "job_id", "id"),
            }

    by_cand: dict[str, list] = defaultdict(list)
    for m in mappings:
        by_cand[str(_get(m, "candidate_id", default=""))].append(m)

    groups: dict[str, str] = {}
    for c in candidates:
        cid = _cand_id(c)
        try:
            g = get_candidate_group(cid)
            groups[cid] = _enum(g) or "UNGROUPED"
        except Exception:  # noqa: BLE001
            logger.exception("Group lookup failed for %s", cid)
            groups[cid] = "UNGROUPED"

    return {"jobs": jobs, "candidates": candidates, "by_cand": by_cand, "groups": groups, "reqs": req_lookup}


def _load_summary(cid: str, job_id: object):
    """Stored summary from the DB layer, if the project exposes one. Never generated here."""
    for fn_name in ("get_candidate_summary", "get_summary", "get_candidate_summaries", "get_summaries"):
        fn = getattr(dbm, fn_name, None)
        if fn is None:
            continue
        for kwargs in ({"candidate_id": cid, "job_id": job_id}, {"candidate_id": cid}):
            try:
                res = fn(**kwargs)
            except TypeError:
                continue
            except Exception:  # noqa: BLE001
                logger.exception("Summary lookup failed (%s)", fn_name)
                return None
            if isinstance(res, (list, tuple)):
                res = res[-1] if res else None
            if res:
                return res
    return None


# --------------------------------------------------------------------------
# Small UI helpers
# --------------------------------------------------------------------------
def _counts(maps: list) -> dict[str, int]:
    out = {s: 0 for s in STATUS_ORDER}
    for m in maps:
        s = _status(m)
        if s in out:
            out[s] += 1
    return out


def _scope(maps: list, job_id: object, reqs: dict) -> list:
    """Mapping has no job_id; the job comes from its requirement."""
    if job_id is None:
        return maps
    return [m for m in maps if reqs.get(_get(m, "requirement_id"), {}).get("job_id") == job_id]


def _go_interview(cid: str, job_id: object) -> None:
    st.session_state["selected_candidate_id"] = cid
    if job_id is not None:
        st.session_state["selected_job_id"] = job_id
    try:
        st.switch_page(INTERVIEW_PAGE)
    except Exception:  # noqa: BLE001
        st.warning("The Interview Prep page is not available yet.")


def _evidence_block(m: object) -> str:
    """Evidence chain for one mapping: quote -> provenance -> confidence/reason -> next action."""
    status = _status(m)
    evidence = _as_list(_get(m, "evidence", default=[]))
    reason = _get(m, "reason", default="")
    conf = _get(m, "confidence", default=None)

    html_ = ""
    if evidence:
        for e in evidence:
            text = e if isinstance(e, str) else _get(e, "text", default="")
            src = "" if isinstance(e, str) else _enum(_get(e, "source", default="")).replace("_", " ").title()
            where = "" if isinstance(e, str) else _get(e, "location", default="")
            html_ += evidence_quote(text, src, str(where) if where else "")
    elif status == "MISSING":
        html_ = '<div class="hf-quote">No supporting evidence found in the available profile.</div>'
    else:
        html_ = '<div class="hf-quote">No evidence excerpt was stored for this requirement.</div>'

    if conf is not None:
        try:
            html_ += f'<div class="hf-ev-meta">Confidence {float(conf):.2f}</div>'
        except (TypeError, ValueError):
            html_ += f'<div class="hf-ev-meta">Confidence {esc(conf)}</div>'
    if reason:
        html_ += f'<div class="hf-ev-meta">Reason: {esc(reason)}</div>'
    if status in ("UNCLEAR", "PARTIAL"):
        html_ += '<div class="hf-note">Needs validation: confirm this in the interview.</div>'
    if status == "MISSING":
        html_ += (
            '<div class="hf-ev-meta">Absence of evidence is not proof of absence. '
            "Validate before drawing conclusions.</div>"
        )
    return html_


def _requirement_cards(maps: list, reqs: dict) -> None:
    ordered = sorted(maps, key=lambda m: STATUS_ORDER.index(_status(m)) if _status(m) in STATUS_ORDER else 9)
    for m in ordered:
        info = reqs.get(_get(m, "requirement_id"), {})
        name = info.get("text") or str(_get(m, "requirement", "requirement_text", default="Requirement"))
        prio = info.get("priority")
        prio_html = f' <span class="hf-muted">· {esc(prio.replace("_", " ").title())}</span>' if prio else ""
        rail = STATUS_RAIL.get(_status(m), "#cbd5e1")
        st.markdown(
            f'<div class="hf-req" style="border-left-color:{rail}"><div class="hf-req-top">'
            f'<div class="hf-req-name">{esc(name)}{prio_html}</div>'
            f"{status_pill(_status(m))}</div>{_evidence_block(m)}</div>",
            unsafe_allow_html=True,
        )


def _why_panel(group: str, maps: list, reqs: dict) -> None:
    g_label = GROUP_META.get(group, (group.replace("_", " ").title(), ""))[0]
    st.markdown(
        '<div class="hf-why"><div class="hf-why-h">Why is this candidate in this group?</div>'
        f'<div class="hf-why-s">Grouped as <b>{esc(g_label)}</b> from the requirement evidence below. '
        "Each finding shows what the resume says.</div></div>",
        unsafe_allow_html=True,
    )
    _requirement_cards(maps, reqs)
    st.markdown(
        '<div class="hf-why-foot">These findings are pointers for human review, not hiring decisions.</div>',
        unsafe_allow_html=True,
    )


# --------------------------------------------------------------------------
# Candidate card (list view)
# --------------------------------------------------------------------------
def _render_card(c: object, group: str, maps: list, job_id: object, reqs: dict) -> None:
    cid = _cand_id(c)
    name = _get(c, "name", default="Unnamed candidate")
    headline = _headline(c)
    years = _get(c, "total_experience_years", default="")
    location = _get(c, "location", default="")
    email = _get(c, "email", default="")
    skills = _skills(c)

    sub = " · ".join(p for p in (headline, f"{years} years" if years else "") if p)
    contact = " · ".join(p for p in (location, email) if p)
    chips = "".join(f'<span class="hf-skill">{esc(s)}</span>' for s in skills[:6])
    if len(skills) > 6:
        chips += f'<span class="hf-skill">+{len(skills) - 6}</span>'

    if maps:
        cov = status_counts_html(_counts(maps))
    else:
        cov = '<div class="hf-muted" style="margin:0.6rem 0 0.3rem 0">Not mapped against a job yet.</div>'

    with st.container(border=True, key=f"card_{cid}"):
        st.markdown(
            f'<div class="hf-cand-top"><div><div class="hf-cand-name">{esc(name)}</div>'
            f'<div class="hf-cand-meta">{esc(sub) if sub else "&nbsp;"}</div>'
            f'<div class="hf-cand-meta">{esc(contact)}</div></div>{group_pill(group)}</div>'
            f'<div class="hf-skills">{chips}</div>{cov}',
            unsafe_allow_html=True,
        )
        b1, b2, b3 = st.columns(3)
        with b1:
            if st.button("View profile", key=f"view_{cid}", use_container_width=True):
                st.session_state["cand_detail_id"] = cid
                st.rerun()
        with b2:
            open_ids: set = st.session_state["cand_why_open"]
            is_open = cid in open_ids
            if st.button("Hide evidence" if is_open else "Why?", key=f"why_{cid}",
                         use_container_width=True, disabled=not maps):
                open_ids.symmetric_difference_update({cid})
                st.rerun()
        with b3:
            if st.button("Prepare interview", key=f"prep_{cid}", use_container_width=True, type="primary"):
                _go_interview(cid, job_id)

        if cid in st.session_state["cand_why_open"] and maps:
            _why_panel(group, maps, reqs)


# --------------------------------------------------------------------------
# Candidate detail view (dossier)
# --------------------------------------------------------------------------
def _section(title: str) -> None:
    st.markdown(f'<div class="hf-section">{esc(title)}</div>', unsafe_allow_html=True)


def _bullets(items: list, empty: str) -> None:
    if not items:
        st.markdown(f'<div class="hf-muted">{esc(empty)}</div>', unsafe_allow_html=True)
        return
    st.markdown(
        '<ul class="hf-list">' + "".join(f"<li>{esc(_item_text(i))}</li>" for i in items) + "</ul>",
        unsafe_allow_html=True,
    )


def _render_detail(c: object, group: str, maps: list, job_id: object, reqs: dict) -> None:
    cid = _cand_id(c)
    top_l, top_r = st.columns([3, 1])
    with top_l:
        if st.button("← Back to candidates", key="back_list"):
            st.session_state["cand_detail_id"] = None
            st.rerun()
    with top_r:
        if st.button("Prepare interview", key="detail_prep_top", type="primary", use_container_width=True):
            _go_interview(cid, job_id)

    name = _get(c, "name", default="Unnamed candidate")
    headline = _headline(c)
    st.markdown(
        f'<div class="hf-card"><div class="hf-cand-top"><div><div class="hf-cand-name" style="font-size:1.6rem">'
        f'{esc(name)}</div><div class="hf-cand-meta">{esc(headline)}</div></div>{group_pill(group)}</div>'
        '<div class="hf-card-sub" style="margin:0.6rem 0 0 0">Groups are pointers for review, '
        "not hiring decisions.</div></div>",
        unsafe_allow_html=True,
    )

    # ---- screening snapshot (counts only) ----
    if maps:
        st.markdown(
            '<div class="hf-snap"><div class="hf-snap-h">Screening snapshot</div>'
            f"{status_counts_html(_counts(maps))}</div>",
            unsafe_allow_html=True,
        )

    # ---- summary (read from DB; never generated here) ----
    _section("Candidate summary")
    summary = _load_summary(cid, job_id)
    if summary is None:
        st.markdown('<div class="hf-muted">No summary has been generated for this candidate yet.</div>', unsafe_allow_html=True)
    else:
        text = summary if isinstance(summary, str) else _get(summary, "fit_summary", "summary", "text", default="")
        if text:
            st.write(text)
        if not isinstance(summary, str):
            s1, s2, s3 = st.columns(3)
            with s1:
                st.markdown("**Key strengths**")
                _bullets(_as_list(_get(summary, "strengths", "key_strengths", default=[])), "None listed.")
            with s2:
                st.markdown("**Gaps**")
                _bullets(_as_list(_get(summary, "gaps", "missing", default=[])), "None listed.")
            with s3:
                st.markdown("**Validation areas**")
                _bullets(_as_list(_get(summary, "validation_areas", "validation", default=[])), "None listed.")

    # ---- profile ----
    _section("Profile")
    p1, p2 = st.columns([1, 2], gap="large")
    with p1:
        st.markdown("**Contact**")
        contact = [
            ("Email", _get(c, "email")),
            ("Phone", _get(c, "phone")),
            ("Location", _get(c, "location")),
        ]
        shown = [f"{k}: {v}" for k, v in contact if v]
        _bullets(shown, "No contact details found.")
        st.markdown("**Skills**")
        skills = _skills(c)
        st.markdown(
            '<div class="hf-skills">' + "".join(f'<span class="hf-skill">{esc(s)}</span>' for s in skills) + "</div>"
            if skills
            else '<div class="hf-muted">No skills extracted.</div>',
            unsafe_allow_html=True,
        )
    with p2:
        st.markdown("**Experience**")
        _bullets(_as_list(_get(c, "experience", default=[])), "No experience extracted.")
        st.markdown("**Projects**")
        _bullets(_as_list(_get(c, "projects", default=[])), "No projects extracted.")
        st.markdown("**Education**")
        _bullets(_as_list(_get(c, "education", default=[])), "No education extracted.")
        certs = _as_list(_get(c, "certifications", default=[]))
        if certs:
            st.markdown("**Certifications**")
            _bullets(certs, "")

    # ---- requirement evidence ----
    _section("Requirement evidence")
    if not maps:
        st.markdown(
            '<div class="hf-muted">This candidate has not been mapped against the selected job.</div>',
            unsafe_allow_html=True,
        )
    else:
        _requirement_cards(maps, reqs)

        # ---- validation areas (listing of partial / unclear / missing mappings) ----
        _section("Validation areas")
        todo = [m for m in maps if _status(m) in ("UNCLEAR", "MISSING", "PARTIAL")]
        if not todo:
            st.markdown('<div class="hf-clear">✓ No open validation areas.</div>', unsafe_allow_html=True)
        else:
            rows = ""
            for m in todo:
                nm = reqs.get(_get(m, "requirement_id"), {}).get("text", "Requirement")
                s = _status(m)
                hint = {
                    "MISSING": "No supporting evidence found in the available profile. Ask for examples.",
                    "UNCLEAR": "Evidence is ambiguous. Ask for specifics.",
                    "PARTIAL": "Partly evidenced. Probe depth and scope.",
                }[s]
                rows += f'<div class="hf-val"><b>{esc(nm)}</b><div>{esc(hint)}</div></div>'
            st.markdown(rows, unsafe_allow_html=True)

    st.write("")
    if st.button("Prepare interview", key="detail_prep", type="primary"):
        _go_interview(cid, job_id)


# --------------------------------------------------------------------------
# Filtering / pagination
# --------------------------------------------------------------------------
def _matches_search(c: object, q: str) -> bool:
    if not q:
        return True
    hay = " ".join(
        str(x)
        for x in (
            _get(c, "name", default=""),
            _get(c, "email", default=""),
            _get(c, "location", default=""),
            _headline(c),
            " ".join(_skills(c)),
        )
    ).lower()
    return all(tok in hay for tok in q.lower().split())


def _apply_filters(data: dict, q: str, job_id: object, group: str | None, status: str | None) -> list:
    out = []
    for c in data["candidates"]:
        cid = _cand_id(c)
        maps = _scope(data["by_cand"].get(cid, []), job_id, data["reqs"])
        if job_id is not None and not maps:
            continue  # only candidates mapped against the selected job
        if group and data["groups"].get(cid, "UNGROUPED") != group:
            continue
        if status and not any(_status(m) == status for m in maps):
            continue
        if not _matches_search(c, q):
            continue
        out.append(c)
    out.sort(
        key=lambda c: (
            GROUP_RANK.get(data["groups"].get(_cand_id(c), ""), 9),
            str(_get(c, "name", default="")).lower(),
        )
    )
    return out


def _pager(total: int, page: int) -> int:
    pages = max(1, -(-total // PAGE_SIZE))
    page = min(max(page, 1), pages)
    if pages > 1:
        st.write("")
        c1, c2, c3 = st.columns([1, 2, 1])
        with c1:
            if st.button("← Previous", key="pg_prev", disabled=page <= 1, use_container_width=True):
                st.session_state["cand_page"] = page - 1
                st.rerun()
        with c2:
            start, end = (page - 1) * PAGE_SIZE + 1, min(page * PAGE_SIZE, total)
            st.markdown(
                f'<div class="hf-muted" style="text-align:center;padding-top:0.6rem">'
                f"Showing {start}–{end} of {total} · Page {page} of {pages}</div>",
                unsafe_allow_html=True,
            )
        with c3:
            if st.button("Next →", key="pg_next", disabled=page >= pages, use_container_width=True):
                st.session_state["cand_page"] = page + 1
                st.rerun()
    return page


def _group_option_label(key: str) -> str:
    return key if key == ALL_GROUPS else GROUP_META.get(key, (key.replace("_", " ").title(), ""))[0]


def _status_option_label(key: str) -> str:
    if key == ALL_STATUS:
        return key
    label = STATUS_META.get(key, (key.title(), "", "", ""))[0]
    return f"Has {label.lower()}"


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main() -> None:
    inject_css()
    st.markdown(f"<style>{EXTRA_CSS}</style>", unsafe_allow_html=True)
    st.session_state.setdefault("selected_job_id", None)
    st.session_state.setdefault("selected_candidate_id", None)
    st.session_state.setdefault("cand_detail_id", None)
    st.session_state.setdefault("cand_page", 1)
    st.session_state.setdefault("cand_why_open", set())
    st.session_state.setdefault("cand_sig", None)
    open_id = st.session_state.pop("open_candidate_id", None)  # set by Ask Pool / Interview Report
    if open_id:
        st.session_state["cand_detail_id"] = str(open_id)

    db_ok = True
    try:
        init_db()
    except Exception as exc:  # noqa: BLE001
        db_ok, db_error = False, str(exc)
    render_sidebar(db_ok)

    page_header(
        "Candidates",
        "Review each candidate's evidence against the job's requirements, and see what still needs validating.",
        eyebrow="Review",
    )
    if not db_ok:
        logger.error("DB unavailable: %s", db_error)
        st.error("⚠ Unable to load candidates. Please check the database connection.")
        if st.button("Retry", key="retry_db"):
            st.rerun()
        return

    try:
        with st.spinner("Loading candidate pool..."):
            data = _load()
    except (DatabaseError, Exception):  # noqa: BLE001
        logger.exception("Failed to load candidates page data")
        st.error("⚠ Unable to load candidates. Please check the database connection.")
        if st.button("Retry", key="retry_load"):
            st.rerun()
        return

    candidates, jobs = data["candidates"], data["jobs"]
    if not candidates:
        st.markdown(
            '<div class="hf-welcome"><h2>No candidates yet</h2>'
            "<p>Upload a job description and candidate resumes to build your candidate pool.</p></div>",
            unsafe_allow_html=True,
        )
        left, _, _ = st.columns([1, 1, 1])
        with left:
            nav_button("Upload candidates", "upload", primary=True, key_suffix="empty")
        return

    job_by_label: dict[str, object] = {}
    for j in jobs:
        label = str(_get(j, "title", default="Untitled job"))
        if label in job_by_label:
            label = f"{label} ({_get(j, 'job_id', 'id')})"
        job_by_label[label] = _get(j, "job_id", "id")

    # ---- detail view ----
    detail_id = st.session_state["cand_detail_id"]
    if detail_id:
        cand = next((c for c in candidates if _cand_id(c) == detail_id), None)
        if cand is None:
            st.session_state["cand_detail_id"] = None
            st.rerun()
        job_id = st.session_state["selected_job_id"]
        maps = _scope(data["by_cand"].get(detail_id, []), job_id, data["reqs"])
        if not maps and job_id is not None:  # selected job has no mappings for this candidate
            maps = data["by_cand"].get(detail_id, [])
        _render_detail(cand, data["groups"].get(detail_id, "UNGROUPED"), maps, job_id, data["reqs"])
        return

    # ---- filters ----
    job_labels = [ALL_JOBS] + list(job_by_label)
    default_job = next(
        (i for i, l in enumerate(job_labels) if job_by_label.get(l) == st.session_state["selected_job_id"]), 0
    )
    f1, f2, f3, f4 = st.columns([2.4, 1.6, 1.4, 1.4], gap="small")
    with f1:
        query = st.text_input("Search", placeholder="Search name, email, location or skill…", label_visibility="collapsed", key="cand_q")
    with f2:
        job_label = st.selectbox("Job", job_labels, index=default_job, label_visibility="collapsed", key="cand_job")
    with f3:
        group_label = st.selectbox(
            "Group", [ALL_GROUPS] + list(GROUP_RANK), format_func=_group_option_label,
            label_visibility="collapsed", key="cand_group",
        )
    with f4:
        status_label = st.selectbox(
            "Requirement status", [ALL_STATUS] + STATUS_ORDER, format_func=_status_option_label,
            label_visibility="collapsed", key="cand_status",
        )

    job_id = job_by_label.get(job_label)
    st.session_state["selected_job_id"] = job_id
    group = None if group_label == ALL_GROUPS else group_label
    status = None if status_label == ALL_STATUS else status_label

    sig = (query, job_id, group, status)
    if st.session_state["cand_sig"] != sig:
        st.session_state["cand_sig"] = sig
        st.session_state["cand_page"] = 1

    try:
        with st.spinner("Updating candidates..."):
            filtered = _apply_filters(data, query.strip(), job_id, group, status)
    except Exception:  # noqa: BLE001
        logger.exception("Filtering failed")
        st.error("Unable to apply this filter. Please try again.")
        return

    total = len(filtered)
    st.markdown(
        f'<div class="hf-muted" style="margin:0.4rem 0 0.8rem 0"><b>{plural(total, "candidate")}</b>'
        f' of {len(candidates)} in the pool</div>',
        unsafe_allow_html=True,
    )

    if total == 0:
        st.markdown(
            '<div class="hf-card"><div class="hf-card-title">No candidates match these filters.</div>'
            '<div class="hf-card-sub">Clear a filter, or upload more resumes.</div></div>',
            unsafe_allow_html=True,
        )
        return

    page = min(max(st.session_state["cand_page"], 1), max(1, -(-total // PAGE_SIZE)))
    for c in filtered[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]:
        cid = _cand_id(c)
        _render_card(
            c,
            data["groups"].get(cid, "UNGROUPED"),
            _scope(data["by_cand"].get(cid, []), job_id, data["reqs"]),
            job_id,
            data["reqs"],
        )
    _pager(total, page)

    st.markdown(
        '<div class="hf-footer">Human hiring decisions stay with the recruiter. '
        "Missing evidence means nothing was found in the available profile, not that the skill is absent.</div>",
        unsafe_allow_html=True,
    )


main()