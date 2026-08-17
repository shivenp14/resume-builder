# Resume Builder — MVP Product Outline

## 1. Purpose

Build a local, single-user resume tailoring application that can:

1. Maintain an editable source of verified resume content.
2. Maintain one or more editable base resume configurations.
3. Accept a job description.
4. Analyze how the job description compares with the user's verified experience.
5. Propose a tailored resume without inventing experience, skills, metrics, or responsibilities.
6. Allow the user to review every proposed change.
7. Generate a polished LaTeX resume and compiled PDF.
8. Save the job application and the exact generated resume revision for future reference.

The MVP should be useful for real applications without trying to solve every possible resume-management problem.

---

## 2. Product Principles

### Local-first
The application runs on the user's Mac and is intended for personal use. Resume data, application history, generated files, and configuration remain local.

### Structured data is the source of truth
The LLM should not directly edit raw LaTeX. Resume facts are stored as structured records, the LLM proposes changes to that structured data, and deterministic code renders the result into LaTeX.

### The master resume is not permanent or frozen
The user's resume content changes over time. The system therefore uses:

- an editable **Content Library** containing verified facts and bullets;
- editable **Base Resumes** that reference content from the library;
- immutable **Application Revisions** that preserve exactly what was generated or submitted for a specific job.

Updating the Content Library should affect future work only. It must not rewrite old application revisions.

### AI proposes; the user approves
The LLM can analyze, rank, select, reorder, and rewrite content, but it should never silently make a final application decision.

### Truthfulness is enforced structurally
The LLM may improve phrasing, but any claim in a generated resume must be traceable to stored user-provided information.

---

## 3. MVP User Flow

### Step 1 — Build the Content Library

The user enters or imports their resume information into structured sections such as:

- personal/contact information;
- education;
- work experience;
- research experience;
- projects;
- leadership/activities;
- awards and certifications;
- coursework;
- skills and technologies.

Each experience or project can contain multiple bullet points, including bullets that may not fit on the normal one-page base resume.

The purpose of the library is to contain more verified information than a single resume can display.

### Step 2 — Create a Base Resume

A Base Resume defines the normal starting point before job-specific optimization.

It stores:

- which Content Library entries are included;
- which bullets are included;
- section order;
- entry order;
- bullet order;
- skills ordering;
- template choice;
- layout constraints.

For the MVP, one template is sufficient.

The Base Resume remains editable. It is not a historical snapshot.

### Step 3 — Create a Job Application

The user creates an application record containing at minimum:

- company;
- position title;
- job description;
- optional job URL;
- optional notes.

The user chooses which Base Resume should be used as the starting point.

### Step 4 — Analyze the Job Description

The LLM returns structured analysis such as:

- major responsibilities;
- required skills;
- preferred skills;
- tools and technologies;
- domain knowledge;
- experience signals;
- important keywords and phrases;
- likely high-priority qualifications.

The analysis should distinguish between meaningful requirements and generic wording.

### Step 5 — Compare the Job Against the Resume

The application compares the job requirements against the Content Library and selected Base Resume.

Results should be separated into categories:

- already represented well;
- represented but weakly emphasized;
- relevant information exists in the Content Library but is absent from the Base Resume;
- requirement is not supported by known user information.

The last category is especially important. Unsupported requirements must not be automatically inserted.

### Step 6 — Resolve Potentially Missing Information

For requirements that might apply but are not already confirmed, the app presents them to the user.

Example:

> Job requires Docker. Docker is not currently found in verified resume data. Do you have meaningful Docker experience?

The user can:

- confirm the skill and add supporting context to the Content Library;
- reject it;
- leave it unresolved.

Only confirmed information becomes available for tailoring.

### Step 7 — Generate an Optimization Proposal

The LLM receives:

- the job analysis;
- the selected Base Resume;
- the relevant Content Library records;
- layout constraints;
- explicit rewrite rules.

It returns a structured proposal containing:

- selected experiences/projects;
- selected bullets;
- proposed bullet rewrites;
- proposed ordering changes;
- proposed skills ordering;
- additions pulled from verified Content Library information;
- removals from the base resume;
- warnings or unsupported job requirements.

The output should reference stable IDs from the Content Library rather than returning an entirely disconnected resume.

### Step 8 — Review Changes

The UI shows the original and proposed resume information side by side.

For each change, the user should be able to see:

- original text;
- proposed text;
- source record;
- reason for the change;
- whether the change is selection, removal, reorder, or rewrite.

The user can accept or reject changes before generation.

### Step 9 — Generate Resume Files

After approval:

