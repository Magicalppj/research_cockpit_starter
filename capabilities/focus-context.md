# Focus Context

Use this capability at the start of an agent session and before making local research decisions.

## Startup Read Order

1. Bootstrap:

```sh
research-cockpit bootstrap --root research_cockpit --json
```

2. If a human assigns a specific node id, go straight to compact node handoff:

```sh
research-cockpit context --root research_cockpit --id <node_id> --with-bootstrap --with-artifacts --compact --json
```

For known-node continuation, compact `context` is the preferred one-command handoff when artifact and validation context matter. Bootstrap plus compact `node-context` remains fine for the older minimal flow. Do not also read both context packs unless you need global state or generated dashboard context.

3. If dashboard files are missing or stale and generated-file writes are allowed:

```sh
research-cockpit build --root research_cockpit
```

For a brand-new data root, use `research-cockpit init --root research_cockpit --build --json` if the next step will read generated context packs. Do not run `bootstrap --build` or `build` during read-only onboarding.

4. Read generated context only for global scans:
   - `research_cockpit/dashboards/agent_context_pack.json`
   - `research_cockpit/dashboards/focus_context_pack.json`
   - `research_cockpit/dashboards/search_index.json` when searching.

Use filtered command discovery when you only need one workflow surface:

```sh
research-cockpit commands --json --compact --workflow focus
research-cockpit commands --json --compact --name context
```

## Node Handoff

When a human assigns a specific node id, use the read-only onboarding command before opening raw YAML:

```sh
research-cockpit context --root research_cockpit --id <node_id> --with-bootstrap --with-artifacts --compact --json
```

The payload is computed from truth-source YAML, not from stale generated dashboards. It includes the current node, compact bootstrap data, validation summary, focus actions, related problem/option/experiments, artifact/resource rows, and command skeletons.

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

## Sync Focus Actions

When the current focus node already has the canonical `next_actions`, sync them into `current_state.yaml` instead of hand-copying action text:

```sh
research-cockpit sync-focus-actions --root research_cockpit --from-node problem_x --dry-run --json --show-diff
research-cockpit sync-focus-actions --root research_cockpit --from-node problem_x --no-build
```

Default mode is `replace`: current_state `next_actions` becomes the node `next_actions`. Use `--mode append` to append de-duplicated node actions after existing current_state actions.

`suggest-next-actions` deterministically de-duplicates focus actions after trimming, lowercasing, collapsing whitespace, and stripping trailing punctuation. This avoids duplicate suggestions when current_state and the focus node differ only in formatting.
