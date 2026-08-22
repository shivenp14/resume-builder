# Resume Builder Backend

Local-first resume intelligence and tailoring backend. It stores verified
resume content, base resumes, applications, analyses, proposals, and immutable
revision snapshots in SQLite.

Frontend development is intentionally deferred. The current project scope is
the backend data model, API contracts, validation, matching, revision history,
and rendering every resume through the canonical baseline LaTeX template to
PDF. The supplied baseline resume was a one-time curated initialization; a
general PDF, DOCX, or pasted-text ingestion pipeline is out of scope.

## Run the API

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
backend/.venv/bin/python backend/seed_checkpoints.py
```

## Generated artifacts

Generated application artifacts belong under
`generated/applications/<application-id>/revision-<number>/`.

The LaTeX renderer is available as
`app.services.renderer.ResumeRenderer`. PDF compilation requires a local
`latexmk` installation, and page-count extraction uses `pdfinfo` when present.
