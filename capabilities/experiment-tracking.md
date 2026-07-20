# Experiment Tracking

Use this capability for advanced gates, findings, artifacts, retention, option workstreams, and compatibility paths. For an ordinary assigned experiment, read `experiment-cycle.md` instead; it contains the bounded three-mutation lifecycle.

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
research-cockpit start-agent-session --root D:/main_repo/research_cockpit --option option_x --label cache_probe --objective "Run experiments" --branch agent/option_x-cache_probe --worktree ../worktrees/cache_probe --base main --create-worktree --dry-run --json --show-diff
research-cockpit start-agent-session --root D:/main_repo/research_cockpit --option option_x --label cache_probe --objective "Run experiments" --branch agent/option_x-cache_probe --worktree ../worktrees/cache_probe --base main --create-worktree --no-build
research-cockpit work open --root D:/main_repo/research_cockpit --assignment <assignment_id> --compact --json
```

Dry-run generated ids are preview-only; pass explicit `--agent` and `--assignment` / `--assignment-id` on the execute command when the same ids must be reused. Relative `--worktree` values resolve against the canonical repository root (`--root` parent), matching `git -C <repo> worktree add`. Do not store local absolute worktree paths in YAML. `start-agent-session` writes `session_id`, `owner`, `status`, `objective`, `git_branch`, `worktree_label`, `report_to_problem`, `started_at`, and `updated_at`; absolute paths appear only in JSON `handoff`/`launch_env`. `--create-worktree` expects a new branch/worktree path; for an already-created worktree, rerun without `--create-worktree`.

For assigned downstream agents, the Work Packet is the primary task context. Treat global `current_state`, `coordinator_state`, and `focus.current_focus_node` as coordinator metadata that may point to another agent's branch. Do not leave the assignment scope unless the coordinator creates or updates an assignment.

Read workstream context:

```sh
research-cockpit context --root research_cockpit --id option_x --view execution --compact --json
research-cockpit assignment-view --root research_cockpit --json
research-cockpit option-workstream-context --root research_cockpit --option option_x --json
research-cockpit option-workstream-context --root research_cockpit --id option_x --compact --json
```

Use `context` as the default handoff for a known option or experiment. Use compact `option-workstream-context` when you specifically need the recursive option subtree report, short experiment summaries, and evidence counts; `--id` is the preferred target flag and `--option` remains compatible for full output.
The compact payload includes `experiment_summaries` with each experiment id, title, status, result summary, success criteria count, first success criterion, metric count, finding count, and linked artifact count. Use full context or `node-context` only when the exact complete field text matters.
Use `assignment-view` when assigning parallel agents. It lists high-priority queued/running experiment nodes with `owner`, `ready_for_agent`, `depends_on`, `blocked_by`, key artifacts, and first `next_action`. Keep `priority` as coarse urgency and use `order` or `rank` for stable dispatch order.

## Worker Experiment Cycle

Mutate one canonical root sequentially and pass `--assignment <assignment_id>` for worker writes. A normal experiment needs only these state mutations:

```sh
research-cockpit work start --root research_cockpit --assignment <assignment_id> --file start.yaml --json --compact
research-cockpit work close --root research_cockpit --assignment <assignment_id> --file closeout.yaml --json --compact
```

The start receipt supplies the runtime-generated run id. `work_close_v1` can finish the run, record gates/finding/result, stage final `evidence_inputs`, create at most one same-scope `next_experiment`, and move or complete the assignment atomically. Use standalone `ingest-artifact` only when incremental evidence must be durable before close, then reference `artifact_record.existing_record_id`. Do not repeat completion, follow-up, or cursor commands.

Both facade mutations validate candidate state and reject stale writes. Trust `verification.status: internally_verified` with `additional_verification_required: false`; do not add validate/context. Compatibility routes are advanced recovery paths, not a mandatory preview or default worker chain.

For a long-running job, call `update-run` only when status or progress metadata changes. Use the following fallback only after a manual truth-source edit or when compact output requires extra verification:

```sh
research-cockpit validate --root research_cockpit --changed-node <node_id> --json
research-cockpit context --root research_cockpit --id <node_id> --view execution --compact --json
```

If changed validation reports `fallback.used_full_validation: true`, follow its `fallback.recommended_commands` and retry. Use `smoke --scope changed --id <node_id>` only when the task needs the integrated one-node workflow. At coordinator merge, release, or research-stage closeout, run one milestone orchestrator without preceding standalone full gates:

```sh
research-cockpit coord handoff --root research_cockpit --file handoff.yaml --json --compact --progress
```

Use `research-cockpit commands --json --compact --name <command>` only when a command or flag is unknown; do not rediscover the broad catalog every turn. Specialized compatibility paths remain available: `complete-experiment` for an experiment with no run record, `complete-experiments` for a true multi-experiment batch, `create-followup-experiment` for a standalone follow-up outside run closeout, and `set-cursor` for cursor-only movement. Standalone gate commands remain useful for blocking preflight or recovery before closeout.

Do not parallelize mutations against the same root. On a conflict, reread bounded context and retry the stale command.

Report a workstream only when an upstream summary is required. The normal write is one command; add dry-run only for a preview:

```sh
research-cockpit report-option-workstream --root research_cockpit --option option_x --agent agent_id --recommend continue --summary "..." --json --no-build
```
Finalize a workstream only when close-out status changes are explicit:

```sh
research-cockpit finalize-workstream --root research_cockpit --file finalize.yaml --json --compact --no-build
```

Inspect `finalize-workstream --print-schema` only when the file contract is unknown; add `--dry-run --show-diff` only when terminal lifecycle changes need a preview. Use `--file` to avoid long close-out commands. The file supports `option`, `status`, `problem_status`, `stage_status`, `summary_file`, `summary_target`, `artifacts`, `sync_focus`, `report`, `agent`, and `locale`; CLI flags override file values. A relative `summary_file` in the file resolves against the finalize file directory, then the data root, then cwd, and JSON output reports the resolved path. `finalize-workstream` does not create artifacts, accept decisions, pause old branches, delete nodes, or invent next actions. `--summary-file` writes only to the workstream report by default; use `--summary-target option|problem|all` when you explicitly want node summaries replaced.

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
research-cockpit ingest-artifact --root <data-root> --assignment <assignment_id> --node experiment_x --from <worktree-run-dir> --run-id run_x --agent agent_x --link metrics=metrics.json --json --compact --no-build
```

