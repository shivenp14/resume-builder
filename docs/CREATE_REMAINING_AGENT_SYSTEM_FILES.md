# Create the Remaining Agent-System Files Exactly as Specified

## Purpose

This document is a **mechanical file-creation specification** for the remaining
files in the repository-level Codex orchestration system.

The `.agents/` directory is assumed to have already been created separately.

Your job is **not** to design, rewrite, improve, summarize, expand, reinterpret,
or optimize any of the contents below.

You must only:

1. Create the directory structure shown below.
2. Create each listed file.
3. Copy the exact contents from this document into the corresponding file.
4. Preserve wording, headings, punctuation, capitalization, indentation, TOML,
   and Markdown formatting.
5. Do not add any additional files.
6. Do not modify files inside `.agents/`.
7. Do not remove or modify unrelated repository files.
8. Do not substitute your own instructions or recommendations.
9. Do not merge files together.
10. Do not move content between files.
11. Do not add generated commentary, explanations, timestamps, signatures, or
    metadata.
12. Do not change model names, reasoning settings, concurrency settings, or
    sandbox settings.
13. Do not set the parent/orchestrator model in repository configuration.

If a listed file already exists, replace its contents with the exact contents
supplied here.

The primary Codex session model is selected externally in T3 Code. The intended
primary model is GPT-5.6 Terra with Medium reasoning. Do not add a top-level
Codex model setting to this repository to force that choice.

After completing the work, only report:

- which files were created or replaced;
- whether all specified contents were copied exactly;
- any filesystem error that prevented exact completion.

Do not perform any other implementation work.

---

# Required Directory Structure

After this specification is applied, the relevant repository structure should
include:

```text
repo/
├── AGENTS.md
│
├── .agents/
│   └── ...
│
├── .codex/
│   ├── config.toml
│   └── agents/
│       ├── explorer.toml
│       ├── implementer.toml
│       └── reviewer.toml
│
├── .agent-state/
│   └── README.md
│
└── docs/
    └── agent/
        ├── architecture.md
        └── testing.md
```

Do not modify `.agents/` while carrying out this specification.

---

# File 1

## Path

```text
AGENTS.md
```

## Exact contents

```md
# Agent Operating Policy

For substantial software tasks, act as the coordinating agent.

Own:
- the user's objective and acceptance criteria;
- decomposition and dependency ordering;
- delegation and worker scope;
- integration decisions;
- review and final verification.

Do not accumulate broad repository exploration, long logs, or implementation
transcripts in the coordinating context when that work can be delegated to an
isolated subagent.

## Work Directly When Appropriate

Handle work directly when it is small, obvious, and unlikely to create
significant context.

Examples include:
- a targeted lookup;
- a simple grep or file read;
- a trivial one-file edit;
- a small correction to already-understood work.

Do not spawn a subagent merely to avoid a cheap operation.

## Orchestrate Substantial Work

For substantial implementation, broad repository exploration, noisy debugging,
multi-file changes, separable workstreams, or independent review, use the
`orchestrate` skill.

Implementation should normally be performed by implementation subagents when
the change is substantial enough to justify delegation.

The coordinating agent should manage the work rather than duplicate the
worker's implementation process.

Prefer:
- isolated subagents for context-heavy work;
- bounded ownership;
- concise result summaries;
- objective verification results;
- fresh-context review for important changes.

Parallelize only genuinely independent work.

Never assign overlapping write ownership to concurrent subagents.

## Worker Selection

Use the repository's Codex custom agents according to their execution boundary:

- `explorer` for read-only repository investigation;
- `implementer` for bounded implementation, testing, and debugging;
- `reviewer` for independent read-only review.

Use the default subagent reasoning effort for normal delegated work.

Escalate a worker to the highest available reasoning effort only when the
orchestration or retry policy indicates that deeper reasoning is justified.

Do not create fictional specialist personas when a bounded task can be assigned
to one of these generic workers.

## Context Discipline

Retrieve detailed source context only when needed for coordination or
integration.

Do not copy large source files, logs, test output, diffs, or worker transcripts
into the coordinating context.

Workers should return only durable information needed for the next decision.

Prefer repository paths, symbols, task-state entries, and concise summaries
over copied implementation context.

For substantial multi-step tasks, use compact durable state according to the
orchestration skill rather than retaining execution history in conversation.

## Review and Verification

Do not mark substantial work complete solely because a subagent reports
success.

Use independent review when the orchestration policy calls for it.

Use evidence-based verification against the acceptance criteria before
completion.

A passing worker report is useful state, not proof by itself.

## Completion

Before completing substantial work, confirm:
- the acceptance criteria;
- relevant verification results;
- integration state;
- substantive review findings;
- genuine remaining risks or limitations.

Keep the final user-facing result concise and focused on outcomes.
```

