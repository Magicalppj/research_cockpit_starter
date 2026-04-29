# Focus Context

Use this capability at the start of an agent session and before making local research decisions.

## Startup Read Order

1. Bootstrap:

```powershell
python .agent\skills\research-cockpit\scripts\agent_bootstrap.py --root research_cockpit --build --json
```

2. If dashboard files are missing or stale:

```powershell
python .agent\skills\research-cockpit\scripts\build_dashboard.py --root research_cockpit
```

3. Read:
   - `research_cockpit/dashboards/agent_context_pack.json`
   - `research_cockpit/dashboards/focus_context_pack.json`
   - `research_cockpit/dashboards/search_index.json` when searching.

## Search

```powershell
python .agent\skills\research-cockpit\scripts\search_knowledge.py --root research_cockpit --query "retrieval branch" --json --limit 10
```

Search covers YAML nodes, Markdown notes, and safe local linked resources under `research_cockpit/`.

## Focus Updates

Use `set_focus.py` for focus changes:

```powershell
python .agent\skills\research-cockpit\scripts\set_focus.py --root research_cockpit --focus-node problem_id
```

Focus changes write `current_state.yaml`, append `interaction_log.yaml`, and rebuild dashboard/context unless `--no-build` is passed.