Successful non-dry-run ingest is internally verified; do not append validate/context or list the record unless another consumer explicitly needs that output.

For experiment targets, `ingest-artifact` defaults to record mode: it copies `--from` to `artifacts/<node_id>/<run_id>/`, writes `_research_cockpit_ingest.json`, records metadata under `artifact_records/<experiment_id>.yaml`, and links the record through `linked_artifact_records`. `--record-only` remains an explicit compatibility flag. Repeated `--link key=relative/path` values must stay inside the source directory and are rewritten to canonical paths. The command does not record findings, accept decisions, or set baselines.

Do not promote ordinary run output. Immediate graph creation requires `ingest-artifact --promote --promotion-reason "..."`; for an existing record, promotion also requires a durable reason:

```sh
research-cockpit promote-artifact-record --root <data-root> --id artifact_experiment_x_run_x --artifact-id artifact_experiment_x_run_x_promoted --link-to experiment_x --promotion-reason "Durable evidence for decision or baseline" --json --compact
```

Use inline `--evidence-path` only for files that already live at a stable path outside the disposable worktree.

## Run Records

Use run/job records for concrete executions of an experiment: launcher command, tmux session, pid, logs, outputs, progress file, and stop command. These records live under `research_cockpit/runs/*.yaml` and reference one experiment node.

Launcher-produced output directories should follow `docs/launcher-output-conventions.md`: `run_record.txt` for the human handoff, `progress.json` for heartbeat state, `gate_result.json` for machine-readable gates, and `artifact_manifest.json` for evidence links. Starter templates live in `templates/launcher/`. The convention works for shell, Python, tmux, scheduler, and manual flows because all stable records are still created through `research-cockpit` commands.

```sh
research-cockpit work start --root research_cockpit --assignment <assignment_id> --file start.yaml --json --compact
# Only when status or operational metadata changed:
research-cockpit update-run --root research_cockpit --assignment <assignment_id> --id run_x --status running --progress-file artifacts/experiment_x/run_x/progress.json --no-build
research-cockpit work close --root research_cockpit --assignment <assignment_id> --file closeout.yaml --json --compact
```

`work start` atomically renews the lease, creates a runtime-named run, and starts the experiment; put launcher metadata under `run` in `work_start_v1`. `work close` is the terminal assigned-work operation and accepts final evidence staging. `complete-run --file` remains an advanced compatibility route for legacy state without an active lease. A successful facade closeout is internally verified, so do not add validate/context.