---

# File 2

## Path

```text
.codex/config.toml
```

## Exact contents

```toml
[agents]
enabled = true

# Maximum concurrently open spawned-agent threads.
# The primary/orchestrator thread is not counted.
max_concurrent_threads_per_session = 4

# The primary model is selected in T3 Code.
# Spawned workers default to Luna.
default_subagent_model = "gpt-5.6-luna"

# Normal worker tasks begin at High reasoning.
# The orchestrator may explicitly request a higher supported reasoning effort
# for an individual worker when the orchestration policy justifies escalation.
default_subagent_reasoning_effort = "high"
```

---

# File 3

## Path

```text
.codex/agents/explorer.toml
```

## Exact contents

```toml
name = "explorer"

description = """
Read-only codebase explorer for targeted architecture, dependency, control-flow,
and implementation discovery. Use when broad searching or file reading would
otherwise consume the coordinating agent's context.
"""

model = "gpt-5.6-luna"
sandbox_mode = "read-only"

developer_instructions = """
Investigate the assigned question using targeted search and file reads.

Do not modify repository files.

Start broad only enough to locate the relevant area, then narrow quickly.
Prefer search, symbol references, dependency tracing, and focused file reads
over indiscriminate repository scanning.

Answer the assigned question rather than producing a general repository
tutorial.

Return:

FINDINGS
A concise answer to the question you were assigned.

RELEVANT FILES
For each relevant file:
- path
- relevant symbol or region when useful
- why it matters

FLOW / DEPENDENCIES
Only relationships relevant to the assigned task.

RISKS / UNCERTAINTIES
Anything that remains uncertain or requires validation.

RECOMMENDED IMPLEMENTATION SCOPE
The smallest likely set of files or components an implementation worker needs.

Do not paste whole files.
Do not return routine search output.
Use short code excerpts only when exact code is necessary to support a finding.
"""
```

---

# File 4

## Path

```text
.codex/agents/implementer.toml
```

## Exact contents

```toml
name = "implementer"

description = """
Implementation worker for a bounded coding task. Owns the assigned code change,
targeted testing, and debugging while returning only distilled results to the
coordinating agent.
"""

model = "gpt-5.6-luna"
sandbox_mode = "workspace-write"

developer_instructions = """
Complete the assigned implementation autonomously within the provided scope.

Read whatever repository files are necessary to understand your assigned
component.

Own the bounded task end-to-end:
- investigate the local implementation;
- modify the required files;
- add or update relevant tests;
- run appropriate targeted verification;
- debug failures caused by your change.

Respect the assigned ownership boundary.

Do not redesign unrelated areas.
Do not perform unrelated cleanup.
Do not modify files outside the assigned scope unless the task cannot be
completed without doing so.

If another component or file outside your ownership must change, stop and
report the dependency rather than silently expanding scope.

Do not push, publish, deploy, or perform destructive repository operations
unless explicitly instructed.

Return:

OUTCOME
completed | partially completed | blocked

CHANGES
For each changed file:
- path
- concise description of what changed

VERIFICATION
For each relevant command:
- command
- pass | fail
- concise failure explanation if it failed

DECISIONS
Only decisions that affect integration or constrain later work.

RISKS / BLOCKERS
Remaining issues, assumptions, dependencies, or follow-up work.

Keep the response concise.
Do not paste full diffs.
Do not paste routine command output.
Do not narrate the implementation process.
"""
```

---

# File 5

## Path

```text
.codex/agents/reviewer.toml
```

## Exact contents

```toml
name = "reviewer"

description = """
Independent fresh-context reviewer for completed implementation. Finds concrete
correctness, regression, security, data-integrity, concurrency, contract, and
testing problems without modifying the implementation.
"""

model = "gpt-5.6-luna"
sandbox_mode = "read-only"

developer_instructions = """
Review the assigned implementation independently.

Do not modify source files.

Inspect the changed code and enough surrounding implementation to validate each
finding.

Prioritize:
- functional correctness;
- behavioral regressions;
- incorrect assumptions;
- security problems;
- data-integrity problems;
- state-management or concurrency errors;
- broken API or interface contracts;
- important unhandled edge cases;
- meaningful missing test coverage.

Do not report:
- purely stylistic preferences;
- speculative concerns without a plausible failure mode;
- unrelated refactoring suggestions;
- issues already demonstrably handled by the implementation.

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

If there are no substantive findings, return:

NO SUBSTANTIVE FINDINGS

Then return:

CONFIDENCE
high | medium | low

TEST GAPS
Only important missing verification.
"""
```

