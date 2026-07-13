---
name: research-cockpit
description: Use this skill to read, validate, update, and summarize project-local Research Cockpit state stored in a repository `research_cockpit/` directory.
---

# Research Cockpit

Research Cockpit stores project-local research state in a repository `research_cockpit/` directory. Use it to read context, validate graph health, record findings, update assignment/coordinator state, and generate dashboard/context files without hand-editing YAML.

## Startup Contract

1. Resolve the data root:
   - Prefer an explicit `--root <path-to-research_cockpit>`.
   - Use an absolute `--root` when the agent shell may not preserve the expected current working directory.
   - If omitted, commands search from the current working directory upward for `research_cockpit/`.
   - In this plugin repo only, commands fall back to `examples/demo_research_cockpit/`.
   - If the caller repository has no data root yet, initialize one with `research-cockpit init --root research_cockpit` from the caller repository root. Use `research-cockpit init --root research_cockpit --build --json` when the next step will read generated context packs.

2. Choose one read path:
   - Assigned downstream agent with an `assignment_id`: run `agent-session-context --compact --json`; use scoped bootstrap only when validation/search/global summaries are needed. Treat global `current_state` focus and `coordinator_state` as coordinator metadata only. Use `--agent` only as a lookup convenience when that agent has exactly one active assignment.
   - Known node id: use compact `context` directly; do not run a separate bootstrap first.
   - Unknown target or global triage: run read-only bootstrap.
   - Minimal older handoff: use compact `node-context` only when artifact/bootstrap aggregation is unnecessary.

Assigned agent handoff:

```sh
research-cockpit agent-session-context --root research_cockpit --assignment <assignment_id> --compact --json
# Optional broad summary:
research-cockpit bootstrap --root research_cockpit --assignment <assignment_id> --json
```

In scoped bootstrap output, use `assignment_scope` / `agent_scope` and `assignment_cursor` as the primary task context. In `agent-session-context`, use the top-level assignment and option context as the primary task context. Do not switch to `focus.current_focus_node`, global `current_state.current_*`, or `coordinator_state.selected_node` unless the user explicitly assigns that other branch.

Known node handoff:

```sh
research-cockpit context --root research_cockpit --id <node_id> --with-bootstrap --with-artifacts --compact --json
```

Global triage:

```sh
research-cockpit bootstrap --root research_cockpit --json
```

Minimal older handoff:

```sh
research-cockpit node-context --root research_cockpit --id <node_id> --compact --json
```

If the current working directory is already the plugin root, use `research-cockpit bootstrap --root <path> --json` instead. If the `research-cockpit` console script is unavailable but the package is installed, use `python -m research_cockpit.cli <command>` with the same Python environment. For `node-context`, add `--command-style python` so returned command drafts use the module entrypoint too.

Read `agent_context_pack.json` or `focus_context_pack.json` only when you need global state, generated dashboard context, or a broader focus scan. For assigned downstream agents, those files can contain coordinator/global focus from another branch; prefer the scoped bootstrap or agent session context.

3. If generated dashboards are missing or stale and the task allows generated-file writes, run `research-cockpit build --root research_cockpit`. Do not run `bootstrap --build` or `build` for read-only onboarding tasks.

4. Use `research-cockpit` commands for mutating operations. Do not bypass helpers by hand-editing YAML unless the relevant capability explicitly says YAML repair is the right path.

Default research graph reasoning centers on `stage`, `problem`, `option`, `experiment`, and `decision`. Treat `artifact` nodes as supporting evidence/resources by default; do not create an artifact node for an ordinary file, config, JSON, or result unless that artifact is itself a long-lived research object or key deliverable.

Status semantics:

- `stage`: `planned`, `active`, `blocked`, `done`.
- `problem`: `open`, `active`, `blocked`, `resolved`, `parked`.
- `option`: `open`, `active`, `promising`, `rejected`, `accepted`, `paused`, `parked`.
- `experiment`: `planned`, `queued`, `running`, `done`, `failed`, `cancelled`.
- `decision`: `proposed`, `accepted`, `superseded`, `rejected`.
- `artifact`: `draft`, `planned`, `active`, `done`, `superseded`, `deprecated`, `archived`.

