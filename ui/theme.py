"""
HireFlow - ui/theme.py
======================
Shared look-and-feel for app.py and every page under pages/.

Streamlit re-renders each page from scratch, so every page must call
`inject_css()` itself. Presentation helpers only: no DB, no LLM, no business logic.

Design direction: a calm, light evidence workspace. One typeface (IBM Plex Sans),
flat surfaces, hairline borders, semantic status colours. The one distinctive
element is the "evidence rail": every requirement/evidence block carries a
coloured left rail that encodes its status, so status is readable at a glance
without ever becoming a score.

Public names used by the pages (kept stable): GROUP_META, STATUS_META, ACTION_LABELS,
esc, collapse, plural, inject_css, page_header, render_sidebar, go, nav_button,
pill, group_pill, status_pill, time_ago, ai_configured.
New, additive helpers: STATUS_RAIL, STATUS_SHORT, status_counts_html, evidence_quote, steps_html.
"""

from __future__ import annotations

import html as _htmllib
import os
from collections import OrderedDict
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
APP_VERSION = "1.0"

PAGES = {
    "home": "app.py",
    "upload": "pages/1_Upload_Screen.py",
    "candidates": "pages/2_Candidates.py",
    "interview": "pages/3_Interview_Prep.py",
    "report": "pages/4_Interview_Report.py",
    "pool": "pages/5_Ask_Pool.py",
}

GROUP_META: "OrderedDict[str, tuple[str, str]]" = OrderedDict(
    [
        ("STRONG_MATCH", ("Strong match", "#2B6B3F")),
        ("PARTIAL_MATCH", ("Partial match", "#EA8624")),
        ("NEEDS_VALIDATION", ("Needs validation", "#B88716")),
        ("WEAK_MATCH", ("Weak match", "#6F684F")),
        ("UNGROUPED", ("Not grouped yet", "#9A927A")),
    ]
)

