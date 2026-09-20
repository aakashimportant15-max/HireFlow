"""
HireFlow - pages/1_Upload_Screen.py
===================================
Entry point of the pipeline: JD + resumes  ->  core.pipeline.process_documents()  ->  DB.

This page only: collects input, validates it, triggers the pipeline, shows
progress and shows the outcome. It never parses files, calls an LLM or
writes SQL itself.
"""

from __future__ import annotations

import logging
from pathlib import Path

import streamlit as st

st.set_page_config(
    page_title="Upload & Process · HireFlow",
    page_icon="📤",
    layout="wide",
    initial_sidebar_state="expanded",
)

try:
    from core.pipeline import (
        PipelineConfigError,
        PipelineError,
        PipelineResult,
        UploadedDoc,
        process_documents,
    )
    from db.database import DatabaseError, init_db
    from ui.theme import (
        GROUP_META,
        collapse,
        esc,
        inject_css,
        nav_button,
        page_header,
        plural,
        render_sidebar,
        steps_html,
    )
except ImportError as exc:  # pragma: no cover - environment problem
    st.error(
        f"Project modules could not be imported: `{exc}`.\n\n"
        "Run the app from the project root: `streamlit run app.py`."
    )
    st.stop()

logger = logging.getLogger("hireflow.upload_page")

ALLOWED_EXT = {".pdf", ".docx", ".txt"}
MAX_FILE_MB = 10
MAX_RESUMES = 50

STATUS_PILL = {
    "processed": ("Processed", "#dcfce7", "#166534"),
    "partial": ("Partly processed", "#fef3c7", "#92400e"),
    "skipped": ("Skipped", "#e2e8f0", "#334155"),
    "failed": ("Failed", "#fee2e2", "#991b1b"),
}

# What the pipeline does, in order. Descriptive only: real progress comes from the pipeline callback.
PIPELINE_STEPS = [
    ("Extract requirements", "Read the job description into must-have and nice-to-have requirements"),
    ("Extract evidence", "Read each resume into skills, experience and projects"),
    ("Map to requirements", "Give every requirement a status backed by resume evidence"),
    ("Create review groups", "Group candidates as pointers for human review"),
    ("Prepare validation", "Note what to confirm in the interview"),
]


# --------------------------------------------------------------------------
# Input helpers
# --------------------------------------------------------------------------
def _size(n: int) -> str:
    return f"{n / 1024:.0f} KB" if n < 1024 * 1024 else f"{n / 1024 / 1024:.1f} MB"


def _file_chips(docs: list[UploadedDoc]) -> None:
    if not docs:
        return
    rows = "".join(f'<div class="hf-file"><b>{esc(d.name)}</b><span>{_size(len(d.data))}</span></div>' for d in docs)
    st.markdown(f'<div class="hf-files">{rows}</div>', unsafe_allow_html=True)


def _to_docs(files) -> list[UploadedDoc]:
    return [UploadedDoc(name=f.name, data=f.getvalue()) for f in (files or [])]


def _validate(jd: UploadedDoc | None, resumes: list[UploadedDoc]) -> list[str]:
    problems: list[str] = []
    if jd is None:
        problems.append("Upload or paste a job description first.")
    if not resumes:
        problems.append("Upload at least one candidate resume.")
    if len(resumes) > MAX_RESUMES:
        problems.append(f"Too many resumes ({len(resumes)}). Please upload at most {MAX_RESUMES} at a time.")

    for doc in ([jd] if jd else []) + resumes:
        ext = Path(doc.name).suffix.lower()
        if ext not in ALLOWED_EXT:
            problems.append(f"Unsupported format: {doc.name}. Supported: PDF, DOCX, TXT.")
        elif len(doc.data) == 0:
            problems.append(f"{doc.name} is empty.")
        elif len(doc.data) > MAX_FILE_MB * 1024 * 1024:
            problems.append(f"{doc.name} is larger than {MAX_FILE_MB} MB.")

    names = [d.name for d in resumes]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        problems.append(f"The same file was added more than once: {', '.join(dupes)}.")
    return problems


# --------------------------------------------------------------------------
# Result rendering
# --------------------------------------------------------------------------
def _pill(text: str, bg: str, fg: str) -> str:
    return f'<span class="hf-pill" style="background:{bg};color:{fg}">{esc(text)}</span>'


