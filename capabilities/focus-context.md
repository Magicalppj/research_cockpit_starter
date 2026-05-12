# Focus Context

Use this capability at the start of an agent session and before making local research decisions.

## Startup Read Order

Choose one startup path. Do not run both bootstrap and `context --with-bootstrap` for normal known-node work.

1. Known node id:

```sh
research-cockpit context --root research_cockpit --id <node_id> --with-bootstrap --with-artifacts --compact --json
```

2. Unknown target or global triage:

```sh
research-cockpit bootstrap --root research_cockpit --json
```

3. Older minimal known-node handoff:

```sh
research-cockpit node-context --root research_cockpit --id <node_id> --compact --json
```

For known-node continuation, compact `context` is the preferred one-command handoff when artifact and validation context matter. Bootstrap plus compact `node-context` remains fine for the older minimal flow. Do not also read both context packs unless you need global state or generated dashboard context.

4. If dashboard files are missing or stale and generated-file writes are allowed:

```sh
research-cockpit build --root research_cockpit
```

For a brand-new data root, use `research-cockpit init --root research_cockpit --build --json` if the next step will read generated context packs. Do not run `bootstrap --build` or `build` during read-only onboarding.

5. Read generated context only for global scans:
   - `research_cockpit/dashboards/agent_context_pack.json`
   - `research_cockpit/dashboards/focus_context_pack.json`
   - `research_cockpit/dashboards/search_index.json` when searching.

Use filtered command discovery when you only need one workflow surface:

```sh
research-cockpit commands --json --compact --workflow focus
research-cockpit commands --json --compact --name context
```

For a downstream agent launched in a git worktree, start from the canonical root session context instead of global bootstrap:

```sh
research-cockpit agent-session-context --root D:/main_repo/research_cockpit --agent agent_x --compact --json
```

The payload includes `required_root`, `do_not_mutate_worktree_root: true`, the agent session, per-agent focus, compact option context, and handoff commands. Use the included `ingest-artifact` command template for worktree run outputs before recording findings or deleting the worktree.

## Node Handoff

When a human assigns a specific node id, use the read-only onboarding command before opening raw YAML:

```sh
research-cockpit context --root research_cockpit --id <node_id> --with-bootstrap --with-artifacts --compact --json
```

The payload is computed from truth-source YAML, not from stale generated dashboards. It includes the current node, compact bootstrap data, validation summary, focus actions, related problem/option/experiments, artifact/resource rows, and command skeletons.

Known-node payloads include `effective_baseline`. This is the default option/decision/artifact bundle the target node should inherit. Use it instead of scanning every accepted decision unless the task is explicitly reviewing accepted history. When `--with-artifacts` is present, baseline artifacts are included in artifact/resource rows.

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

The combined `context` payload names the work target and global focus separately:

- `target_context`: the node this command was asked to inspect.
- `current_global_focus`: the current `current_state.yaml` focus.
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

## Focus Updates

Use `research-cockpit set-focus` for focus changes:

```sh
research-cockpit set-focus --root research_cockpit --focus-node problem_id
```

Focus changes write `current_state.yaml`, append `interaction_log.yaml`, and rebuild dashboard/context unless `--no-build` is passed.

Passing `--next-action` replaces the current_state `next_actions` list with the repeated values supplied in this command. It does not append.

Dashboard "Current Next Actions" is sourced from `current_state.yaml`, not from problem/option/experiment node-local `next_actions`. Updating a node's `next_actions` is useful for local planning, but the dashboard will not show it as current work until you run `set-focus --next-action ...` or `sync-focus-actions`.

In multi-agent worktree runs, downstream agents should not use global `set-focus`. Use per-agent focus instead:

```sh
research-cockpit set-agent-focus --root D:/main_repo/research_cockpit --agent agent_x --node experiment_x --next-action "Run follow-up" --dry-run --json --show-diff
research-cockpit set-agent-focus --root D:/main_repo/research_cockpit --agent agent_x --node experiment_x --no-build
```

`set-agent-focus` writes `current_state.agent_focuses[agent_id]` and leaves global `current_focus_node`, `current_focus_path`, and `next_actions` unchanged. Passing one or more `--next-action` values replaces that agent's existing `next_actions`; omitting `--next-action` preserves them. A coordinator or human should own global focus when several agents are active.

## Sync Focus Actions

When the current focus node already has the canonical `next_actions`, sync them into `current_state.yaml` instead of hand-copying action text:

```sh
research-cockpit sync-focus-actions --root research_cockpit --from-node problem_x --dry-run --json --show-diff
research-cockpit sync-focus-actions --root research_cockpit --from-node problem_x --no-build
```

Default mode is `replace`: current_state `next_actions` becomes the node `next_actions`. Use `--mode append` to append de-duplicated node actions after existing current_state actions.

`suggest-next-actions` deterministically de-duplicates focus actions after trimming, lowercasing, collapsing whitespace, and stripping trailing punctuation. This avoids duplicate suggestions when current_state and the focus node differ only in formatting.

Run semantic lint when generated context looks stale even though `validate` passes:

```sh
research-cockpit lint --root research_cockpit --semantic --json
```

Semantic lint warns about terminal global/agent focus nodes, `next_actions` that still mention closed nodes, open experiments that already have results, and option workstream state that no longer matches child experiment state. Warning output exits with status 1; a zero exit means no semantic warnings were found. The command discovery manifest lists this as `research-cockpit lint --semantic`.