1. The accepted structured resume data is validated.
2. Jinja2 renders the resume into a LaTeX template.
3. `latexmk` compiles the `.tex` file.
4. The generated PDF is shown in the application.
5. The `.tex` and `.pdf` files are saved with the revision.

### Step 10 — Save an Immutable Revision

The generated resume becomes a revision associated with the application.

The revision preserves:

- exact selected content;
- exact bullet wording;
- exact ordering;
- source Content Library version or timestamps;
- generated LaTeX;
- generated PDF;
- creation timestamp;
- page count;
- status such as draft or submitted.

Old revisions are never automatically rewritten after Content Library changes.

---

## 4. Core Data Model

The MVP does not need an overly complicated schema, but the important entities should be separate.

### ContentItem

Represents one major resume item.

Examples:

- Artia Solutions experience;
- research role;
- software project;
- education record.

Suggested fields:

```text
id
type
title
organization
location
start_date
end_date
summary
tags[]
is_archived
created_at
updated_at
```

### Bullet

Represents a verified statement associated with a ContentItem.

Suggested fields:

```text
id
content_item_id
text
tags[]
supporting_facts[]
is_locked
is_preferred
created_at
updated_at
```

`supporting_facts` can contain metrics, technologies, scope, or context that may safely be used during rewriting.

### Skill

Suggested fields:

```text
id
name
category
aliases[]
notes
verified
```

### BaseResume

Suggested fields:

```text
id
name
template_id
section_order[]
layout_settings
created_at
updated_at
```

### BaseResumeEntry

Defines the selected content and ordering for a Base Resume.

```text
base_resume_id
content_item_id
selected_bullet_ids[]
entry_order
```

### Application

```text
id
company
position
job_url
job_description
notes
status
base_resume_id
created_at
updated_at
```

### JobAnalysis

```text
id
application_id
requirements[]
keywords[]
technologies[]
responsibilities[]
preferred_qualifications[]
created_at
```

### ResumeRevision

```text
id
application_id
revision_number
resume_json
latex_path
pdf_path
page_count
status
created_at
```

For the MVP, `resume_json` can store the complete resolved snapshot rather than fully normalizing every revision into relational tables.

---

## 5. LLM Responsibilities

The LLM should be used only where language understanding or contextual ranking is valuable.

### Appropriate LLM tasks

- parse a job description;
- identify important requirements;
- compare job requirements against known resume information;
- rank resume content by relevance;
- propose truthful bullet rewrites;
- reorder skills and bullets;
- explain why a change was suggested;
- flag unsupported job requirements.

### Tasks the LLM should not perform

The LLM should not:

- directly modify the SQLite database;
- directly edit LaTeX;
- run arbitrary shell commands;
- decide that an unsupported skill belongs to the user;
- invent metrics;
- invent technologies;
- invent responsibilities;
- invent employment or project history;
- approve its own output.

### Output format

Every AI operation should return strict JSON matching a known schema.

Example optimization response:

```json
{
  "selected_entries": [],
  "bullet_changes": [],
  "skill_order": [],
  "section_order": [],
  "warnings": []
}
```

The backend validates this response before the user sees or applies it.

---

## 6. Validation Rules

Before a proposed resume can be saved or rendered:

- every referenced Content Library ID must exist;
- every selected experience must be a verified record;
- rewrites must preserve the factual meaning of their source bullet;
- newly mentioned technologies must exist in the supporting source data;
- numerical claims must be supported by stored facts;
- unsupported job requirements must remain warnings rather than resume claims;
- required fields must be present;
- section and bullet limits must be respected;
- generated LaTeX must compile successfully.

The application should reject malformed LLM output instead of trying to guess what the model intended.

---

## 7. MVP Screens

### Dashboard

Shows:

- recent applications;
- recent generated resumes;
- shortcut to create an application;
- shortcut to edit resume data.

### Content Library

Sections for:

- experience;
- projects;
- education;
- skills;
- research;
- leadership/awards as needed.

Required actions:

- create;
- edit;
- archive;
- reorder bullets;
- mark a bullet preferred;
- lock a bullet against AI rewriting.

### Base Resumes

Functions:

- create/edit a base resume;
- select entries and bullets;
- reorder content;
- preview the current resume.

For the first MVP, supporting one primary Base Resume is acceptable even if the database structure permits more.

### New Application

Inputs:

- company;
- role;
- job URL;
- pasted job description;
- selected Base Resume.

### Application Analysis

Shows:

- extracted requirements;
- keywords;
- matched resume evidence;
- weakly represented requirements;
- potentially missing skills;
- unsupported requirements.

### Optimization Review

Shows:

