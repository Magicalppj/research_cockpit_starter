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
  assignment_records.py
  work_packets.py
  coordination.py
  synthesis.py
  coordinator_operations.py
  coordinator_decisions.py
  assignment_results.py
  assignment_reviews.py
  maintenance_actions.py
  artifact_records.py
  artifact_inventory.py
  artifact_migration.py
  artifact_gc.py
  evidence_bundles.py
  evidence_staging.py
  run_closeout.py
  operation_receipts.py
  milestone_handoffs.py
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
  storage_layout.py
  paths.py
  types.py
```

Dependency direction should generally flow downward. For example, `commands/*` may import domain modules, but `decisions.py` should not import command modules. UI code may call domain/context helpers, but domain helpers should not import Streamlit code.

## Module Responsibilities

### Public Entry Points

- `cli.py`: exposes canonical role groups plus the retained diagnostic/read surface.
- `command_registry.py`: owns CLI routing and re-exports the role contract choices used by discovery.
- `role_contracts.py`: is the single source for command audiences, surfaces, intents, scope, verification, canonical replacement, and cutover disposition.
- `commands/list_agent_commands.py`: projects the route and role contract into full, compact, role-filtered, and name-filtered manifests.
- `commands/*.py`: command-specific argument parsing and workflow orchestration.
- `commands/work_*.py`, `review_*.py`, `coord_*.py`, and `maintenance_role_*.py`: public role facade argument parsing.
- `commands/_runtime.py`: shared command helpers for `load_validated_state(...)` and `finish_mutation(...)`.
- `commands/_assignment_scope_cli.py`: shared `--assignment` / `--coordinator` CLI flags and structured assignment-scope error output.
- `ui/`: researcher-facing Streamlit app, graph rendering wrappers, text labels, and view formatting helpers. The Coordination page delegates to `coordination.py` and must not duplicate assignment readiness or overlap semantics.

Commands are the public write boundary. A mutating command should validate, prepare candidate data, write truth-source files, append through the active interaction backend via `interaction_log.py`, and optionally rebuild dashboard/context output. Dry-run paths must not write truth sources, interaction history, or generated dashboards.

The root `SKILL.md` is only a role router. Default agent instructions live in `worker-loop.md`, `reviewer-loop.md`, `coordinator-loop.md`, and `maintainer-loop.md`; deeper capability documents are conditional references, not startup context. Broad manifest discovery is not a normal startup step.

### Workflow And Domain Layer

- `agent_sessions.py`: assignment-scoped handoff payloads, canonical root boundaries, and worker startup command templates.
- `assignment_scope.py`: assignment mutation boundaries, out-of-scope write checks, and coordinator override handling.
- `assignment_leases.py`: claim, renew, release, owner/epoch checks, lease renewal planning, heartbeat hooks, and expired-lease reassignment guards.
- `assignment_runs.py`: composes lease renewal with the existing run-creation domain transaction for `work start`.
- `assignment_records.py`: admits incremental reference or explicit managed evidence, renews leases, and writes idempotent record receipts.
- `coordinator_operations.py`: validates and executes `coord_assign_v1` graph/session operations.
- `coordinator_decisions.py`: dispatches strict `coord_decide_v1` decision/baseline actions.
- `maintenance_actions.py`: validates and dispatches bounded maintenance plans, including explicit legacy storage migration and revision-bound managed-payload GC.
- `work_packets.py`: bounded assignment projections, dependency/input readiness, lease state, stable revisions, and unchanged polling.
- `assignment_results.py`: validates `work_close_v1`, performs operation replay checks, stages optional final evidence, and delegates one atomic assignment closeout.
- `coordination.py`: builds the indexed, revisioned, paginated Coordination Snapshot and its shared internal state projection without loading the full graph on a fresh index.
- `synthesis.py`: projects revision-bound selected dependency Evidence Bundles into a bounded Synthesis Packet; it does not scan unrelated accepted history.
- `assignment_reviews.py`: builds bounded review packets, records reviewer-only Evidence Bundles, and applies revision-bound coordinator verdicts without rewriting producer results.
- `evidence_bundles.py`: constructs and validates bounded work/review result contracts and their stable revisions.
- `evidence_staging.py`: admits final evidence outside the truth commit lock. Reference mode writes bounded metadata without copying bytes; explicit managed mode streams copy/hash into configured external storage before preparing atomic artifact-record changes.
- `artifact_records.py` and `artifact_inventory.py`: normalize provenance records and maintain bounded metadata inventory without treating payload bytes as state truth.
- `artifact_migration.py`: performs resumable, verified legacy-to-managed transitions and preserves source evidence on cross-filesystem copy.
- `artifact_gc.py`: enforces revision-bound verified managed-payload quarantine and delayed purge, with immutable transition manifests and exact-retry recovery.
- `run_closeout.py`: owns the combined run, gate, finding, artifact record, Evidence Bundle, experiment, cursor, and lease transaction.
- `operation_receipts.py`: normalized operation hashes, durable receipt lookup from interaction events, and the derived incremental operation index.
- `mutation_runtime.py`: optimistic multi-file commits, rollback, operation-event append, and post-commit derived-index patching.
- `milestone_handoffs.py`: captures a root truth revision from structured state and transition manifests, never payload roots; it reuses one full validation state across build and compact smoke, evaluates coordination blockers, and commits an immutable operation-id-scoped handoff report.
- `root_snapshot.py`: targeted graph snapshots. `load_indexed_root_snapshot(...)` is the no-full-fallback entry point for latency-bounded reads.
- `validation_index.py`: generated graph/sidecar signatures and targeted lookup maps used by incremental validation and read models.
- `node_onboarding.py`: builds legacy-compatible node handoff data used by bounded context projections.
- `context_packs.py`: builds agent context, focus context, current-state payloads, context metadata, and dashboard Markdown.
- `search_index.py`: indexes nodes, notes, and linked local resources.
- `decisions.py`: decision evidence summaries, acceptance checklists, traces, and decision rows.
- `option_workstreams.py`: option subtree, workstream context, branch comparison, and workstream rows.
- `run_summaries.py`: run/job summaries attached to experiments and bounded execution/coordination projections.
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
- `storage_layout.py`: resolves external managed artifact storage and rejects overlap with the state root.
- `paths.py`: plugin-root, portable project locator, and data-root discovery.

These modules should remain free of command, UI, and dashboard dependencies.

## Compatibility Facade

`model.py` remains as a compatibility facade for older imports and tests. It should re-export focused helpers where needed, but new code should prefer direct imports from the owning module:

- Use `graph_core.py` for graph traversal and node context.
- Use `agent_state.py` for agent, assignment, and coordinator state records/loaders.
- Use `assignment_scope.py` for assignment-scoped mutation boundaries.
- Use `work_packets.py` for assignment-facing read projections and revision polling.
- Use `assignment_leases.py` and `assignment_runs.py` for lease-aware worker mutations.
- Use `coordination.py` for coordinator/UI portfolio projections and `synthesis.py` for selected-evidence synthesis assignments.
- Use `milestone_handoffs.py` only for coordinator merge, release, or research-stage closeout gates.
- Use `operation_receipts.py` for ordinary mutation idempotency; do not create per-operation receipt files. `handoffs/*.yaml` is the deliberate exception because a milestone report is durable research/release truth, not only a retry receipt.
- Use `assignment_results.py` and `run_closeout.py` for assigned terminal mutations.
- Use `assignment_reviews.py` for reviewer/coordinator review lifecycle operations.
- Use `evidence_bundles.py` and `evidence_staging.py` for bounded result contracts and final payload staging.
- Use `runtime_ids.py` for collision-resistant run, record, artifact, and follow-up ids.
- Use `root_snapshot.py` and `validation_index.py` for bounded indexed graph reads.
- Use `resources.py` for link/resource rows.
- Use `context_packs.py` for context payloads.
- Use `decisions.py`, `option_workstreams.py`, `run_summaries.py`, `progress.py`, `gate_results.py`, `gate_result_records.py`, and `suggestions.py` for domain logic.
- Use `storage.py` and `types.py` for low-level data contracts.

Do not add new large domain logic to `model.py`. If a new behavior is hard to place, create a small focused module or extend the closest existing domain module.

## Generated Versus Truth-Source Data

Truth-source data lives in:

- `<data-root>/storage.yaml` for the machine-local external managed artifact policy
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
- `<data-root>/gate_results/*.json` for structured gate payloads
- `<data-root>/artifact_records/*.yaml` for lightweight evidence metadata, provenance, storage mode, integrity, inventory, retention, lifecycle, and availability
- `<data-root>/artifact_migrations/*.yaml` for resumable legacy-to-managed migration journals
- `<data-root>/artifact_gc_manifests/*.yaml` for immutable managed-payload GC transition manifests
- `<data-root>/artifacts/**` for readable legacy payloads only; configured external managed storage owns new Cockpit-managed payload bytes
- `<data-root>/handoffs/*.yaml` for immutable operation-id-scoped milestone reports and revision-bound gate summaries

Runtime access rules:

- `root_snapshot.py` owns compact indexed reads for known-node and assignment-scoped context.
- `operation_receipts.py` derives `<data-root>/dashboards/operation_index.json` from immutable interaction events; a missing or stale index rebuilds from events and never becomes truth.
- `mutation_runtime.py` owns targeted preflight, optimistic file checks, atomic multi-file transactions, rollback, and validation-index patching.
- `validation_index.py` is a derived acceleration index; missing, incompatible, or stale indexes must fall back to full validation and return explicit refresh commands.
- `coordination.py` consumes the assignment projection in the validation index when fresh and performs an explicit assignment-file fallback when stale; UI and CLI use this same builder.
- `interaction_log.py` owns both legacy YAML compatibility and the JSONL event backend. Commands must append through this module and must not rewrite interaction history.
- `run_closeout.py` owns both legacy `run_closeout_v1` and facade `work_close_v1` terminal transactions; reference evidence records metadata without copying bytes, while explicit managed copy/hash occurs before lock acquisition and publishes record truth atomically.
- `milestone_handoffs.py` never holds the canonical mutation lock during validate/build/smoke. It checks the target revision before and inside the short report transaction; `handoffs/` is excluded from the target revision to avoid a self-referential receipt.
- `commands/build_dashboard.py` uses the derived `.dashboard-build.lock` for generated files. A handoff may therefore build outside the canonical truth lock while same-root dashboard writers remain serialized.

Generated output lives in `<data-root>/dashboards/` and can be rebuilt with:

```sh
research-cockpit build --root <data-root>
```

Generated dashboard files should not become the source of truth for commands or domain logic. The Streamlit graph views may use fresh generated dashboard files as a read-through cache for refresh speed, but they must fall back to truth-source builders and surface a stale warning when generated files are missing, malformed, or older than truth-source state.

A milestone caller invokes `coord handoff` directly and must not run standalone full validate/build/smoke first. The orchestrator performs one sequence and emits one bounded receipt; standalone commands remain diagnostic entry points.

## Adding A New Workflow

1. Put shared business logic in the relevant domain module, not directly in a command.
2. Add a `commands/<name>.py` wrapper for CLI argument parsing and write orchestration.
3. Register the command in `cli.py` and `command_registry.py`.
4. If the workflow writes YAML, use or extend `commands/_runtime.py`.
5. Add tests for the domain helper and CLI behavior.
6. Update capability documentation if agents need to call the workflow.
