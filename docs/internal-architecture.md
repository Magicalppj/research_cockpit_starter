# Internal Architecture

Current multi-agent state is split across three truth-source layers: `assignments/*.yaml` stores worker-local cursors and next actions, `agents/*.yaml` stores generated agent identities and active assignment ids, and `coordinator_state.yaml` stores coordinator/UI selection. `current_state.yaml` remains supported for legacy/global compatibility and coordinator mirroring, but ordinary worker agents should not treat it as their own cursor.

This document is for maintainers and coding agents changing the Research Cockpit plugin internals. It describes the current source organization and the boundaries that should stay stable as the project grows.

## Goals

- Keep one canonical public CLI surface per release while preserving legacy structured data and artifact compatibility.
- Keep project-specific research state outside the plugin, under the caller repository's `research_cockpit/` data root.
- Make source modules small enough that future changes can be reviewed by domain area.
- Avoid circular imports by keeping lower-level data helpers independent from commands and UI.
- Preserve `research_cockpit.model` as a compatibility facade while new code imports focused modules directly.

## Layering

```text
Public entrypoints
  cli.py
  command_registry.py
  role_contracts.py
  commands/*
  ui/*

Workflow/domain layer
  agent_sessions.py
  assignment_scope.py
  assignment_leases.py
  assignment_runs.py
  work_packets.py
  operation_receipts.py
  mutation_runtime.py
  root_snapshot.py
  validation_index.py
  node_onboarding.py
  context_packs.py
  search_index.py
  decisions.py
  option_workstreams.py
  run_summaries.py
  progress.py
  gate_results.py
  gate_result_records.py
  suggestions.py

Graph and sidecar state
  agent_state.py
  graph_core.py
  resources.py
  graph_views.py
  interaction_log.py

Storage and shared types
  storage.py
  paths.py
  types.py
```

Dependency direction should generally flow downward. For example, `commands/*` may import domain modules, but `decisions.py` should not import command modules. UI code may call domain/context helpers, but domain helpers should not import Streamlit code.

## Module Responsibilities

### Public Entry Points

- `cli.py`: maps `research-cockpit <subcommand>` to command modules.
- `command_registry.py`: owns CLI routing and re-exports the role contract choices used by discovery.
- `role_contracts.py`: is the single source for command audiences, surfaces, intents, scope, verification, canonical replacement, and cutover disposition.
- `commands/list_agent_commands.py`: projects the route and role contract into full, compact, role-filtered, and name-filtered manifests.
- `commands/*.py`: command-specific argument parsing and workflow orchestration.
- `commands/_runtime.py`: shared command helpers for `load_validated_state(...)` and `finish_mutation(...)`.
- `commands/_assignment_scope_cli.py`: shared `--assignment` / `--coordinator` CLI flags and structured assignment-scope error output.
- `ui/`: researcher-facing Streamlit app, graph rendering wrappers, text labels, and view formatting helpers.

Commands are the public write boundary. A mutating command should validate, prepare candidate data, write truth-source files, append through the active interaction backend via `interaction_log.py`, and optionally rebuild dashboard/context output. Dry-run paths must not write truth sources, interaction history, or generated dashboards.

The root `SKILL.md` is only a role router. Default agent instructions live in `worker-loop.md`, `reviewer-loop.md`, `coordinator-loop.md`, and `maintainer-loop.md`; deeper capability documents are conditional references, not startup context. Broad manifest discovery is not a normal startup step.

### Workflow And Domain Layer

- `agent_sessions.py`: assignment-scoped handoff payloads, canonical root boundaries, and worker startup command templates.
- `assignment_scope.py`: assignment mutation boundaries, out-of-scope write checks, and coordinator override handling.
- `assignment_leases.py`: claim, renew, release, owner/epoch checks, lease renewal planning, heartbeat hooks, and expired-lease reassignment guards.
- `assignment_runs.py`: composes lease renewal with the existing create-run domain transaction for `work start`.
- `work_packets.py`: bounded assignment projections, dependency/input readiness, lease state, stable revisions, and unchanged polling.
- `operation_receipts.py`: normalized operation hashes, durable receipt lookup from interaction events, and the derived incremental operation index.
- `mutation_runtime.py`: optimistic multi-file commits, rollback, operation-event append, and post-commit derived-index patching.
- `root_snapshot.py`: targeted graph snapshots. `load_indexed_root_snapshot(...)` is the no-full-fallback entry point for latency-bounded reads.
- `validation_index.py`: generated graph/sidecar signatures and targeted lookup maps used by incremental validation and read models.
- `node_onboarding.py`: builds read-only node handoff payloads for `node-context`.
- `context_packs.py`: builds agent context, focus context, current-state payloads, context metadata, and dashboard Markdown.
- `search_index.py`: indexes nodes, notes, and linked local resources.
- `decisions.py`: decision evidence summaries, acceptance checklists, traces, and decision rows.
- `option_workstreams.py`: option subtree, workstream context, branch comparison, and workstream rows.
- `run_summaries.py`: run/job summaries attached to experiments, bootstrap, and option workstream context.
- `progress.py`: standard `progress.json` heartbeat parsing and stale heartbeat warnings.
- `gate_results.py`: standard `gate_result.json` validation, blocking semantics, and preflight resource normalization.
- `gate_result_records.py`: sidecar metadata records that link gate files to experiments, runs, and artifacts.
- `suggestions.py`: next-action suggestion generation and suggestion lifecycle summaries.