CSS = """
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&display=swap');

:root {
  /* Radha/Krishna-inspired HireFlow palette: forest + saffron orange + warm ivory */
  --hf-paper: #FFFDF8; --hf-surface: #FFFFFF; --hf-surface-2: #FFF9EF;
  --hf-ink: #19300F; --hf-ink-2: #38502A; --hf-muted: #6F684F; --hf-faint: #9A927A;
  --hf-line: #E9E2D6; --hf-line-2: #F3EEE6;
  --hf-primary: #EA8624; --hf-primary-dark: #C86B13; --hf-primary-soft: #FFF1E3;
  --hf-forest: #0D1506; --hf-forest-2: #2B4217;
  --hf-gold: #F8C759; --hf-gold-soft: #FFF7E3;
  --hf-ai: #8A641C; --hf-ai-soft: #FFF3D2;
}

/* ---------- base ---------- */
.stApp, [data-testid="stAppViewContainer"] { background: var(--hf-paper); }
.stApp, .stApp p, .stApp label, .stApp li, .stApp button, .stApp input, .stApp textarea,
.stApp h1, .stApp h2, .stApp h3, .stApp h4 {
  font-family: 'IBM Plex Sans', -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
}
.stApp { color: var(--hf-ink); }
header[data-testid="stHeader"] { background: transparent; }
footer { visibility: hidden; }
.block-container { padding-top: 1.8rem; padding-bottom: 3rem; max-width: 1200px; }

/* ---------- sidebar ---------- */
/* Make the sidebar a real flex column (nav on top, status/principle pinned to the
   actual bottom of the sidebar's own box, not the browser window) so it never
   overlaps content and always scrolls together with the rest of the sidebar. */
[data-testid="stSidebar"] { background: var(--hf-forest); border-right: 1px solid #243414;
  display: flex; flex-direction: column; }
[data-testid="stSidebar"] > div { display: flex; flex-direction: column; flex: 1 1 auto; min-height: 0; }
[data-testid="stSidebarContent"] { display: flex; flex-direction: column; flex: 1 1 auto; min-height: 0; overflow-y: auto; }
[data-testid="stSidebarUserContent"] { display: flex; flex-direction: column; flex: 1 1 auto; padding-bottom: 0.5rem; }

[data-testid="stSidebarNav"]::before {
  content: "HireFlow"; display: block; padding: 1.3rem 1rem 0.5rem 1rem;
  font-weight: 700; font-size: 1.25rem; letter-spacing: -0.01em; color: #FFFFFF;
}
[data-testid="stSidebarNav"] a { border-radius: 8px; color: #D9E3C9; transition: background 0.15s ease, transform 0.1s ease;
  margin: 0.15rem 0.6rem !important; }
/* Streamlit derives the home label from app.py ("app"). Keep the route intact,
   but present a clean product label in the sidebar. */
[data-testid="stSidebarNav"] li:first-child a {
  font-size: 0 !important;
}
[data-testid="stSidebarNav"] li:first-child a::after {
  content: "Home";
  font-size: 0.86rem;
  font-weight: 600;
  color: #D9E3C9;
}
[data-testid="stSidebarNav"] li:first-child a[aria-current="page"]::after {
  color: #0D1506;
}

/* Streamlit shows a small caption above the nav list, taken straight from the
   entry script's filename ("app.py" -> "app"). It isn't a testid'd element in
   every Streamlit version, so target it structurally: the first non-list child
   of the nav container, whatever tag/class it happens to render as. */
[data-testid="stSidebarNav"] > *:first-child:not(ul),
[data-testid="stSidebarNavSectionHeader"] {
  font-size: 0 !important; line-height: 1 !important; display: block;
  padding: 0.5rem 1rem 0.35rem 1rem !important; margin: 0 !important;
}
[data-testid="stSidebarNav"] > *:first-child:not(ul)::after,
[data-testid="stSidebarNavSectionHeader"]::after {
  content: "HOME"; display: block; font-size: 0.68rem; font-weight: 700;
  letter-spacing: 0.09em; color: #7C8C68;
}

[data-testid="stSidebarNav"] a span { color: #D9E3C9; font-weight: 500; }
[data-testid="stSidebarNav"] a:hover { background: #1D2B10; }
[data-testid="stSidebarNav"] a[aria-current="page"] { background: #EA8624; box-shadow: 0 2px 8px rgba(234,134,36,0.35); }
[data-testid="stSidebarNav"] a[aria-current="page"] span { color: #0D1506; font-weight: 700; }
/* numbered workflow: items 2-5 (Upload, Candidates, Interview, Report) are the sequence;
   item 6 (Ask Pool) is a separate "Discover" utility. Item 1 is Home. */
[data-testid="stSidebarNav"] ul { counter-reset: hfnav; padding-bottom: 0.4rem; }
[data-testid="stSidebarNav"] li:nth-child(n+2):nth-child(-n+5) a::before {
  counter-increment: hfnav; content: counter(hfnav, decimal-leading-zero);
  font-size: 0.72rem; font-weight: 600; color: #A9B994; margin-right: 0.6rem;
  font-variant-numeric: tabular-nums;
}
[data-testid="stSidebarNav"] li:nth-child(6) { margin-top: 1.1rem; padding-top: 0.6rem; border-top: 1px solid #31431F; }
[data-testid="stSidebarNav"] li:nth-child(6)::before {
  content: "Discover"; display: block; font-size: 0.72rem; font-weight: 600; color: #A9B994;
  padding: 0 1rem 0.25rem 1rem;
}
.sb-box { margin: 0.6rem 0.2rem; padding: 0.8rem 0.9rem; border-radius: 10px;
  background: #16230B; border: 1px solid #31431F; transition: border-color 0.15s ease; }
.sb-box:hover { border-color: #46603099; }
.sb-title { font-size: 0.74rem; color: #A9B994; margin-bottom: 0.4rem; font-weight: 600; }
.sb-row { display: flex; align-items: center; gap: 0.5rem; font-size: 0.84rem; padding: 0.12rem 0; color: #E1E8D5; }
.sb-foot { font-size: 0.75rem; color: #8FA17A; padding: 0.3rem 0.4rem; }

/* Pinned to the bottom of the sidebar's own flex box via margin-top:auto —
   not position:fixed, so it can never drift away from or overlap the sidebar,
   and it scrolls together with the nav on short viewports instead of floating. */
.sb-bottom {
  margin-top: auto;
  padding: 0.6rem 0.75rem 0.65rem 0.75rem;
}
.sb-bottom .sb-box { margin: 0.45rem 0 0 0; }
.sb-bottom .sb-foot { padding: 0.3rem 0.4rem 0; }

.hf-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; flex-shrink: 0; }

/* ---------- page header ---------- */
.hf-hero { padding: 0 0 1.1rem 0; margin-bottom: 1.3rem; border-bottom: 1px solid var(--hf-line); }
.hf-eyebrow { font-size: 0.8rem; color: var(--hf-primary-dark); font-weight: 700; letter-spacing: 0.03em; text-transform: uppercase; }
.hf-title { font-size: 1.95rem; font-weight: 700; line-height: 1.2; letter-spacing: -0.02em;
  margin: 0.25rem 0 0.4rem 0; color: var(--hf-ink); }
.hf-sub { font-size: 0.99rem; color: var(--hf-muted); max-width: 680px; line-height: 1.55; }
.hf-chips { display: flex; gap: 0.5rem; flex-wrap: wrap; }
.hf-chip { display: inline-flex; align-items: center; gap: 0.4rem; padding: 0.3rem 0.7rem; border-radius: 99px;
  font-size: 0.8rem; font-weight: 500; background: var(--hf-surface); border: 1px solid var(--hf-line); color: var(--hf-ink-2);
  transition: border-color 0.15s ease; }
.hf-chip:hover { border-color: var(--hf-primary); }

/* ---------- cards / sections ---------- */
.hf-card { background: var(--hf-surface); border: 1px solid var(--hf-line); border-radius: 12px; padding: 1.15rem 1.3rem;
  box-shadow: 0 1px 2px rgba(25,48,15,0.04); transition: box-shadow 0.15s ease, border-color 0.15s ease; }
.hf-card:hover { box-shadow: 0 4px 14px rgba(25,48,15,0.07); border-color: #DED3B8; }
.hf-card-title { font-size: 1.02rem; font-weight: 600; color: var(--hf-ink); margin-bottom: 0.15rem; }
.hf-card-sub { font-size: 0.84rem; color: var(--hf-muted); margin-bottom: 0.8rem; line-height: 1.45; }
.hf-section { font-size: 1.15rem; font-weight: 600; color: var(--hf-ink); margin: 1.8rem 0 0.7rem 0; letter-spacing: -0.005em; }
.hf-empty { color: var(--hf-faint); font-size: 0.88rem; padding: 0.6rem 0; }
.hf-footer { text-align: center; color: var(--hf-faint); font-size: 0.8rem; margin-top: 2.2rem; }
.hf-welcome { background: var(--hf-surface); border: 1px dashed #D9B875; border-radius: 12px;
  padding: 2rem 1.5rem 1.6rem 1.5rem; margin-bottom: 1rem; text-align: left; }
.hf-welcome h2 { margin: 0 0 0.35rem 0; color: var(--hf-ink); font-size: 1.4rem; font-weight: 600; padding: 0; }
.hf-welcome p { color: var(--hf-muted); margin: 0; max-width: 560px; line-height: 1.5; }
.hf-clear { background: #EDF6E8; border: 1px solid #B9D5A8; color: #2B6B3F; border-radius: 10px;
  padding: 0.85rem 1rem; font-size: 0.9rem; font-weight: 500; }

/* ---------- KPI strip (one connected ledger, not four floating cards) ---------- */
.hf-kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 1px;
  background: var(--hf-line); border: 1px solid var(--hf-line); border-radius: 12px; overflow: hidden; margin: 1rem 0; }
.hf-kpi { background: var(--hf-surface); padding: 0.95rem 1.15rem; display: block; border: none; border-radius: 0; box-shadow: none;
  transition: background 0.15s ease; }
.hf-kpi:hover { background: var(--hf-surface-2); }
.hf-kpi-ico { display: none; }
.hf-kpi-val { font-size: 1.7rem; font-weight: 700; color: var(--hf-ink); line-height: 1.1; font-variant-numeric: tabular-nums; letter-spacing: -0.01em; }
.hf-kpi-lbl { font-size: 0.84rem; font-weight: 500; color: var(--hf-ink-2); margin-top: 0.15rem; }
.hf-kpi-sub { font-size: 0.78rem; color: var(--hf-muted); }

/* ---------- workflow steps (a real sequence, so numbered) ---------- */
.hf-steps { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 0; margin: 0.8rem 0;
  border: 1px solid var(--hf-line); border-radius: 12px; overflow: hidden; background: var(--hf-line); column-gap: 1px; }
.hf-step { background: var(--hf-surface); padding: 0.95rem 1.05rem; border: none; border-radius: 0; }
.hf-step .i { font-size: 0.78rem; font-weight: 700; color: var(--hf-primary-dark); margin-bottom: 0.35rem;
  width: auto; height: auto; background: none; display: block; }
.hf-step b { color: var(--hf-ink); font-size: 0.92rem; font-weight: 600; }
.hf-step p { color: var(--hf-muted); font-size: 0.8rem; margin: 0.2rem 0 0 0; line-height: 1.4; }

/* ---------- files / pills / tables ---------- */
.hf-files { display: flex; flex-direction: column; gap: 0.35rem; margin-top: 0.5rem; }
.hf-file { display: flex; justify-content: space-between; gap: 0.6rem; background: var(--hf-paper);
  border: 1px solid var(--hf-line); border-radius: 8px; padding: 0.4rem 0.7rem; font-size: 0.82rem; color: var(--hf-ink); }
.hf-file span { color: var(--hf-muted); white-space: nowrap; }
.hf-pill { display: inline-block; padding: 0.15rem 0.6rem; border-radius: 99px; font-size: 0.74rem; font-weight: 600; white-space: nowrap; }
.hf-table { width: 100%; border-collapse: collapse; font-size: 0.88rem; }
.hf-table th { text-align: left; color: var(--hf-muted); font-size: 0.78rem; font-weight: 600;
  padding: 0.5rem 0.6rem; border-bottom: 1px solid var(--hf-line); }
.hf-table td { padding: 0.65rem 0.6rem; border-bottom: 1px solid var(--hf-line-2); color: var(--hf-ink); vertical-align: top; }
.hf-table tr:last-child td { border-bottom: none; }
.hf-muted { color: var(--hf-muted); font-size: 0.8rem; }

/* ---------- banners ---------- */
.hf-banner { border-radius: 10px; padding: 0.85rem 1.05rem; font-size: 0.92rem; font-weight: 500; margin-bottom: 0.6rem; }
.hf-banner.ok { background: #EDF6E8; border: 1px solid #B9D5A8; color: #2B6B3F; }
.hf-banner.warn { background: #FFF3D2; border: 1px solid #E8C76A; color: #7A5A13; }
.hf-banner.err { background: #FBECEA; border: 1px solid #E3B2AA; color: #8A3028; }

/* ---------- buttons / inputs / containers ---------- */
.stButton > button { border-radius: 8px; font-weight: 500; padding: 0.55rem 1rem;
  border: 1px solid #D8CBAF; background: var(--hf-surface); color: var(--hf-ink); box-shadow: 0 1px 2px rgba(25,48,15,0.05);
  transition: border-color 0.15s ease, color 0.15s ease, box-shadow 0.15s ease, transform 0.05s ease; }
.stButton > button:hover { border-color: var(--hf-primary); color: var(--hf-primary-dark); background: var(--hf-surface);
  box-shadow: 0 2px 6px rgba(234,134,36,0.15); }
.stButton > button:active { transform: translateY(1px); }
.stButton > button:focus-visible { outline: 2px solid var(--hf-primary); outline-offset: 2px; }
.stButton > button[kind="primary"], button[data-testid="stBaseButton-primary"] {
  background: var(--hf-primary); color: #0D1506; border: 1px solid var(--hf-primary); font-weight: 600;
  box-shadow: 0 2px 8px rgba(234,134,36,0.28); }
.stButton > button[kind="primary"]:hover, button[data-testid="stBaseButton-primary"]:hover {
  color: #0D1506; background: var(--hf-gold); border-color: var(--hf-gold); box-shadow: 0 3px 10px rgba(248,199,89,0.35); }
div[data-testid="stVerticalBlockBorderWrapper"] { background: var(--hf-surface); border-radius: 12px; border-color: var(--hf-line);
  transition: border-color 0.15s ease, box-shadow 0.15s ease; }
div[data-testid="stVerticalBlockBorderWrapper"]:hover { box-shadow: 0 3px 10px rgba(25,48,15,0.05); }
[data-baseweb="input"], [data-baseweb="select"] > div, [data-baseweb="textarea"] { border-radius: 8px; background: #FFFFFF; border-color: var(--hf-line);
  transition: border-color 0.15s ease, box-shadow 0.15s ease; }
[data-baseweb="input"]:focus-within, [data-baseweb="select"] > div:focus-within, [data-baseweb="textarea"]:focus-within {
  border-color: var(--hf-primary) !important; box-shadow: 0 0 0 3px rgba(234,134,36,0.12); }

/* ---------- candidates / evidence ---------- */
.hf-cand-top { display: flex; justify-content: space-between; align-items: flex-start; gap: 0.8rem; flex-wrap: wrap; }
.hf-cand-name { font-size: 1.12rem; font-weight: 600; color: var(--hf-ink); }
.hf-cand-meta { font-size: 0.84rem; color: var(--hf-muted); margin-top: 0.1rem; }
.hf-skills { display: flex; flex-wrap: wrap; gap: 0.35rem; margin: 0.7rem 0 0.2rem 0; }
.hf-skill { background: var(--hf-paper); color: var(--hf-ink-2); border: 1px solid var(--hf-line); border-radius: 6px;
  padding: 0.12rem 0.5rem; font-size: 0.78rem; font-weight: 500; }
.hf-cov { display: flex; flex-wrap: wrap; gap: 0.45rem; margin: 0.6rem 0 0.3rem 0; }
.hf-cnt { border-radius: 99px; padding: 0.15rem 0.65rem; font-size: 0.78rem; font-weight: 500; }
.hf-cnt b { font-weight: 700; }
.hf-cnt.zero { opacity: 0.45; }
.hf-req { background: var(--hf-surface); border: 1px solid var(--hf-line); border-left: 4px solid var(--hf-line);
  border-radius: 10px; padding: 0.95rem 1.1rem; margin-bottom: 0.65rem; transition: box-shadow 0.15s ease; }
.hf-req:hover { box-shadow: 0 2px 10px rgba(25,48,15,0.06); }
.hf-req-top { display: flex; justify-content: space-between; gap: 0.6rem; flex-wrap: wrap; align-items: center; }
.hf-req-t, .hf-req-name { font-weight: 600; color: var(--hf-ink); font-size: 0.96rem; }
.hf-quote { border-left: 3px solid var(--hf-primary); background: var(--hf-surface-2); padding: 0.5rem 0.8rem; margin-top: 0.5rem;
  border-radius: 0 8px 8px 0; font-size: 0.87rem; color: var(--hf-ink-2); line-height: 1.5; }
.hf-quote .src { display: block; font-size: 0.76rem; color: var(--hf-muted); margin-top: 0.3rem; }
.hf-list { margin: 0.2rem 0 0.7rem 1.1rem; padding: 0; color: var(--hf-ink-2); font-size: 0.9rem; }
.hf-list li { margin-bottom: 0.25rem; }
.hf-h { font-weight: 600; color: var(--hf-ink); font-size: 0.92rem; margin: 0.9rem 0 0.25rem 0; }
"""


