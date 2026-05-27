# Plan: Long-Running Experiment Operations

## Status

Proposed todo list. Do not implement directly from this document without first choosing a slice and reviewing the current code paths.

## Date

2026-05-27

## Context

Downstream agents report that Research Cockpit already works well for structured research state: nodes, findings, artifacts, and next actions. The main gap is operational visibility for long-running experiments, multi-agent updates, and background jobs. The requested direction is to evolve from research state recording toward a lightweight research operations system.

This plan records the optimization backlog only. It intentionally does not change runtime code, schemas, CLI behavior, or dashboards.

## Goals

- Make concrete experiment runs observable without reading ad hoc logs.
- Standardize progress heartbeat and gate result files so agents can make mechanical decisions.
- Reduce build and lock friction in multi-agent mutation workflows.
- Separate completed conclusions from active follow-up work.
- Provide launcher and preflight conventions for repeatable long experiments.

## Non-Goals

- Do not replace the existing experiment, artifact, finding, or decision model.
- Do not make run/job records graph nodes by default until UI noise and lifecycle semantics are reviewed.
- Do not introduce a background service before the no-build and batch mutation workflows are clearly documented and tested.
- Do not require all experiments to use a launcher template; templates should start as recommended conventions.

## Recommended Order

### Phase 1: Run/Job Observability Foundation

#### Task 1: Design run/job record model

Define a first-class run/job record associated with an experiment. Start as a truth-source file collection rather than graph nodes unless a later decision says otherwise.

Candidate fields:

```yaml
run_id:
status: queued | running | completed | failed | cancelled
experiment_id:
started_at:
finished_at:
launcher:
command:
tmux_session:
pid:
log_root:
output_root:
monitor_command:
stop_command:
progress_file:
config_file:
```

Acceptance criteria:

- A run can be associated with exactly one experiment.
- The model supports external launchers such as tmux, shell scripts, Python scripts, and manual runs.
- The model can represent running, completed, failed, and cancelled lifecycle states.
- Validation rejects missing `run_id`, invalid `status`, and unknown `experiment_id`.

Likely files:

- `src/research_cockpit/model.py`
- `src/research_cockpit/types.py`
- `src/research_cockpit/commands/`
- `tests/`
- `capabilities/experiment-tracking.md`

#### Task 2: Add run/job CLI read and mutation commands

Add a minimal CLI surface for run lifecycle operations.

Candidate commands:

- `create-run`
- `update-run`
- `complete-run`
- `list-runs`
- `run-context`

Acceptance criteria:

- Commands support `--json` and safe compact output where useful.
- Mutating commands support `--no-build` if they update truth-source state.
- `run-context` gives an agent enough information to monitor or stop a run.
- Commands are listed by `research-cockpit commands --json`.

Verification:

- Unit tests cover create, update, complete, cancel, and invalid experiment id.
- Release check passes.

#### Task 3: Surface run status in context outputs

Expose active and recent runs in bootstrap, node context, and option workstream context.

Acceptance criteria:

- Experiment node context includes current/recent runs.
- Bootstrap summarizes running, stale, failed, and recently completed runs.
- Option workstream context includes run status for its experiments without requiring per-node reads.

Verification:

- Tests assert compact context includes run summary but does not become verbose.

### Phase 2: Progress And Gate Schemas

#### Task 4: Define progress heartbeat schema

Standardize a `progress.json` convention for long-running tasks.

Candidate schema:

```json
{
  "status": "running",
  "completed_steps": 12,
  "total_steps": 64,
  "last_update": "2026-05-26T16:30:00Z",
  "current_stage": "synthesis",
  "latest_artifact": "...",
  "warnings": []
}
```

Acceptance criteria:

- Schema supports unknown total steps.
- `last_update` can be used to detect stale heartbeat files.
- Dashboard/context can show progress without parsing arbitrary logs.
- Invalid progress files produce warnings, not hard crashes.

#### Task 5: Read progress heartbeat from run context

Teach context/dashboard code to read the `progress_file` linked from run records.

Acceptance criteria:

- Running run summaries include percent complete when possible.
- Stale runs are identified when `last_update` exceeds a configured or documented threshold.
- Latest artifact and warnings are visible to agents.

Verification:

- Tests cover missing, stale, malformed, and valid progress files.

#### Task 6: Define standard gate result schema

Standardize `gate_result.json` for dataset, cache, smoke, training, evaluation, and preflight gates.

Candidate schema:

```json
{
  "gate_type": "dataset_check",
  "passed": true,
  "expected": {},
  "observed": {},
  "fatal_failures": {},
  "warnings": [],
  "next_allowed_action": "precompute"
}
```

Acceptance criteria:

- Gate results are machine-readable and linked to experiments or runs.
- `passed: false` blocks recommended next steps unless explicitly overridden.
- Warnings remain non-blocking but visible.
- `next_allowed_action` can drive action guidance.

#### Task 7: Add gate result ingest and context summary

Add CLI support for recording or ingesting gate results.

Candidate commands:

- `record-gate-result`
- `ingest-gate-result`

Acceptance criteria:

- Gate result files can be attached to an experiment or run.
- Context summaries show latest gate state and blocking failures.
- Gate result records can be linked to artifacts when the file is in the artifact store.

Verification:

- Tests cover passed, failed, warning-only, and malformed gate files.

### Phase 3: Multi-Agent Build And Lock Workflow

#### Task 8: Strengthen no-build and batch mutation guidance

