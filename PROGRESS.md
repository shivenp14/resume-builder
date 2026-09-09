# Backend Progress

Updated: September 7, 2026

## Done

- [x] FastAPI backend
- [x] SQLite database with SQLAlchemy
- [x] Content items and bullets
- [x] Skills
- [x] Base resumes and selected entries
- [x] Applications and job descriptions
- [x] Application lookup, update, status filtering, search, and pagination
- [x] Codex-backed job analysis with local CLI authentication (GPT-5.6 Luna, low reasoning, default service tier)
- [x] Resume-to-job comparison
- [x] Missing-information confirmations
- [x] Optimization proposal storage and decisions
- [x] Approved-proposal materialization with source fingerprints and provenance
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
- [x] Stable public Pydantic API response DTOs
- [x] SQLite foreign-key enforcement and additive integrity indexes
- [x] Baseline-template contact, education, project, skills, and activity rendering parity
- [x] Deterministic renderer checks, PDF page-count fallback, and renderer failure diagnostics
- [x] Checkpoint migration ownership, idempotence, and rollback tests
- [x] Backend test suite: 37/37 passing

## Remaining

- [ ] Add personal-information records
- [ ] Add content and bullet version history
- [x] Add claim provenance and supporting evidence
- [x] Add archive restore and duplication
- [x] Add normalized skill relationships and aliases
- [ ] Expand application metadata and status history
- [ ] Add submitted-revision tracking
- [ ] Add structured job requirements and evidence links
- [ ] Improve matching classifications and scoring
- [ ] Turn confirmed missing information into verified source records
- [x] Add an `LLMProvider` interface
- [x] Add versioned AI prompts and structured output schemas
- [x] Generate evidence-grounded optimization proposals
- [x] Store optimization runs and per-change decisions
- [ ] Add revision comparison
- [ ] Add backup and export

> Editable verified source data feeds future resumes; immutable revisions preserve past resumes.
