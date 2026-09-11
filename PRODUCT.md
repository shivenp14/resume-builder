# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

delegated: React and Vite, selected for the planned multi-route application workspace and client-side server-state handling.

## Users

Job seekers managing several applications who need to preserve verified reusable resume content while tailoring immutable application-specific revisions.

## Product Purpose

Provide a local-first resume workspace that helps a user move from a verified source library to evidence-backed application proposals, generated revisions, and exact submissions without overwriting the source material.

## Positioning

The product makes the provenance boundary explicit: source records remain editable and verified; approval only materializes a job-specific immutable revision.

## Operating Context

The user works through applications, requirements, evidence, proposal review, generated artifacts, revisions, and local backups. The UI consumes the FastAPI service in `backend/app/main.py`.

## Capabilities and Constraints

- Analysis and proposals never modify source records.
- A generated revision is required for submission, and submissions always reference one exact revision.
- Evidence, locks, archive state, source freshness, identifiers, and history are visible product concepts.
- The frontend must use server-returned artifact URLs and retain forms locally until save.

## Brand Commitments

The supplied Gemini mockup is binding visual authority: a calm resume-notebook voice, precise reassurance, and the stated forest, sage, warm, and paper palette. Avoid implying an automated change has already happened.

## Evidence on Hand

- `docs/FRONTEND_APPLICATION_PLAN.md` defines routes, behavior, and product copy.
- `mockups/gemini-mockup/index.html` defines visual authority.
- `backend/app/main.py` and `backend/tests/` define API behavior.

## Product Principles

- Preserve the difference between reusable truth and tailored output.
- Make the next valid action clear without competing calls to action.
- Keep provenance and auditability understandable at a glance.
- Use calm, exact language for uncertain and irreversible states.

## Accessibility & Inclusion

Keyboard-operable controls, readable contrast, semantic structure, reduced motion support, and responsive navigation are required by the implementation plan.