Use `promising` only for an `option` that has positive signal but is not yet accepted. Do not set decisions to `accepted` directly; use `research-cockpit accept-decision`.

Terminal parent lifecycle guard: do not mark a `problem` as `resolved`/`parked` or an `option` as `accepted`/`rejected`/`paused`/`parked` while its descendant `problem`, `option`, or `experiment` nodes still contain active work. Active downstream work means `problem` in `open|active|blocked`, `option` in `open|active|promising`, or `experiment` in `planned|queued|running`. If a terminal transition reports `terminal_parent_has_active_descendants`, run `close-branch --dry-run --json --show-diff`, inspect the `updates`, `skipped`, and `remaining_active_descendants`, then explicitly close or cancel downstream work before retrying the parent status change.

## Capability Routing

- Graph state, data files, saved graph views, and interaction log: `capabilities/graph-state.md`
- Current focus, context packs, search, and startup read order: `capabilities/focus-context.md`
- Creating nodes, status updates, suggestions, and safe YAML boundaries: `capabilities/node-management.md`
- Experiments, findings, evidence, and option workstream reports: `capabilities/experiment-tracking.md`
- Decisions, ADR-style acceptance, checklist repair, promote/accept flows: `capabilities/decision-adr.md`
- Streamlit UI, React Flow graph, refresh behavior, and frontend build rules: `capabilities/ui-dashboard.md`
- Installation shape, CLI, wrappers, environment variables, and agent integration: `capabilities/integrations.md`
- Cleanup, artifact retention, worktree closeout, branch lifecycle, active resources, and large-repo hygiene: `capabilities/maintenance.md`
- Validation failures, release checks, dependency issues, and recovery: `capabilities/troubleshooting.md`
- Launcher output files and starter templates for shell, Python, scheduler, and manual experiment runs: `docs/launcher-output-conventions.md`, `templates/launcher/`
- Dashboard build profiling, large-graph refresh behavior, and deferred incremental-build plan: `docs/plans/2026-05-28-dashboard-build-performance.md`

Read only the capability files needed for the current task.

## Golden Paths

Choose one startup path and one verification scope:

```sh
# Assignment-scoped worker
research-cockpit agent-session-context --root <data-root> --assignment <assignment_id> --compact --json

# Known node without an assignment
research-cockpit context --root <data-root> --id <node_id> --with-bootstrap --with-artifacts --compact --json

# After a worker mutation, run the returned changed-scope verify_commands.
research-cockpit validate --root <data-root> --changed-node <node_id> --json

# Run closeout: reference the ingest record_id as artifact_record.existing_record_id.
research-cockpit complete-run --root <data-root> --file <closeout.yaml> --assignment <assignment_id> --json --compact --no-build
```

Use `research-cockpit commands --json --compact --summary-only` for broad discovery and `--name <command>` for one command's detailed flags. Full `validate`, `build`, and root `smoke` are coordinator/release/final-handoff gates.

## Command Reference

The following catalog is for lookup, not a sequence to execute. Prefer the golden paths above and read only the capability file relevant to the task.

