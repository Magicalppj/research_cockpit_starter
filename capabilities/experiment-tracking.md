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
research-cockpit assignment-view --root research_cockpit --json
research-cockpit option-workstream-context --root research_cockpit --option option_x --json
research-cockpit option-workstream-context --root research_cockpit --id option_x --compact --json
```

Use `context` as the default handoff for a known option or experiment. Use compact `option-workstream-context` when you specifically need the recursive option subtree report, short experiment summaries, and evidence counts; `--id` is the preferred target flag and `--option` remains compatible for full output.
The compact payload includes `experiment_summaries` with each experiment id, title, status, result summary, success criteria count, first success criterion, metric count, finding count, and linked artifact count. Use full context or `node-context` only when the exact complete field text matters.
Use `assignment-view` when assigning parallel agents. It lists high-priority queued/running experiment nodes with `owner`, `ready_for_agent`, `depends_on`, `blocked_by`, key artifacts, and first `next_action`. Keep `priority` as coarse urgency and use `order` or `rank` for stable dispatch order.

## Multi-Agent Batch Updates

In multi-agent workflows, each agent should mutate the canonical `research_cockpit/` root sequentially with `--no-build` on supported write commands. A coordinator or final handoff step should then run one validation/build/smoke pass:

```sh
research-cockpit validate --root research_cockpit --json
research-cockpit build --root research_cockpit
research-cockpit smoke --root research_cockpit --json
```

Use `research-cockpit commands --json --compact --name <command>` to check `supports_no_build`, `can_batch`, and `batch_policy.mode` before choosing a write path. Prefer file-based batch commands such as `apply-graph-plan`, `create-workstream`, and `complete-experiments` when several changes share one intent; otherwise run smaller commands one after another.

An optional `research-cockpit build --root research_cockpit --watch --interval 5 --json` process can keep generated dashboards fresh during a batch, but it only runs dashboard builds. It does not replace the final `validate` and `smoke` checks.

Consecutive finding updates:

```sh
research-cockpit record-finding --root research_cockpit --experiment experiment_a --statement "..." --confidence medium --artifact-id artifact_a --no-build
research-cockpit complete-experiment --root research_cockpit --id experiment_b --finding "..." --confidence medium --artifact-id artifact_b --no-build
research-cockpit complete-experiments --root research_cockpit --file findings.yaml --no-build
```

Artifact capture and linking:

```sh
research-cockpit ingest-artifact --root research_cockpit --node experiment_x --from ../worktrees/agent_x/.agent_runs/run_x --run-id run_x --agent agent_x --link metrics=metrics.json --no-build
research-cockpit create-artifact --root research_cockpit --id artifact_x --title "Review bundle" --path artifacts/experiment_x/run_x --link-to experiment_x --no-build
research-cockpit link-artifact --root research_cockpit --artifact artifact_x --to option_x --no-build
```

Run/job status updates:

```sh
research-cockpit create-run --root research_cockpit --id run_x --experiment experiment_x --status running --progress-file artifacts/experiment_x/run_x/progress.json --no-build
research-cockpit update-run --root research_cockpit --id run_x --status running --progress-file artifacts/experiment_x/run_x/progress.json --no-build
research-cockpit complete-run --root research_cockpit --id run_x --status completed --no-build
```

Next action updates:

```sh
research-cockpit update-node-fields --root research_cockpit --id experiment_x --clear-next-actions --next-action "Review metrics" --next-action "Draft decision" --no-build
research-cockpit sync-focus-actions --root research_cockpit --from-node experiment_x --no-build
research-cockpit update-suggestion-state --root research_cockpit --id sg_x --state completed --reason "Recorded in experiment_x" --no-build
```

Do not parallelize mutating commands against the same root. If a mutation conflict is reported, reread compact context and retry the stale command.

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

If `finalize-workstream` would mark an option `accepted`/`rejected`/`paused`/`parked` or mark a problem `resolved`/`parked`, active descendants must already be closed. When the command reports `terminal_parent_has_active_descendants`, preview the cleanup, then explicitly close descendants before retrying the finalization:

```sh
research-cockpit close-branch --root research_cockpit --id option_x --downstream-status parked --dry-run --json --show-diff
research-cockpit close-branch --root research_cockpit --id option_x --downstream-status parked --include-experiments --no-build
research-cockpit finalize-workstream --root research_cockpit --file finalize.yaml --no-build
```

With `--include-experiments`, active experiments move to `cancelled`, not `parked`. Do not use it until planned, queued, or running jobs under that branch are intentionally stopped or abandoned.

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

## Run Records

Use run/job records for concrete executions of an experiment: launcher command, tmux session, pid, logs, outputs, progress file, and stop command. These records live under `research_cockpit/runs/*.yaml` and reference one experiment node.

Launcher-produced output directories should follow `docs/launcher-output-conventions.md`: `run_record.txt` for the human handoff, `progress.json` for heartbeat state, `gate_result.json` for machine-readable gates, and `artifact_manifest.json` for evidence links. Starter templates live in `templates/launcher/`. The convention works for shell, Python, tmux, scheduler, and manual flows because all stable records are still created through `research-cockpit` commands.

```sh
research-cockpit create-run --root research_cockpit --id run_x --experiment experiment_x --status running --launcher tmux --command "python train.py" --tmux-session train_x --progress-file artifacts/experiment_x/run_x/progress.json --monitor-command "tail -f artifacts/experiment_x/run_x/logs/run.log" --stop-command "tmux kill-session -t train_x" --no-build
research-cockpit run-context --root research_cockpit --id run_x --compact --json
research-cockpit update-run --root research_cockpit --id run_x --status running --progress-file artifacts/experiment_x/run_x/progress.json --no-build
research-cockpit complete-run --root research_cockpit --id run_x --status completed --finished-at 2026-05-27T02:00:00Z --no-build
research-cockpit list-runs --root research_cockpit --experiment experiment_x --json --compact
```

Prefer `--no-build` for frequent status updates in multi-agent workflows, then run `research-cockpit build --root research_cockpit` after batching. Use `run-context` before monitoring or stopping a known run. A completed run is not a finding; record conclusions with `complete-experiment` and preserve output directories with `ingest-artifact`.

`bootstrap`, `node-context` for experiment nodes, and `option-workstream-context --compact --json` include short run summaries so agents can see active, failed, stale, and recently completed executions without reading every run file. Use `run-context` for full operational details.

Standard `progress.json` heartbeat files should be JSON objects with this shape:

```json
{
  "status": "running",
  "completed_steps": 12,
  "total_steps": 64,
  "last_update": "2026-05-26T16:30:00Z",
  "current_stage": "synthesis",
  "latest_artifact": "artifacts/experiment_x/run_x/partial.json",
  "warnings": []
}
```

`total_steps` may be omitted or null when the total is unknown. `last_update` should be an ISO-8601 timestamp; active heartbeats are considered stale after 60 minutes without an update. `run-context --json` and run summaries read a run's relative `progress_file` and expose normalized progress, percent complete when possible, heartbeat warnings, and schema warnings. Missing or malformed progress files produce warnings instead of blocking context reads.

Standard `gate_result.json` files should use this machine-readable shape:

```json
{
  "gate_type": "dataset_check",
  "passed": true,
  "expected": {},
  "observed": {},
  "fatal_failures": {},
  "warnings": [],
  "next_allowed_action": "precompute"
}
```

`gate_type` is a required string, `passed` is a required boolean, and `expected`, `observed`, and `fatal_failures` are JSON objects. Warning-only gates report `blocks_next_action: false`. A failed gate, malformed gate file, or non-empty `fatal_failures` reports `blocks_next_action: true` so later ingest/context workflows can block or override the next action explicitly. Gate files may also include `experiment_id` and `run_id` so later ingest/context commands can link them back to the execution that produced them.

Use `gate_type: preflight` for long-run resource checks. Put resource fields in a `preflight` object or at the gate file top level: `disk_available_gb`, `estimated_required_gb`, `gpu_ids`, `port`, `port_available`, `cache_dir`, `cache_dir_exists`, `cache_available_gb`, and `conflicting_processes`. Failed preflight gates expose `blocked_actions: ["full_run"]` in context so agents do not recommend expensive full runs before the resource issue is cleared.

```json
{
  "gate_type": "preflight",
  "passed": false,
  "preflight": {
    "disk_available_gb": 120,
    "estimated_required_gb": 800,
    "gpu_ids": [0, 1],
    "port_available": true,
    "cache_dir": "cache/precompute",
    "cache_dir_exists": true,
    "conflicting_processes": ["python train.py"]
  },
  "fatal_failures": {
    "disk": "insufficient"
  },
  "next_allowed_action": "full_run"
}
```

Use `record-gate-result` when Research Cockpit should write the standard gate file:

```sh
research-cockpit record-gate-result --root research_cockpit --id gate_x --experiment experiment_x --run run_x --type smoke_check --passed false --fatal-json '{"exit_code":1}' --next-allowed-action inspect_logs --no-build
research-cockpit record-gate-result --root research_cockpit --id preflight_x --experiment experiment_x --run run_x --type preflight --passed false --preflight-json '{"disk_available_gb":120,"estimated_required_gb":800,"gpu_ids":[0,1],"port_available":true,"cache_dir":"cache/precompute","cache_dir_exists":true,"conflicting_processes":["python train.py"]}' --fatal-json '{"disk":"insufficient"}' --next-allowed-action full_run --no-build
```

Use `ingest-gate-result` when a launcher or artifact bundle already produced `gate_result.json`:

```sh
research-cockpit ingest-gate-result --root research_cockpit --id gate_x --file artifacts/experiment_x/run_x/gate_result.json --run run_x --artifact artifact_x --no-build
```

Both commands create a `gate_results/<gate_id>.yaml` metadata record. `record-gate-result` also writes the JSON payload, while `ingest-gate-result` only links an existing file. `run-context --json` and experiment `node-context --json` expose compact gate details including latest gate state, blocking gates, warnings, and linked artifact id when present. Option workstream summaries, bootstrap, and dashboard context expose aggregate gate counts and gate ids so agents can decide when to read the narrower run or node context.

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
research-cockpit complete-experiment --root research_cockpit --id experiment_x --finding "..." --confidence medium --outcome mixed --result-summary "..." --artifact-id artifact_experiment_x_run_x --no-build
research-cockpit complete-experiment --root research_cockpit --id experiment_x --finding "..." --confidence medium --evidence-path artifacts/experiment_x/run_x --evidence-link metrics=artifacts/experiment_x/run_x/metrics.json --json --compact
```

`complete-experiment` appends a structured finding, sets the experiment status to `done`, optionally updates `result_summary`, creates inline evidence artifacts when evidence fields are present, and clears experiment-local `next_actions` so completed nodes do not carry live work. It does not change focus, option status, problem status, or `current_best_option`. Use `create-followup-experiment` only for a single small follow-up gate; when a conclusion opens a multi-step branch, use `create-workstream` with `problem.parent` and `problem.derived_from` so the node graph stays hierarchical.

If the completed experiment is still the global `current_focus_node` or a per-agent focus node, JSON output includes focus-stale warnings plus recommended `set-focus` / `set-agent-focus` commands. Use explicit focus movement when closing a branch:

```sh
research-cockpit close-current-experiment --root research_cockpit --id experiment_x --finding "..." --confidence medium --next-focus option_x --sync-agent agent_x --json --compact
```

`--next-focus` must point to a non-terminal node and cannot be the experiment being closed.
When `--next-focus` is present, the command copies that node's node-local `next_actions` into global `current_state.next_actions`. `--sync-agent <agent_id>` moves one agent focus to the same node; `--sync-agent all` moves only agents currently focused on the experiment being closed.

For mixed or incomplete findings that need a follow-up gate, derive the next experiment instead of hand-editing YAML:

```sh
research-cockpit create-followup-experiment --root research_cockpit --from experiment_x --id experiment_x_followup --title "Follow-up gate" --priority high --next-action "Run follow-up gate" --set-focus --json --compact
```

The source experiment must be `done` or `running`. The new experiment is queued, records `derived_from`, reuses the source experiment's option parent unless `--parent` is supplied, and gets a default success criterion that references the source. `--next-action` writes the initial follow-up experiment `next_actions`. When combined with `--set-focus`, the same action is also written to `current_state.next_actions`, so dashboard "Current Next Actions" immediately points at the new gate.
If a `done` experiment still has real follow-up work, move that work into a derived queued experiment instead of leaving live `next_actions` on the completed node.
Use this command only for a single follow-up gate. If the result creates a new worktree branch, several follow-up experiments, or a new question, create a child workstream instead: use `create-workstream` with `problem.parent` set to the inherited option id and `derived_from` pointing back to the source experiment. That keeps the graph as `option -> problem -> option -> experiment` instead of flattening all later experiments under one option.

Use `migrate-terminal-next-actions` when an older terminal node already carries live `next_actions`:

```sh
research-cockpit migrate-terminal-next-actions --root research_cockpit --id experiment_x --followup-id experiment_x_followup --title "Follow-up gate" --dry-run --json --show-diff
research-cockpit migrate-terminal-next-actions --root research_cockpit --id experiment_x --followup-id experiment_x_followup --title "Follow-up gate" --no-build
```

The command creates one queued follow-up experiment only for a `done` experiment with exactly one node-local next action, then clears the source node's `next_actions`. For multiple actions, non-experiment terminal nodes, or larger branches, the JSON output points to `create-workstream` instead of mutating.

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
research-cockpit smoke --root research_cockpit --json
```

For agent-readable success summaries, add `--compact` to `--json` on supported high-level mutation commands. Check `commands --json` for `supports_compact`; `complete-experiment` and `complete-experiments` both support it. The compact payload omits bulky `before`/`after` blocks. If you also pass `--show-diff`, the full diff is included and `diff_line_count` tells the agent how large it is.