Use `run-context` only for full operational details of a known long-running job and `list-runs` only for an explicit run inventory. Bounded bootstrap/context summaries already expose short active, failed, stale, and recently completed run state.

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
research-cockpit ingest-gate-result --root research_cockpit --id gate_x --file artifacts/experiment_x/run_x/gate_result.json --run run_x --json --compact --no-build
```

Both commands create a `gate_results/<gate_id>.yaml` metadata record. `record-gate-result` also writes the JSON payload, while `ingest-gate-result` only links an existing file. Add `--artifact <artifact_id>` only when the gate payload is already represented by a promoted graph artifact node. `run-context --json` and experiment `node-context --json` expose compact gate details including latest gate state, blocking gates, warnings, and linked artifact id when present. Option workstream summaries, bootstrap, and dashboard context expose aggregate gate counts and gate ids so agents can decide when to read the narrower run or node context.

## Run Retention And Active Resources

For long-running or disk-heavy experiments, record enough run metadata to support later cleanup decisions. Existing run fields such as `pid`, `tmux_session`, `log_root`, `output_root`, `progress_file`, and `config_file` are operational hints; they do not by themselves prove a path is safe to remove.

For assigned work, put initial resource declarations under `work_start_v1.run`, update them only when they change, and include terminal values in `work close`. `create-run` and `complete-run` are legacy/unleased recovery routes. Launcher output and artifact manifests may duplicate the details for human review, but they are not the primary write path.

```yaml
resources:
  gpus:
    - 0
  ports:
    - 8000
  process_ids:
    - 123456
  worktree: /repo/.worktrees/example
  output_roots:
    - /repo/outputs/example_run
  cache_roots:
    - /repo/data/example/.precomputed
  dataset_roots:
    - /repo/data/example
  model_paths:
    - /repo/outputs/example_run/checkpoint-final
```

At run closeout, capture retention intent for large outputs:

```yaml
output_retention:
  keep_checkpoints:
    - final
  keep_optimizer_state: false
  resume_planned: false
  raw_outputs_disposable: true
  portable_bundle_path: outputs/example/listening_bundle.tar.gz
  cleanup_after_completion: true
  cleanup_notes: "Metrics and bundle preserved; intermediate generations are reproducible."
