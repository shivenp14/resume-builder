# Create the `.agents/` Directory Exactly as Specified

## Purpose

This document is a **mechanical file-creation specification**.

Your job is **not** to design, rewrite, improve, summarize, expand, or reinterpret any of the agent instructions below.

You must only:

1. Create the directory structure shown below.
2. Create each listed file.
3. Copy the exact contents from this document into the corresponding file.
4. Preserve wording, headings, punctuation, capitalization, and formatting.
5. Do not add any additional files inside `.agents/`.
6. Do not remove or modify existing files outside `.agents/`.
7. Do not substitute your own instructions or recommendations.
8. Do not merge files together.
9. Do not move content between files.
10. Do not add generated commentary, explanations, timestamps, signatures, or metadata.

If a listed file already exists, replace its contents with the exact contents supplied here.

After completing the work, only report:

- which files were created or replaced;
- whether all specified contents were copied exactly;
- any filesystem error that prevented exact completion.

Do not perform any other implementation work.

---

# Required Directory Structure

Create exactly this structure:

```text
.agents/
└── skills/
    ├── orchestrate/
    │   ├── SKILL.md
    │   └── references/
    │       ├── worker-contracts.md
    │       ├── retry-policy.md
    │       └── state-schema.md
    │
    ├── review-change/
    │   └── SKILL.md
    │
    └── verify-change/
        └── SKILL.md
```

---

# File 1

## Path

```text
.agents/skills/orchestrate/SKILL.md
```

## Exact contents

