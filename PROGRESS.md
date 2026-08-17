# Resume Builder MVP Progress

Status snapshot: August 17, 2026

## MVP status

The local-first MVP workflow is implemented across the FastAPI/SQLite backend and React/Vite frontend. The application supports the core trustworthy tailoring loop: maintain source content and a base resume, create and analyze an application, inspect requirement coverage, resolve missing information, review proposal decisions, generate an application-scoped resume, and retain immutable revision artifacts.

The implementation remains intentionally local and single-user. Job analysis is deterministic and rule-based; an LLM provider is deferred from this MVP.

## Completed

### Backend and persistence

- SQLite persistence for content items, bullets, skills, base resumes/entries, applications, job analyses, proposals, missing-information confirmations, and revisions.
- CRUD/archive APIs for source content and bullets, skill APIs, base-resume and entry-selection APIs, application APIs, and revision history APIs.
- Application-scoped analysis, comparison, missing-confirmation, proposal decision, snapshot, render, and generation endpoints.
- Stable content-item/bullet ownership checks, locked-bullet protection, unsupported numeric-claim checks, and structured snapshot/proposal validation.
- Revision numbers are application-scoped; generated revisions retain their own resolved JSON snapshot and are not changed when source content is edited.

### Generation and artifacts

- Deterministic Jinja2-to-LaTeX rendering with LaTeX escaping.
- Application-scoped generation writes `generated/applications/<id>/revision-<n>/resume.json`, `resume.tex`, and `resume.pdf` when the local LaTeX toolchain is available.
- Revision records retain artifact paths, status, and page count when `pdfinfo` is available; generated artifacts are exposed through the backend static route.

### Frontend

- Responsive React/Vite workspace with dashboard, content library, base resume, applications, application detail, and revisions views.
- Live API-backed counts and application/content data, loading/error/empty states, and mobile navigation.
- Application detail flow displays analysis, coverage categories, missing-information decisions, proposal decisions, and generation results with PDF access.
- Connected workspace controls support creating source items and creating/analyzing applications.

### Tests and verification coverage

- `backend/tests/test_api.py` covers health, proposal safeguards, analysis, revision numbering/order, and LaTeX escaping.
- `backend/tests/test_mvp_flow.py` covers the end-to-end tailoring contract, comparison and confirmation, proposal approval, generation artifacts, ownership validation, malformed snapshots, and revision immutability.
- Frontend production build passes with `npm run build`.
- Python compilation checks pass for backend modules.

## Known limitations and environment-dependent checks

- The active environment used for this handoff does not provide the required `pytest`/FastAPI runtime, so the backend test suite could not be executed there. The tests and prior passing results remain in the repository as the contract for the implemented flow.
- PDF generation requires `latexmk`; page-count extraction additionally requires `pdfinfo`. If either tool is absent, generation reports the expected infrastructure limitation or leaves `page_count` unset.
- Analysis is deterministic keyword/sentence extraction rather than an LLM integration. Optimization proposals are persisted and reviewed through the API/UI, but there is no model provider or automatic optimizer.
- The app is local/single-user only. Authentication, cloud storage, collaboration, scraping, submission automation, ATS scoring, DOCX export, and cover-letter generation remain outside MVP scope.
- Editing surfaces are functional at the API level, while some advanced library/base-resume editing controls (bulk import, drag reorder, and full layout configuration) remain intentionally lightweight.

## Deferred follow-up

1. Add a model-provider interface and strict schemas for analysis and optimization.
2. Expand matching beyond keyword coverage and improve weak-representation scoring.
3. Add richer source/base-resume editing, ordering, import, and layout controls.
4. Add CI with pinned Python dependencies plus an environment containing `pytest`, FastAPI, `latexmk`, and `pdfinfo` for full automated verification.