Document and expose a recommended multi-agent mutation pattern:

1. Agent mutations use `--no-build`.
2. Agents run lightweight validation where needed.
3. Coordinator or watcher performs `validate`, `build`, and `smoke`.

Acceptance criteria:

- `commands --json` clearly identifies commands that support `--no-build`.
- Bootstrap mutation guidance recommends batch mode for multi-agent workflows.
- Docs show examples for consecutive findings, artifacts, runs, and next action updates.

#### Task 9: Evaluate background builder or watcher

Design a `watch-build` or background builder workflow after Task 8 is stable.

Acceptance criteria:

- Builder watches truth-source files and rebuilds dashboards asynchronously.
- Builder avoids overlapping builds.
- Builder reports last build time, status, and errors.
- Mutating commands do not depend on the watcher being active.

Open decision:

- Decide whether this should be a long-running CLI process, a simple polling script, or an optional integration outside the core plugin.

### Phase 4: Next Action Clarity

#### Task 10: Split next action context by scope

Separate next actions in dashboard/context output.

Proposed buckets:

- `focus_next_actions`
- `parent_option_next_actions`
- `parent_problem_next_actions`
- `global_coordinator_next_actions`
- `stale_terminal_node_next_actions`

Acceptance criteria:

- Agents can identify the current focus task without scanning unrelated project-level actions.
- Terminal node next actions are clearly marked stale or migration candidates.
- Existing `current_state.next_actions` remains supported for compatibility.

Verification:

- Tests cover focus node, parent chain, global-only, and terminal stale next action cases.

#### Task 11: Add terminal next action cleanup workflow

Provide a way to migrate live next actions off completed experiments.

Candidate command:

- `migrate-terminal-next-actions`

Acceptance criteria:

- Completed nodes do not keep live work silently.
- The command can create a follow-up experiment for a single gate.
- For larger work, guidance points to `create-workstream` child branches.
- Dry-run output shows proposed changes before mutation.

### Phase 5: Launcher Templates And Preflight Gates

#### Task 12: Define launcher output conventions

Document standard launcher output files:

- `run_record.txt`
- `progress.json`
- `gate_result.json`
- `artifact_manifest.json`

Acceptance criteria:

- Templates can be used by shell, Python, or manual launch flows.
- Outputs are easy to ingest into run, gate, and artifact records.
- Templates do not require a specific scheduler.

#### Task 13: Add launcher templates

Provide recommended templates for:

- dry run
- smoke gate
- full run
- artifact capture
- validate/build
- next action update

Acceptance criteria:

- Templates write standard output files.
- Templates include safe stop/monitor command hints when applicable.
- Documentation shows how to adapt templates to a project.

#### Task 14: Define resource budget and preflight gate schema

Standardize preflight checks for long runs.

Candidate fields:

```json
{
  "disk_available_gb": 1200,
  "estimated_required_gb": 800,
  "gpu_ids": [0, 1, 2],
  "port_available": true,
  "conflicting_processes": []
}
```

Acceptance criteria:

- Preflight gate can be represented as a normal gate result.
- Disk, GPU, port, cache directory, and conflicting process checks are supported as fields.
- Failed preflight gates block full-run recommendations.

## Checkpoints

### Checkpoint A: After Phase 1

- Run records can be created, updated, listed, and read in context.
- No dashboard build behavior changes are required.
- Full unit tests and release check pass.

### Checkpoint B: After Phase 2

- Progress and gate files are machine-readable and visible in context.
- Agents can answer: "is the run alive?", "how far did it get?", and "can it proceed?"
- Malformed external files produce warnings instead of crashes.

### Checkpoint C: After Phase 3

- Multi-agent workflows have a documented no-build path.
- Async build strategy is either implemented or explicitly deferred.

### Checkpoint D: After Phase 5

- Long experiments can start from a template and produce standard run/progress/gate/artifact outputs.
- Preflight failures are visible before expensive runs begin.

## Risks And Mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Run/job records duplicate experiment state | Medium | Keep runs as execution instances attached to experiments; do not turn them into graph nodes by default. |
| Dashboard becomes noisy | Medium | Show compact run summaries first; detailed run views stay behind context commands or expandable UI sections. |
| External progress files are malformed or missing | Medium | Treat as warnings; never block reading the graph. |
| Background builder introduces concurrency bugs | High | Implement only after no-build batch guidance is stable; avoid overlapping builds. |
| Gate schemas become too generic to be useful | Medium | Start with common fields and allow domain-specific `expected`/`observed` maps. |
| Launcher templates become too opinionated | Medium | Treat templates as conventions, not mandatory runtime dependencies. |

## Open Questions

- Should run/job records live under `research_cockpit/runs/*.yaml`, under each experiment, or in a dedicated graph-adjacent truth-source directory?
- Should runs ever appear as graph nodes, or only as experiment detail records?
- What heartbeat stale threshold should be the default?
- Should gate result ingestion create artifacts automatically, or only link existing artifact records?
- Should background building be core CLI functionality or a dev/operator helper script?
- How strict should validation be for terminal nodes carrying `next_actions`?

## Suggested First Slice

Start with Phase 1 and implement only the run/job foundation:

1. Add the run record model and validation.
2. Add minimal run lifecycle CLI commands.
3. Surface run summaries in experiment context.

This slice is enough to solve the immediate observability gap without changing launcher behavior, dashboard build workflow, or gate semantics.
