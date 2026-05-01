# Experiment Tracking

Use this capability for experiments, findings, and option workstreams.

## Option Workstreams

Create a new problem/option/experiment branch from a file when planning a new workstream:

```sh
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
research-cockpit option-workstream-context --root research_cockpit --option option_x --json
```

Report a workstream:

```sh
research-cockpit report-option-workstream --root research_cockpit --option option_x --agent agent_id --recommend continue --summary "..." --dry-run --json
research-cockpit report-option-workstream --root research_cockpit --option option_x --agent agent_id --recommend continue --summary "..."
```

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
