# Experiment Tracking

Use this capability for experiments, findings, and option workstreams.

## Option Workstreams

Create a new problem/option/experiment branch from a file when planning a new workstream:

```sh
research-cockpit create-workstream --print-schema
research-cockpit create-workstream --root research_cockpit --file workstream.yaml --dry-run --json --show-diff
research-cockpit create-workstream --root research_cockpit --file workstream.yaml --no-build
```

This creates planned experiment nodes under the active option and records them as that option's `supporting_experiments`. It does not claim an agent workstream, change focus, or pause existing options.

Claim an option branch:

```sh
research-cockpit claim-option --root research_cockpit --option option_x --agent agent_id --objective "..." --dry-run --json
research-cockpit claim-option --root research_cockpit --option option_x --agent agent_id --objective "..."
research-cockpit claim-workstream --root research_cockpit --option option_x --agent agent_id --objective "..." --dry-run --json
```

For parallel agents that run code in git worktrees, prefer `start-agent-session` over plain claim. It can create the worktree and records portable session metadata on the option while keeping the canonical Research Cockpit root in the main repo:

```sh
research-cockpit start-agent-session --root D:/main_repo/research_cockpit --option option_x --agent agent_x --objective "Run experiments" --branch agent/option_x --worktree ../worktrees/agent_option_x --base main --create-worktree --dry-run --json --show-diff
research-cockpit start-agent-session --root D:/main_repo/research_cockpit --option option_x --agent agent_x --objective "Run experiments" --branch agent/option_x --worktree ../worktrees/agent_option_x --base main --create-worktree --no-build
research-cockpit agent-session-context --root D:/main_repo/research_cockpit --agent agent_x --compact --json
```

Relative `--worktree` values resolve against the canonical repository root (`--root` parent), matching `git -C <repo> worktree add`. Do not store local absolute worktree paths in YAML. `start-agent-session` writes `session_id`, `owner`, `status`, `objective`, `git_branch`, `worktree_label`, `report_to_problem`, `started_at`, and `updated_at`; absolute paths appear only in JSON `handoff`/`launch_env`. `--create-worktree` expects a new branch/worktree path; for an already-created worktree, rerun without `--create-worktree`.

Read workstream context:

```sh
research-cockpit context --root research_cockpit --node option_x --with-bootstrap --with-artifacts --compact --json
research-cockpit option-workstream-context --root research_cockpit --option option_x --json
research-cockpit option-workstream-context --root research_cockpit --id option_x --compact --json
```

Use `context` as the default handoff for a known option or experiment. Use compact `option-workstream-context` when you specifically need the recursive option subtree report, short experiment summaries, and evidence counts; `--id` is the preferred target flag and `--option` remains compatible for full output.
The compact payload includes `experiment_summaries` with each experiment id, title, status, result summary, success criteria count, first success criterion, metric count, finding count, and linked artifact count. Use full context or `node-context` only when the exact complete field text matters.

Report a workstream:

```sh
research-cockpit report-option-workstream --root research_cockpit --option option_x --agent agent_id --recommend continue --summary "..." --dry-run --json
research-cockpit report-option-workstream --root research_cockpit --option option_x --agent agent_id --recommend continue --summary "..."
```

Finalize a workstream only when the close-out status changes are explicit:

```sh
research-cockpit finalize-workstream --print-schema
research-cockpit finalize-workstream --root research_cockpit --file finalize.yaml --dry-run --json --show-diff
research-cockpit finalize-workstream --root research_cockpit --file finalize.yaml --json --compact
research-cockpit finalize-workstream --root research_cockpit --file finalize.yaml --no-build
research-cockpit finalize-workstream --root research_cockpit --option option_x --status accepted --problem-status resolved --summary-file summary.md --summary-target report --artifact artifact_x --sync-focus --report --dry-run --json --show-diff
research-cockpit finalize-workstream --root research_cockpit --option option_x --status accepted --problem-status resolved --summary-file summary.md --summary-target report --artifact artifact_x --sync-focus --report --no-build
```