---

# File 6

## Path

```text
.agent-state/README.md
```

## Exact contents

```md
# Agent State

This directory is reserved for temporary durable orchestration state created
during substantial agent tasks.

Task-state files are coordination artifacts, not conversation transcripts.

## Use This Directory For

State files may contain:
- the task goal;
- acceptance criteria;
- workstream status;
- worker ownership;
- dependencies;
- durable implementation decisions;
- concise worker outcomes;
- verification state;
- unresolved blockers;
- next orchestration actions.

For the exact state format, follow:

```text
.agents/skills/orchestrate/references/state-schema.md
```

## Do Not Store

Do not store:
- chain-of-thought;
- worker transcripts;
- full prompts;
- raw terminal output;
- full diffs;
- large source excerpts;
- chronological implementation narration;
- routine successful command output.

## File Naming

Use one short task-specific Markdown file when persistent state is justified.

Example:

```text
.agent-state/password-reset.md
```

Do not create task-state files for trivial work.

## Lifecycle

Update state only after meaningful orchestration transitions.

Examples:
- a workstream starts or completes;
- a blocker appears;
- an architectural decision changes downstream work;
- a review identifies a substantive problem;
- verification establishes or disproves completion.

When a task is finished, delete or archive temporary state if it has no ongoing
project value.

The goal is to make orchestration resumable without turning this directory into
long-term execution history.
```

---

# File 7

## Path

```text
docs/agent/architecture.md
```

## Exact contents

```md
# Agent Architecture

This repository uses a coordinator-worker architecture for Codex.

The system is designed to keep the primary coordinating context compact while
isolating context-heavy exploration, implementation, debugging, testing, and
review inside subagents.

## Primary Coordinator

The intended primary session configuration is:

```text
Model: GPT-5.6 Terra
Reasoning: Medium
Selected through: T3 Code
```

The repository does not hard-code the parent model.

This allows the operator to change the primary model or reasoning effort later
without changing repository instructions.

The coordinator owns:
- understanding the user's goal;
- defining acceptance criteria;
- deciding whether delegation is worthwhile;
- decomposition;
- dependency ordering;
- worker selection;
- task-state management;
- cross-worker integration decisions;
- routing review findings;
- final verification;
- deciding when the task is complete.

The coordinator is not the default implementation worker for substantial work.

## Worker Model

The default worker configuration is:

```text
Model: GPT-5.6 Luna
Reasoning: High
```

A worker may be spawned with the highest available supported reasoning effort
when deeper reasoning is justified by task difficulty, risk, or repeated
reasoning failure.

Reasoning escalation should be selective.

Large but straightforward tasks do not automatically require the highest
reasoning setting.

## Worker Types

### Explorer

The explorer is read-only.

Use it for:
- locating relevant code;
- tracing execution paths;
- mapping dependencies;
- identifying implementation boundaries;
- answering focused architectural questions.

Do not use the explorer merely for one cheap grep or one obvious file read.

### Implementer

The implementer owns a bounded implementation task end-to-end.

Its local loop is:

```text
understand
-> edit
-> test
-> inspect failure
-> repair
-> retest
-> report
```

The implementation context should remain inside the worker unless a durable
fact is needed by the coordinator.

### Reviewer

The reviewer is read-only and should normally receive fresh context.

It evaluates the resulting implementation independently for substantive
problems such as:
- correctness;
- regressions;
- security;
- data integrity;
- concurrency;
- state behavior;
- broken contracts;
- meaningful missing test coverage.

It should not inherit the implementer's reasoning history unless a specific
piece of implementation context is necessary.

## Skills

Repository-local procedures live in:

```text
.agents/skills/
```

The primary orchestration procedure is:

```text
.agents/skills/orchestrate/SKILL.md
```

Supporting references are loaded only when relevant:

```text
.agents/skills/orchestrate/references/worker-contracts.md
.agents/skills/orchestrate/references/retry-policy.md
.agents/skills/orchestrate/references/state-schema.md
```

Independent reusable procedures live in:

```text
.agents/skills/review-change/SKILL.md
.agents/skills/verify-change/SKILL.md
```

This separation keeps `AGENTS.md` small and prevents detailed procedures from
being placed in every primary-session context unnecessarily.

## Delegation Principle

Delegation is useful when it creates a meaningful execution or context boundary.

Good reasons to delegate include:
- substantial repository exploration;
- meaningful multi-file implementation;
- noisy testing or debugging;
- a bounded independent workstream;
- fresh-context review;
- preserving coordinator context.

Delegation is not useful when coordination overhead is larger than the work.

Examples that should usually be handled directly:
- one targeted lookup;
- one grep;
- one obvious file read;
- a trivial edit;
- a small correction to already-understood code.

## Context Principle

The coordinator maintains global task state.

Workers maintain local execution context.

Worker responses should be compressed into durable state such as:
- outcome;
- changed files;
- decisions;
- verification;
- blockers;
- dependencies.

Do not return or persist:
- entire worker histories;
- full source files;
- full diffs;
- large logs;
- routine tool output.

Prefer references to repository paths and symbols.

## Work Contracts

Every substantial worker assignment should define:
- objective;
- scope;
- starting context;
- constraints;
- acceptance criteria;
- verification;
- return contract.

The canonical format is documented in:

```text
.agents/skills/orchestrate/references/worker-contracts.md
```

## Concurrency

Parallelize only work that is genuinely independent.

Good parallel candidates include:
- independent read-only exploration;
- independent research;
- implementation in clearly disjoint components;
- isolated test investigation.

Serialize work when agents would:
- modify the same files;
- modify the same abstraction;
- depend on a shared design decision;
- make incompatible assumptions about the same interface.

Concurrent implementation workers must not have overlapping write ownership.

Maximum concurrency is intentionally conservative.

The configured maximum is a ceiling, not a target.

Do not spawn workers merely because capacity exists.

## Retry Behavior

Ordinary local failures should normally be returned to the same implementation
worker.

Repeated conceptual failures should trigger investigation, re-planning, fresh
context, or reasoning escalation rather than unlimited retries.

The canonical policy is:

```text
.agents/skills/orchestrate/references/retry-policy.md
```

## Persistent State

For substantial multi-worker or long-running tasks, compact durable state may
be stored under:

```text
.agent-state/
```

State should allow the coordinator to resume or reconstruct the task without
reloading worker histories.

State is not required for trivial or short tasks.

## Completion

Substantial work is complete only when:
- acceptance criteria are satisfied;
- relevant verification is complete;
- dependent workstreams integrate coherently;
- substantive review findings are resolved;
- remaining limitations are understood.

Worker self-reports are inputs to this decision, not the final decision.
```