```md
---
name: orchestrate
description: Coordinate substantial software tasks with isolated subagents while keeping the parent context compact. Use for non-trivial implementation, broad repository exploration, noisy debugging, multi-file changes, separable workstreams, or independent implementation review. Do not use for trivial reads, simple greps, or obvious tiny edits.
---

# Orchestrate

Coordinate the work instead of absorbing every implementation detail into the
parent context.

The coordinating agent owns:
- the user's objective;
- acceptance criteria;
- decomposition;
- dependencies;
- worker assignment;
- integration decisions;
- review routing;
- final acceptance.

Subagents own bounded exploration, implementation, testing, debugging, and
review.

The goal is not to minimize total tokens at all costs. The goal is to keep the
coordinating context compact and high-signal while moving context-heavy work
into cheaper isolated workers.

## 1. Decide whether delegation is worthwhile

Work directly when the task is small and obvious.

Do not delegate merely to perform:
- one grep;
- one targeted file read;
- a tiny one-file edit;
- a simple correction to already-understood work.

Delegate when one or more of these apply:
- substantial repository exploration is required;
- implementation spans meaningful logic or multiple files;
- tests or debugging may generate significant context;
- multiple independent components can be separated cleanly;
- an independent review would materially reduce risk;
- preserving parent context is valuable.

## 2. Define completion before delegation

Determine the actual acceptance criteria.

Prefer observable criteria such as:
- requested behavior works;
- relevant tests pass;
- build or typecheck succeeds;
- important regressions are covered;
- required interfaces remain compatible;
- no unrelated changes were introduced.

Do not delegate an ambiguous outcome when it can be made concrete first.

## 3. Explore only when necessary

If the relevant architecture is not sufficiently understood to divide the
work, spawn one explorer.

Give the explorer a specific question.

Good:
"Trace how password-reset tokens are created, persisted, validated, and
invalidated. Identify the files and symbols an implementation worker needs."

Bad:
"Understand the repository."

Use the explorer's summary to plan the task.

Do not reread every file the explorer inspected.

## 4. Build a bounded work graph

Divide the task into the smallest coherent implementation units that can be
owned independently.

For each delegated task determine:
- objective;
- writable or investigative scope;
- dependencies;
- required starting context;
- constraints;
- acceptance criteria;
- verification;
- expected return format.

Prefer one capable worker owning a coherent component over many workers owning
tiny fragments.

Parallelize:
- independent research;
- read-only exploration;
- independent test investigation;
- implementation in clearly disjoint components.

Serialize:
- changes to the same files;
- changes to the same abstraction;
- shared API redesigns;
- work where one decision materially constrains another.

Never give concurrent implementation workers overlapping write ownership.

## 5. Dispatch workers with a compact contract

Every delegated prompt should contain only:

OBJECTIVE
The bounded result required.

SCOPE
Files, components, or subsystem the worker owns.

CONTEXT
Only durable facts needed to begin.
Prefer file paths and symbols over pasted source code.

CONSTRAINTS
What must remain unchanged or outside scope.

ACCEPTANCE CRITERIA
Observable conditions for success.

VERIFICATION
Relevant tests, build, lint, typecheck, or reproduction steps.

RETURN CONTRACT
The structured concise result expected from the worker.

Do not send the worker the entire parent conversation.
Do not include unrelated project history.

Use `references/worker-contracts.md` when constructing or validating a worker
assignment.

## 6. Implementation belongs to the implementation worker

For delegated implementation, the implementation worker owns the local loop:

understand
-> edit
-> test
-> inspect failure
-> repair
-> retest
-> report

The coordinating agent should not repeat that investigation after the worker
returns unless a specific integration concern requires it.

This is deliberate: implementation context should normally remain inside the
worker rather than accumulating in the coordinating context.

## 7. Choose worker reasoning effort

Use the normal high reasoning setting by default.

Escalate to the highest available reasoning setting when deeper reasoning is
likely to materially improve the result, including:
- architecture remains genuinely ambiguous after exploration;
- several plausible root causes remain after investigation;
- the implementation involves difficult algorithms or state interactions;
- a high-reasoning worker failed because of reasoning rather than a mechanical
  mistake;
- a high-risk correctness, security, concurrency, or data-integrity review
  warrants deeper analysis.

Do not escalate merely because:
- a command failed once;
- a test had an obvious local failure;
- the task is large but straightforward;
- the worker has not yet been given sufficient context.

## 8. Consume worker responses as state updates

Do not absorb worker transcripts.

Retain only:
- outcome;
- changed files;
- important decisions;
- verification results;
- unresolved risks;
- dependencies for later work.

Do not copy full logs, diffs, or source files into parent context.

If detailed evidence may be needed later, refer to the repository file,
test artifact, or path instead of reproducing it.

For long-running or multi-workstream tasks, use the state format in
`references/state-schema.md`.

## 9. Handle implementation failures

On the first clear local failure:
send the owning worker the relevant failure evidence and allow it to repair the
problem.

Do not respawn a fresh worker for every minor failure.

If the same conceptual approach fails repeatedly:
- stop repeating the same prompt;
- identify the assumption that appears wrong;
- use a fresh explorer or fresh implementation worker;
- increase reasoning effort when the unresolved problem is reasoning-heavy;
- reconsider the decomposition if necessary.

Use `references/retry-policy.md` for retry and escalation behavior.

Do not solve repeated worker failures by simply adding more workers.

## 10. Review important changes

Use a fresh reviewer when one or more of these apply:
- the change is substantial;
- multiple workers contributed;
- the change crosses component boundaries;
- authentication or authorization is involved;
- security or data integrity matters;
- concurrency or state behavior is important;
- implementation required substantial debugging;
- automated verification is incomplete;
- the coordinating agent has meaningful uncertainty.

The reviewer must receive the intended behavior and changed scope, but should
not receive the implementation worker's reasoning history.

Ask it to independently inspect the resulting code.

Use the `review-change` skill for the review procedure.

Use normal high reasoning for ordinary review.

Use the highest available reasoning setting for difficult or high-risk review.

## 11. Route review findings

Treat reviewer findings as hypotheses that require evidence.

For a valid finding:
send the concrete finding back to the implementation worker that owns the
affected area.

Do not make the reviewer implement its own fixes.

After a meaningful fix:
rerun the relevant verification.

Request another review only when the correction is substantial enough to
justify it.

## 12. Integrate centrally

The coordinating agent owns cross-worker integration.

Before completion:
- confirm changed-file ownership is coherent;
- confirm interfaces between workstreams agree;
- check the relevant verification results;
- resolve substantive reviewer findings;
- inspect exact source only where integration cannot otherwise be established;
- ensure there are no unexplained or unrelated modifications.

Do not reread every implementation file merely because a worker touched it.

## 13. Verify before completion

Use the `verify-change` skill when verification requires more than an obvious
single check.

The coordinating agent must establish that the relevant acceptance criteria
are actually satisfied.

Verification evidence is more important than a worker's assertion of success.

## 14. Finish only from evidence

A worker saying "completed" is not sufficient.

The task is complete when:
- acceptance criteria are met;
- necessary verification has passed or limitations are explicitly understood;
- substantive review findings are resolved;
- dependent workstreams are integrated consistently.

Return the user a concise summary of:
- what changed;
- important decisions;
- verification performed;
- any genuine remaining risk.
```

---

# File 2

## Path

```text
.agents/skills/orchestrate/references/worker-contracts.md
```

## Exact contents