def _render_result(res: PipelineResult) -> None:
    if res.n_processed:
        st.markdown(
            f'<div class="hf-banner ok">✓ Screening complete. {plural(res.n_processed, "candidate")} '
            f'processed for <b>{esc(res.job_title)}</b>.</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div class="hf-banner err">No candidates could be fully processed. See the details below.</div>',
            unsafe_allow_html=True,
        )
    if res.n_problems and res.n_processed:
        st.markdown(
            f'<div class="hf-banner warn">⚠ {plural(res.n_problems, "file")} need attention. See the table below.</div>',
            unsafe_allow_html=True,
        )
    if res.audit_failures:
        st.markdown(
            f'<div class="hf-banner warn">⚠ {plural(res.audit_failures, "audit entry", "audit entries")} '
            "could not be recorded.</div>",
            unsafe_allow_html=True,
        )

    # Real numbers straight from the pipeline result.
    kpis = [
        ("Candidates", res.n_processed),
        ("Requirements", res.n_requirements),
        ("Evidence mappings", res.n_mappings),
        ("Review groups", res.n_groups),
        ("Summaries", res.n_summaries),
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

    st.write("")
    c1, c2, c3 = st.columns(3)
    with c1:
        nav_button("Review candidate pool", "candidates", primary=True, key_suffix="res")
    with c2:
        nav_button("Prepare interviews", "interview", key_suffix="res")
    with c3:
        if st.button("Process another batch", key="reset_batch", use_container_width=True):
            st.session_state["upload_result"] = None
            st.session_state["upload_nonce"] += 1
            st.session_state["upload_processing_status"] = "idle"
            st.rerun()

    rows = ""
    for o in res.outcomes:
        label, bg, fg = STATUS_PILL.get(o.status, STATUS_PILL["failed"])
        group_html = "-"
        if o.group:
            g_label, g_color = GROUP_META.get(o.group, (o.group, "#94a3b8"))
            group_html = _pill(g_label, g_color + "22", g_color)
        rows += (
            f"<tr><td><b>{esc(o.name or '-')}</b><div class='hf-muted'>{esc(o.file)}</div></td>"
            f"<td>{group_html}</td><td>{o.n_mappings or '-'}</td>"
            f"<td>{_pill(label, bg, fg)}<div class='hf-muted'>{esc(o.reason)}</div></td></tr>"
        )
    st.markdown('<div class="hf-section">What was processed</div>', unsafe_allow_html=True)
    st.markdown(
        collapse(
            f"""
            <div class="hf-card"><div class="hf-card-title">{esc(res.job_title)}</div>
            <div class="hf-card-sub">Groups come from the screening rules and are
            pointers for review, not hiring decisions.</div>
            <table class="hf-table"><thead><tr><th>Candidate</th><th>Group</th><th>Mappings</th><th>Status</th></tr></thead>
            <tbody>{rows}</tbody></table></div>
            """
        ),
        unsafe_allow_html=True,
    )


# --------------------------------------------------------------------------
# Pipeline run (progress UI)
# --------------------------------------------------------------------------
def _run(jd: UploadedDoc, resumes: list[UploadedDoc]) -> None:
    st.session_state["upload_processing_status"] = "running"
    lines: list[str] = []
    failed_message: str | None = None

    with st.status("Running the screening pipeline...", expanded=True) as status:
        bar = st.progress(0.0, text="Starting...")
        log = st.empty()

        def on_progress(message: str, fraction: float) -> None:
            bar.progress(fraction, text=message)
            lines.append(message)
            log.code("\n".join(lines[-6:]), language=None)

        try:
            result = process_documents(jd, resumes, on_progress=on_progress)
        except PipelineConfigError as exc:
            logger.exception("Pipeline adapter misconfigured")
            failed_message = f"Pipeline setup problem: {exc}"
        except PipelineError as exc:
            failed_message = str(exc)
        except DatabaseError:
            logger.exception("Database error during processing")
            failed_message = (
                "Processing could not be completed because saving to the database failed. "
                "Please retry."
            )
        except Exception:  # noqa: BLE001
            logger.exception("Unexpected pipeline failure")
            failed_message = "Something went wrong while processing. Please retry."
        else:
            status.update(label="Processing complete", state="complete", expanded=False)

        if failed_message:
            status.update(label="Processing failed", state="error", expanded=True)

    if failed_message:
        st.session_state["upload_processing_status"] = "error"
        st.markdown(f'<div class="hf-banner err">❌ {esc(failed_message)}</div>', unsafe_allow_html=True)
        return

    st.session_state["upload_result"] = result
    st.session_state["upload_processing_status"] = "done" if result.n_processed else "error"
    if result.n_processed:
        st.session_state["selected_job_id"] = result.job_id
    st.rerun()


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main() -> None:
    inject_css()
    st.session_state.setdefault("upload_result", None)
    st.session_state.setdefault("upload_nonce", 0)
    st.session_state.setdefault("upload_processing_status", "idle")
    st.session_state.setdefault("selected_job_id", None)
    nonce = st.session_state["upload_nonce"]

    db_ok = True
    try:
        init_db()
    except Exception as exc:  # noqa: BLE001
        db_ok, db_error = False, str(exc)
    render_sidebar(db_ok)

    existing: PipelineResult | None = st.session_state["upload_result"]
    if existing is not None:
        page_header(
            "Screening results",
            "Every mapping below is stored with its evidence, ready for review.",
            eyebrow="Upload & process",
        )
        if db_ok:
            _render_result(existing)
        else:
            st.error(f"Unable to open the database: {db_error}")
        return

    page_header(
        "Turn resumes into traceable evidence",
        "Upload a job description and candidate resumes. HireFlow maps each requirement to what the "
        "resumes actually say, so you can review the evidence and decide who to interview.",
        eyebrow="Upload & process",
    )
    if not db_ok:
        st.error(f"Unable to open the database: {db_error}")
        return

    left, right = st.columns(2, gap="medium")

    # ---- Job description ----
    jd_doc: UploadedDoc | None = None
    with left:
        with st.container(border=True, key="hf_jd"):
            st.markdown(
                '<div class="hf-card-title">Job description</div>'
                '<div class="hf-card-sub">One role per batch. PDF, DOCX or TXT.</div>',
                unsafe_allow_html=True,
            )
            tab_file, tab_text = st.tabs(["Upload file", "Paste text"])
            with tab_file:
                jd_file = st.file_uploader(
                    "Job description file", type=["pdf", "docx", "txt"], key=f"jd_file_{nonce}",
                    label_visibility="collapsed",
                )
            with tab_text:
                jd_text = st.text_area(
                    "Job description text", height=170, key=f"jd_text_{nonce}",
                    placeholder="Paste the job description here...", label_visibility="collapsed",
                )
            if jd_file is not None:
                jd_doc = UploadedDoc(jd_file.name, jd_file.getvalue())
                if jd_text.strip():
                    st.caption("Both a file and pasted text were provided. The file will be used.")
            elif jd_text.strip():
                jd_doc = UploadedDoc("pasted_job_description.txt", jd_text.encode("utf-8"))
            _file_chips([jd_doc] if jd_doc else [])

    # ---- Resumes ----
    resume_docs: list[UploadedDoc] = []
    with right:
        with st.container(border=True, key="hf_resumes"):
            st.markdown(
                '<div class="hf-card-title">Candidate resumes</div>'
                f'<div class="hf-card-sub">Upload one or many (up to {MAX_RESUMES}). PDF, DOCX or TXT.</div>',
                unsafe_allow_html=True,
            )
            tab_files, tab_paste = st.tabs(["Upload files", "Paste text"])
            with tab_files:
                resume_files = st.file_uploader(
                    "Resume files", type=["pdf", "docx", "txt"], accept_multiple_files=True,
                    key=f"resume_files_{nonce}", label_visibility="collapsed",
                )
            with tab_paste:
                paste_label = st.text_input("Candidate name (optional)", key=f"paste_name_{nonce}")
                paste_text = st.text_area(
                    "Resume text", height=130, key=f"paste_resume_{nonce}",
                    placeholder="Paste one resume here...", label_visibility="collapsed",
                )
            resume_docs = _to_docs(resume_files)
            if paste_text.strip():
                safe = "".join(ch if ch.isalnum() or ch in "-_ " else "" for ch in paste_label).strip().replace(" ", "_")
                resume_docs.append(UploadedDoc(f"{safe or 'pasted_resume'}.txt", paste_text.encode("utf-8")))
            _file_chips(resume_docs)

    st.write("")
    _, mid, _ = st.columns([1, 1.2, 1])
    with mid:
        clicked = st.button("Process documents", type="primary", use_container_width=True, key="process_btn")

    if clicked:
        problems = _validate(jd_doc, resume_docs)
        if problems:
            for p in problems:
                st.markdown(f'<div class="hf-banner warn">⚠ {esc(p)}</div>', unsafe_allow_html=True)
        else:
            _run(jd_doc, resume_docs)
            return

    st.markdown('<div class="hf-section">What happens next</div>', unsafe_allow_html=True)
    st.markdown(steps_html(PIPELINE_STEPS), unsafe_allow_html=True)
    st.markdown(
        '<div class="hf-muted" style="margin-top:0.6rem">Every step is written to the audit trail.</div>',
        unsafe_allow_html=True,
    )


main()