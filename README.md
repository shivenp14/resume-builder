# Resume Builder Backend

Local-first resume intelligence and tailoring backend. It stores verified
resume content, base resumes, applications, analyses, proposals, and immutable
revision snapshots in SQLite.

## Codex provider

AI analysis and proposal generation use the locally authenticated Codex CLI;
the application does not require or store an OpenAI API key. The configured
defaults are model `gpt-6-luna`, low reasoning, and the normal/default
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

The React workspace includes application, source-library, skill, profile,
base-resume, revision, and backup flows. Resume rendering uses the canonical
baseline LaTeX template. General PDF, DOCX, or pasted-text ingestion remains
out of scope.

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

## Browser flow tests

Install the test browser once, then run the desktop and mobile suites:

```bash
npx playwright install chromium
npm run test:ui
```

Tests start their own Vite server on port 5174 and API on port 8011. Each run
uses a temporary workspace and a deterministic AI provider; saved local records
and Codex sessions are untouched. PDF tests use the real LaTeX renderer and
require `latexmk`. To watch the tests, run `npm run test:ui:headed`.

Coverage includes application creation/search/filtering, analysis failure and
retry, source/bullet editing, archive/restore, skills and aliases, profiles,
base-resume entries, confirmation materialization, proposals, PDF/LaTeX links,
exact-revision submission, comparison, backups, validation errors, and mobile
keyboard navigation with reduced motion. Failure traces and screenshots are
saved in `test-results/`.

The API accepts `RESUME_WORKSPACE_ROOT` to isolate database and artifact storage;
without it, storage remains in the repository as before.

On each backend process start, an existing non-empty database is snapshotted
before schema creation or startup migrations run. The validated SQLite and
generated-artifact archive is stored locally under `data/backups/`; startup
stops with an error if that backup cannot be created. A brand-new or empty
database is initialized without a backup. Development reloads start a new
process and check for changes before running migrations. If the WAL-safe
database snapshot and generated artifacts match the newest valid compatible
backup, startup logs that it skipped the redundant archive. Manual backup
requests always create a new archive. Backups are limited to the 10 newest
valid archives and 512 MiB total. Retention runs after a new archive is
validated and published, or after an unchanged startup check; it preserves the
newest archive even if that archive alone exceeds the size limit. Unknown files,
malformed archives, and symlinks are left untouched. If pruning fails, startup
stops after keeping the newly published backup. Backups are excluded from Git
and are local recovery copies, so copy them to a separate location if you need
protection from disk loss. The backend console logs when the automatic backup
starts, succeeds (including its path and retention totals), or fails.

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
