# Focus Context

Use this capability at the start of an agent session and before making local research decisions.

## Startup Read Order

1. Bootstrap:

```sh
research-cockpit bootstrap --root research_cockpit --json
```

2. If dashboard files are missing or stale:

```sh
research-cockpit build --root research_cockpit
```

3. Read:
   - `research_cockpit/dashboards/agent_context_pack.json`
   - `research_cockpit/dashboards/focus_context_pack.json`
   - `research_cockpit/dashboards/search_index.json` when searching.

## Node Handoff

When a human assigns a specific node id, use the read-only onboarding command before opening raw YAML:

```sh
research-cockpit node-context --root research_cockpit --id <node_id> --json
```

The payload is computed from truth-source YAML, not from stale generated dashboards. It includes parent chain, relations, blockers, next actions, relevant suggestions, resources, recent interactions, and type-specific context for options, experiments, and decisions. Command drafts in the payload include `--root` so they can be reused safely from an agent shell.

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