```md
# Worker Contracts

Every delegated task must be bounded enough that the worker can determine
whether it succeeded without needing the full parent conversation.

Use the following structure when assigning substantial work.

## Objective

Describe the concrete result required.

The objective should describe an outcome rather than an activity.

Good:

"Add server-side validation preventing expired reset tokens from being used."

Bad:

"Look at the reset-token code and improve it."

## Scope

Define the files, components, subsystem, or behavior the worker owns.

For implementation workers, write ownership must not overlap with another
concurrent implementation worker.

Prefer a coherent ownership boundary over an arbitrary file count.

## Context

Provide only durable information necessary to begin.

Prefer:
- relevant file paths;
- relevant symbols;
- interfaces;
- architectural facts;
- decisions already made;
- concise upstream worker findings.

Avoid:
- complete parent conversations;
- complete source files;
- raw search output;
- routine command logs;
- unrelated task history;
- another worker's chain of reasoning.

When possible, point the worker to repository locations rather than copying the
contents into the assignment.

## Constraints

State anything the worker must preserve or avoid.

Examples:
- do not change the public API;
- do not modify database schema;
- preserve backward compatibility;
- remain within the assigned component;
- do not perform unrelated cleanup.

## Acceptance Criteria

Define observable conditions for success.

Examples:
- expired tokens are rejected;
- valid unexpired tokens still succeed;
- existing authentication tests pass;
- a regression test covers the new behavior.

Avoid acceptance criteria based only on implementation details unless those
details are themselves requirements.

## Verification

Specify the most relevant validation when known.

Examples:
- targeted unit tests;
- integration tests;
- build;
- typecheck;
- lint;
- reproduction steps.

Workers may run additional focused checks when necessary.

## Return Contract

Implementation workers should return exactly the durable information the
coordinator needs.

Use this structure:

OUTCOME

completed | partially completed | blocked

CHANGES

For each changed file:
- path;
- concise description of what changed.

VERIFICATION

For each meaningful command:
- command;
- pass | fail;
- concise failure explanation if it failed.

DECISIONS

Only decisions that affect integration, interfaces, or later work.

RISKS / BLOCKERS

Remaining issues, assumptions, dependencies, or follow-up work.

Do not return:
- full source files;
- full diffs;
- routine shell output;
- chronological implementation narration;
- speculative commentary unrelated to the assigned task.

## Explorer Return Contract

Exploration workers should return:

FINDINGS

A concise answer to the exact assigned question.

RELEVANT FILES

For each relevant file:
- path;
- relevant symbol or region when useful;
- why it matters.

FLOW / DEPENDENCIES

Only relationships relevant to the assigned task.

RISKS / UNCERTAINTIES

Anything unresolved that affects planning.

RECOMMENDED IMPLEMENTATION SCOPE

The smallest likely file or component set needed for implementation.

Do not paste whole files or routine search output.

## Reviewer Return Contract

Reviewers should return only substantive findings.

For each finding:

SEVERITY

critical | high | medium | low

LOCATION

File and symbol or line when available.

PROBLEM

What is wrong.

IMPACT

The concrete failure mode.

EVIDENCE

Why the implementation supports the finding.

FIX DIRECTION

A concise correction approach.

If no substantive issue is found, return:

NO SUBSTANTIVE FINDINGS

Then include:

CONFIDENCE

high | medium | low

TEST GAPS

Only important missing verification.
```

---

# File 3

## Path

```text
.agents/skills/orchestrate/references/retry-policy.md
```

## Exact contents

