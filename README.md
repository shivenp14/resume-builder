# Resume Builder MVP

Local-first resume tailoring app. The backend stores verified resume content,
base resumes, applications, proposals, and immutable revisions in SQLite.

## Run the API

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

The API is available at `http://127.0.0.1:8000`; interactive documentation is
at `/docs`. Run tests from the repository root with:

```bash
PYTHONPATH=. pytest backend/tests
```

## Run the web app

In a second terminal from the repository root:

```bash
npm install
npm run dev
```

The current UI is served by Vite at `http://localhost:5173`. Backend API
routes are available under both `/` and `/api/`; when wiring live frontend
requests, point `src/api.ts` at `http://127.0.0.1:8000/api` (or add a Vite
proxy).

Generated application artifacts belong under `generated/applications/<id>/`.
The LaTeX renderer is available as `app.services.renderer.ResumeRenderer` and
requires a local `latexmk` installation for PDF compilation.