Use `--file` to avoid long close-out commands. The file supports `option`, `status`, `problem_status`, `stage_status`, `summary_file`, `summary_target`, `artifacts`, `sync_focus`, `report`, `agent`, and `locale`; CLI flags override file values. A relative `summary_file` in the file resolves against the finalize file directory, then the data root, then cwd, and JSON output reports the resolved path. `finalize-workstream` does not create artifacts, accept decisions, pause old branches, delete nodes, or invent next actions. `--summary-file` writes only to the workstream report by default; use `--summary-target option|problem|all` when you explicitly want node summaries replaced.

## Baseline Selection

Treat findings as raw evidence, promising/accepted options as evaluated branches, and `baseline` as the default branch a downstream agent should inherit. Do not put every accepted option or decision into a known-node handoff. Set the default explicitly:

```sh
research-cockpit set-baseline --root research_cockpit --node problem_x --option option_x --decision decision_x --artifact artifact_bundle_x --dry-run --json --show-diff
research-cockpit set-baseline --root research_cockpit --node problem_x --option option_x --decision decision_x --artifact artifact_bundle_x --no-build
research-cockpit set-baseline --root research_cockpit --node problem_x --clear --no-build
```

`context` and `node-context` return `effective_baseline`, resolved from the target node, inherited parents, problem `current_best_option` / `resolved_by`, then `current_state.current_option`. Use the UI Baselines / Accepted page to review accepted history and generate these commands. The overview table is per problem: it uses that problem's baseline or current best, not the global `current_state.current_option`.

If an agent accidentally wrote evidence to a worktree-local `research_cockpit/`, use the import command as a recovery step, not as the normal workflow:

```sh
research-cockpit import-worktree-findings --root D:/main_repo/research_cockpit --from-root ../worktrees/agent_option_x/research_cockpit --agent agent_x --option option_x --dry-run --json --show-diff
research-cockpit import-worktree-findings --root D:/main_repo/research_cockpit --from-root ../worktrees/agent_option_x/research_cockpit --agent agent_x --option option_x --no-build
```

The importer only accepts artifact nodes, experiment findings, result summaries, experiment-local `next_actions`, and option workstream reports. It rejects structural node changes, global focus changes, per-agent focus changes, and decision acceptance.

## Worktree Artifact Ingest

Worktree output is temporary. Before deleting a worktree or using its files as evidence, copy the run directory into the canonical artifact store:

```sh
research-cockpit ingest-artifact --root D:/main_repo/research_cockpit --node experiment_x --from ../worktrees/agent_option_x/.agent_runs/run_x --run-id run_x --agent agent_x --link metrics=metrics.json --dry-run --json --show-diff
research-cockpit ingest-artifact --root D:/main_repo/research_cockpit --node experiment_x --from ../worktrees/agent_option_x/.agent_runs/run_x --run-id run_x --agent agent_x --link metrics=metrics.json --json --compact
research-cockpit complete-experiment --root D:/main_repo/research_cockpit --id experiment_x --finding "..." --confidence medium --artifact-id artifact_experiment_x_run_x --no-build
research-cockpit validate --root D:/main_repo/research_cockpit --json
research-cockpit build --root D:/main_repo/research_cockpit
```

`ingest-artifact` copies `--from` to `research_cockpit/artifacts/<node_id>/<run_id>/`, writes `_research_cockpit_ingest.json`, creates a `done` artifact, and links it to `--node`. Repeated `--link key=relative/path` values must point inside the source directory; they are rewritten to stable artifact-store paths. The ingest manifest records the source path relative to the canonical root parent when possible; external source directories are recorded only as a short hint, not as a machine-local path. Source directories containing symlinks are rejected in v1. The command does not record findings, accept decisions, or set baselines.

Use this as the normal path for multi-agent worktrees. Use inline `--evidence-path` only for files that already live at a stable path outside the disposable worktree.

