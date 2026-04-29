# Node Management

Use this capability when adding nodes, updating status, or applying suggestions.

## Add Nodes

```powershell
python .agent\skills\research-cockpit\scripts\add_node.py --root research_cockpit --id problem_x --type problem --title "..." --parent stage_x
```

Prefer explicit parent links. Keep IDs stable ASCII identifiers.

## Update Status

```powershell
python .agent\skills\research-cockpit\scripts\update_status.py --root research_cockpit --id option_x --status active
```

Use only statuses accepted by validation for that node type.

## Suggestions

Read suggestions:

```powershell
python .agent\skills\research-cockpit\scripts\suggest_next_actions.py --root research_cockpit --json
```

Apply suggestions through scripts, not direct YAML edits:

```powershell
python .agent\skills\research-cockpit\scripts\apply_suggestion.py --root research_cockpit --id sg_x --target current --dry-run --json
python .agent\skills\research-cockpit\scripts\apply_suggestion.py --root research_cockpit --id sg_x --target current
```

Update suggestion lifecycle:

```powershell
python .agent\skills\research-cockpit\scripts\update_suggestion_state.py --root research_cockpit --id sg_x --state dismissed --reason "..."
```