def collapse(s: str) -> str:
    """Strip indentation/blank lines so Markdown never turns HTML into a code block."""
    return "".join(line.strip() for line in s.strip().splitlines())


def esc(value: object) -> str:
    return _htmllib.escape("" if value is None else str(value), quote=True)


def plural(n: int, singular: str, plural_form: str | None = None) -> str:
    return f"{n} {singular if n == 1 else (plural_form or singular + 's')}"


def inject_css() -> None:
    st.markdown(f"<style>{collapse(CSS)}</style>", unsafe_allow_html=True)


def ai_configured() -> bool:
    keys = ("ANTHROPIC_API_KEY", "GROQ_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY")
    return any(os.environ.get(k) for k in keys)


def page_header(title: str, subtitle: str, eyebrow: str = "HireFlow") -> None:
    # Older pages pass ALL-CAPS eyebrows ("STEP 2 · REVIEW"); soften them to title case.
    eyebrow_txt = eyebrow.title() if eyebrow.isupper() else eyebrow
    st.markdown(
        collapse(
            f"""
            <div class="hf-hero">
              <div class="hf-eyebrow">{esc(eyebrow_txt)}</div>
              <div class="hf-title">{esc(title)}</div>
              <div class="hf-sub">{esc(subtitle)}</div>
            </div>
            """
        ),
        unsafe_allow_html=True,
    )