```sh
research-cockpit validate --root research_cockpit
research-cockpit validate --root research_cockpit --changed-node <node_id> --json
research-cockpit validate --root research_cockpit --strict-lifecycle --json
research-cockpit bootstrap --root research_cockpit --assignment <assignment_id> --json
research-cockpit add-node --root research_cockpit --id <node_id> --type <type> --title "..." --parent <parent_id> --dry-run --json --show-diff
research-cockpit apply-graph-plan --print-schema
research-cockpit apply-graph-plan --root research_cockpit --file graph_update.yaml --dry-run --json --show-diff
research-cockpit create-workstream --print-schema
research-cockpit create-workstream --root research_cockpit --file workstream.yaml --dry-run --json --show-diff
research-cockpit create-workstream --root research_cockpit --file workstream.yaml --json --compact
research-cockpit context --root research_cockpit --id <node_id> --with-bootstrap --with-artifacts --compact --json
research-cockpit smoke --root research_cockpit --scope changed --id <node_id> --json --progress
research-cockpit assignment-view --root research_cockpit --json
research-cockpit create-run --root research_cockpit --id <run_id> --experiment <experiment_id> --status running --launcher tmux --command "python train.py" --progress-file artifacts/<experiment_id>/<run_id>/progress.json --dry-run --json --show-diff
research-cockpit create-run --root research_cockpit --id <run_id> --experiment <experiment_id> --status running --progress-file artifacts/<experiment_id>/<run_id>/progress.json --no-build
research-cockpit update-run --root research_cockpit --id <run_id> --status running --progress-file artifacts/<experiment_id>/<run_id>/progress.json --no-build
research-cockpit complete-run --print-schema
research-cockpit complete-run --root research_cockpit --file closeout.yaml --assignment <assignment_id> --json --compact --no-build
research-cockpit run-context --root research_cockpit --id <run_id> --compact --json
research-cockpit list-runs --root research_cockpit --experiment <experiment_id> --json --compact
research-cockpit record-gate-result --root research_cockpit --id <gate_id> --experiment <experiment_id> --run <run_id> --type smoke_check --passed true --next-allowed-action full_run --no-build
# Add --artifact <artifact_id> only for an explicitly promoted graph artifact.
research-cockpit ingest-gate-result --root research_cockpit --id <gate_id> --file artifacts/<experiment_id>/<run_id>/gate_result.json --run <run_id> --no-build
research-cockpit create-artifact --root research_cockpit --id <artifact_id> --title "..." --path <path> --link-to <node_id> --dry-run --json --show-diff
research-cockpit create-artifact --print-schema
research-cockpit create-artifact --root research_cockpit --file artifact.yaml --dry-run --json --show-diff
research-cockpit create-artifact --root research_cockpit --file artifact.yaml --json --compact
research-cockpit link-artifact --root research_cockpit --artifact <artifact_id> --to <node_id> --dry-run --json --show-diff
research-cockpit ingest-artifact --root research_cockpit --node <experiment_id> --from <worktree_output_dir> --run-id <run_id> --agent <agent_id> --link metrics=metrics.json --dry-run --json --show-diff
research-cockpit ingest-artifact --root research_cockpit --node <experiment_id> --from <worktree_output_dir> --run-id <run_id> --agent <agent_id> --link metrics=metrics.json --json --compact --no-build
research-cockpit complete-experiment --root research_cockpit --id <experiment_id> --finding "..." --confidence medium --artifact-id <artifact_id> --no-build
research-cockpit complete-experiment --root research_cockpit --id <experiment_id> --finding "..." --confidence medium --evidence-path artifacts/<experiment_id>/<run_id> --evidence-link metrics=artifacts/<experiment_id>/<run_id>/metrics.json --json --compact
research-cockpit complete-experiments --print-schema
research-cockpit complete-experiments --root research_cockpit --file findings.yaml --dry-run --json --show-diff
research-cockpit complete-experiments --root research_cockpit --file findings.yaml --json --compact
research-cockpit close-current-experiment --root research_cockpit --id <experiment_id> --finding "..." --confidence medium --next-focus <next_node> --sync-agent all --json --compact
research-cockpit create-followup-experiment --root research_cockpit --assignment <assignment_id> --from <done_or_running_experiment_id> --id <followup_id> --title "..." --priority high --next-action "Run follow-up gate" --json --compact
research-cockpit migrate-terminal-next-actions --root research_cockpit --assignment <assignment_id> --id <experiment_id> --followup-id <followup_id> --title "Follow-up gate" --dry-run --json --show-diff
research-cockpit set-cursor --root research_cockpit --assignment <assignment_id> --node <followup_id> --no-build
research-cockpit update-finding --root research_cockpit --experiment <experiment_id> --finding-id <finding_id> --statement "..." --dry-run --json --show-diff
research-cockpit update-finding --root research_cockpit --experiment <experiment_id> --finding-id <finding_id> --statement "..." --json --compact
research-cockpit finalize-workstream --print-schema
research-cockpit finalize-workstream --root research_cockpit --file finalize.yaml --dry-run --json --show-diff
research-cockpit finalize-workstream --root research_cockpit --file finalize.yaml --json --compact
research-cockpit finalize-workstream --root research_cockpit --option <option_id> --status accepted --problem-status resolved --report --dry-run --json --show-diff
research-cockpit close-branch --root research_cockpit --id <problem_or_option_id> --downstream-status parked --dry-run --json --show-diff
research-cockpit close-branch --root research_cockpit --id <problem_or_option_id> --downstream-status parked --include-experiments --no-build
research-cockpit option-workstream-context --root research_cockpit --id <option_id> --compact --json
research-cockpit start-agent-session --root <canonical_root> --option <option_id> --label <short_label> --objective "..." --branch agent/<option_id>-<short_label> --worktree ../worktrees/<short_label> --dry-run --json --show-diff
research-cockpit agent-session-context --root <canonical_root> --assignment <assignment_id> --compact --json
research-cockpit set-cursor --root <canonical_root> --assignment <assignment_id> --node <node_id> --no-build
research-cockpit import-worktree-findings --root <canonical_root> --from-root <worktree>/research_cockpit --agent <agent_id> --option <option_id> --dry-run --json --show-diff
research-cockpit build --root <canonical_root> --watch --interval 5 --json
research-cockpit build --root <canonical_root> --json --profile --profile-output dashboards/build_profile.json
research-cockpit build --root <canonical_root> --json --profile --skip-resource-search
research-cockpit update-node-fields --root research_cockpit --id <node_id> --question "..." --tag <tag> --no-build
research-cockpit update-workstream-fields --root research_cockpit --option <option_id> --status reported --objective "..." --no-build
research-cockpit sync-focus-actions --root research_cockpit --from-node <node_id> --dry-run --json --show-diff
research-cockpit lint --root research_cockpit --semantic --json
research-cockpit active-resources --root research_cockpit --json
research-cockpit worktree-audit --root research_cockpit --repo . --json
research-cockpit branch-audit --root research_cockpit --repo . --base main --json
research-cockpit artifact-retention-audit --root research_cockpit --repo . --min-size-gb 10 --json
research-cockpit artifact-records --root research_cockpit --experiment <experiment_id> --json --compact
research-cockpit promote-artifact-record --root research_cockpit --id <record_id> --artifact-id <artifact_id> --link-to <node_id> --promotion-reason "Durable evidence for decision or baseline" --json --compact
research-cockpit compact-artifacts --root research_cockpit --dry-run --json --show-diff
research-cockpit maintenance-audit --root research_cockpit --repo . --base main --json
research-cockpit worktree-closeout --root research_cockpit --repo . --worktree ../worktrees/<label> --classification discard_after_recording --dry-run --json
research-cockpit update-suggestion-state --root research_cockpit --id <suggestion_id> --state dismissed --reason "..." --dry-run --json --show-diff
research-cockpit update-decision-evidence --root research_cockpit --id <decision_id> --dry-run --json --show-diff
research-cockpit update-decision-checklist --root research_cockpit --id <decision_id> --alternative <option_id> --consequence "..." --next-required-action "..." --dry-run --json --show-diff
research-cockpit node-context --root research_cockpit --id <node_id> --compact --json
research-cockpit search --root research_cockpit --query "..." --json --limit 5 --source node
research-cockpit suggest-next-actions --root research_cockpit --json --limit 10 --focus-only
research-cockpit commands --json --compact --summary-only --workflow evidence
research-cockpit commands --json --compact --name <command>
research-cockpit repair-interaction-log --root research_cockpit --dry-run --json --show-diff
```

