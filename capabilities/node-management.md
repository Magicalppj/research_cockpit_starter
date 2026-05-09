# Node Management

Use this capability when adding nodes, updating status, or applying suggestions.

## Add Nodes

```sh
research-cockpit add-node --root research_cockpit --id problem_x --type problem --title "..." --parent stage_x
```

Prefer explicit parent links. Keep IDs stable ASCII identifiers.

`add-node` supports `--dry-run --json --show-diff` for previewing one node and `--no-build` for batching. It rebuilds dashboards by default. Batching means serial command execution; do not parallelize mutating commands against the same data root. If a mutation conflict is reported, another write changed the target truth-source file after this command planned its write; reread context and retry.

```sh
research-cockpit add-node --root research_cockpit --id experiment_x --type experiment --title "..." --parent option_x --dry-run --json --show-diff
research-cockpit add-node --root research_cockpit --id experiment_x --type experiment --title "..." --parent option_x --no-build
```

Do not create `artifact` nodes for routine files, configs, JSON outputs, or experiment byproducts. Prefer `linked_artifacts`, `links`, notes, or resource references on the relevant research node. Create an `artifact` node only when the artifact is a long-lived research object or key deliverable that needs its own status and history.

## Batch Graph Plans

Use `apply-graph-plan` when creating or updating several graph nodes. It validates the candidate graph once, writes all YAML only after validation passes, and rebuilds once by default.
Use one batch command or run smaller mutating commands sequentially. Mutating commands share `graph/interaction_log.yaml`, use `graph/.mutation.lock`, and refuse stale writes when target files changed after planning.

```sh
research-cockpit apply-graph-plan --print-schema
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
research-cockpit create-workstream --print-schema
research-cockpit create-workstream --root research_cockpit --file workstream.yaml --dry-run --json --show-diff
research-cockpit create-workstream --root research_cockpit --file workstream.yaml --dry-run --json --compact
research-cockpit create-workstream --root research_cockpit --file workstream.yaml --no-build
```

Workstream file v1:

```yaml
problem:
  id: problem_x
  title: New research problem
  parent: stage_x
  status: active
  summary: Scope the next research branch.
  question: What should we optimize next?
  hypothesis: A narrower branch will reduce command count.
  tags:
    - workflow
  next_actions:
    - Run the first planned experiment.
active_option:
  id: option_x
  title: Active route
  status: active
  summary: Try the shortest route to evidence.
  hypothesis: This route has the shortest path to signal.
experiments:
  - id: experiment_x1
    title: Run first check
    success_criteria:
      - The check produces a comparable metric.
    metrics:
      - command_count
  - id: experiment_x2
    title: Run second check
    success_criteria:
      - The check can be reviewed without reading full node YAML.
followup_options:
  - id: option_followup_x
    title: Follow-up route
    status: open
```

It creates the branch, sets the problem `current_best_option`, and adds experiment ids to the active option `supporting_experiments`. It does not change focus or pause old options.
Use `open` for not-yet-selected follow-up options. File-based graph commands accept option status `planned` as an input alias and write `open` to truth-source YAML because `planned` is not a stored option status. JSON results include `normalized_statuses` when this happens.
`create-workstream --print-schema` shows the short supported example. Common node fields such as `summary`, `question`, `hypothesis`, `tags`, `success_criteria`, `metrics`, and `next_actions` pass through to the created graph nodes.
After creation, use `option-workstream-context --root research_cockpit --id option_x --compact --json` to verify experiment ids, statuses, success criteria count, metric count, finding count, and linked artifact count. Read per-experiment `node-context` only when exact full text is needed.

## Update Status

```sh
research-cockpit update-status --root research_cockpit --id option_x --status active --dry-run --json --show-diff
research-cockpit update-status --root research_cockpit --id option_x --status active --no-build
```

Use only statuses accepted by validation for that node type.
`update-status` rebuilds dashboards by default; pass `--no-build` when batching several mutations and run one final `validate` + `build`. `--summary` still replaces the node summary for compatibility, so preview with `--dry-run --show-diff` before using it on nodes that already have summaries. Dry-run strict-parses `interaction_log.yaml`, so a bad log fails before a misleading preview. For content-only summary changes, prefer `update-node-fields --summary`.

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
research-cockpit update-suggestion-state --root research_cockpit --id sg_x --state dismissed --reason "..." --dry-run --json --show-diff
research-cockpit update-suggestion-state --root research_cockpit --id sg_x --state dismissed --reason "..." --no-build
```

Both `apply-suggestion` and `update-suggestion-state` accept either stable `suggestion_id`/`key` or display `display_id`/`id`.
`update-suggestion-state` and `cleanup-suggestion-lifecycle` support JSON/dry-run preview; add `--show-diff` only when reviewing the exact `current_state.yaml` change.

## Recipes

Add a new problem + active option + experiments:

```sh
research-cockpit create-workstream --print-schema
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

Parallel agents with git worktrees:

```sh
research-cockpit start-agent-session --root D:/main_repo/research_cockpit --option option_x --agent agent_x --objective "Run branch experiments" --branch agent/option_x --worktree ../worktrees/agent_option_x --base main --create-worktree --dry-run --json --show-diff
research-cockpit start-agent-session --root D:/main_repo/research_cockpit --option option_x --agent agent_x --objective "Run branch experiments" --branch agent/option_x --worktree ../worktrees/agent_option_x --base main --create-worktree --no-build
research-cockpit set-agent-focus --root D:/main_repo/research_cockpit --agent agent_x --node experiment_x --no-build
research-cockpit validate --root D:/main_repo/research_cockpit --json
```

The worktree is only for code/experiment isolation. Research graph mutations still go to the canonical root in the main repository. Relative `--worktree` values resolve against the canonical repository root (`--root` parent). Use `set-agent-focus` for downstream progress and reserve global `set-focus` for coordinator-level focus changes. Preserve useful run outputs with `ingest-artifact` before deleting the worktree; see `experiment-tracking.md` and `integrations.md`.