def render_sidebar(db_ok: bool) -> None:
    ai_ok = ai_configured()
    db_dot = "#22c55e" if db_ok else "#ef4444"
    ai_dot = "#22c55e" if ai_ok else "#f59e0b"
    with st.sidebar:
        st.markdown(
            collapse(
                f"""
                <div class="sb-bottom">
                  <div class="sb-box">
                    <div class="sb-title">System status</div>
                    <div class="sb-row"><span class="hf-dot" style="background:{db_dot}"></span>
                      Database {'connected' if db_ok else 'unavailable'}</div>
                    <div class="sb-row"><span class="hf-dot" style="background:{ai_dot}"></span>
                      AI key {'configured' if ai_ok else 'not found'}</div>
                  </div>
                  <div class="sb-box">
                    <div class="sb-title">Principle</div>
                    <div class="sb-row" style="align-items:flex-start">AI organizes the evidence. People make the hiring decision.</div>
                  </div>
                  <div class="sb-foot">HireFlow v{APP_VERSION}</div>
                </div>
                """
            ),
            unsafe_allow_html=True,
        )


def go(page_key: str) -> None:
    try:
        st.switch_page(PAGES[page_key])
    except Exception:  # noqa: BLE001
        st.warning("That page is not available yet.")


def nav_button(label: str, page_key: str, *, icon: str = "", primary: bool = False, key_suffix: str = "") -> None:
    exists = (ROOT / PAGES[page_key]).exists()
    clicked = st.button(
        f"{icon}  {label}".strip(),
        key=f"nav_{page_key}_{key_suffix}",
        use_container_width=True,
        type="primary" if primary else "secondary",
        disabled=not exists,
        help=None if exists else "This page has not been created yet.",
    )
    if clicked:
        go(page_key)


