# Resume Builder Backend

Local-first resume intelligence and tailoring backend. It stores verified
resume content, base resumes, applications, analyses, proposals, and immutable
revision snapshots in SQLite.

## Codex provider

AI analysis and proposal generation use the locally authenticated Codex CLI;
the application does not require or store an OpenAI API key. The configured
defaults are model `gpt-5.6-luna`, low reasoning, and the normal/default
service tier. Sign in to Codex on the machine running the API before invoking
the optimization endpoints. Provider calls are read-only, scoped to the
selected application, and persisted as auditable optimization runs; failures
return `503` without creating partial analysis or proposal records.

The provider can be replaced with an injected fake in tests, so the test suite
does not make network calls or depend on a Codex session.

Approved proposals are applied only when generation receives their explicit
ID. A proposal must belong to the application, remain current with its source
fingerprint, and pass the evidence and ownership checks again before rendering:

```http
POST /applications/{application_id}/generate
Content-Type: application/json

{"proposal_id": 123}
```

Generation materializes an immutable tailored snapshot; it never edits the
verified content library or base resume records.

Frontend development is intentionally deferred. The current project scope is
the backend data model, API contracts, validation, matching, revision history,
and rendering every resume through the canonical baseline LaTeX template to
PDF. The supplied baseline resume was a one-time curated initialization; a
general PDF, DOCX, or pasted-text ingestion pipeline is out of scope.

## Run the frontend and API

Install the frontend dependencies and create the backend virtual environment
once:

```bash
npm install
python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements.txt
```

Then start both services, with combined and labeled logs:

```bash
npm run dev
```

The frontend is available at `http://127.0.0.1:5173` and the API at
`http://127.0.0.1:8000`. Press `Ctrl+C` to stop both services.

To run either service separately:

```bash
npm run dev:frontend
npm run dev:backend
```

## Run the API directly

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

The API is available at `http://127.0.0.1:8000`. Interactive OpenAPI
documentation is available at `http://127.0.0.1:8000/docs`.

## Run tests

From the repository root:

```bash
backend/.venv/bin/python -m pytest -q
```

## Seed checkpoint data

The checkpoint importer replaces the local database contents with the resumes
and application data stored under `checkpoints/`:

```bash
backend/.venv/bin/python -m backend.seed_checkpoints
```

To non-destructively backfill contact metadata and mark Shiven's verified
checkpoint bullets as evidence-backed and rewriteable:

```bash
backend/.venv/bin/python -m backend.seed_checkpoints --migrate-verified
```

## Generated artifacts

Generated application artifacts belong under
`generated/applications/<application-id>/revision-<number>/`.

The LaTeX renderer is available as
`app.services.renderer.ResumeRenderer`. PDF compilation requires a local
`latexmk` installation, and page-count extraction uses `pdfinfo` when present.