- current content;
- proposed content;
- reason for each proposed change;
- accept/reject controls.

### Resume Preview / Revisions

Shows:

- PDF preview;
- revision history;
- generated `.tex`;
- generated `.pdf`;
- submitted/draft status.

---

## 8. Technical Architecture

### Frontend

Recommended:

- React;
- TypeScript;
- Vite;
- a lightweight component library or custom CSS.

The frontend only needs to communicate with the local backend.

### Backend

Recommended:

- Python;
- FastAPI;
- Pydantic;
- SQLAlchemy.

Responsibilities:

- CRUD operations;
- LLM orchestration;
- schema validation;
- optimization logic;
- version creation;
- template rendering;
- PDF compilation;
- file management.

### Database

SQLite.

This is sufficient because the application is:

- single-user;
- local;
- low-concurrency;
- structured but not extremely large.

### AI Integration

Primary personal-use integration:

```text
Local backend
    ↓
Local Codex SDK / app-server
    ↓
ChatGPT-authenticated Codex account
```

The AI layer should be wrapped behind an internal interface so a normal model API can be substituted later without changing the rest of the application.

### Resume Generation

```text
Validated Resume JSON
        ↓
Jinja2
        ↓
LaTeX template
        ↓
.tex file
        ↓
latexmk
        ↓
PDF
```

---

## 9. File Storage

A straightforward local layout is sufficient:

```text
data/
  app.db

generated/
  applications/
    <application-id>/
      revision-001/
        resume.json
        resume.tex
        resume.pdf
      revision-002/
        resume.json
        resume.tex
        resume.pdf
```

The database stores metadata and paths. The generated artifacts remain ordinary local files.

---

## 10. Suggested Repository Structure

```text
resume-builder/
  frontend/
    src/
      components/
      pages/
      api/
      types/

  backend/
    app/
      api/
      models/
      schemas/
      services/
        llm/
        optimizer/
        renderer/
        validation/
      templates/
        resume.tex.j2
      db/
      main.py

  data/
  generated/
  tests/
  README.md
```

---

## 11. MVP Scope

### Required

- editable Content Library;
- editable Base Resume;
- create application from pasted job description;
- structured job analysis;
- job/resume comparison;
- user confirmation for unsupported or potentially missing skills;
- AI optimization proposal;
- side-by-side review;
- deterministic LaTeX rendering;
- PDF compilation;
- application history;
- immutable resume revisions;
- basic validation against hallucinated claims.

### Explicitly Deferred

The MVP does not need:

- cloud hosting;
- authentication;
- multiple users;
- team collaboration;
- mobile support;
- browser extension;
- automatic LinkedIn import;
- job-board scraping;
- automatic submission;
- DOCX export;
- multiple sophisticated templates;
- ATS scoring claims;
- analytics dashboards;
- cover-letter generation;
- email generation;
- real-time job discovery.

These can be added only after the resume-tailoring loop works reliably.

---

## 12. MVP Implementation Order

### Phase 1 — Data and manual resume generation

Build:

- SQLite models;
- Content Library CRUD;
- Base Resume CRUD;
- Jinja2 LaTeX template;
- PDF generation.

Success condition:

A user can manually define resume content, select it into a Base Resume, and produce a valid PDF without any AI.

### Phase 2 — Applications and job analysis

Build:

- Application entity;
- job description input;
- LLM job analysis;
- structured analysis UI.

Success condition:

A job description can be converted into validated structured requirements.

### Phase 3 — Resume matching

Build:

- requirement-to-content matching;
- coverage categories;
- missing-information confirmation workflow.

Success condition:

The app can explain what the current resume covers and what it does not.

### Phase 4 — AI optimization

Build:

- optimization prompt;
- strict response schema;
- change proposal UI;
- accept/reject workflow;
- factual validation.

Success condition:

The AI can produce a useful tailored resume proposal without modifying data automatically.

### Phase 5 — Revisions and application history

Build:

- immutable ResumeRevision records;
- revision file directories;
- PDF preview;
- draft/submitted state.

Success condition:

Every generated resume can later be recovered exactly as it existed at generation time.

---

## 13. MVP Completion Criteria

The MVP is complete when the following workflow works reliably:

```text
Edit verified resume information
        ↓
Choose/edit Base Resume
        ↓
Create job application
        ↓
Paste job description
        ↓
Analyze requirements
        ↓
Resolve questionable missing skills
        ↓
Generate tailoring proposal
        ↓
Review and approve changes
        ↓
Generate LaTeX + PDF
        ↓
Save immutable application revision
```

At that point, the application solves the core problem and is suitable for actual personal internship/job applications.