# ---------------------------------------------------------------------------
# Shared status / audit helpers (used by Candidates, Interview and Report pages)
# ---------------------------------------------------------------------------
# key -> (label, foreground, background, icon)
STATUS_META: "OrderedDict[str, tuple[str, str, str, str]]" = OrderedDict(
    [
        ("MET", ("Met", "#2B6B3F", "#E8F3E4", "\u2713")),
        ("PARTIAL", ("Partial", "#9A5415", "#FFF0D8", "\u25d0")),
        ("UNCLEAR", ("Unclear", "#7A5A13", "#FFF3D2", "?")),
        ("MISSING", ("Missing evidence", "#8A3028", "#FBECEA", "\u2717")),
    ]
)

# Solid colour for the evidence rail (left edge of requirement/evidence blocks).
STATUS_RAIL = {"MET": "#2B6B3F", "PARTIAL": "#EA8624", "UNCLEAR": "#B88716", "MISSING": "#9B3D32"}

# Short labels for compact count chips ("11 met").
STATUS_SHORT = {"MET": "met", "PARTIAL": "partial", "UNCLEAR": "unclear", "MISSING": "missing"}

ACTION_LABELS = {
    "EXTRACT_JD": "Job description processed",
    "EXTRACT_RESUME": "Resume processed",
    "MAP_REQUIREMENT": "Requirement mapped",
    "GROUP_CANDIDATE": "Candidate grouped",
    "SUMMARIZE_CANDIDATE": "Summary generated",
    "GENERATE_QUESTION": "Interview question generated",
    "GENERATE_FOLLOWUP": "Follow-up question generated",
    "MAP_INTERVIEW_NOTES": "Interview notes mapped",
    "GENERATE_REPORT": "Interview report generated",
    "POOL_QUERY": "Pool query run",
}