```md
# Retry Policy

Retries should preserve useful context without allowing repeated failure to
consume unlimited tokens.

The goal is to distinguish a normal local implementation mistake from a wrong
approach, insufficient context, poor decomposition, or insufficient reasoning.

## 1. Local Failure

A local failure is an issue such as:
- a targeted test failure;
- a compile or type error caused by the current change;
- a straightforward incorrect implementation detail;
- a missed local dependency;
- a mechanical mistake.

When a clear local failure occurs:

1. Send the relevant failure evidence back to the same worker.
2. Keep the original ownership boundary.
3. Allow the worker to diagnose and repair it.
4. Rerun the relevant verification.

Do not create a fresh worker for every ordinary failure.

Do not send the entire command history when a concise error excerpt or
description is sufficient.

## 2. Repeated Conceptual Failure

Treat the problem as conceptual rather than local when one or more of the
following occur:
- the same underlying problem survives two meaningful repair attempts;
- the worker repeatedly changes symptoms without addressing the cause;
- the implementation depends on an architectural assumption that appears
  false;
- the worker cannot determine the relevant ownership or control flow;
- the same approach repeatedly produces incompatible behavior.

When this occurs:

1. Stop repeating the same task prompt.
2. Identify the assumption or uncertainty blocking progress.
3. Use a focused explorer when repository understanding is missing.
4. Reformulate the implementation task using the new durable findings.
5. Use a fresh implementation worker when a clean context is valuable.
6. Increase reasoning effort when the unresolved problem is reasoning-heavy
   rather than mechanical.

## 3. Reasoning Escalation

Start with the normal high reasoning setting.

Use the highest available reasoning setting when:
- multiple plausible root causes remain after investigation;
- architecture is still genuinely ambiguous;
- correctness depends on subtle state interaction;
- an algorithmic problem requires deeper reasoning;
- security, concurrency, data integrity, or another high-risk property requires
  stronger independent analysis;
- a high-reasoning worker failed despite receiving adequate and correct
  context.

Do not escalate merely because:
- a command failed once;
- a test had an obvious local failure;
- a task contains many files but is mechanically straightforward;
- the worker lacks context that can simply be provided.

## 4. Decomposition Failure

The decomposition is likely wrong when:
- workers repeatedly need to modify each other's files;
- apparently independent workstreams are blocked on shared design decisions;
- integration requires substantial rewriting;
- multiple workers independently make incompatible assumptions about the same
  interface.

When decomposition fails:

1. Stop parallel write work.
2. Reassess the shared abstraction or interface.
3. Establish the necessary decision centrally.
4. Redefine ownership boundaries.
5. Serialize coupled implementation when necessary.

Do not preserve parallelism merely for speed.

## 5. Review Failure

Treat reviewer findings as hypotheses rather than automatic truth.

For each substantive finding:

1. Check whether the finding identifies a concrete failure mode.
2. Route valid findings to the implementation owner.
3. Provide only the relevant finding and required context.
4. Allow the owner to fix and verify the issue.

Do not ask the reviewer to both critique and implement the correction unless
there is a specific reason to combine those roles.

If a reviewer repeatedly produces speculative or invalid findings, do not keep
re-running identical review prompts. Tighten the review scope or stop.

## 6. Agent Explosion Prevention

Do not create additional subagents solely because another subagent failed.

Prefer this sequence:

same worker repair
-> clarify missing context
-> focused exploration
-> fresh worker
-> reasoning escalation
-> decomposition revision

before increasing agent count.

A larger number of agents is not evidence of better orchestration.

## 7. Stop Conditions

Stop retrying the current approach when:
- the same conceptual failure persists after appropriate investigation;
- required information or access is unavailable;
- success requires violating the task's constraints;
- further retries would repeat already-tested assumptions;
- the task should be returned to the coordinator for re-planning.

Report the blocker concisely and preserve any verified useful result.
```

---

# File 4

## Path

```text
.agents/skills/orchestrate/references/state-schema.md
```

## Exact contents

```md
# Orchestration State Schema

Use durable state only for substantial tasks where the work spans multiple
workers, multiple phases, or enough time that reconstructing orchestration state
from conversation context would be wasteful.

State is a compact coordination artifact.

State is not a transcript.

## When to Create State

Create a task state file when one or more of these apply:
- multiple implementation workstreams exist;
- dependencies between workers must be tracked;
- the task is likely to survive context compaction;
- significant review or retry loops are expected;
- work may need to resume later;
- the coordinator would otherwise need to retain substantial execution history.

Do not create state for trivial tasks.

## Suggested Location

When the repository provides an `.agent-state/` directory, create one
task-specific Markdown file there.

Example:

```text
.agent-state/password-reset.md
```

Use a short descriptive task name.

## Required Structure

Use the following structure.

```md
# Task State: <short task name>

## Goal

<one concise statement of the requested outcome>

## Acceptance Criteria

- <criterion>
- <criterion>

## Decisions

- <only durable decisions that constrain later work>

## Workstreams

### <task-id>

Status: pending | running | completed | blocked | needs-review

Owner: <worker identifier or unassigned>

Scope:
- <component, subsystem, or files>

Depends on:
- <task-id or none>

Outcome:
<one or two sentence durable summary>

Verification:
- <relevant check and result>

### <task-id>

Status: pending | running | completed | blocked | needs-review

Owner: <worker identifier or unassigned>

Scope:
- <component, subsystem, or files>

Depends on:
- <task-id or none>

Outcome:
<one or two sentence durable summary>