## Artifacts

Use artifact commands for result folders, review bundles, metrics directories, and other evidence objects that need their own status or links:

```sh
research-cockpit create-artifact --print-schema
research-cockpit create-artifact --root research_cockpit --file artifact.yaml --dry-run --json --show-diff
research-cockpit create-artifact --root research_cockpit --file artifact.yaml --json --compact
research-cockpit create-artifact --root research_cockpit --file artifact.yaml --no-build
research-cockpit create-artifact --root research_cockpit --id artifact_x --title "Result bundle" --status done --path artifacts/experiment_x/run_x --link metrics=artifacts/experiment_x/run_x/metrics.json --link-to experiment_x --dry-run --json --show-diff
research-cockpit create-artifact --root research_cockpit --id artifact_x --title "Result bundle" --status done --path artifacts/experiment_x/run_x --link metrics=artifacts/experiment_x/run_x/metrics.json --link-to experiment_x --no-build
research-cockpit link-artifact --root research_cockpit --artifact artifact_x --to option_x --link review=notes/review.md --no-build
```

Use `--file` when an artifact has several `links` or `link_to` targets; it is shorter and easier to review than a long repeated-flag command.

`create-artifact` and `link-artifact` update artifact `path`/`links` and reverse `linked_artifacts` references. They do not require local resource paths to exist. YAML stores paths exactly as provided; JSON resource rows include `resolved_target`, `resolution_base`, `resolution_attempts`, and `exists`. Relative paths are checked against the root parent, then the data root, then cwd.

## Findings

Record experiment findings through `research-cockpit record-finding`:

```sh
research-cockpit record-finding --root research_cockpit --experiment experiment_x --statement "..." --confidence medium --outcome positive --summary "..." --evidence-path artifacts/experiment_x/run_x --evidence-link metrics=artifacts/experiment_x/run_x/metrics.json
research-cockpit record-finding --root research_cockpit --experiment experiment_x --statement "..." --confidence medium --artifact-id artifact_x
```

Use `--evidence-path` and repeated `--evidence-link key=value` when the result directory, image, report, or metrics JSON should be recorded with the finding. The command creates an `artifact_<finding_id>` artifact, links it from the finding, and mirrors it on the experiment top-level `linked_artifacts` field so context, resource tables, and dashboards can show where the conclusion came from. Use `create-artifact` plus `--artifact-id` when you need custom artifact metadata.

Do not pass worktree-local output paths to `--evidence-path` for long-lived evidence. First run `ingest-artifact`, then pass the created artifact id with `--artifact-id`.

`--artifact-id` must be an existing artifact node id, not a file path. A finding can be recorded without any artifact; the command succeeds but JSON output includes `warnings: ["missing_evidence_artifact"]`.

Use `complete-experiment` when you want the conservative "record conclusion and mark done" workflow in one command:

```sh
research-cockpit complete-experiment --root research_cockpit --id experiment_x --finding "..." --confidence medium --outcome mixed --result-summary "..." --artifact-id artifact_experiment_x_run_x --next-action "Review follow-up" --no-build
research-cockpit complete-experiment --root research_cockpit --id experiment_x --finding "..." --confidence medium --evidence-path artifacts/experiment_x/run_x --evidence-link metrics=artifacts/experiment_x/run_x/metrics.json --json --compact
```

`complete-experiment` appends a structured finding, sets the experiment status to `done`, optionally updates `result_summary`, creates inline evidence artifacts when evidence fields are present, and appends de-duplicated experiment-local `next_actions`. It does not change focus, option status, problem status, or `current_best_option`.

If the completed experiment is still the global `current_focus_node` or a per-agent focus node, JSON output includes focus-stale warnings plus recommended `set-focus` / `set-agent-focus` commands. Use explicit focus movement when closing a branch:

```sh
research-cockpit close-current-experiment --root research_cockpit --id experiment_x --finding "..." --confidence medium --next-focus option_x --sync-agent agent_x --json --compact
```

