# Backend API feature reference

> Read [`backend/app/main.py`](../backend/app/main.py) for exact request and response schemas.

## Overall flow

```text
Profiles + verified skills + source items + bullets
  → base resume
  → application
  → analyze job description
  → requirements, matching, and evidence
  → missing-confirmation resolution (when needed)
  → generate and approve a proposal
  → generate immutable PDF/LaTeX revision
  → submit one exact revision
```

Source items, bullets, skills, profiles, and base resumes are editable. Proposals and generated revisions do not overwrite them.

## Service health

| Endpoint | What it does |
| --- | --- |
| `GET /health` | Returns service health. |

## Backups

| Endpoint | What it does |
| --- | --- |
| `POST /backups` | Creates a local backup archive containing database and generated artifacts. Returns metadata. |
| `GET /backups` | Lists backup metadata. |

There is no API for in-app restore or backup download.

## Source items

| Endpoint | What it does |
| --- | --- |
| `POST /content-items` | Creates a source item. |
| `GET /content-items` | Lists active source items. Supports `include_archived` and `archived` query parameters. |
| `GET /content-items/archived` | Lists archived source items. `/content-items/archive` is an alias. |
| `GET /content-items/:id` | Gets one source item. |
| `PATCH /content-items/:id` | Updates a source item. |
| `DELETE /content-items/:id` | Archives a source item. |
| `POST /content-items/:id/restore` | Restores an archived source item. |
| `POST /content-items/:id/duplicate` | Duplicates a source item, including bullets and skill links. Optional body: `{ title }`. |
| `GET /content-items/:id/versions` | Lists immutable item history. `/history` is an alias. |
| `GET /content-items/:id/versions/:versionNumber` | Gets one item history version. |

## Bullets

| Endpoint | What it does |
| --- | --- |
| `POST /content-items/:id/bullets` | Adds a bullet to a source item. |
| `GET /content-items/:id/bullets` | Lists a source item’s bullets. |
| `PATCH /bullets/:id` | Replaces a bullet’s editable fields. |
| `DELETE /bullets/:id` | Deletes a bullet unless it is selected by a base-resume entry. |
| `GET /bullets/:id/versions` | Lists immutable bullet history. `/history` is an alias. |
| `GET /bullets/:id/versions/:versionNumber` | Gets one bullet history version. |

## Skills and source relationships

| Endpoint | What it does |
| --- | --- |
| `POST /skills` | Creates a canonical skill. |
| `GET /skills` | Lists skills. |
| `GET /skills/:id` | Gets one skill. |
| `PATCH /skills/:id` | Updates a skill. |
| `POST /skills/:id/aliases` | Adds one alias. Body: `{ alias }`. |
| `DELETE /skills/:id/aliases/:alias` | Removes one alias. |
| `POST /content-items/:id/skills` | Links a skill to a source item. |
| `GET /content-items/:id/skills` | Lists a source item’s skill links. |
| `DELETE /content-items/:id/skills/:skillId` | Removes a source-item skill link. |
| `GET /skills/:id/content-items` | Lists source items linked to a skill. |

Only verified skills contribute automatically to matching and generated snapshots.

## Profiles

| Endpoint | What it does |
| --- | --- |
| `POST /personal-information` | Creates reusable personal/contact information. `/personal-info` is an alias. |
| `GET /personal-information` | Lists profiles. |
| `GET /personal-information/:id` | Gets one profile. |
| `PATCH /personal-information/:id` | Updates a profile. |
| `DELETE /personal-information/:id` | Deletes a profile unless a base resume uses it. |

## Base resumes

| Endpoint | What it does |
| --- | --- |
| `POST /base-resumes` | Creates a base resume. |
| `GET /base-resumes` | Lists base resumes. |
| `GET /base-resumes/:id` | Gets one base resume. |
| `PATCH /base-resumes/:id` | Updates a base resume. |
| `POST /base-resumes/:id/entries` | Adds an ordered source-item entry with exact selected bullet IDs. |
| `GET /base-resumes/:id/entries` | Lists base-resume entries. |
| `PATCH /base-entries/:id` | Updates an entry’s item, selected bullets, or order. |
| `DELETE /base-entries/:id` | Removes an entry. |

Archived source items cannot be newly added to a base resume.

## Applications and lifecycle

| Endpoint | What it does |
| --- | --- |
| `POST /applications` | Creates an application from company, position, job description, and base resume. |
| `GET /applications` | Lists applications. Supports `status`, `q`, `limit`, and `offset`. |
| `GET /applications/:id` | Gets one application. |
| `PATCH /applications/:id` | Updates application details. |
| `POST /applications/:id/status` | Changes lifecycle status. Body: `{ status, reason? }`. |
| `GET /applications/:id/status-history` | Lists status history. `/history` is an alias. |

Statuses are `draft`, `applied`, `interviewing`, `offer`, `rejected`, and `withdrawn`. `rejected` and `withdrawn` are terminal. Submission sets the exact submitted revision and handles `applied_at`.

## Job analysis, requirements, and evidence

