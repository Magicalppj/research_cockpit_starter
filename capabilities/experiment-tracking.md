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
```

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

## Artifacts

Use artifact commands for result folders, review bundles, metrics directories, and other evidence objects that need their own status or links:

```sh
research-cockpit create-artifact --print-schema
research-cockpit create-artifact --root research_cockpit --file artifact.yaml --dry-run --json --show-diff
research-cockpit create-artifact --root research_cockpit --file artifact.yaml --json --compact
research-cockpit create-artifact --root research_cockpit --file artifact.yaml --no-build
research-cockpit create-artifact --root research_cockpit --id artifact_x --title "Result bundle" --status done --path outputs/run_x --link metrics=outputs/run_x/metrics.json --link-to experiment_x --dry-run --json --show-diff
research-cockpit create-artifact --root research_cockpit --id artifact_x --title "Result bundle" --status done --path outputs/run_x --link metrics=outputs/run_x/metrics.json --link-to experiment_x --no-build
research-cockpit link-artifact --root research_cockpit --artifact artifact_x --to option_x --link review=notes/review.md --no-build
```

Use `--file` when an artifact has several `links` or `link_to` targets; it is shorter and easier to review than a long repeated-flag command.

`create-artifact` and `link-artifact` update artifact `path`/`links` and reverse `linked_artifacts` references. They do not require local resource paths to exist. YAML stores paths exactly as provided; JSON resource rows include `resolved_target`, `resolution_base`, `resolution_attempts`, and `exists`. Relative paths are checked against the root parent, then the data root, then cwd.

## Findings

Record experiment findings through `research-cockpit record-finding`:

```sh
research-cockpit record-finding --root research_cockpit --experiment experiment_x --statement "..." --confidence medium --outcome positive --summary "..." --artifact-id artifact_x
```

`--artifact-id` must be an existing artifact node id, not a file path. The older `--artifact` flag remains as a compatibility alias.

Use `complete-experiment` when you want the conservative "record conclusion and mark done" workflow in one command:

```sh
research-cockpit complete-experiment --root research_cockpit --id experiment_x --finding "..." --confidence medium --outcome mixed --result-summary "..." --next-action "Review follow-up" --no-build
research-cockpit complete-experiment --root research_cockpit --id experiment_x --finding "..." --confidence medium --json --compact
```

`complete-experiment` appends a structured finding, sets the experiment status to `done`, optionally updates `result_summary`, and appends de-duplicated experiment-local `next_actions`. It does not change focus, option status, problem status, or `current_best_option`.

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
  - id: experiment_b
    finding: Second finding.
    confidence: strong
    outcome: positive
```

The batch command validates every experiment and artifact before writing any YAML. It writes one interaction event and rebuilds once by default.

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