`node-context` is read-only and computed from truth-source YAML. Use `--compact --json` as the shortest older onboarding path when a human asks you to continue from one node; use full `--json` when you need parent chain, relations, resources, recent interactions, and type-specific traces. The combined `context` payload separates `target_context` from `current_global_focus`; use `context_boundary.warning` to notice when a target node differs from the global focus.

When making several related state changes, run mutating commands sequentially. Do not parallelize mutating commands against the same data root; they share the active `graph/interaction_events/` backend (or the legacy prefix before migration) and are protected by a mutation lock. Truth-source mutations also verify that target files did not change after command planning; if a command reports a mutation conflict, reread compact context and retry the command. Pass `--no-build` to supported mutating commands. After a small worker edit, run the changed-scope commands printed in compact `verify_commands`, usually:

```sh
research-cockpit validate --root research_cockpit --changed-node <node_id> --json
research-cockpit context --root research_cockpit --id <node_id> --with-bootstrap --with-artifacts --compact --json
```

Only the coordinator, release, or final handoff should run the full gate:

```sh
research-cockpit validate --root research_cockpit --json
research-cockpit build --root research_cockpit
research-cockpit smoke --root research_cockpit --json --progress
```

Default `smoke` is compact for large roots and avoids full `bootstrap`, full `suggest-next-actions`, and full `node-context` JSON. For one-node worker checks, use `smoke --scope changed --id <node_id> --json --progress`; use `--full` only when explicitly diagnosing the older full subprocess workflow. `--progress` writes JSON-line phase events to stderr, so stdout remains parseable JSON.

