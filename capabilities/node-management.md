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
Use one batch command or run smaller mutating commands sequentially. Mutating commands share `graph/interaction_log.yaml`, use `graph/.mutation.lock`, and refuse stale writes when target files changed after planning. After a small node edit, use changed-scope validation and compact context; reserve full build/smoke for coordinator or final handoff.

```sh
research-cockpit apply-graph-plan --print-schema
research-cockpit apply-graph-plan --root research_cockpit --file graph_update.yaml --dry-run --json --show-diff
research-cockpit apply-graph-plan --root research_cockpit --file graph_update.yaml --no-build
research-cockpit validate --root research_cockpit --changed-node experiment_x --json
research-cockpit context --root research_cockpit --id experiment_x --with-bootstrap --with-artifacts --compact --json
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
  - id: experiment_x
    type: experiment
    title: First check
    parent: option_x
    status: queued
    fields:
      priority: high
      order: p2.2
      owner: agent_x
      ready_for_agent: true
      handoff_context: Run the first check and record one finding.
updates:
  - id: problem_x
    fields:
      tag: timeline-control
      next_actions:
        - Review first experiment result.
  - id: experiment_x
    status: running
```

New nodes with `parent` automatically append themselves to the parent `children` list.
For existing nodes, put status transitions at `updates[*].status`; put content and assignment metadata under `updates[*].fields`. `apply-graph-plan --print-schema` lists supported fields and rejects unsupported update top-level keys instead of silently ignoring them.

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

For a derived branch, set `problem.parent` to the inherited option id instead of the stage id. This creates a readable `option -> problem -> option -> experiment` subtree and keeps later agents from flattening every follow-up experiment under the same option. Add `derived_from` to the new problem or first experiment to record the prior experiment, worktree result, or option that motivated the branch.

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

## Terminal Parent Lifecycle Guard

A `problem` or `option` with active descendants cannot be marked terminal. This keeps the graph from claiming a branch is closed while work under that branch is still live.

Terminal parent statuses:

- `problem`: `resolved`, `parked`
- `option`: `accepted`, `rejected`, `paused`, `parked`

Active downstream statuses:

- `problem`: `open`, `active`, `blocked`
- `option`: `open`, `active`, `promising`
- `experiment`: `planned`, `queued`, `running`

If `update-status`, `finalize-workstream`, `apply-graph-plan`, `accept-decision`, `promote-decision --status accepted`, or `create-workstream` reports `terminal_parent_has_active_descendants`, do not force the parent status. Preview the explicit cleanup first:

```sh
research-cockpit close-branch --root research_cockpit --id <problem_or_option_id> --downstream-status parked --dry-run --json --show-diff
```

The result lists:

- `updates`: descendants that can be safely moved, usually active `problem`/`option` nodes to `parked`.
- `skipped`: descendants that were not changed, including experiments that require explicit confirmation.
- `remaining_active_descendants`: blockers that must be handled before the parent can become terminal.
- `parent_ready_for_terminal_status`: whether the parent status command can be retried.

By default `close-branch` does not cancel `planned`, `queued`, or `running` experiments because those may correspond to external jobs. After confirming the run or job is stopped or intentionally abandoned, rerun with:

```sh
research-cockpit close-branch --root research_cockpit --id <problem_or_option_id> --downstream-status parked --include-experiments --no-build
research-cockpit update-status --root research_cockpit --id <problem_or_option_id> --status <terminal_status> --dry-run --json --show-diff
research-cockpit update-status --root research_cockpit --id <problem_or_option_id> --status <terminal_status> --no-build
```

With `--include-experiments`, active experiments move to `cancelled`, not `parked`. Choose `<terminal_status>` from `resolved|parked` for a `problem`, or `accepted|rejected|paused|parked` for an `option`.

Use `validate --strict-lifecycle --json` as an opt-in audit for older repositories. Plain `validate` remains compatible with historical data.

## Safe Archive Instead of Delete

There is no public `delete-node` command. If a user asks to delete, close, or archive a node, use a safe status transition such as `parked`, `rejected`, `resolved`, or `archived` when that status is valid for the node type:

```sh
research-cockpit close-branch --root research_cockpit --id option_x --downstream-status parked --dry-run --json --show-diff
research-cockpit update-status --root research_cockpit --id option_x --status parked
```

Do not physically remove YAML files unless a human explicitly requests a structural data repair and you can validate the resulting graph.

`--result-summary` is only accepted for `experiment` nodes.

## Narrow Field Updates

Use `update-node-fields` when a supported node field would otherwise require hand-editing YAML:

```sh
research-cockpit update-node-fields --root research_cockpit --id problem_x --current-best-option option_x --no-build
research-cockpit update-node-fields --root research_cockpit --id experiment_x --replace-next-actions "Review metrics" --replace-next-actions "Draft decision" --no-build
research-cockpit update-node-fields --root research_cockpit --id experiment_x --clear-next-actions --next-action "Review metrics" --next-action "Draft decision" --no-build
research-cockpit update-node-fields --root research_cockpit --id experiment_x --priority high --order p2.2 --owner agent_x --ready-for-agent --depends-on option_x --handoff-context "Run and record one finding" --no-build
research-cockpit update-node-fields --root research_cockpit --id problem_x --question "..." --hypothesis "..." --tag timeline-control --success-criterion "..." --supporting-experiment experiment_x --no-build
```

