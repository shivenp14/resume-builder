# Project Progress

Updated: September 9, 2026

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
- [x] Backend test suite: 100/100 passing

## Remaining

- [x] Add personal-information records
- [x] Add content and bullet version history
- [x] Add claim provenance and supporting evidence
- [x] Add archive restore and duplication
- [x] Add normalized skill relationships and aliases
- [x] Expand application metadata and status history
- [x] Add submitted-revision tracking
- [x] Add structured job requirements and evidence links
- [x] Improve matching classifications and scoring
- [x] Turn confirmed missing information into verified source records
- [x] Add an `LLMProvider` interface
- [x] Add versioned AI prompts and structured output schemas
- [x] Generate evidence-grounded optimization proposals
- [x] Store optimization runs and per-change decisions
- [x] Add revision comparison
- [x] Add backup and export

> Editable verified source data feeds future resumes; immutable revisions preserve past resumes.

## Frontend — Done

- [x] Vite + React application scaffold with production build (`npm run build`)
- [x] Responsive Gemini-mockup-inspired resume notebook visual system: 250px sidebar, paper previews, forest/sage/warm tokens, Fraunces/DM Sans/DM Mono typography, reduced-motion support, and keyboard focus styling
- [x] Application shell and responsive mobile navigation
- [x] Route-level screens for home, applications, new application, application overview, coverage, proposals, preview, revisions, revision comparison, library, source editor, skills, profiles, base resumes, composer, and data safety
- [x] Shared interface primitives for cards, status/provenance cues, requirements, evidence chips, paper previews, workflow steps, revision cards, drawers, and empty states
- [x] API client helper with FastAPI error normalization groundwork
- [x] Live `GET /applications` hydration when the local API returns application records; curated local fixtures remain available for the empty-development state
- [x] Frontend documentation consolidated under `docs/`; root `index.html` remains the Vite application entrypoint
- [x] Independent frontend review completed; visual hierarchy, tokens, sidebar, paper preview, and calm product tone were confirmed as aligned with the supplied mockup

## Frontend — Remaining

- [ ] Replace fixture/local-state mutations with complete server-backed query and mutation flows for all planned endpoints
- [ ] Wire application create/edit/status changes, including full metadata, validation, field errors, loading states, and server pagination/filtering
- [ ] Wire source library, bullets, archival/restore, skills, profiles, and base-resume composer CRUD to the API
- [ ] Implement real analysis, requirements, comparison, evidence-link, and missing-confirmation materialization flows with query invalidation and stale-source recovery
- [ ] Implement proposal generation, approval/rejection, source-freshness conflicts, and locked-bullet behavior against the backend contracts
- [ ] Require an approved explicit `proposal_id` for server generation; use returned PDF/LaTeX URLs, then wire exact-revision submission and submitted-revision loading
- [ ] Load and render real snapshots, revision history, semantic comparison, backups, and API empty/error/not-found states
- [ ] Strengthen accessibility: dialog semantics/focus management for drawers, mobile-nav `aria-expanded`/inert handling, and native required form controls
- [ ] Complete browser-based desktop and mobile visual QA when an automation-capable preview is available

> The current frontend is a polished route-and-interaction prototype with live application-list hydration. It must not imply that locally simulated analysis, proposal, generation, or submission controls have persisted changes until their API mutations are wired.