For ordinary experiment output, `ingest-artifact` defaults to a lightweight artifact record; `--record-only` remains an explicit compatibility flag. Use `--promote --promotion-reason "..."` only when immediate graph navigation is required, or later run `promote-artifact-record --promotion-reason "..."`. Demote old graph artifact nodes only after `compact-artifacts --dry-run` classifies them as `can_demote`; execution never deletes payload files.

For several node creations or rich field edits, prefer a single plan file. After applying, verify only the changed nodes in the worker loop:

```sh
research-cockpit apply-graph-plan --root research_cockpit --file graph_update.yaml --dry-run --json --show-diff
research-cockpit apply-graph-plan --root research_cockpit --file graph_update.yaml --no-build
research-cockpit validate --root research_cockpit --changed-node <node_id> --json
research-cockpit context --root research_cockpit --id <node_id> --with-bootstrap --with-artifacts --compact --json
```

`apply-graph-plan` supports `updates[*].status` at the update entry top level. Put content fields under `updates[*].fields`; `status` inside `fields` is rejected. Run `apply-graph-plan --print-schema` for the supported field list, including experiment assignment fields: `owner`, `blocked_by`, `depends_on`, `ready_for_agent`, and `handoff_context`. For dispatch ordering, keep `priority` coarse and put stable sequence labels in `order` or `rank`.

Use `create-workstream` for the common `problem -> active option -> experiments + follow-up options` shape. It creates the branch and sets the new problem `current_best_option`, but it does not change focus or pause old options.
Follow-up options should use status `open`; file-based graph commands accept option status `planned` only as an input alias and write `open` to truth-source YAML. Dry-run/JSON output reports this under `normalized_statuses`.
After creating a workstream, use `option-workstream-context --id <option_id> --compact --json` to verify experiment ids, statuses, success criteria count, metric count, finding count, and linked artifact count. Read per-experiment `node-context` only when you need the full criterion text or other detailed fields.

For hierarchical research branches, prefer `option -> problem -> option -> experiment/decision` over a flat list of sibling experiments. When a worktree result or accepted finding opens a new line of work, create a child workstream with `create-workstream` and set `problem.parent` to the inherited option id; record the source experiment or option in `derived_from`. Use `create-followup-experiment` only for one small queued gate that should stay under the same option. Do not use `experiment -> experiment` as the primary hierarchy; keep that relationship in `derived_from`.

