# Resume Builder frontend plan

> **Status:** implementation blueprint  
> **Audience:** frontend, backend, design, and QA agents  
> **Visual authority:** [`mockups/gemini-mockup/index.html`](mockups/gemini-mockup/index.html)  
> **Backend authority:** [`backend/app/main.py`](../backend/app/main.py), its tests, and [`backend-feature-guide.html`](backend-feature-guide.html)

## 1. Product contract — do not violate

The product is a local-first, provenance-first resume workspace. Its UI must make the boundary between these two lanes unmistakable:

```text
Editable, verified source library
profile → content items → bullets → skills → base resume
                                                ↓
Job-specific application workspace
job → analysis → requirements/evidence → proposal → immutable revision → submission
```

- **Analysis and generated proposals never modify source records.** They are suggestions until the user approves them.
- **Approval is not “apply to library.”** It authorizes materializing a job-specific, immutable revision.
- **A generated revision is the only resume that can be submitted.** Later source edits or revisions never replace the submitted revision.
- **Evidence, IDs, locks, source freshness, archive status, and history are product concepts—not implementation details.** Show them deliberately.
- **Prefer stable IDs as UI keys.** Never key requirements, bullets, evidence, or revisions by display text.

## 2. Visual direction

Extend the Gemini mockup’s calm “resume notebook” feel. This is an **Operate** interface: clear and deliberate, with warmth reserved for reassurance and attention—not decoration.

| Token | Value | Use |
| --- | --- | --- |
| Canvas | `#f7f8f5` | app background |
| Sidebar | `#f2f5f0` | persistent navigation rail |
| Forest | `#27423d` | primary actions, display emphasis |
| Ink | `#243733` | headings and high-emphasis text |
| Sage | `#7daa91` | verified/ready accents |
| Deep sage | `#5f927d` | links and subtle emphasis |
| Muted | `#75827c` | supporting copy and metadata |
| Rule | `#e4ebe4` | quiet borders |
| Warm | `#e7c7ac` | missing-information/attention accent |
| Warm panel | `#fbf5ef` | support and caution panels |

- **Type:** Fraunces for page titles, resume/document names, and rare large metrics; DM Sans for UI/body; DM Mono for dates, IDs, labels, and status metadata.
- **Shell:** 250px pale-sage sidebar, off-white main canvas, white cards, 18px radius, light rules, restrained shadows.
- **Language:** calm and exact: “Review suggestions,” “Source-backed,” “Needs your confirmation,” “This will create a tailored revision.” Never promise an AI change has already happened.
- **Color semantics:** sage = verified/ready/success; warm = missing/attention; neutral = draft/inactive; muted red only for destructive/failed states. Do not introduce a competing blue-heavy dashboard palette.
- **Responsive behavior:** preserve desktop information density; at tablet/mobile, sidebar becomes a drawer, thread strips stack, two-column workspaces become one column, and sticky action bars remain reachable.

### Shared visual building blocks

Build these primitives before individual screens:

- `AppShell`, `SidebarNav`, mobile `NavigationDrawer`, `PageHeader`
- `QuietCard`, `SoftPanel`, `PaperPreview`, `EmptyState`, `AsyncState`
- `StatusPill`, `ProvenanceBadge`, `EvidenceChip`, `SourceLink`, `LockBadge`
- `ThreadStrip` (current focus / next action / small metric), `WorkflowStepper`, `Timeline`
- `RequirementRow`, `CoverageSummary`, `BeforeAfterDiff`, `RevisionCard`
- `ConfirmDialog` and `FormDrawer`, with errors beside the affected field

## 3. Route map and ownership

| Route | Screen | Primary job |
| --- | --- | --- |
| `/home` | Home | resume work that needs attention |
| `/applications` | Applications | search, filter, and create applications |
| `/applications/new` | New application | capture job and select base resume |
| `/applications/:id` | Application overview | orient, show lifecycle, choose next step |
| `/applications/:id/coverage` | Analysis & coverage | analyze requirements, inspect matches/evidence, resolve gaps |
| `/applications/:id/proposal` | Proposal review | review and decide job-specific suggestions |
| `/applications/:id/preview` | Resume preview | inspect canonical snapshot and generated artifact |
| `/applications/:id/revisions` | Revision history | inspect immutable outputs and compare them |
| `/applications/:id/revisions/compare` | Revision comparison | semantic before/after review |
| `/library` | Source library | manage reusable verified content |
| `/library/archived` | Archive | restore or duplicate prior content |
| `/library/:itemId` | Content editor | edit item, bullets, relationships, and history |
| `/skills` | Skills | manage canonical skills and aliases |
| `/profiles` | Profiles | manage reusable personal information |
| `/resumes` | Base resumes | browse reusable resume compositions |
| `/resumes/:id` | Base-resume composer | choose source entries and exact bullets |
| `/settings/data` | Data safety | create/list local backups |

