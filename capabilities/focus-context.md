# Focus Context

Use this capability at the start of an agent session and before making local research decisions.

## Startup Read Order

Choose one startup path. Do not run both bootstrap and a wider context view for normal known-node work.

1. Assigned downstream agent with an `assignment_id`:

```sh
research-cockpit agent-session-context --root research_cockpit --assignment <assignment_id> --compact --json
# Optional broad summary:
research-cockpit bootstrap --root research_cockpit --assignment <assignment_id> --json
```

Use `agent-session-context` assignment data as the primary task context; in scoped bootstrap output, use `assignment_scope` / `agent_scope` and `assignment_cursor`. Global `current_state`, `coordinator_state`, and `focus.current_focus_node` are coordinator metadata and may point to another agent's branch. Use `--agent` only when it resolves to exactly one active assignment.

2. Known node id:

```sh
research-cockpit context --root research_cockpit --id <node_id> --view execution --compact --json
# Repeated poll after the first response:
research-cockpit context --root research_cockpit --id <node_id> --view execution --since <revision> --compact --json
```

The execution view contains only the node status/next action, assignment boundary, active run, blocking gate, effective baseline, warnings, required action, and revision. An unchanged `--since` poll returns only `changed: false` and the revision. It intentionally omits global focus, bootstrap, artifact history, historical findings, and command catalogs.

3. Unknown target or global triage:

```sh
research-cockpit bootstrap --root research_cockpit --coordinator --json
```

4. Older minimal known-node handoff:

```sh
research-cockpit node-context --root research_cockpit --id <node_id> --compact --json
```

For known-node continuation, the execution view is the preferred one-command handoff. Request the wider default view with `--with-bootstrap --with-artifacts` only when validation aggregation, related history, or artifact/resource rows are required. Do not also read both context packs unless you need global state or generated dashboard context.

5. If dashboard files are missing or stale and generated-file writes are allowed:

```sh
research-cockpit build --root research_cockpit
```

For a brand-new data root, use `research-cockpit init --root research_cockpit --build --json` if the next step will read generated context packs. Do not run `bootstrap --build` or `build` during read-only onboarding.

6. Read generated context only for global scans:
   - `research_cockpit/dashboards/agent_context_pack.json`
   - `research_cockpit/dashboards/focus_context_pack.json`
   - `research_cockpit/dashboards/search_index.json` when searching.

Use filtered command discovery when you only need one workflow surface:

```sh
research-cockpit commands --json --compact --summary-only --workflow focus
research-cockpit commands --json --compact --summary-only --group context
research-cockpit commands --json --compact --name context
```

Use `--summary-only` for broad command discovery. It includes only command selection fields such as `group`, `status`, `workflow_tags`, `input_modes`, support flags, and `batch_policy_mode`. Use `commands --json --compact --name <command>` when you need one command's full compact contract, aliases, or detailed `batch_policy`.

For a downstream agent launched in a git worktree, start from the canonical root scoped context instead of unscoped global bootstrap:

```sh
research-cockpit agent-session-context --root D:/main_repo/research_cockpit --assignment <assignment_id> --compact --json
# Optional broad summary:
research-cockpit bootstrap --root D:/main_repo/research_cockpit --assignment <assignment_id> --json
```

The payload includes `required_root`, `do_not_mutate_worktree_root: true`, the assignment record, assignment cursor, compact option context, and handoff commands. Use the included record-only `ingest-artifact` command template for ordinary worktree run outputs before recording findings or deleting the worktree; promote the record only for durable evidence.

## Node Handoff

When a human assigns a specific node id, use the read-only onboarding command before opening raw YAML:

```sh
research-cockpit context --root research_cockpit --id <node_id> --view execution --compact --json
```

The execution payload is computed from truth-source files, not generated dashboards. It stays bounded as the root grows and reports `scope.index_fast_path`, `scope.nodes_loaded`, and `scope.nodes_total` for diagnostics.

Known-node payloads include `effective_baseline`. This is the default option/decision/artifact bundle the target node should inherit. Use it instead of scanning every accepted decision unless the task is explicitly reviewing accepted history. In the wider default view, `--with-artifacts` adds baseline artifacts and resource rows.

## Set Baseline

Use `set-baseline` when a problem, stage, option, or experiment should provide the default option/decision/artifact bundle for follow-up agents:

