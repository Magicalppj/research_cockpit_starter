# Internal Architecture

Current multi-agent state is split across three truth-source layers: `assignments/*.yaml` stores worker-local cursors and next actions, `agents/*.yaml` stores generated agent identities and active assignment ids, and `coordinator_state.yaml` stores coordinator/UI selection. `current_state.yaml` remains supported for legacy/global compatibility and coordinator mirroring, but ordinary worker agents should not treat it as their own cursor.

This document is for maintainers and coding agents changing the Research Cockpit plugin internals. It describes the current source organization and the boundaries that should stay stable as the project grows.

## Goals

- Keep the public `research-cockpit` CLI stable for humans and agents.
- Keep project-specific research state outside the plugin, under the caller repository's `research_cockpit/` data root.
- Make source modules small enough that future changes can be reviewed by domain area.
- Avoid circular imports by keeping lower-level data helpers independent from commands and UI.
- Preserve `research_cockpit.model` as a compatibility facade while new code imports focused modules directly.

## Layering

```text
Public entrypoints
  cli.py
  command_registry.py
  commands/*
  ui/*

Workflow/domain layer
  agent_sessions.py
  assignment_scope.py
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
- `command_registry.py`: owns command metadata and legacy script-name to CLI-name mapping used in suggested commands.
- `commands/*.py`: command-specific argument parsing and workflow orchestration.
- `commands/_runtime.py`: shared command helpers for `load_validated_state(...)` and `finish_mutation(...)`.
- `commands/_assignment_scope_cli.py`: shared `--assignment` / `--coordinator` CLI flags and structured assignment-scope error output.
- `ui/`: researcher-facing Streamlit app, graph rendering wrappers, text labels, and view formatting helpers.

Commands are the public write boundary. A mutating command should validate, prepare candidate data, write YAML, append `interaction_log.yaml`, and optionally rebuild dashboard/context output. Dry-run paths must not write YAML, logs, or generated dashboards.

### Workflow And Domain Layer

- `agent_sessions.py`: assignment-scoped handoff payloads, canonical root boundaries, and worker startup command templates.
- `assignment_scope.py`: assignment mutation boundaries, out-of-scope write checks, and coordinator override handling.
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

- `agent_state.py`: `AgentRecord`, `AssignmentRecord`, `CoordinatorState`, and loaders for `agents/*.yaml`, `assignments/*.yaml`, and `coordinator_state.yaml`.
- `graph_core.py`: node/edge loading, focus path derivation, graph traversal, node context serialization, and graph JSON.
- `resources.py`: extracts link rows from node `links`, `linked_artifacts`, `config_path`, `path`, and `run_id` fields.
- `graph_views.py`: saved graph view normalization, ID generation, load, and upsert.
- `interaction_log.py`: append-only interaction event helpers and recent interaction selection.

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
- sidecar files such as `<data-root>/graph/graph_views.yaml` and `<data-root>/graph/interaction_log.yaml`
- `<data-root>/runs/*.yaml` for concrete experiment executions
- `<data-root>/gate_results/*.yaml` for gate metadata records
- `<data-root>/gate_results/*.json` for gate payloads written by `record-gate-result`
- `<data-root>/artifacts/**` for long-lived evidence payloads and ingest manifests

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