```

For assigned work, put retention intent under `work_start_v1.run`, use `update-run` only when it changes, and preserve the terminal value in `work close`. File input remains preferable to inline JSON for compatibility commands because shell quoting differs across platforms. Retention metadata is advisory unless a project opts into stricter lint rules; missing metadata is a maintenance warning, not a reason to bypass normal closeout. For the full cleanup and branch/worktree policy, read `capabilities/maintenance.md`.

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

## Standalone Findings (Compatibility)

Record experiment findings through `research-cockpit record-finding`:

```sh
research-cockpit record-finding --root research_cockpit --experiment experiment_x --statement "..." --confidence medium --outcome positive --summary "..." --evidence-path artifacts/experiment_x/run_x --evidence-link metrics=artifacts/experiment_x/run_x/metrics.json
research-cockpit record-finding --root research_cockpit --experiment experiment_x --statement "..." --confidence medium --artifact-id artifact_x
```

Use `--evidence-path` and repeated `--evidence-link key=value` when the result directory, image, report, or metrics JSON should be recorded with the finding. The command creates an `artifact_<finding_id>` artifact, links it from the finding, and mirrors it on the experiment top-level `linked_artifacts` field so context, resource tables, and dashboards can show where the conclusion came from. Use `create-artifact` plus `--artifact-id` when you need custom artifact metadata.

Do not pass worktree-local output paths to `--evidence-path` for long-lived evidence. For final run output, use `work_close_v1.evidence_inputs`; for earlier durable ingest, reference the returned record with `artifact_record.existing_record_id` in `work close`. A standalone finding may use `--artifact-id` only after explicit promotion created a graph artifact node.

`--artifact-id` must be an existing artifact node id, not a file path. A finding can be recorded without any artifact; the command succeeds but JSON output includes `warnings: ["missing_evidence_artifact"]`.

Use `complete-experiment` only for a standalone experiment that has no run record to close:

```sh
research-cockpit complete-experiment --root research_cockpit --id experiment_x --finding "..." --confidence medium --outcome mixed --result-summary "..." --artifact-id artifact_experiment_x_run_x --no-build
research-cockpit complete-experiment --root research_cockpit --id experiment_x --finding "..." --confidence medium --evidence-path artifacts/experiment_x/run_x --evidence-link metrics=artifacts/experiment_x/run_x/metrics.json --json --compact
```

`complete-experiment` is a compatibility operation that appends a finding and updates one experiment without closing an assignment run. Prefer `work close` whenever run, evidence, result, follow-up, cursor, and lease changes belong to one assigned closeout.

In this compatibility path, a terminal assignment cursor still requires a separate `set-cursor`; the structured run closeout avoids that extra mutation. Coordinator/global focus remains separate:

```sh
research-cockpit complete-experiment --root research_cockpit --assignment <assignment_id> --id experiment_x --finding "..." --confidence medium --artifact-id artifact_experiment_x_run_x --no-build
research-cockpit set-cursor --root research_cockpit --assignment <assignment_id> --node option_x --no-build
```

`close-current-experiment --next-focus` is a coordinator/legacy shortcut. `--next-focus` must point to a non-terminal node and cannot be the experiment being closed. When `--next-focus` is present, the command copies that node's node-local `next_actions` into coordinator/global next actions and compatibility `current_state.next_actions`. `--sync-agent` updates legacy per-agent focus only; assignment-scoped workers should use `set-cursor`.

Outside a run closeout, derive one standalone follow-up gate instead of hand-editing YAML:

```sh
research-cockpit create-followup-experiment --root research_cockpit --assignment <assignment_id> --from experiment_x --id experiment_x_followup --title "Follow-up gate" --priority high --next-action "Run follow-up gate" --json --compact
research-cockpit set-cursor --root research_cockpit --assignment <assignment_id> --node experiment_x_followup --no-build
```

The source experiment must be `done` or `running`. The new experiment is queued, records `derived_from`, reuses the source experiment's option parent unless `--parent` is supplied, and gets a default success criterion that references the source. `--next-action` writes the initial follow-up experiment `next_actions`. It does not move any assignment cursor; run `set-cursor --assignment <assignment_id> --node <followup_id>` when the worker should continue on that gate.
The `--set-focus` flag is coordinator/global only. Coordinator runs may use `--coordinator --set-focus` to also write coordinator/global next actions and compatibility `current_state.next_actions`; assignment-scoped workers should pass `--assignment <assignment_id>` and move their worker cursor with `set-cursor`. Combining `--assignment` with `--set-focus` is rejected.
If a `done` experiment still has real follow-up work, move that work into a derived queued experiment instead of leaving live `next_actions` on the completed node.
Use this command only for a single follow-up gate. If the result creates a new worktree branch, several follow-up experiments, or a new question, create a child workstream instead: use `create-workstream` with `problem.parent` set to the inherited option id and `derived_from` pointing back to the source experiment. That keeps the graph as `option -> problem -> option -> experiment` instead of flattening all later experiments under one option.

Use `migrate-terminal-next-actions` when an older terminal node already carries live `next_actions`:

```sh
research-cockpit migrate-terminal-next-actions --root research_cockpit --assignment <assignment_id> --id experiment_x --followup-id experiment_x_followup --title "Follow-up gate" --dry-run --json --show-diff
research-cockpit migrate-terminal-next-actions --root research_cockpit --assignment <assignment_id> --id experiment_x --followup-id experiment_x_followup --title "Follow-up gate" --no-build
research-cockpit set-cursor --root research_cockpit --assignment <assignment_id> --node experiment_x_followup --no-build
```

The command creates one queued follow-up experiment only for a `done` experiment with exactly one node-local next action, then clears the source node's `next_actions`. For multiple actions, non-experiment terminal nodes, or larger branches, the JSON output points to `create-workstream` instead of mutating.

`complete-experiment` records linked artifact ids both in the finding `linked_artifacts` field and in the finding `evidence` list, so a finding remains traceable when read without the full experiment node.

Use `complete-experiments` only for a true multi-experiment sweep or repeated backend/ablation batch. If the file format is unknown, inspect `complete-experiments --print-schema` once; the normal write is one command:

```sh
research-cockpit complete-experiments --root research_cockpit --file findings.yaml --json --compact --no-build
```

Use `--dry-run --show-diff` only when unfamiliar batch input needs a preview.

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

Revise an existing standalone finding without patching YAML:

```sh
research-cockpit update-finding --root research_cockpit --experiment experiment_x --finding-id experiment_x_finding_001 --statement "Updated finding" --artifact-id artifact_x --json --compact --no-build
```

Use dry-run only when a diff preview is required. `update-finding` preserves `created_at`, writes `updated_at`, and can append or replace metrics/artifacts with `--replace-metrics` / `--replace-artifacts`.

Successful finding and completion writes append compact JSONL events to the active `graph/interaction_events/` backend; migrated roots retain `graph/interaction_log.yaml` only as the legacy prefix.

Treat structured `findings` as truth. Use Markdown notes only for human-readable details that do not need to drive dashboards or decisions.

After findings change, rebuild decision evidence when a decision depends on them:

```sh
research-cockpit update-decision-evidence --root research_cockpit --id decision_x
```

Run related mutations sequentially with `--no-build`. On conflict, reread bounded context and retry. Accept `verified: true` with `additional_verification_required: false` without another check; use the following changed-scope fallback only when the result requests it:

```sh
research-cockpit validate --root research_cockpit --changed-node experiment_x --json
research-cockpit context --root research_cockpit --id experiment_x --view execution --compact --json
```

Run standalone full validate/build/smoke only for diagnosis; milestone verification uses one coordinator `coord handoff` invocation.

For agent-readable success summaries, set `experiment.result_summary` in `run_closeout_v1`. Keep detailed evidence in the finding and artifact record instead of issuing another summary mutation.