Use `complete-experiment --evidence-path ... --evidence-link key=value` or per-entry `complete-experiments` evidence blocks when the finding depends on result folders, plots, reports, or metrics JSON that already live at a stable path. These commands create and link an artifact from both the finding and experiment so node resources show the evidence path, but they do not copy files. If outputs were produced inside a git worktree, run `ingest-artifact` first and then record the finding with `--artifact-id`. Use `complete-experiments` for sweeps or multi-backend experiment sets. Use `create-artifact --file artifact.yaml` for stable result folders with several links or target nodes, and use `link-artifact` for attaching existing artifacts, so agents do not patch `path`, `links`, or `linked_artifacts` by hand. Artifact paths are stored exactly as provided; JSON resource rows report `resolved_target`, `resolution_base`, `resolution_attempts`, and `exists` using root parent, data root, then cwd for relative paths. If a finding has no linked artifact, the command may still succeed but JSON includes `missing_evidence_artifact`. Use `update-finding` when revising an existing finding statement, confidence, outcome, metrics, or evidence artifacts.

Use run records for concrete executions and findings for conclusions. During execution, use `create-run` / `update-run` and read `run-context`. At closeout, prefer one `complete-run --file closeout.yaml` transaction for terminal run status, gate metadata, finding, artifact-record link, and next actions. Use the older individual commands only when those records are intentionally written at different times.

When an assignment-scoped agent completes an experiment, `complete-experiment` records the conclusion but does not move the assignment cursor. If JSON output warns that the assignment cursor is terminal, move it explicitly with `set-cursor --assignment <assignment_id> --node <next_node> --no-build`. `close-current-experiment`, `set-focus`, `sync-focus-actions`, and `create-followup-experiment --set-focus` are coordinator/legacy focus helpers; `--set-focus` updates coordinator selection and compatibility `current_state` fields, not the assignment cursor.
Generated dashboard context includes `next_action_scopes`; read it before choosing work so focus-node, parent option/problem, global coordinator, and stale terminal-node actions are not conflated.
Done nodes should keep conclusions, not live work. If a done experiment still has real follow-up work in `next_actions`, create a small derived queued experiment with `create-followup-experiment` or clean up an older stale action with `migrate-terminal-next-actions`. Use `create-workstream` when the follow-up is a branch, not one gate.

Before marking a branch terminal, make active descendants explicit. If `update-status`, `finalize-workstream`, `apply-graph-plan`, `create-workstream`, `accept-decision`, or `promote-decision --status accepted` returns `terminal_parent_has_active_descendants`, do not force the parent status. Run `close-branch --id <problem_or_option_id> --downstream-status parked --dry-run --json --show-diff`. By default it does not cancel `planned`, `queued`, or `running` experiments; those appear in `skipped` and `remaining_active_descendants`. After confirming external runs are stopped or intentionally abandoned, rerun with `--include-experiments --no-build`; active experiments become `cancelled`. Then retry the parent terminal status command with the intended terminal status, such as `resolved` for a closed problem or `accepted` for the chosen option.

Known-node tasks should use `effective_baseline` from `context` or `node-context` as the default option/decision/artifact bundle for follow-up work. When an accepted branch should become the default for later agents, use `research-cockpit set-baseline`; do not expand every accepted decision into normal handoffs unless the task is explicitly auditing accepted history.

Use `finalize-workstream --file finalize.yaml` when the close-out needs several flags. `--file` supports `option`, `status`, `problem_status`, `stage_status`, `summary_file`, `summary_target`, `artifacts`, `sync_focus`, `report`, `agent`, and `locale`; explicit CLI flags override file values. A relative `summary_file` in `finalize.yaml` resolves against the finalize file directory, then the data root, then the current working directory. Use `finalize-workstream` only for explicit close-out. It updates the named option/problem/stage statuses and optional report/artifact/focus fields that you pass; it does not accept decisions, pause old options, delete branches, or invent next actions. If the option or parent problem still has active descendants, close or cancel those descendants with `close-branch` first.

## Parallel Agents With Git Worktrees

Use git worktrees for code and experiment isolation, not for separate Research Cockpit truth sources. The canonical data root stays in the main repository and every downstream agent writes only that root:

```sh
research-cockpit start-agent-session --root D:/main_repo/research_cockpit --option option_x --label cache_probe --objective "Run downstream experiments" --branch agent/option_x-cache_probe --worktree ../worktrees/cache_probe --base main --create-worktree --dry-run --json --show-diff
research-cockpit start-agent-session --root D:/main_repo/research_cockpit --option option_x --label cache_probe --objective "Run downstream experiments" --branch agent/option_x-cache_probe --worktree ../worktrees/cache_probe --base main --create-worktree --no-build
```

