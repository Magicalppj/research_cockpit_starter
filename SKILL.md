---
name: research-cockpit
description: Read, validate, update, and summarize project-local Research Cockpit state under a repository research_cockpit/ directory.
---

# Research Cockpit

Use the cockpit as structured research state. Load only the context needed for the current task; generated dashboards and broad summaries are not startup prerequisites.

## Read Route

Resolve one canonical data root. Prefer an explicit absolute `--root <data-root>` when cwd is unreliable. If no root exists, initialize it with `init --root <data-root>`; build only when a consumer needs generated dashboards.

Choose exactly one startup path:

```sh
# Assignment-scoped worker
research-cockpit agent-session-context --root <data-root> --assignment <assignment_id> --compact --json
# Known node without an assignment
research-cockpit context --root <data-root> --id <node_id> --view execution --compact --json
# Unknown target or coordinator triage
research-cockpit bootstrap --root <data-root> --coordinator --json
```

For repeated known-node polling, reuse the returned revision:

```sh
research-cockpit context --root <data-root> --id <node_id> --view execution --since <revision> --compact --json
```

An unchanged poll returns only `changed: false` and `revision`. A changed poll returns the complete bounded execution view; the opaque revision is not a historical field-delta cursor.

Request wider context only when needed: add `--with-bootstrap --with-artifacts` to the default context view, or read generated context packs for a global dashboard scan. Do not chain bootstrap and context for normal known-node work.

## Invariants

- `assignments/*.yaml` is the worker cursor and task boundary; `coordinator_state.yaml` is coordinator/UI selection; `current_state.yaml` is legacy/coordinator compatibility state.
- Structured truth lives in nodes, assignments, runs, gate results, artifact records, coordinator state, and append-only interaction events. Markdown notes are supporting records.
- Use CLI mutations where supported. Keep mutations against one root sequential and normally pass `--no-build`.
- Never execute a suggested command merely because it appears in action guidance.
- Do not set a decision directly to `accepted`; use `accept-decision`.
- Do not close a problem or option while active descendants remain. Inspect `close-branch --dry-run` before explicitly closing or cancelling descendants.
- Ordinary run output is an artifact record. Promote a graph artifact only for durable navigation/evidence and record a promotion reason.
- Treat `graph/interaction_log.yaml` as an immutable legacy prefix after the event manifest exists; use migration/repair commands rather than editing either backend.
- Generated `dashboards/*` files are rebuilt, never hand-authored.

## Worker Loop
1. Read one assignment or execution context.
2. Make the smallest supported mutation with assignment scope when acting as a worker.
3. Read compact fields `verified` and `additional_verification_required`. When they are `true` and `false`, respectively, the write is worker-verified; do not validate or reread context.
4. Otherwise verify only the reported changed nodes/files/records. Do not run full build or root smoke after an ordinary edit.

```sh
research-cockpit validate --root <data-root> --changed-node <node_id> --json
research-cockpit context --root <data-root> --id <node_id> --view execution --compact --json
```
If changed validation reports `fallback.used_full_validation: true`, run its `fallback.recommended_commands` to refresh the affected validation index, then retry.

## Run Closeout
A normal experiment cycle uses three mutations: create and start the run, optionally ingest its payload, then close everything else atomically.

```sh
research-cockpit create-run --root <data-root> --assignment <assignment_id> --id <run_id> --experiment <experiment_id> --status running --start-experiment --json --compact --no-build
research-cockpit ingest-artifact --root <data-root> --assignment <assignment_id> --node <experiment_id> --from <output_dir> --run-id <run_id> --json --compact --no-build
research-cockpit complete-run --root <data-root> --assignment <assignment_id> --file closeout.yaml --json --compact --no-build
```

Use `run_closeout_v1` to finish the run, record gates and a finding, link the existing artifact record, set the experiment outcome, create at most one sibling `next_experiment`, and advance the assignment cursor in one transaction. After ingest, set `artifact_record.existing_record_id`; do not repeat `complete-experiment`, `create-followup-experiment`, or `set-cursor` for the same closeout.

Successful non-dry-run results from these three commands are internally verified. Omit ingest when no payload exists; run extra worker verification only when the compact result explicitly requires it.

## Verification Tiers
- `internal_verify`: a successful mutation already validated its candidate and committed without a stale-write conflict.
- `worker_verify`: changed-scope validate plus bounded execution context only when a mutation requests additional verification.
- `milestone_handoff`: coordinator merge, release, or research-stage closeout. An ordinary agent turn is not a milestone handoff.

Use changed smoke only when the task explicitly needs the integrated one-node check:

```sh
research-cockpit smoke --root <data-root> --scope changed --id <node_id> --json --progress
```

Only milestone handoff runs the full gate:

```sh
research-cockpit validate --root <data-root> --json
research-cockpit build --root <data-root>
research-cockpit smoke --root <data-root> --json --progress
```

Default `smoke` is compact for large roots. Use `--full` only to diagnose the legacy subprocess workflow. Progress events go to stderr; stdout remains one JSON payload.

## Discovery
The skill is a router, not a sequence to execute. Discover commands only when the selected capability does not provide the needed path:

```sh
research-cockpit commands --json --compact --summary-only
research-cockpit commands --json --compact --name <command>
```

## Capability Routing
- Graph files, views, and interaction history: `capabilities/graph-state.md`
- Startup, focus, context, search, and polling: `capabilities/focus-context.md`
- Nodes, lifecycle, fields, and suggestions: `capabilities/node-management.md`
- Normal experiment start, optional ingest, and transactional closeout: `capabilities/experiment-cycle.md`
- Advanced gates, findings, artifacts, retention, and workstreams: `capabilities/experiment-tracking.md`
- Decisions and acceptance: `capabilities/decision-adr.md`
- UI and frontend behavior: `capabilities/ui-dashboard.md`
- Installation, CLI, environment, and launchers: `capabilities/integrations.md`, `docs/launcher-output-conventions.md`
- Cleanup, worktrees, retention, and large-root hygiene: `capabilities/maintenance.md`
- Validation, dependency, and recovery failures: `capabilities/troubleshooting.md`

Read only the selected capability file. Use `docs/internal-architecture.md` for implementation boundaries, not routine operation.

## Write Boundary
Project state belongs under the caller's `research_cockpit/`, never the plugin directory. CLI-managed truth includes agents, assignments, coordinator/current state, graph nodes/edges/views/events, runs, gate results, artifact records/migrations, and artifact metadata. Notes and artifact payloads may be written directly when appropriate; structured links and status still go through the CLI.

Use one canonical root across git worktrees. Workers update assignment cursors; coordinator commands own global focus and lifecycle cleanup. Preserve useful evidence and closeout records before deleting a worktree.

Set `RESEARCH_COCKPIT_ROOT` and, for workers, `RESEARCH_COCKPIT_ASSIGNMENT_ID` when repeated absolute flags are inconvenient. If the console script is unavailable, use the same interpreter with `python -m research_cockpit.cli`. If dependencies are missing, install the plugin editable from its root. Markdown is UTF-8; do not persist machine-specific absolute paths.