Domain modules should work with loaded `ResearchNode` objects and plain dictionaries. They should not perform writes unless explicitly documented.

### Graph And Sidecar State

- `agent_state.py`: `AgentRecord`, additive `AssignmentRecord` truth fields, field-level contract validation, targeted/full assignment loaders, `CoordinatorState`, and sidecar loaders.
- `graph_core.py`: node/edge loading, focus path derivation, graph traversal, node context serialization, and graph JSON.
- `resources.py`: extracts link rows from node `links`, `linked_artifacts`, `config_path`, `path`, and `run_id` fields.
- `graph_views.py`: saved graph view normalization, ID generation, load, and upsert.
- `interaction_log.py`: append-only interaction event helpers, recent interaction selection, and the truth record for idempotent operation receipts.

Sidecar files such as `graph_views.yaml` and `interaction_log.yaml` support UI and agent context. They are not schema replacements for truth-source graph nodes.

### Storage And Shared Types

- `types.py`: `ResearchNode`, `ValidationError`, valid node/status/workstream constants, and shared search constants.
- `storage.py`: `load_yaml`, `save_yaml`, node-file path helpers, and relative path normalization.
- `paths.py`: plugin-root and data-root discovery.

These modules should remain free of command, UI, and dashboard dependencies.

## Compatibility Facade

`model.py` remains as a compatibility facade for older imports and tests. It should re-export focused helpers where needed, but new code should prefer direct imports from the owning module:

- Use `graph_core.py` for graph traversal and node context.
- Use `agent_state.py` for agent, assignment, and coordinator state records/loaders.
- Use `assignment_scope.py` for assignment-scoped mutation boundaries.
- Use `work_packets.py` for assignment-facing read projections and revision polling.
- Use `assignment_leases.py` and `assignment_runs.py` for lease-aware worker mutations.
- Use `operation_receipts.py` for operation idempotency; do not create per-operation receipt files.
- Use `runtime_ids.py` for collision-resistant run, record, artifact, and follow-up ids.
- Use `root_snapshot.py` and `validation_index.py` for bounded indexed graph reads.
- Use `resources.py` for link/resource rows.
- Use `context_packs.py` for context payloads.
- Use `decisions.py`, `option_workstreams.py`, `run_summaries.py`, `progress.py`, `gate_results.py`, `gate_result_records.py`, and `suggestions.py` for domain logic.
- Use `storage.py` and `types.py` for low-level data contracts.

Do not add new large domain logic to `model.py`. If a new behavior is hard to place, create a small focused module or extend the closest existing domain module.

## Generated Versus Truth-Source Data

Truth-source data lives in:

- `<data-root>/agents/*.yaml` for generated agent identities, display names, and active assignment ids
- `<data-root>/assignments/*.yaml` for worker-local assignment roots, cursors, next actions, and assignment status
- `<data-root>/coordinator_state.yaml` for coordinator/UI selected node, selected assignment, global next actions, and dashboard filters
- `<data-root>/current_state.yaml` for legacy/coordinator compatibility focus, baseline, and global next-action state
- `<data-root>/graph/nodes/*.yaml`
- `<data-root>/graph/edges.yaml` when present
- sidecar files such as <data-root>/graph/graph_views.yaml and the legacy <data-root>/graph/interaction_log.yaml prefix
- <data-root>/graph/interaction_events/** for the active append-only JSONL audit backend
- `<data-root>/runs/*.yaml` for concrete experiment executions
- `<data-root>/gate_results/*.yaml` for gate metadata records
- `<data-root>/gate_results/*.json` for gate payloads written by `record-gate-result`
- `<data-root>/artifact_records/*.yaml` for lightweight evidence metadata created by record-only artifact ingest
- `<data-root>/artifact_migrations/*.yaml` for artifact demotion audit reports
- `<data-root>/artifacts/**` for long-lived evidence payloads and ingest manifests

Runtime access rules:

- `root_snapshot.py` owns compact indexed reads for known-node and assignment-scoped context.
- `operation_receipts.py` derives `<data-root>/dashboards/operation_index.json` from immutable interaction events; a missing or stale index rebuilds from events and never becomes truth.
- `mutation_runtime.py` owns targeted preflight, optimistic file checks, atomic multi-file transactions, rollback, and validation-index patching.
- `validation_index.py` is a derived acceleration index; missing, incompatible, or stale indexes must fall back to full validation and return explicit refresh commands.
- `interaction_log.py` owns both legacy YAML compatibility and the JSONL event backend. Commands must append through this module and must not rewrite interaction history.
- `run_closeout.py` owns the `run_closeout_v1` transaction across run, gate metadata, artifact-record link, finding, and next actions.

Generated output lives in `<data-root>/dashboards/` and can be rebuilt with:

```sh
research-cockpit build --root <data-root>
```

Generated dashboard files should not become the source of truth for commands or domain logic. The Streamlit UI may use fresh generated dashboard files as a read-through cache for refresh speed, but it must fall back to truth-source builders and surface a stale warning when generated files are missing, malformed, or older than truth-source state.

## Adding A New Workflow

1. Put shared business logic in the relevant domain module, not directly in a command.
2. Add a `commands/<name>.py` wrapper for CLI argument parsing and write orchestration.
3. Register the command in `cli.py` and `command_registry.py`.
4. If the workflow writes YAML, use or extend `commands/_runtime.py`.
5. Add tests for the domain helper and CLI behavior.
6. Update capability documentation if agents need to call the workflow.
