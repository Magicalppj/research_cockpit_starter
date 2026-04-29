# Node Management

Use this capability when adding nodes, updating status, or applying suggestions.

## Add Nodes

```sh
research-cockpit add-node --root research_cockpit --id problem_x --type problem --title "..." --parent stage_x
```

Prefer explicit parent links. Keep IDs stable ASCII identifiers.

## Update Status

```sh
research-cockpit update-status --root research_cockpit --id option_x --status active
```

Use only statuses accepted by validation for that node type.

## Safe Archive Instead of Delete

There is no public `delete-node` command. If a user asks to delete, close, or archive a node, use a safe status transition such as `parked`, `rejected`, `resolved`, or `archived` when that status is valid for the node type:

```sh
research-cockpit update-status --root research_cockpit --id option_x --status parked
```

Do not physically remove YAML files unless a human explicitly requests a structural data repair and you can validate the resulting graph.

`--result-summary` is only accepted for `experiment` nodes.

## Suggestions

Read suggestions:

```sh
research-cockpit suggest-next-actions --root research_cockpit --json
```

Apply suggestions through `research-cockpit` commands, not direct YAML edits:

```sh
research-cockpit apply-suggestion --root research_cockpit --id sg_x --target current --dry-run --json
research-cockpit apply-suggestion --root research_cockpit --id sg_x --target current
```

Update suggestion lifecycle:

```sh
research-cockpit update-suggestion-state --root research_cockpit --id sg_x --state dismissed --reason "..."
```