`--current-best-option` is only valid on `problem` nodes and must point to a child `option`. `--replace-next-actions` replaces the node `next_actions` list with the repeated values supplied in the command. Prefer `--clear-next-actions --next-action ...` when composing a replacement list interactively; it reads as "clear then rebuild". `--next-action` alone appends de-duplicated actions and cannot be used with `--replace-next-actions` in the same call.

Supported scalar replace flags: `--title`, `--summary`, `--question`, `--hypothesis`, `--evidence-summary`, `--result-summary`, `--priority`, `--order`, `--rank`, `--owner`, `--handoff-context`.

Supported list append flags: `--tag`, `--success-criterion`, `--metric`, `--pro`, `--con`, `--next-action`, `--supporting-experiment`, `--contradicting-experiment`, `--supporting-decision`, `--linked-artifact`, `--alternative`, `--derived-from`, `--depends-on`, `--blocked-by`.

Supported boolean flags: `--ready-for-agent`, `--not-ready-for-agent`.

Use `priority` as coarse urgency and `order`/`rank` for stable sequencing. Experiment assignment fields (`owner`, `ready_for_agent`, `depends_on`, `blocked_by`, `handoff_context`) validate only on experiment nodes.

For nested option workstream metadata, use the narrow allowlisted command instead of patching YAML:

```sh
research-cockpit update-workstream-fields --root research_cockpit --option option_x --status reported --objective "Summarize downstream results" --owner agent_x --report-to-problem problem_x --no-build
```

`--report-to-problem` must reference an existing `problem` node id.

## Suggestions

Read suggestions:

```sh
research-cockpit suggest-next-actions --root research_cockpit --json --limit 10 --focus-only
```

Run this bounded read once before choosing work. Re-run only after changing `next_actions` or suggestion lifecycle state. Use full unbounded output only when auditing the complete suggestion list.
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
research-cockpit validate --root research_cockpit --changed-node experiment_x --changed-node option_x --json
research-cockpit context --root research_cockpit --id option_x --with-bootstrap --with-artifacts --compact --json
```

Pivot focus without deleting old branch:

```sh
research-cockpit create-workstream --root research_cockpit --file workstream.yaml --no-build
research-cockpit set-focus --root research_cockpit --focus-node problem_new --no-build
research-cockpit sync-focus-actions --root research_cockpit --from-node problem_new --no-build
research-cockpit validate --root research_cockpit --changed-file current_state.yaml --changed-node problem_new --json
research-cockpit context --root research_cockpit --id problem_new --with-bootstrap --with-artifacts --compact --json
```

Pause old option and preserve it as supporting analysis:

```sh
research-cockpit update-status --root research_cockpit --id option_old --status paused --summary "Preserved as supporting analysis." --no-build
research-cockpit update-node-fields --root research_cockpit --id problem_new --supporting-experiment experiment_old --no-build
research-cockpit validate --root research_cockpit --changed-node option_old --changed-node problem_new --json
research-cockpit context --root research_cockpit --id problem_new --with-bootstrap --with-artifacts --compact --json
```

Parallel agents with git worktrees:

```sh
research-cockpit start-agent-session --root D:/main_repo/research_cockpit --option option_x --label branch_probe --objective "Run branch experiments" --branch agent/option_x-branch_probe --worktree ../worktrees/branch_probe --base main --create-worktree --dry-run --json --show-diff
research-cockpit start-agent-session --root D:/main_repo/research_cockpit --option option_x --label branch_probe --objective "Run branch experiments" --branch agent/option_x-branch_probe --worktree ../worktrees/branch_probe --base main --create-worktree --no-build
research-cockpit set-cursor --root D:/main_repo/research_cockpit --assignment <assignment_id> --node experiment_x --no-build
research-cockpit validate --root D:/main_repo/research_cockpit --changed-file assignments/<assignment_id>.yaml --json
research-cockpit context --root D:/main_repo/research_cockpit --id experiment_x --with-bootstrap --with-artifacts --compact --json
```

Dry-run generated ids are preview-only; pass explicit `--agent` and `--assignment` / `--assignment-id` on execute when the same previewed ids must be reused. The worktree is only for code/experiment isolation. Research graph mutations still go to the canonical root in the main repository. Relative `--worktree` values resolve against the canonical repository root (`--root` parent). Use `set-cursor --assignment <assignment_id>` for downstream progress and reserve `set-focus` for coordinator/UI selection. `set-agent-focus` is legacy compatibility only. Preserve useful run outputs with `ingest-artifact` before deleting the worktree; see `experiment-tracking.md` and `integrations.md`.
