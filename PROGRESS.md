# Backend Progress

Updated: August 22, 2026

## Done

- [x] FastAPI backend
- [x] SQLite database with SQLAlchemy
- [x] Content items and bullets
- [x] Skills
- [x] Base resumes and selected entries
- [x] Applications and job descriptions
- [x] Codex-backed job analysis with local CLI authentication (GPT-5.6 Luna, low reasoning, default service tier)
- [x] Resume-to-job comparison
- [x] Missing-information confirmations
- [x] Optimization proposal storage and decisions
- [x] Offline provider-injection tests for model configuration, failures, audit runs, and proposal validation
- [x] Locked-bullet and source-ownership validation
- [x] Basic unsupported-number validation
- [x] Immutable revision snapshots
- [x] LaTeX rendering and escaping
- [x] PDF generation with `latexmk`
- [x] Canonical baseline LaTeX template as the rendering standard
- [x] Generated artifact storage and static routes
- [x] Checkpoint resume and application importer
- [x] OpenAPI documentation at `/docs`
- [x] Backend test suite: 14/14 passing

## Remaining

- [ ] Finalize and document stable API response schemas
- [ ] Add personal-information records
- [ ] Add content and bullet version history
- [ ] Add claim provenance and supporting evidence
- [ ] Add archive restore and duplication
- [ ] Add normalized skill relationships and aliases
- [ ] Expand application metadata and status history
- [ ] Add application update, filtering, search, and pagination
- [ ] Add submitted-revision tracking
- [ ] Add structured job requirements and evidence links
- [ ] Improve matching classifications and scoring
- [ ] Turn confirmed missing information into verified source records
- [x] Add an `LLMProvider` interface
- [x] Add versioned AI prompts and structured output schemas
- [x] Generate evidence-grounded optimization proposals
- [x] Store optimization runs and per-change decisions
- [ ] Add revision comparison
- [ ] Finalize baseline-template layout constraints and rendering parity
- [ ] Improve LaTeX failure diagnostics
- [ ] Add deterministic rendering tests
- [ ] Add backup and export

> Editable verified source data feeds future resumes; immutable revisions preserve past resumes.