Keep application pages nested: proposals and revisions belong to one application and must never look like global source records.

## 4. Screens and feature connections

### Home — `/home`

Reuse the mockup’s composition directly: greeting/header, three-part “Today’s thread,” applications-in-motion list, latest paper-resume card, and a quiet safety note.

| Area | Data / rule |
| --- | --- |
| Applications in motion | `GET /applications` with a small page; show company, role, status, updated time, submitted-revision marker |
| Today’s thread | derive a single actionable next state: no analysis → analyze; unsupported requirements → resolve; pending proposal → review; approved proposal → generate; generated revision → submit |
| Recent resume | `GET /base-resumes`, then show the most recently used/available composition as `PaperPreview` |
| Safety note | link to revisions or data safety; explain that tailoring preserves the original |

Empty state: “Add your first application” with the only prominent CTA. If no dashboard aggregate endpoint exists, compose this view from small client queries initially; add an aggregate endpoint only if request fan-out becomes a measured performance issue.

### Applications — `/applications`, `/applications/new`

- List using `GET /applications?status=&q=&limit=&offset=`. Support server pagination, company/role search, and status filters.
- Use the mockup’s compact application rows/cards, not a dense enterprise table by default.
- Create/edit drawer maps the full application contract: company, role, job description, base resume, URL, source, location, employment type, salary, contact, deadline, follow-up, notes.
- Use `POST /applications`, `PATCH /applications/{id}`, and `POST /applications/{id}/status` for lifecycle changes. Show status-history context and inline explanation if a transition is rejected.
- Do **not** present `applied_at` as an ordinary editable form field; submission/lifecycle governs it.

### Application overview — `/applications/:id`

Load the application first, then lazy-load page sections: analysis, comparison, requirements, confirmations, proposals, revisions, status history, and submitted revision.

Layout order:

1. Company, role, job URL, source/location/deadline, current status, Edit details.
2. Workflow stepper: Base resume → Analysis → Coverage → Suggestions → Revision → Submitted.
3. A `ThreadStrip` with current focus, next action, and one useful metric (for example coverage count).
4. Requirement/coverage preview, latest proposal or revision, and activity timeline.

Use the current state to make one primary CTA visible. Secondary actions belong in the header/menu, not in a competing CTA cluster.

### Analysis, coverage, evidence, and confirmations — `/applications/:id/coverage`

| Interaction | Backend connection | UI contract |
| --- | --- | --- |
| Analyze / re-analyze | `POST /applications/{id}/analyze`; `GET .../analysis` | Explain that re-analysis creates a new audit record while stable requirements retain IDs when unchanged. |
| Requirements | `GET /applications/{id}/requirements` | Group by category and priority; each row includes requirement ID, source excerpt, status, score, reasons, and evidence count. |
| Matching | `GET /applications/{id}/comparison` | Use four visible groups: well represented, weakly represented, library only, unsupported. Library-only is useful but not equivalent to selected-base-resume evidence. |
| Evidence | `GET/POST /applications/{id}/requirements/{requirementId}/evidence`; `DELETE /requirement-evidence/{id}` | Requirement drawer lists bullet/content/skill evidence, excerpt, and note. Picker only permits owned, verified source-backed records. |
| Confirmations | `GET/POST /applications/{id}/missing-confirmations`; `PATCH /missing-confirmations/{id}` | Treat unresolved gaps as a dedicated “Needs your input” queue—not a generic error. |
| Materialize confirmation | `POST /missing-confirmations/{id}/materialize` | Wizard offers verified skill or verified bullet, describes that it enters the shared library, and shows resulting provenance. Disable duplicate submit while pending. |

After a confirmation materializes, invalidate source items, skills, requirements, comparison, snapshot, and proposals. A materialized confirmation cannot be quietly demoted; surface its source record and fingerprints instead.