def pill(text: str, fg: str, bg: str) -> str:
    return f'<span class="hf-pill" style="background:{bg};color:{fg}">{esc(text)}</span>'


def group_pill(group_key: str) -> str:
    label, color = GROUP_META.get(group_key, (group_key, "#94a3b8"))
    return pill(label, color, color + "1f")


def status_pill(status_key: str) -> str:
    label, fg, bg, icon = STATUS_META.get(status_key, (status_key, "#334155", "#e2e8f0", "-"))
    return pill(f"{icon} {label}", fg, bg)


def status_counts_html(counts: dict) -> str:
    """Status distribution as count chips. Counts only: never a score or percentage."""
    chips = ""
    for key, (_label, fg, bg, icon) in STATUS_META.items():
        n = int(counts.get(key, 0) or 0)
        cls = "hf-cnt zero" if n == 0 else "hf-cnt"
        chips += f'<span class="{cls}" style="background:{bg};color:{fg}">{icon} <b>{n}</b> {STATUS_SHORT[key]}</span>'
    return f'<div class="hf-cov">{chips}</div>'


def evidence_quote(text: object, source: str = "", location: str = "") -> str:
    """A quoted piece of evidence with its provenance underneath."""
    meta = " · ".join(p for p in (f"Source: {source}" if source else "", f"Location: {location}" if location else "") if p)
    tail = f'<span class="src">{esc(meta)}</span>' if meta else ""
    return f'<div class="hf-quote">\u201c{esc(text)}\u201d{tail}</div>'


def steps_html(steps: list[tuple[str, str]]) -> str:
    """Numbered workflow strip. Only use for content that is genuinely a sequence."""
    cells = "".join(
        f'<div class="hf-step"><div class="i">{i:02d}</div><b>{esc(t)}</b><p>{esc(d)}</p></div>'
        for i, (t, d) in enumerate(steps, start=1)
    )
    return f'<div class="hf-steps">{cells}</div>'


def time_ago(ts: object) -> str:
    from datetime import datetime, timezone

    try:
        dt = datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    secs = max(0, int((datetime.now(timezone.utc) - dt).total_seconds()))
    if secs < 60:
        return "just now"
    mins = secs // 60
    if mins < 60:
        return f"{mins} min ago"
    hours = mins // 60
    if hours < 24:
        return f"{hours} hr ago"
    days = hours // 24
    if days < 30:
        return f"{days} day{'s' if days != 1 else ''} ago"
    return dt.strftime("%d %b %Y")