```sh
research-cockpit set-baseline --root research_cockpit --node problem_x --option option_x --decision decision_x --artifact artifact_bundle_x --dry-run --json --show-diff
research-cockpit set-baseline --root research_cockpit --node problem_x --option option_x --decision decision_x --artifact artifact_bundle_x --no-build
research-cockpit set-baseline --root research_cockpit --node problem_x --clear --no-build
```

`baseline` is not a node type. Do not edit the YAML field by hand when this command covers the change.

Use `node-context --compact --json` only when you need the narrow node onboarding payload without bootstrap/artifact aggregation. Use full `node-context --json` without `--compact` when you need complete relations, resources, recent interactions, or type-specific traces.
Avoid the old chain `bootstrap` + generated context packs + `node-context` for known-node work unless you are explicitly auditing global dashboard state.

The wider default `context` payload names the work target and coordinator/global focus separately:

- `target_context`: the node this command was asked to inspect.
- `current_global_focus`: coordinator/global focus from coordinator state and legacy compatibility fields.
- `context_boundary.warning`: non-empty when those two differ.

If the console script is unavailable, use:

```sh
python -m research_cockpit.cli node-context --root research_cockpit --id <node_id> --compact --json --command-style python
```

## Search

```sh
research-cockpit search --root research_cockpit --query "retrieval branch" --json --limit 10
```

Search covers YAML nodes, Markdown notes, and safe local linked resources under `research_cockpit/`.

## Coordinator Focus Updates

Use `research-cockpit set-focus` for coordinator/UI selection:

```sh
research-cockpit set-focus --root research_cockpit --focus-node problem_id
```

Coordinator focus changes write `coordinator_state.yaml`, mirror legacy `current_state.yaml` focus fields for compatibility, append through the active interaction backend via `interaction_log.py`, and rebuild dashboard/context unless `--no-build` is passed.

Passing `--next-action` replaces coordinator/global `next_actions` with the repeated values supplied in this command and mirrors them to legacy `current_state.next_actions`. It does not append.

Dashboard "Current Next Actions" is coordinator/global state and remains mirrored to `current_state.yaml` for compatibility. Generated and computed context also include `next_action_scopes` so agents can distinguish `focus_next_actions`, `parent_option_next_actions`, `parent_problem_next_actions`, `global_coordinator_next_actions`, and `stale_terminal_node_next_actions`. Updating a node's `next_actions` is useful for local planning, but it will not become a global coordinator action until a coordinator runs `set-focus --next-action ...` or `sync-focus-actions`.

In multi-agent worktree runs, downstream agents should not use coordinator `set-focus`. Move the assignment-local cursor instead:

```sh
research-cockpit set-cursor --root D:/main_repo/research_cockpit --assignment <assignment_id> --node experiment_x --next-action "Run follow-up" --dry-run --json --show-diff
research-cockpit set-cursor --root D:/main_repo/research_cockpit --assignment <assignment_id> --node experiment_x --no-build
```

`set-cursor` writes `assignments/<assignment_id>.yaml` and leaves coordinator focus unchanged. Passing one or more `--next-action` values replaces the assignment's existing `next_actions`; omitting `--next-action` preserves them. `set-agent-focus` is a legacy compatibility command that writes `current_state.agent_focuses[agent_id]`; do not use it for new assignment-scoped sessions.

## Sync Focus Actions

When the coordinator-selected focus node already has the canonical `next_actions`, sync them into coordinator/global state instead of hand-copying action text:

```sh
research-cockpit sync-focus-actions --root research_cockpit --from-node problem_x --dry-run --json --show-diff
research-cockpit sync-focus-actions --root research_cockpit --from-node problem_x --no-build
```

Default mode is `replace`: coordinator/global `next_actions` becomes the node `next_actions`. Use `--mode append` to append de-duplicated node actions after existing coordinator actions.

`suggest-next-actions` deterministically de-duplicates focus actions after trimming, lowercasing, collapsing whitespace, and stripping trailing punctuation. This avoids duplicate suggestions when current_state and the focus node differ only in formatting.

Run semantic lint when generated context looks stale even though `validate` passes:

```sh
research-cockpit lint --root research_cockpit --semantic --json
```

Semantic lint warns about terminal global/agent focus nodes, `next_actions` that still mention closed nodes, open experiments that already have results, and option workstream state that no longer matches child experiment state. Warning output exits with status 1; a zero exit means no semantic warnings were found. The command discovery manifest lists this as `research-cockpit lint --semantic`.