Send the JSON `handoff` to the downstream agent. It contains the generated `agent_id`, `assignment_id`, `launch_env`, and `startup_command`; the downstream agent should not invent its own id. A dry-run previews generated ids but does not reserve them; pass explicit `--agent` and `--assignment` / `--assignment-id` on execute when you need the exact previewed ids. Relative `--worktree` values resolve against the canonical repo root (`--root` parent). In the worktree, set `RESEARCH_COCKPIT_ROOT` and `RESEARCH_COCKPIT_ASSIGNMENT_ID` from `launch_env`, or pass the same absolute `--root` and `--assignment` on every command. The downstream agent starts with:

```sh
research-cockpit agent-session-context --root D:/main_repo/research_cockpit --assignment <assignment_id> --compact --json
# Optional broad summary:
research-cockpit bootstrap --root D:/main_repo/research_cockpit --assignment <assignment_id> --json
```

For coordinator-side task dispatch, use `assignment-view --json` to list high-priority queued/running experiments with `owner`, `depends_on`, `blocked_by`, key artifacts, and first `next_action`. Prefer one small queued experiment per downstream agent handoff.

Do not run `init`, `set-focus`, or any mutation against a worktree-local `research_cockpit/`. Use `set-cursor --assignment <assignment_id>` for worker progress; reserve `set-focus` for coordinator/UI selection. `set-agent-focus` remains a legacy compatibility command for older roots and should not be used for new assignment-scoped sessions. Keep all canonical root mutations sequential and usually `--no-build`; run one main-root dashboard watcher when you want the panel to refresh:

```sh
research-cockpit build --root D:/main_repo/research_cockpit --watch --interval 5 --json
```

`build --watch --json` prints one JSON object per iteration. Each event includes `last_build_at`, `last_build_status`, and `last_build_error`; the watcher only refreshes generated dashboards and does not replace final `validate` or `smoke`. `import-worktree-findings` is only a recovery tool for evidence accidentally written in a worktree-local cockpit root. It imports artifact nodes, experiment findings, result summaries, experiment-local `next_actions`, and workstream reports; it refuses structural graph changes, global/per-agent focus changes, and decision acceptance.

Before deleting a worktree, generate a closeout plan, ingest any useful run directory into the canonical artifact store, and record the conclusion. The worker closeout verifies the changed experiment only:

```sh
research-cockpit worktree-closeout --root <data-root> --repo <repo-root> --worktree <worktree-path> --classification discard_after_recording --dry-run --json
research-cockpit ingest-artifact --root <data-root> --node <experiment_id> --from <worktree-run-dir> --run-id <run_id> --agent <agent_id> --link metrics=metrics.json --json --compact --no-build
research-cockpit complete-run --print-schema
# In closeout.yaml, set artifact_record.existing_record_id to the ingest result record_id.
research-cockpit complete-run --root <data-root> --file closeout.yaml --assignment <assignment_id> --json --compact --no-build
# Run the compact result's verify_commands; do not add build or root smoke here.
```

Coordinator or final handoff later runs the full gate once across the canonical root.

Do not use worktree-local paths as long-lived `--evidence-path` values. Keep run directories free of symlinks before `ingest-artifact`; v1 rejects symlinked files or directories instead of copying through them. Deletion is safe only after artifact files, finding/decision/baseline updates, and any useful commit/patch have been preserved outside the worktree.
For large experiment repositories, prefer sparse or minimal worktrees and keep generated outputs, caches, logs, and bulky artifacts outside temporary worktree checkouts. Before deleting or moving any worktree, branch, output, cache, checkpoint, or large artifact payload, follow the maintenance closeout checklist in `capabilities/maintenance.md`.

