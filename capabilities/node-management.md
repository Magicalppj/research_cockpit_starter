# Node Management

Use this capability when adding nodes, updating status, or applying suggestions.

## Add Nodes

```sh
research-cockpit add-node --root research_cockpit --id problem_x --type problem --title "..." --parent stage_x
```

Prefer explicit parent links. Keep IDs stable ASCII identifiers.

`add-node` supports `--dry-run --json --show-diff` for previewing one node and `--no-build` for batching. It rebuilds dashboards by default.

```sh
research-cockpit add-node --root research_cockpit --id experiment_x --type experiment --title "..." --parent option_x --dry-run --json --show-diff
research-cockpit add-node --root research_cockpit --id experiment_x --type experiment --title "..." --parent option_x --no-build
```

Do not create `artifact` nodes for routine files, configs, JSON outputs, or experiment byproducts. Prefer `linked_artifacts`, `links`, notes, or resource references on the relevant research node. Create an `artifact` node only when the artifact is a long-lived research object or key deliverable that needs its own status and history.

## Batch Graph Plans

Use `apply-graph-plan` when creating or updating several graph nodes. It validates the candidate graph once, writes all YAML only after validation passes, and rebuilds once by default.

```sh
research-cockpit apply-graph-plan --root research_cockpit --file graph_update.yaml --dry-run --json --show-diff
research-cockpit apply-graph-plan --root research_cockpit --file graph_update.yaml --no-build
research-cockpit validate --root research_cockpit --json
research-cockpit build --root research_cockpit
```

Plan file v1:

```yaml
nodes:
  - id: problem_x
    type: problem
    title: New problem
    parent: stage_x
    status: active
    fields:
      question: What should we test?
      current_best_option: option_x
  - id: option_x
    type: option
    title: Active option
    parent: problem_x
    status: active
updates:
  - id: problem_x
    fields:
      tag: timeline-control
      next_actions:
        - Review first experiment result.
```

New nodes with `parent` automatically append themselves to the parent `children` list.

## Create Workstreams

Use `create-workstream` for the common `problem -> active option -> planned experiments + follow-up options` shape. It is a thin wrapper over `apply-graph-plan`.

```sh
research-cockpit create-workstream --root research_cockpit --file workstream.yaml --dry-run --json --show-diff
research-cockpit create-workstream --root research_cockpit --file workstream.yaml --no-build
```

Workstream file v1:

```yaml
problem:
  id: problem_x
  title: New research problem
  parent: stage_x
  question: What should we optimize next?
active_option:
  id: option_x
  title: Active route
  hypothesis: This route has the shortest path to signal.
experiments:
  - id: experiment_x1
    title: Run first check
  - id: experiment_x2
    title: Run second check
followup_options:
  - id: option_followup_x
    title: Follow-up route
```

It creates the branch, sets the problem `current_best_option`, and adds experiment ids to the active option `supporting_experiments`. It does not change focus or pause old options.

## Update Status

```sh
research-cockpit update-status --root research_cockpit --id option_x --status active
```

Use only statuses accepted by validation for that node type.
`update-status` rebuilds dashboards by default; pass `--no-build` when batching several mutations and run one final `validate` + `build`.

Status meanings:

| Node type | Valid statuses | Meaning |
| --- | --- | --- |
| `stage` | `planned`, `active`, `blocked`, `done` | planned milestone, active phase, blocked phase, completed phase |
| `problem` | `open`, `active`, `blocked`, `resolved`, `parked` | recorded problem, actively handled problem, blocked problem, solved problem, intentionally parked problem |
| `option` | `open`, `active`, `promising`, `rejected`, `accepted`, `paused`, `parked` | candidate branch, active branch, positive-signal branch, rejected branch, adopted branch, paused branch, parked branch |
| `experiment` | `planned`, `queued`, `running`, `done`, `failed`, `cancelled` | planned check, queued run, running run, completed run, failed run, cancelled run |
| `decision` | `proposed`, `accepted`, `superseded`, `rejected` | proposed ADR-style decision, accepted decision, replaced decision, rejected decision |
| `artifact` | `draft`, `planned`, `active`, `done`, `superseded`, `deprecated`, `archived` | supporting material draft, planned artifact, active artifact, completed artifact, replaced artifact, deprecated artifact, archived artifact |

