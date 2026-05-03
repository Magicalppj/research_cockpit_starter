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
```

Use `context` as the default handoff for a known option or experiment. Use `option-workstream-context` when you specifically need the recursive option subtree report.

Report a workstream:

```sh
research-cockpit report-option-workstream --root research_cockpit --option option_x --agent agent_id --recommend continue --summary "..." --dry-run --json
research-cockpit report-option-workstream --root research_cockpit --option option_x --agent agent_id --recommend continue --summary "..."
```

Finalize a workstream only when the close-out status changes are explicit:

```sh
research-cockpit finalize-workstream --root research_cockpit --option option_x --status accepted --problem-status resolved --summary-file summary.md --summary-target report --artifact artifact_x --sync-focus --report --dry-run --json --show-diff
research-cockpit finalize-workstream --root research_cockpit --option option_x --status accepted --problem-status resolved --summary-file summary.md --summary-target report --artifact artifact_x --sync-focus --report --no-build
```

`finalize-workstream` does not create artifacts, accept decisions, pause old branches, delete nodes, or invent next actions. `--summary-file` writes only to the workstream report by default; use `--summary-target option|problem|all` when you explicitly want node summaries replaced.

## Artifacts

Use artifact commands for result folders, review bundles, metrics directories, and other evidence objects that need their own status or links:

```sh
research-cockpit create-artifact --print-schema
research-cockpit create-artifact --root research_cockpit --file artifact.yaml --dry-run --json --show-diff
research-cockpit create-artifact --root research_cockpit --file artifact.yaml --no-build
research-cockpit create-artifact --root research_cockpit --id artifact_x --title "Result bundle" --status done --path outputs/run_x --link metrics=outputs/run_x/metrics.json --link-to experiment_x --dry-run --json --show-diff
research-cockpit create-artifact --root research_cockpit --id artifact_x --title "Result bundle" --status done --path outputs/run_x --link metrics=outputs/run_x/metrics.json --link-to experiment_x --no-build
research-cockpit link-artifact --root research_cockpit --artifact artifact_x --to option_x --link review=notes/review.md --no-build
```

Use `--file` when an artifact has several `links` or `link_to` targets; it is shorter and easier to review than a long repeated-flag command.

`create-artifact` and `link-artifact` update artifact `path`/`links` and reverse `linked_artifacts` references. They do not require local resource paths to exist, but JSON output includes resource existence rows where possible.

## Findings

Record experiment findings through `research-cockpit record-finding`:

```sh
research-cockpit record-finding --root research_cockpit --experiment experiment_x --statement "..." --confidence medium --outcome positive --summary "..." --artifact-id artifact_x
```

`--artifact-id` must be an existing artifact node id, not a file path. The older `--artifact` flag remains as a compatibility alias.

Use `complete-experiment` when you want the conservative "record conclusion and mark done" workflow in one command:

```sh
research-cockpit complete-experiment --root research_cockpit --id experiment_x --finding "..." --confidence medium --outcome mixed --result-summary "..." --next-action "Review follow-up" --no-build
```

`complete-experiment` appends a structured finding, sets the experiment status to `done`, optionally updates `result_summary`, and appends de-duplicated experiment-local `next_actions`. It does not change focus, option status, problem status, or `current_best_option`.

Use `complete-experiments` for sweeps or repeated backend/ablation runs:

```sh
research-cockpit complete-experiments --print-schema
research-cockpit complete-experiments --root research_cockpit --file findings.yaml --dry-run --json --show-diff
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
research-cockpit update-finding --root research_cockpit --experiment experiment_x --finding-id experiment_x_finding_001 --statement "Updated finding" --artifact-id artifact_x --no-build
```

`update-finding` preserves `created_at`, writes `updated_at`, and can append or replace metrics/artifacts with `--replace-metrics` / `--replace-artifacts`.

Successful finding and completion writes append compact events to `graph/interaction_log.yaml`.

Treat structured `findings` as truth. Use Markdown notes only for human-readable details that do not need to drive dashboards or decisions.

After findings change, rebuild decision evidence when a decision depends on them:

```sh
research-cockpit update-decision-evidence --root research_cockpit --id decision_x
```

When recording several related updates, use `--no-build` on each supported command and run one final:

```sh
research-cockpit validate --root research_cockpit --json
research-cockpit build --root research_cockpit
```
