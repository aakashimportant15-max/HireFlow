<div align="center">

# HireFlow: AI Candidate Screening & Interview Intelligence Agent

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

![HireFlow Dashboard](https://raw.githubusercontent.com/aakashimportant15-max/HireFlow/main/scr/1..png)

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

## Problem Statement

### The Problem

Recruiters and hiring teams review large numbers of resumes for every open role.

Important candidate information can be buried across resumes, portfolios, application forms and interview notes. Manual screening is repetitive and makes it harder to consistently compare candidate evidence against the actual requirements of a role.

Interviewers also spend time preparing questions and reviewing notes instead of focusing on candidate conversations.

The core challenge is not simply finding candidates. It is **organizing, connecting and validating the evidence behind candidate-requirement matches while keeping human hiring decisions at the center.**

| Recruitment friction | What HireFlow addresses |
|---|---|
| **High-volume manual screening** | Structures candidate information against role requirements. |
| **Information buried in documents** | Extracts relevant skills, experience, projects and qualifications into structured records. |
| **Weak requirement-to-evidence traceability** | Maps candidate evidence to individual requirements and preserves the supporting evidence. |
| **Missing or unclear information** | Surfaces areas that require validation and carries them into interview preparation. |
| **Interview preparation overhead** | Creates role-specific questions and follow-ups around the candidate's evidence. |
| **Disconnected interview evidence** | Keeps interview evidence tied to the same requirements while remaining distinct from resume evidence. |

---

## The Solution

HireFlow is an **AI-powered recruitment intelligence workspace** that turns the problem statement into an evidence-first workflow:

```text
Job Description + Candidate Resumes
                ↓
        Requirement Extraction
                ↓
         Candidate Evidence
                ↓
        Requirement Mapping
                ↓
         Screening Groups
                ↓
      Candidate Summaries
                ↓
       Targeted Interview
                ↓
 Interview Evidence + Follow-ups
                ↓
      Standardized Report
                ↓
       Audit + Candidate Pool
```

### Extract

Extract relevant skills, experience, projects, qualifications and job requirements from recruitment documents.

### Connect

Map candidate evidence to specific job requirements and preserve the evidence behind each insight.

### Validate

Identify missing or unclear information, generate role-specific questions and create follow-ups for deeper validation.

### Evaluate

Bring resume and interview evidence together in a structured evaluation report without turning the workflow into an automated hiring decision.

### Query

Allow recruiters to explore the candidate pool using natural language and inspect the evidence behind results.

> **The goal is to reduce repetitive recruitment work while keeping human hiring decisions at the center of the process.**

---

## Product Walkthrough

### 01 · Dashboard

A centralized workspace showing the candidate pipeline, system status, group distribution, and recent activity from the audit trail.

![HireFlow Dashboard](https://raw.githubusercontent.com/aakashimportant15-max/HireFlow/main/scr/1..png)

---

### 02 · Upload & Process

Upload a job description and candidate resumes, or provide document text directly. HireFlow processes the inputs through ingestion, extraction, mapping, grouping and validation preparation.

![Upload and Process](https://raw.githubusercontent.com/aakashimportant15-max/HireFlow/main/scr/2..png)

---

### 03 · Candidate Review

Review candidates against job requirements with requirement-level statuses:

`MET` · `PARTIAL` · `UNCLEAR` · `MISSING`

Candidates can be filtered by job, group or requirement status, with evidence available for review.

![Candidate Review](https://raw.githubusercontent.com/aakashimportant15-max/HireFlow/main/scr/3..png)

---

### 04 · Interview Preparation

Screening gaps are carried forward into targeted interview preparation so recruiters can focus on what still needs validation.

Notes are recorded as interview evidence; they are not treated as automated hiring decisions.

![Interview Preparation](https://raw.githubusercontent.com/aakashimportant15-max/HireFlow/main/scr/4..png)

---

### 05 · Interview Report

Interview evidence is kept alongside the original resume evidence while remaining distinct, allowing findings to be traced back to the relevant requirement.

![Interview Report](https://raw.githubusercontent.com/aakashimportant15-max/HireFlow/main/scr/5..png)

---

### 06 · Ask Pool

Ask questions about the candidate pool in plain language. HireFlow shows how it interpreted the query before showing matching candidates and the stored evidence behind those results.

![Ask Pool](https://raw.githubusercontent.com/aakashimportant15-max/HireFlow/main/scr/6..png)

> **Note:** The dashboard values visible in these screenshots reflect the demo database at the time of capture. They are separate from the verified real-data validation run below.

---

## How HireFlow Works

| Stage | What happens |
|---|---|
| **1. Ingestion** | PDF, DOCX and TXT documents are converted into usable text. |
| **2. Extraction** | Job requirements and candidate skills, experience, education, projects and qualifications are extracted into structured records. |
| **3. Requirement Mapping** | Each requirement is evaluated against candidate evidence and assigned `MET`, `PARTIAL`, `UNCLEAR` or `MISSING`. |
| **4. Grouping** | Candidates are placed into explainable screening groups based on requirement evidence. |
| **5. Candidate Summary** | A concise summary is generated for each candidate. |
| **6. Interview Preparation** | Unclear, missing and partial areas become validation targets and question topics. |
| **7. Interview Evaluation & Report** | Interview answers and notes become additional evidence, kept separate from resume evidence but tied to the same requirements; unanswered areas are surfaced for follow-up. |
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

This validation demonstrates pipeline completion and database integrity for the tested run. It is **not** a claim about screening accuracy, candidate quality or hiring outcomes.

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
├── db/
├── llm/
├── models/
├── pages/
├── ui/
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

## 👤 Author

### Aakash

**AI & Data Analytics Enthusiast | Python Developer | AI/ML Builder**

📧 Email: [aakashimportant15@gmail.com](mailto:aakashimportant03@gmail.com)  
🐙 GitHub: [aakashimportant15-max](https://github.com/aakashimportant15-max)  
📦 Project Repository: [HireFlow](https://github.com/aakashimportant15-max/HireFlow)

---
<div align="center">

# HireFlow: AI Candidate Screening & Interview Intelligence Agent
### From resumes to traceable evidence.
**AI organizes evidence. Humans make hiring decisions.**


</div>