Use `promising` only for `option` nodes that have evidence or strong rationale but still need comparison, experiment results, or a decision gate before acceptance. Do not use `promising` for `experiment` or `decision` nodes.

## Safe Archive Instead of Delete

There is no public `delete-node` command. If a user asks to delete, close, or archive a node, use a safe status transition such as `parked`, `rejected`, `resolved`, or `archived` when that status is valid for the node type:

```sh
research-cockpit update-status --root research_cockpit --id option_x --status parked
```

Do not physically remove YAML files unless a human explicitly requests a structural data repair and you can validate the resulting graph.

`--result-summary` is only accepted for `experiment` nodes.

## Narrow Field Updates

Use `update-node-fields` when a supported node field would otherwise require hand-editing YAML:

```sh
research-cockpit update-node-fields --root research_cockpit --id problem_x --current-best-option option_x --no-build
research-cockpit update-node-fields --root research_cockpit --id experiment_x --replace-next-actions "Review metrics" --replace-next-actions "Draft decision" --no-build
research-cockpit update-node-fields --root research_cockpit --id problem_x --question "..." --hypothesis "..." --tag timeline-control --success-criterion "..." --supporting-experiment experiment_x --no-build
```

`--current-best-option` is only valid on `problem` nodes and must point to a child `option`. `--replace-next-actions` replaces the node `next_actions` list with the repeated values supplied in the command. `--next-action` appends de-duplicated actions and cannot be used with `--replace-next-actions` in the same call.

Supported scalar replace flags: `--title`, `--summary`, `--question`, `--hypothesis`, `--evidence-summary`, `--result-summary`, `--priority`.

Supported list append flags: `--tag`, `--success-criterion`, `--metric`, `--pro`, `--con`, `--next-action`, `--supporting-experiment`, `--contradicting-experiment`, `--supporting-decision`, `--linked-artifact`, `--alternative`, `--derived-from`.

## Suggestions

Read suggestions:

```sh
research-cockpit suggest-next-actions --root research_cockpit --json
```

Run this once before choosing work. Re-run only after changing `next_actions` or suggestion lifecycle state.
JSON output includes stable `suggestion_id` (`sg_...`) and human display `display_id` (`next_action_...`). Older fields remain: `key` equals `suggestion_id`, and `id` equals `display_id`.

Apply suggestions through `research-cockpit` commands, not direct YAML edits:

```sh
research-cockpit apply-suggestion --root research_cockpit --id sg_x --target current --dry-run --json
research-cockpit apply-suggestion --root research_cockpit --id sg_x --target current
```

Update suggestion lifecycle:

```sh
research-cockpit update-suggestion-state --root research_cockpit --id sg_x --state dismissed --reason "..."
```

Both `apply-suggestion` and `update-suggestion-state` accept either stable `suggestion_id`/`key` or display `display_id`/`id`.

## Recipes

Add a new problem + active option + experiments:

```sh
research-cockpit create-workstream --root research_cockpit --file workstream.yaml --dry-run --json --show-diff
research-cockpit create-workstream --root research_cockpit --file workstream.yaml --no-build
research-cockpit validate --root research_cockpit --json
research-cockpit build --root research_cockpit
```

Pivot focus without deleting old branch:

```sh
research-cockpit create-workstream --root research_cockpit --file workstream.yaml --no-build
research-cockpit set-focus --root research_cockpit --focus-node problem_new --no-build
research-cockpit sync-focus-actions --root research_cockpit --from-node problem_new --no-build
research-cockpit validate --root research_cockpit --json
research-cockpit build --root research_cockpit
```

Pause old option and preserve it as supporting analysis:

```sh
research-cockpit update-status --root research_cockpit --id option_old --status paused --summary "Preserved as supporting analysis." --no-build
research-cockpit update-node-fields --root research_cockpit --id problem_new --supporting-experiment experiment_old --no-build
research-cockpit validate --root research_cockpit --json
research-cockpit build --root research_cockpit
```