| Endpoint | What it does |
| --- | --- |
| `POST /applications/:id/analyze` | Uses the saved job description to create an analysis and durable requirements. Optional body: `{ idempotency_key }`. |
| `GET /applications/:id/analysis` | Gets the latest analysis. |
| `GET /applications/:id/optimization-runs` | Lists analysis/proposal generation runs. |
| `GET /optimization-runs/:id` | Gets one optimization run. |
| `GET /applications/:id/requirements` | Lists active requirements for an application. |
| `GET /applications/:id/requirements/:requirementId` | Gets one application-owned requirement. |
| `GET /requirements/:requirementId` | Gets one requirement directly. |
| `POST /applications/:id/requirements/:requirementId/evidence` | Links one source item, bullet, or verified skill as evidence. |
| `GET /applications/:id/requirements/:requirementId/evidence` | Lists evidence links for a requirement. |
| `DELETE /requirement-evidence/:linkId` | Removes an evidence link. |
| `GET /applications/:id/comparison` | Recomputes matching against current verified source data. Returns well represented, weakly represented, library only, and unsupported requirements. |

Analysis can be run again. Stable requirements retain their IDs when unchanged. The API has per-requirement scores, but no canonical overall coverage percentage.

## Missing confirmations

| Endpoint | What it does |
| --- | --- |
| `POST /applications/:id/missing-confirmations` | Creates missing-confirmation records for unsupported requirements. |
| `POST /applications/:id/confirmations` | Compatibility endpoint to create or decide a confirmation. |
| `GET /applications/:id/missing-confirmations` | Lists confirmations for an application. |
| `PATCH /missing-confirmations/:id` | Sets a confirmation to confirmed, rejected, or unresolved. |
| `POST /missing-confirmations/:id/materialize` | Creates a verified skill or bullet from confirmed user-authored source data. |
| `POST /applications/:id/missing-confirmations/:confirmationId/materialize` | Application-scoped materialization endpoint. |
| `GET /missing-confirmations/:id/materializations` | Lists records materialized from a confirmation. |
| `POST /applications/:id/confirmations/:confirmationId/materialize` | Compatibility alias for application-scoped materialization. |

A confirmation must be confirmed before it can be materialized. Materialization is for user-authored source facts, not unreviewed generated text.

## Proposals

| Endpoint | What it does |
| --- | --- |
| `POST /applications/:id/proposals/generate` | Generates and stores a pending proposal from completed analysis. Optional body: `{ idempotency_key }`. |
| `GET /applications/:id/proposals` | Lists proposals for an application. |
| `POST /applications/:id/proposals` | Stores a client-supplied proposal payload after backend validation. |
| `POST /proposals/:id/approve` | Approves a proposal after validating source freshness and snapshot validity. |
| `POST /proposals/:id/decision` | Sets proposal status to pending, approved, or rejected. |

Proposal validation checks source freshness, selected entries/bullets, evidence, locks, and required source entries. A stale proposal returns a conflict and must be regenerated.

## Snapshots, revisions, generation, and submission

| Endpoint | What it does |
| --- | --- |
| `GET /applications/:id/snapshot` | Returns the current canonical resume snapshot. |
| `POST /applications/:id/revisions` | Creates an immutable canonical revision from a snapshot that exactly matches current source state. |
| `GET /applications/:id/revisions` | Lists revisions for an application. |
| `GET /revisions/:id` | Gets one revision. |
| `GET /applications/:id/revisions/compare?from_revision_id=&to_revision_id=` | Returns a semantic comparison between two revisions. `before_revision_id`/`after_revision_id` are accepted aliases. |
| `GET /applications/:id/revisions/:fromRevisionId/compare/:toRevisionId` | Path-based revision comparison. |
| `GET /revisions/:fromRevisionId/compare/:toRevisionId` | Compares two revisions when their shared application is implicit. |
| `POST /applications/:id/generate` | Generates PDF and LaTeX from one approved proposal. Body: `{ proposal_id }`. Returns revision data plus `pdf_url` and `latex_url`. |
| `POST /applications/:id/submit` | Submits one exact generated revision. Body: `{ revision_id, submitted_at? }`. `/submissions` and `/submitted-revision` are aliases. |
| `GET /applications/:id/submitted-revision` | Gets the exact revision that was submitted. |
| `POST /render` | Validates a supplied snapshot and returns LaTeX source. |

Generation requires an approved, current proposal from the same application. Failed revisions cannot be submitted. Submission verifies the generated artifacts and PDF page count before recording the revision.

## Typical endpoint flows

### Build reusable source data

```text
POST /personal-information
POST /skills
POST /content-items
POST /content-items/:id/bullets
POST /content-items/:id/skills
POST /base-resumes
POST /base-resumes/:id/entries
```

### Create and analyze an application

```text
GET  /base-resumes
POST /applications
POST /applications/:id/analyze
GET  /applications/:id/requirements
GET  /applications/:id/comparison
```

### Add evidence or resolve a gap

```text
POST /applications/:id/requirements/:requirementId/evidence

# Or, for unsupported requirements:
POST  /applications/:id/missing-confirmations
PATCH /missing-confirmations/:id
POST  /missing-confirmations/:id/materialize
GET   /applications/:id/comparison
```

### Produce and submit a tailored resume

```text
POST /applications/:id/proposals/generate
GET  /applications/:id/proposals
POST /proposals/:proposalId/approve
POST /applications/:id/generate
GET  /applications/:id/revisions
POST /applications/:id/submit
GET  /applications/:id/submitted-revision
```