Verification:
- <relevant check and result>

## Known Risks

- <only unresolved substantive risks>

## Next Actions

1. <next orchestration action>
2. <next orchestration action>
```

## State Content Rules

Record:
- the goal;
- observable acceptance criteria;
- durable architectural or behavioral decisions;
- workstream status;
- ownership;
- dependencies;
- concise worker outcomes;
- relevant verification status;
- unresolved blockers;
- next orchestration actions.

Do not record:
- chain-of-thought;
- worker transcripts;
- complete prompts;
- raw terminal output;
- full diffs;
- large source excerpts;
- chronological narration;
- routine successful command output.

## Updating State

Update the existing task state after meaningful state transitions, such as:
- a workstream starts;
- a workstream completes;
- a blocker is discovered;
- a decision changes downstream work;
- review finds a substantive issue;
- verification establishes or disproves completion.

Do not update state after every command or minor implementation action.

## Worker Results

Compress worker output into durable facts.

Bad:

"Worker tried implementation A, ran the test, saw error X, then searched Y,
changed Z, reran the test, and eventually..."

Good:

"Backend reset-token validation completed. Auth tests pass. Tokens are
single-use and expire after 30 minutes. Frontend integration remains pending."

## Completion

When the task is complete, the state file should make the final status obvious.

Do not retain stale temporary reasoning.

Delete or archive completed task state according to repository policy if the
state has no ongoing project value.
```

---

# File 5

## Path

```text
.agents/skills/review-change/SKILL.md
```

## Exact contents

```md
---
name: review-change
description: Independently review a completed or proposed code change for substantive correctness, regression, security, state, concurrency, contract, and testing issues. Use for meaningful implementation review. Do not use for ordinary style commentary.
---

# Review Change

Perform an independent implementation review.

The purpose of review is to find concrete problems that could cause incorrect
behavior, regressions, unsafe behavior, broken contracts, or important missing
verification.

Do not review merely to generate comments.

## 1. Establish Intended Behavior

Before judging the implementation, identify:
- the requested behavior;
- the relevant acceptance criteria;
- the changed scope;
- important constraints.

Do not rely on the implementation itself to define what correct behavior means.

## 2. Preserve Independence

When possible, review from fresh context.

Use:
- the intended behavior;
- changed files or diff;
- relevant surrounding source;
- relevant tests;
- repository contracts.

Avoid inheriting:
- the implementer's chain-of-thought;
- the implementer's full conversation;
- speculative justifications for questionable code;
- unnecessary implementation history.

The reviewer should evaluate the resulting artifact rather than defend the
implementation process.

## 3. Inspect the Change

Inspect the changed code and enough surrounding implementation to understand its
actual effect.

Trace relevant:
- callers;
- callees;
- state changes;
- data flow;
- error paths;
- interfaces;
- persistence behavior;
- security boundaries;
- concurrency assumptions;
- tests.

Do not indiscriminately read unrelated areas of the repository.

## 4. Prioritize Substantive Problems

Prioritize:
- functional bugs;
- behavioral regressions;
- incorrect assumptions;
- security vulnerabilities;
- authorization or authentication mistakes;
- data-integrity problems;
- race conditions or state-management errors;
- broken public or internal contracts;
- important unhandled edge cases;
- resource lifecycle problems;
- meaningful missing test coverage.

Do not report:
- personal style preferences;
- purely cosmetic formatting;
- speculative concerns without a plausible failure mode;
- unrelated refactoring opportunities;
- changes that are merely different from the reviewer's preferred design;
- issues already demonstrably handled by the implementation.

## 5. Validate Findings

A finding should identify a plausible concrete failure mode.

Before reporting it:
- inspect enough source to confirm the assumption;
- check whether existing code already handles the condition;
- distinguish confirmed problems from uncertainty.

Do not inflate severity.

If evidence is insufficient, either investigate further or label the
uncertainty clearly.

## 6. Review Tests

Determine whether the tests meaningfully exercise the requested behavior.

Look for:
- missing regression coverage;
- tests that assert implementation details but not behavior;
- important failure paths that remain untested;
- tests that would pass even if the bug remained;
- missing boundary cases where risk is material.

Do not demand exhaustive testing for low-risk trivial behavior.

## 7. Output Format

For every substantive finding return:

SEVERITY

critical | high | medium | low

LOCATION

File and symbol or line when available.

PROBLEM

What is wrong.

IMPACT

The concrete failure mode.

EVIDENCE

Why the implementation supports the finding.

FIX DIRECTION

A concise correction approach.

Order findings by severity.

Do not bury severe findings beneath minor observations.

If there are no substantive findings, return:

NO SUBSTANTIVE FINDINGS

Then return:

CONFIDENCE

high | medium | low

TEST GAPS

Only important missing verification.

## 8. Do Not Implement During Review

Unless explicitly assigned both roles, review should remain independent from
implementation.

Do not modify source files.

Route valid findings back to the implementation owner or coordinator.
```