`--next-focus` must point to a non-terminal node and cannot be the experiment being closed.
When `--next-focus` is present, the command copies that node's node-local `next_actions` into global `current_state.next_actions`. `--sync-agent <agent_id>` moves one agent focus to the same node; `--sync-agent all` moves only agents currently focused on the experiment being closed.

For mixed or incomplete findings that need a follow-up gate, derive the next experiment instead of hand-editing YAML:

```sh
research-cockpit create-followup-experiment --root research_cockpit --from experiment_x --parent option_x --id experiment_x_followup --title "Follow-up gate" --next-action "Run follow-up gate" --set-focus --json --compact
```

`--next-action` writes the initial follow-up experiment `next_actions`. When combined with `--set-focus`, the same action is also written to `current_state.next_actions`, so dashboard "Current Next Actions" immediately points at the new gate.

`complete-experiment` records linked artifact ids both in the finding `linked_artifacts` field and in the finding `evidence` list, so a finding remains traceable when read without the full experiment node.

Use `complete-experiments` for sweeps or repeated backend/ablation runs:

```sh
research-cockpit complete-experiments --print-schema
research-cockpit complete-experiments --root research_cockpit --file findings.yaml --dry-run --json --show-diff
research-cockpit complete-experiments --root research_cockpit --file findings.yaml --json --compact
research-cockpit complete-experiments --root research_cockpit --file findings.yaml --no-build
```

`findings.yaml` v1:

```yaml
defaults:
  confidence: medium
  outcome: mixed
  artifact_ids:
    - artifact_shared
experiments:
  - id: experiment_a
    finding: First finding.
    result_summary: First summary.
    evidence:
      path: artifacts/experiment_a/run_a
      links:
        metrics: artifacts/experiment_a/run_a/metrics.json
  - id: experiment_b
    finding: Second finding.
    confidence: strong
    outcome: positive
```

The batch command validates every experiment, existing artifact reference, and inline evidence artifact before writing any YAML. It writes one interaction event and rebuilds once by default.

Revise an existing finding without patching YAML:

```sh
research-cockpit update-finding --root research_cockpit --experiment experiment_x --finding-id experiment_x_finding_001 --statement "Updated finding" --artifact-id artifact_x --dry-run --json --show-diff
research-cockpit update-finding --root research_cockpit --experiment experiment_x --finding-id experiment_x_finding_001 --statement "Updated finding" --artifact-id artifact_x --json --compact
research-cockpit update-finding --root research_cockpit --experiment experiment_x --finding-id experiment_x_finding_001 --statement "Updated finding" --artifact-id artifact_x --no-build
```

`update-finding` preserves `created_at`, writes `updated_at`, and can append or replace metrics/artifacts with `--replace-metrics` / `--replace-artifacts`.

Successful finding and completion writes append compact events to `graph/interaction_log.yaml`.

Treat structured `findings` as truth. Use Markdown notes only for human-readable details that do not need to drive dashboards or decisions.

After findings change, rebuild decision evidence when a decision depends on them:

```sh
research-cockpit update-decision-evidence --root research_cockpit --id decision_x
```

When recording several related updates, run mutating commands sequentially. Do not parallelize writes against the same data root; mutating commands share `graph/interaction_log.yaml`, use a mutation lock, and fail without writing if target truth-source files changed after command planning. On conflict, reread context and retry the stale command. Use `--no-build` on each supported command and run one final:

```sh
research-cockpit validate --root research_cockpit --json
research-cockpit build --root research_cockpit
```

For agent-readable success summaries, add `--compact` to `--json` on supported high-level mutation commands. Check `commands --json` for `supports_compact`; `complete-experiment` and `complete-experiments` both support it. The compact payload omits bulky `before`/`after` blocks. If you also pass `--show-diff`, the full diff is included and `diff_line_count` tells the agent how large it is.