### Proposal review — `/applications/:id/proposal`

This is a calm editorial review, not an AI chat.

- Normal generation: `POST /applications/{id}/proposals/generate`; list with `GET /applications/{id}/proposals`.
- Each suggestion is a `BeforeAfterDiff` with entry/bullet ID, requirement links, evidence chips, rationale, warnings, and lock state.
- Show proposal-level status (pending/approved/rejected) plus timestamp; place model/prompt/schema/run data under a compact “Run details” disclosure.
- Approve with `POST /proposals/{id}/approve`; reject/record a decision with `POST /proposals/{id}/decision`.
- A stale source is a blocking state: “Your source library changed. Regenerate suggestions.” Never silently re-run or permit a stale approval.
- Locked bullets must visibly be unavailable for rewrite. Preserve foundational entries that backend validation requires.

**Required copy:** “Approving these suggestions does not change your source library. It lets you create a tailored revision for this application.”

### Preview, generate, and submit — `/applications/:id/preview`

- Canonical preview: `GET /applications/{id}/snapshot`. Use this structured output for the pre-generation `PaperPreview`.
- Generate only with an approved explicit `proposal_id`: `POST /applications/{id}/generate`.
- Progress presentation: validating proposal → building snapshot → compiling PDF → validating artifact → saved revision. Keep the result/error state on screen.
- On success, use `pdf_url` and `latex_url` returned by the server. Do not reconstruct file paths client-side.
- Show revision number, page count, generated time, proposal/source provenance, PDF viewer/download, and optional LaTeX link.
- Submit an exact revision using `POST /applications/{id}/submit`; confirmation must name the revision number and timestamp. After success, load `GET /applications/{id}/submitted-revision` and label it “Submitted revision #N.”

Failed render revisions are never submit-ready; show diagnostics and a safe retry route.

### Revisions and semantic comparison — `/applications/:id/revisions`, `/compare`

- List via `GET /applications/{id}/revisions`; detail via `GET /revisions/{id}`.
- Use immutable `RevisionCard`s: revision number, status, page count, timestamp, proposal provenance, artifact availability, submitted marker.
- Compare via `GET /applications/{id}/revisions/compare?from_revision_id=&to_revision_id=`.
- Render semantic changes (added, removed, changed, reordered) across contact, entries, bullets, skills, and ordering. Do **not** lead with raw JSON diff.
- The comparison is ephemeral; do not assume or create a saved comparison record.

### Source library — `/library`, `/library/:itemId`, `/library/archived`

This is the verified-truth workspace. It must look intentionally different from the job-tailoring workbench: source actions are editable; revisions are not.

- Active content: `GET/POST /content-items`; archived: `GET /content-items/archived`.
- Editor: `GET/PATCH /content-items/{id}` plus bullets through `POST /content-items/{id}/bullets`, `PATCH/DELETE /bullets/{id}`.
- Show metadata, tags, bullets, supporting facts, preferred/locked state, skill links, and immutable version-history drawers.
- Archive via `DELETE /content-items/{id}`; restore/duplicate with `POST .../restore` and `POST .../duplicate`.
- Archived content can remain in historical references but cannot be newly added to a base resume. Explain “Restore this item to use it again.”
- Source history uses `GET /content-items/{id}/versions` and `GET /bullets/{id}/versions`. Show action, timestamp, changed fields, snapshot detail. History remains useful after deletion.

### Skills and profiles — `/skills`, `/profiles`

- Skills: `GET/POST/PATCH /skills`; add/remove aliases; view/manage content-item relationships.
- Convey canonical name, aliases, category, verified status, and linked source items. Warn when an alias change can affect matching.
- Profiles: `GET/POST/PATCH/DELETE /personal-information`; fields include contact links, summary, and private notes.
- Profile private notes must not appear in resume snapshots. Let a base resume choose its reusable profile.

### Base-resume composer — `/resumes`, `/resumes/:id`

- Load `GET /base-resumes/{id}`, `GET /base-resumes/{id}/entries`, active content, bullets, and profiles.
- Three-panel desktop layout: ordered selected entries; source/bullet selector; paper preview. Stack it on smaller devices.
- Create/update base resumes with `POST/PATCH /base-resumes`; add/manage entries with `POST/GET /base-resumes/{id}/entries`, `PATCH/DELETE /base-entries/{id}`.
- Make exact selected bullet IDs visible: this is a controlled reusable composition, not a freeform resume editor.
- Communicate that base resumes are editable starting points; tailored revisions are separate immutable outputs.