For terse machine-readable mutation feedback, add `--compact` with `--json` on supported high-level commands such as `apply-graph-plan`, `create-workstream`, `close-branch`, `create-run`, `update-run`, `complete-run`, `create-artifact`, `ingest-artifact`, `record-gate-result`, `ingest-gate-result`, `complete-experiment`, `complete-experiments`, `close-current-experiment`, `create-followup-experiment`, `migrate-terminal-next-actions`, `update-finding`, `update-workstream-fields`, and `finalize-workstream`. Compact output keeps only target, changed status, created/updated ids, changed file count, resolved inputs where useful, changed-scope `verify_commands`, `post_apply_verify_commands`, and full `final_handoff_commands`. Worker agents should run `verify_commands` after a real write. If changed-scope `validate` reports `fallback.used_full_validation: true`, follow `fallback.recommended_commands` to refresh `dashboards/validation_index.json`; do not escalate to full `smoke` unless this is coordinator/final handoff. For dry-runs, `verify_commands` is empty; inspect `post_apply_verify_commands` and run those only after applying without `--dry-run`. Coordinator/final handoff should run `final_handoff_commands`. `close-branch --compact` additionally keeps `parent_ready_for_terminal_status`, `skipped`, and `remaining_active_descendants`; read those before retrying a parent terminal transition. `--show-diff` still includes the full diff; use it only when reviewing write content.
For legacy mutation commands without `--compact`, use `--dry-run --json --show-diff` to preview writes and keep the JSON payload focused on `changed/would_change`, affected path, before/after summary, and optional diff. Dry-run also performs mutation preflight; if `interaction_log.yaml` is malformed, it fails before showing a misleading successful preview.
Use `commands --json --compact --summary-only --workflow <graph|evidence|decision|focus|maintenance|read>` for broad command discovery, then `commands --json --compact --name <command>` for one command's detailed batching flags such as `batch_policy`. The summary payload is intentionally terse; use full `commands --json` only when you need the complete command contract. If `validate` reports malformed legacy interaction YAML, use `repair-interaction-log --dry-run --json --show-diff`. For a large valid legacy log, run `migrate-interaction-log --dry-run --json` and then `--execute`; the legacy YAML is preserved while new events use append-only JSONL segments.

Use bounded `search` and `suggest-next-actions` reads. Prefer `context` and `bootstrap.top_suggestions` for normal startup; run `suggest-next-actions --limit 10 --focus-only` once before choosing work, and re-run it only after you changed `next_actions` or suggestion lifecycle state.

## Write Boundary

Allowed project-state writes are under:

- `research_cockpit/current_state.yaml`
- `research_cockpit/coordinator_state.yaml`
- `research_cockpit/agents/*.yaml`
- `research_cockpit/assignments/*.yaml`
- `research_cockpit/graph/nodes/*.yaml`
- `research_cockpit/graph/edges.yaml`
- `research_cockpit/graph/graph_views.yaml`
- esearch_cockpit/graph/interaction_log.yaml (legacy prefix)
- esearch_cockpit/graph/interaction_events/** (active append-only event backend)
- `research_cockpit/runs/*.yaml`
- `research_cockpit/gate_results/*.yaml`
- `research_cockpit/gate_results/*.json`
- `research_cockpit/artifact_records/*.yaml`
- `research_cockpit/artifact_migrations/*.yaml`
- `research_cockpit/notes/**/*.md`
- `research_cockpit/artifacts/**`

Agents should normally write structured state files through `research-cockpit` CLI commands. Direct YAML repair is a last-resort structural fix and must be followed by validation and dashboard rebuild.

Markdown notes under `research_cockpit/notes/**/*.md` may be edited directly for human-readable detail. Artifact payloads under `research_cockpit/artifacts/**` may be copied or preserved by launcher handoff and `ingest-artifact`, but structured metadata should still be written through CLI commands. Keep structured findings, status, coordinator focus, assignment cursor, decision state, run records, gate results, artifact records, artifact migration reports, `baseline`, `current_best_option`, and `next_actions` in YAML via CLI where a command exists.

Generated files under `research_cockpit/dashboards/` must be rebuilt, not hand-authored.

Never create or update project research state inside the plugin directory itself unless you are intentionally editing `examples/demo_research_cockpit/` for plugin development.