---

# File 6

## Path

```text
.agents/skills/verify-change/SKILL.md
```

## Exact contents

```md
---
name: verify-change
description: Verify that a software change satisfies its acceptance criteria using targeted tests, builds, typechecks, reproduction steps, repository inspection, and integration checks. Use before accepting substantial implementation work.
---

# Verify Change

Verification establishes whether the requested outcome actually works.

A worker reporting success is evidence to inspect, not proof of completion.

Choose the smallest set of checks that provides strong evidence for the
acceptance criteria.

## 1. Start From Acceptance Criteria

List the observable requirements that must be true.

Verification should map back to these requirements.

Do not run broad commands solely because they exist.

Prefer checks that can disprove an incorrect implementation.

## 2. Identify Affected Surfaces

Determine which parts of the system are materially affected.

Consider:
- directly changed code;
- callers and consumers;
- interfaces;
- persistence or schema behavior;
- build or type relationships;
- user-visible behavior;
- security boundaries;
- important integration points.

Use this to select verification.

## 3. Run Targeted Tests First

Prefer the narrowest meaningful tests that exercise the changed behavior.

Examples:
- focused unit tests;
- package-level tests;
- component tests;
- targeted integration tests;
- regression tests for the original failure.

If a targeted test fails, investigate before running increasingly broad suites.

## 4. Run Static or Build Checks When Relevant

Use relevant project checks such as:
- typecheck;
- compilation;
- build;
- lint when lint rules affect correctness or repository acceptance;
- schema validation;
- generated-code consistency.

Do not run unrelated expensive checks without a reason.

## 5. Verify Behavior Directly When Useful

For behavior that is not adequately established by automated tests, use a
focused reproduction.

Examples:
- call the affected API;
- exercise the relevant CLI command;
- reproduce the original bug;
- verify the state transition;
- inspect produced output.

Prefer deterministic verification over subjective visual inspection when
possible.

## 6. Inspect Integration State

Before accepting substantial work, check for integration problems such as:
- incompatible interfaces between worker changes;
- unresolved merge conflict artifacts;
- unexpected changed files;
- accidental unrelated modifications;
- stale generated output;
- dependencies that were assumed but not implemented.

Inspect exact source only where needed to establish integration.

Do not reread every changed file without a verification reason.

## 7. Handle Failures

When verification fails, report:
- the exact failed check;
- the relevant concise error;
- which acceptance criterion is not established;
- whether the failure appears local, conceptual, or integration-related.

Route the failure to the implementation owner when appropriate.

Do not hide failed verification behind other passing checks.

## 8. Verification Result

Return:

STATUS

verified | partially verified | failed

ACCEPTANCE CRITERIA

For each criterion:
- criterion;
- verified | not verified;
- evidence.

CHECKS

For each meaningful check:
- command or method;
- pass | fail;
- concise result.

INTEGRATION

Any relevant integration observations.

REMAINING GAPS

Only verification that remains materially important.

Do not include:
- full terminal logs;
- routine successful output;
- unrelated repository observations;
- implementation narration.

## 9. Completion Standard

A substantial change should not be considered complete until:
- relevant acceptance criteria are verified;
- important automated checks pass;
- substantive review findings are resolved;
- integration is coherent;
- any remaining limitation is explicitly understood.

Verification should increase confidence through evidence, not through repeated
assertions that the implementation looks correct.
```

---

# Final Validation

Before reporting completion, verify that the resulting repository contains
exactly these `.agents/` files:

```text
.agents/skills/orchestrate/SKILL.md
.agents/skills/orchestrate/references/worker-contracts.md
.agents/skills/orchestrate/references/retry-policy.md
.agents/skills/orchestrate/references/state-schema.md
.agents/skills/review-change/SKILL.md
.agents/skills/verify-change/SKILL.md
```

Confirm that:

- every file exists;
- every file contains the exact supplied contents;
- no content was rewritten;
- no content was omitted;
- no extra `.agents/` files were invented;
- no files outside `.agents/` were modified.

Do not perform any additional work after this validation.