### Data safety — `/settings/data`

- Create/list backups with `POST /backups` and `GET /backups`.
- Show version, creation time, size, checksums, artifact count, and validation state in a quiet local-first panel.
- Do **not** show an in-app Restore control: restore is service-level, not an exposed safe API workflow.

## 5. API and client-state rules

### Query boundaries

Use server state as the source of truth. Recommended query scopes:

```text
applications | application(id) | analysis(id) | requirements(id)
comparison(id) | confirmations(id) | proposals(id) | revisions(id)
contentItems | contentItem(id) | skills | profiles | baseResume(id) | backups
```

- Keep form drafts local until save.
- Do not optimistically update status, source edits, approval, generation, materialization, or submission; each can change audit history, fingerprints, or validation outcome.
- Preserve mutation responses before refetching to prevent visual flicker.
- Use idempotency keys for retryable analysis/proposal/materialization operations where the API accepts them.
- Normalize FastAPI errors to `{ status, message, fieldErrors?, conflictType? }` in one API client layer.

### Status/error behavior

| Condition | UI behavior |
| --- | --- |
| `404` no analysis/submitted revision | empty state with the next valid action, not a global error |
| `409` stale proposal/lifecycle conflict/not-ready revision | preserve work, explain what changed, offer reload/regenerate |
| `422` validation/ownership/lock failure | actionable field/drawer error; never expose unrelated record data |
| `503` Codex unavailable or render failure | preserve existing screen state; offer retry and show diagnostics where available |
| Loading | skeleton for structure, disabled duplicate mutations, never replace useful cached content with a spinner |
| Empty | one concise explanation and one relevant CTA |

## 6. Implementation order

1. App shell, tokens, responsive navigation, API client, query/error primitives.
2. Home; applications list and create/edit flow.
3. Profiles, source library, skills, and base-resume composer.
4. Application overview, lifecycle, and status history.
5. Analysis, requirements, matching classifications, and evidence links.
6. Missing-confirmation review and verified materialization.
7. Proposal generation, review, approval/rejection, and stale-source handling.
8. Snapshot/PDF preview, generation, submission, revision history, and comparison.
9. Data-safety backups; accessibility, responsive, and negative-path QA.

## 7. Definition of done / agent handoff checklist

- [ ] Every route above has loading, empty, error, not-found, and success feedback appropriate to its task.
- [ ] Desktop and mobile retain the mockup’s calm hierarchy; sidebar collapses without losing navigation.
- [ ] Keyboard focus, semantic controls, readable contrast, and reduced-motion behavior are present.
- [ ] No UI implies that analysis or proposals edit source records.
- [ ] Proposal approval requires current, valid source evidence; stale conflicts are visible and recoverable.
- [ ] Locked bullets, archived items, verified skills, and library-only evidence have distinct visual treatment.
- [ ] Generated PDF/LaTeX URLs come from server responses; generated and submitted revisions are immutable in the UI.
- [ ] Submission always references one exact ready revision.
- [ ] Revision comparison is semantic and understandable without raw JSON.
- [ ] Backup screen only exposes the backend-supported create/list workflow.

## 8. Evidence anchors

- Main API and contracts: [`backend/app/main.py`](../backend/app/main.py)
- Backend feature map: [`backend-feature-guide.html`](backend-feature-guide.html)
- Product status and scope: [`README.md`](../README.md), [`PROGRESS.md`](PROGRESS.md)
- Matching: [`backend/app/services/matching.py`](../backend/app/services/matching.py)
- Proposal validation: [`backend/app/services/validation.py`](../backend/app/services/validation.py)
- Revision comparison: [`backend/app/services/revision_comparison.py`](../backend/app/services/revision_comparison.py)
- Rendering: [`backend/app/services/renderer.py`](../backend/app/services/renderer.py)
- Backup safety: [`backend/app/services/backup.py`](../backend/app/services/backup.py)
- Contract tests: `../backend/tests/test_mvp_flow.py`, `test_application_lifecycle.py`, `test_matching.py`, `test_structured_requirements.py`, `test_validation.py`, `test_tailored_generation.py`, `test_revision_comparison.py`, and `test_backup.py`