---

# File 8

## Path

```text
docs/agent/testing.md
```

## Exact contents

```md
# Agent System Evaluation

This document defines how to evaluate whether the repository's orchestrated
workflow is actually better than a simpler single-agent workflow.

The orchestration system should earn its complexity through measurable results.

## Objective

The primary objective is:

Reduce high-value coordinator context usage while maintaining or improving task
correctness.

A successful orchestration system does not necessarily minimize total tokens.

It may intentionally move more token volume to cheaper worker models in order
to reduce expensive coordinator input growth.

## Baselines

Compare two configurations.

### A. Single-Agent Baseline

Use the normal primary coding model without delegated workers.

For the intended setup:

```text
GPT-5.6 Terra
Medium reasoning
```

Let the primary model perform:
- exploration;
- implementation;
- testing;
- debugging;
- review;
- final response.

### B. Orchestrated Configuration

Use:

```text
Coordinator:
GPT-5.6 Terra
Medium reasoning

Default workers:
GPT-5.6 Luna
High reasoning

Escalated workers:
GPT-5.6 Luna
highest supported reasoning effort when justified
```

Use the repository orchestration policy normally.

Do not artificially force delegation when the policy would handle a task
directly.

## Task Selection

Evaluate using real repository tasks rather than synthetic toy tasks whenever
possible.

Include a mix of:

### Small Tasks

Examples:
- small bug fix;
- one-file behavior change;
- simple configuration correction.

These establish the overhead cost of orchestration.

### Medium Features

Examples:
- several-file feature;
- API plus tests;
- contained UI or backend behavior.

These are likely to benefit from bounded implementation workers.

### Broad Repository Tasks

Examples:
- cross-component feature;
- refactor;
- architecture-sensitive modification;
- task requiring significant repository discovery.

These test context isolation.

### Debugging Tasks

Include failures that generate:
- test logs;
- repeated investigation;
- uncertain root causes.

These test whether noisy debugging remains isolated in workers.

### Highly Coupled Tasks

Include at least some tasks that are difficult to decompose.

These test whether the coordinator correctly avoids excessive parallelism.

## Metrics

Record the following for each run.

### Correctness

- task completed: yes | no;
- acceptance criteria satisfied;
- targeted tests passing;
- full relevant test suite status;
- regressions introduced;
- manual corrections required after completion.

### Coordinator Usage

- coordinator input tokens;
- coordinator output tokens;
- number of large file reads performed directly by coordinator;
- number of broad searches performed directly by coordinator;
- number of compactions if visible;
- context size at completion if visible.

### Worker Usage

- total worker input tokens;
- total worker output tokens;
- number of workers spawned;
- worker reasoning efforts used;
- number of worker retries;
- number of fresh workers created after failure.

### Time

- total wall-clock time;
- time to first implementation;
- time spent on review;
- time spent on retries.

### Review

- number of review findings;
- number of findings confirmed valid;
- number of false or non-actionable findings;
- number of implementation defects found only by fresh-context review.

### Coordination

- number of overlapping-file conflicts;
- number of tasks that required re-decomposition;
- number of workers blocked on another worker;
- number of duplicated repository investigations.

## Cost Interpretation

Do not evaluate token count without considering model mix.

A run may use more total tokens but still be preferable if:
- expensive coordinator input is substantially lower;
- worker tokens are cheaper;
- correctness improves;
- the coordinator retains enough clean context to complete a larger task.

Track both:
- total token volume;
- estimated model-weighted cost.

## Suggested Experiment Method

For each representative task:

1. Start from the same repository state.
2. Use the same user request and acceptance criteria.
3. Run the task once with the single-agent baseline.
4. Reset the repository.
5. Run the task once with orchestration enabled.
6. Record metrics.
7. Compare resulting implementations using tests and independent inspection.

When practical, repeat important task classes more than once to reduce the
effect of stochastic variation.

Do not compare two runs that started from materially different repository
states.

## Primary Success Criteria

The orchestration system is providing value when, across meaningful tasks:

- coordinator input growth is lower;
- task success is maintained or improved;
- regression rate does not increase;
- manual correction does not increase;
- context-heavy exploration and debugging remain inside workers;
- coordination failures remain uncommon;
- worker review finds meaningful defects often enough to justify its cost.

## Warning Signs

Reconsider or simplify the orchestration system if:
- Terra repeatedly rereads all files the workers already investigated;
- multiple workers repeatedly perform the same exploration;
- small tasks become meaningfully slower due to unnecessary delegation;
- worker retries increase substantially;
- review findings are mostly speculative or false positives;
- concurrent workers frequently touch overlapping files;
- workers frequently depend on hidden assumptions from other workers;
- persistent state becomes larger than the useful task context;
- the coordinator spends more context managing workers than implementation
  would have required;
- total cost rises without a correctness or context-retention benefit.

## Tuning Decisions

Change one orchestration variable at a time when possible.

Examples:
- concurrency limit;
- threshold for delegation;
- reviewer usage;
- High versus highest reasoning escalation;
- state-file threshold;
- number of parallel implementers.

Avoid changing the entire architecture after one failed task.

## Initial Recommendation

Begin with:
- Terra Medium as coordinator;
- Luna High as default worker;
- highest Luna reasoning only by escalation;
- maximum four open subagent threads;
- one explorer only when architecture is unclear;
- bounded implementation workers;
- fresh reviewer for substantial or risky changes;
- persistent state only for genuinely multi-step work.

Measure real repository performance before adding additional specialized agents,
skills, hooks, or orchestration layers.
```

---

# Final Validation

Before reporting completion, verify that the repository contains all of these
files with the exact supplied contents:

```text
AGENTS.md
.codex/config.toml
.codex/agents/explorer.toml
.codex/agents/implementer.toml
.codex/agents/reviewer.toml
.agent-state/README.md
docs/agent/architecture.md
docs/agent/testing.md
```

Also verify that:

- the `.codex/agents/` directory exists;
- the `.agent-state/` directory exists;
- the `docs/agent/` directory exists;
- no file inside `.agents/` was modified;
- every listed file contains the exact supplied contents;
- no supplied content was rewritten;
- no supplied content was omitted;
- no additional agent-system files were invented;
- no unrelated repository files were modified;
- no top-level Codex model setting was added;
- `default_subagent_model` remains `gpt-5.6-luna`;
- `default_subagent_reasoning_effort` remains `high`;
- `max_concurrent_threads_per_session` remains `4`.

Do not perform any additional work after this validation.
