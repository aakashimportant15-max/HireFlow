<div align="center">

# HireFlow

### Evidence-backed AI recruiting workspace

**AI organizes evidence. Humans make hiring decisions.**

[![Python](https://img.shields.io/badge/Python-3.x-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Streamlit](https://img.shields.io/badge/Streamlit-UI-FF4B4B?logo=streamlit&logoColor=white)](https://streamlit.io/)
[![SQLite](https://img.shields.io/badge/SQLite-Database-003B57?logo=sqlite&logoColor=white)](https://www.sqlite.org/)
[![Pydantic](https://img.shields.io/badge/Pydantic-Validation-E92063?logo=pydantic&logoColor=white)](https://docs.pydantic.dev/)
[![Groq](https://img.shields.io/badge/Groq-LLM-F55036)](https://groq.com/)
[![pytest](https://img.shields.io/badge/Tested%20with-pytest-0A9EDC?logo=pytest&logoColor=white)](https://pytest.org/)

</div>

---

HireFlow transforms job descriptions and candidate resumes into structured, traceable evidence, connects that evidence to individual job requirements, supports targeted interview preparation, and produces an auditable view of resume and interview findings.

> **AI organizes evidence. Humans make hiring decisions.**

## Product at a Glance

![HireFlow Dashboard](src/1.png)

---

## Table of Contents

- [The Problem](#the-problem)
- [The Solution](#the-solution)
- [Product Walkthrough](#product-walkthrough)
- [How HireFlow Works](#how-hireflow-works)
- [Technical Architecture](#technical-architecture)
- [Evidence-First Design](#evidence-first-design)
- [Real E2E Validation](#real-e2e-validation)
- [Technology Stack](#technology-stack)
- [Project Structure](#project-structure)
- [Quick Start](#quick-start)
- [Testing](#testing)
- [Future Scope](#future-scope)
- [If We Had More Time](#if-we-had-more-time)

---

## The Problem

Recruiting information is fragmented across:

- Job descriptions
- Resumes
- Candidate experience and projects
- Interview notes
- Hiring requirements

The challenge isn't simply finding candidates. It is **organizing and validating the evidence behind candidate-requirement matches.**

| Friction | Why it matters |
|---|---|
| **Repetitive manual screening** | Recruiters repeatedly review similar documents against the same requirements. |
| **Unstructured candidate information** | Skills, experience, education and projects are presented inconsistently across resumes. |
| **Weak requirement-to-evidence traceability** | It can be difficult to see *why* a requirement was considered met, partial, unclear or missing. |
| **Unclear or missing information** | Areas that need follow-up can be easy to overlook. |
| **Resume and interview evidence kept apart** | Screening evidence and interview validation can become disconnected. |

---

## The Solution

HireFlow creates an **evidence-first recruiting workflow**:

```text
Job Description
       ↓
Requirement Extraction
       ↓
Candidate Evidence
       ↓
Requirement Mapping
       ↓
Screening Groups
       ↓
Targeted Interview
       ↓
Interview Evidence
       ↓
Auditable Report
```

### Extract

Pull requirements and candidate information out of documents.

### Connect

Link candidate evidence to individual job requirements.

### Validate

Surface unclear or missing areas and prepare targeted interview questions.

> HireFlow is designed to support human review rather than replace the human hiring decision.

---

## Product Walkthrough

### 01 · Dashboard

A centralized workspace showing the candidate pipeline, system status, group distribution, and recent activity from the audit trail.

![HireFlow Dashboard](src/1.png)

---

### 02 · Upload & Process

Upload a job description and candidate resumes, or provide document text directly. HireFlow processes the inputs through ingestion, extraction, mapping, grouping and validation preparation.

![Upload and Process](src/2.png)

---

### 03 · Candidate Review

Review candidates against job requirements with requirement-level statuses:

`MET` · `PARTIAL` · `UNCLEAR` · `MISSING`

Candidates can be filtered by job, group or requirement status, with evidence available for review.

![Candidate Review](src/3.png)

---

### 04 · Interview Preparation

Screening gaps are carried forward into targeted interview preparation so recruiters can focus on what still needs validation.

Notes are recorded as interview evidence; they are not treated as automated hiring decisions.

![Interview Preparation](src/4.png)

---

### 05 · Interview Report

Interview evidence is kept alongside the original resume evidence while remaining distinct, allowing findings to be traced back to the relevant requirement.

![Interview Report](src/5.png)

---

### 06 · Ask Pool

Ask questions about the candidate pool in plain language. HireFlow shows how it interpreted the query before showing matching candidates and the stored evidence behind those results.

![Ask Pool](src/6.png)

> **Note:** The dashboard values visible in these screenshots reflect the demo database at the time of capture. They are separate from the verified real-data validation run below.

---

## How HireFlow Works

| Stage | What happens |
|---|---|
| **1. Ingestion** | PDF, DOCX and TXT documents are converted into usable text. |
| **2. Extraction** | Job requirements and candidate skills, experience, education and projects are extracted into structured records. |
| **3. Requirement Mapping** | Each requirement is evaluated against candidate evidence and assigned `MET`, `PARTIAL`, `UNCLEAR` or `MISSING`. |
| **4. Grouping** | Candidates are placed into explainable screening groups based on requirement evidence. |
| **5. Candidate Summary** | A concise summary is generated for each candidate. |
| **6. Interview Preparation** | Unclear, missing and partial areas become validation targets and question topics. |
| **7. Interview Evaluation & Report** | Interview answers become additional evidence, kept separate from resume evidence but tied to the same requirements. |
| **8. Audit** | Key pipeline actions and evidence are persisted for traceability. |
| **9. Ask Pool** | Natural-language questions are translated into filters over stored candidate data. |

### Screening groups

HireFlow uses explainable screening buckets rather than presenting a black-box candidate score:

```text
STRONG_MATCH
PARTIAL_MATCH
NEEDS_VALIDATION
WEAK_MATCH
```

---

## Technical Architecture

```text
                         HireFlow
                            │
              ┌─────────────┴─────────────┐
              │                           │
         Streamlit UI                Core Pipeline
              │                           │
              │       ┌───────────────────┼───────────────────┐
              │       │          │         │         │         │
              │   Ingestion  Extraction  Mapping  Grouping  Evaluation
              │                                      │
              │                              Summary / Interview
              │                                      │
              │                                  Pool Query
              └───────────────────┬──────────────────┘
                                  │
                              SQLite DB
                                  │
                       ┌──────────┼──────────┐
                       │          │          │
                    Evidence     Audit    Interview
                                  │
                             LLM Gateway
                                  │
                                Groq
```

### Why modular?

HireFlow separates ingestion, extraction, mapping, grouping, interview and evaluation into distinct modules. This keeps the UI thin, makes individual stages testable, and allows AI-assisted steps to be audited without mixing them into deterministic application logic.

---

## Evidence-First Design

HireFlow is intentionally **not a black-box scoring system**.

| Layer | Responsibility |
|---|---|
| **AI assistance** | Information extraction, interpretation of ambiguous evidence, interview question generation and interview evidence extraction. |
| **Deterministic logic** | Clear requirement checks, status aggregation, candidate grouping, persistence and audit trail. |
| **Human decision** | Reviewing evidence, conducting interviews and making the final hiring decision. |

> **HireFlow does not make hiring decisions.** It organizes evidence and highlights areas that need human review.

> **AI organizes evidence. Humans make hiring decisions.**

---

## Real E2E Validation

A real pipeline run was executed end to end against actual resumes.

| Validation metric | Result |
|---|---:|
| Resumes processed | **12** |
| Requirements extracted | **13** |
| Requirement ↔ candidate mappings | **156** |
| Candidate groups | **12** |
| Candidate summaries | **12** |
| Audit records | **193** |
| Resumes processed successfully | **12 / 12** |
| Database foreign-key integrity checks | **Passed** |

```text
RESULT: PASS
```

This validation demonstrates pipeline completion and database integrity for the tested run. It is **not** a claim about screening accuracy or hiring outcomes.

---

## Technology Stack

| Layer | Technology | Purpose |
|---|---|---|
| **UI** | Streamlit | Multi-page recruiting workspace |
| **Language** | Python | Application and pipeline logic |
| **Database** | SQLite | Evidence, mappings, groups, interviews and audit trail |
| **Validation** | Pydantic | Typed schemas for extracted and stored data |
| **LLM** | Groq | LLM-assisted extraction, question generation and interview evidence processing |
| **Documents** | PDF / DOCX / TXT | Supported document inputs |
| **Testing** | pytest | Unit, database and pipeline tests |

---

## Project Structure

```text
hireflow/
├── app.py
├── requirements.txt
├── README.md
│
├── core/
│   ├── ingestion.py
│   ├── extraction.py
│   ├── mapping.py
│   ├── grouping.py
│   ├── summary.py
│   ├── interview.py
│   ├── evaluation.py
│   ├── pipeline.py
│   └── pool_query.py
│
├── db/
│   └── database.py
│
├── llm/
│   ├── client.py
│   └── prompts/
│
├── models/
│   └── schemas.py
│
├── pages/
│   ├── 1_Upload_Screen.py
│   ├── 2_Candidates.py
│   ├── 3_Interview_Prep.py
│   ├── 4_Interview_Report.py
│   └── 5_Ask_Pool.py
│
├── ui/
│   └── theme.py
│
├── src/
│   ├── 1.png
│   ├── 2.png
│   ├── 3.png
│   ├── 4.png
│   ├── 5.png
│   └── 6.png
│
└── tests/
```

---

## Quick Start

### Prerequisites

- Python 3.x
- A Groq API key

### 1. Clone the repository

```bash
git clone <repository-url>
cd hireflow
```

### 2. Create a virtual environment

```bash
python -m venv .venv
```

Activate it:

```bash
# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

Create a local `.env` file and add the required API key:

```text
GROQ_API_KEY=your_api_key_here
```

Keep `.env` out of version control.

### 5. Run HireFlow

```bash
streamlit run app.py
```

Then open:

```text
http://localhost:8501
```

> **Security:** Never commit `.env` or a real API key to the repository.

---

## Testing

The repository includes tests covering core modules, database behavior, the end-to-end pipeline, interview/evaluation flows and related functionality.

Run the test suite with:

```bash
pytest -q
```

The real-data pipeline validation described above is separate from the unit/integration test suite and verifies the complete persistence flow on the tested dataset.

---

## Future Scope

The following are planned directions, **not current capabilities**.

| Area | Direction |
|---|---|
| **Production** | Cloud deployment, monitoring and observability |
| **Integrations** | ATS / HRIS integrations |
| **Scale** | Larger candidate and document volumes, background processing and larger evaluation datasets |
| **Evidence** | Additional sources such as portfolios and applications feeding a unified evidence layer |
| **Enterprise** | Authentication, permissions and multi-tenancy |

---

## If We Had More Time

1. **Production-grade cloud deployment**
2. **ATS integration**
3. **Large-scale evaluation datasets**
4. **Enterprise authentication and multi-tenancy**
5. **Expanded evidence sources**
6. **Monitoring and observability**

---

<div align="center">

# HireFlow

### From resumes to traceable evidence.

**AI organizes evidence. Humans make hiring decisions.**

**Built for HackDay 1.0**

</div>
