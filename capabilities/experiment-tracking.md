# Experiment Tracking

Use this capability for experiments, findings, and option workstreams.

## Option Workstreams

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
research-cockpit record-finding --root research_cockpit --experiment experiment_x --statement "..." --confidence medium --outcome positive --summary "..."
```

Successful finding writes append a compact `record_finding` event to `graph/interaction_log.yaml`.

After findings change, rebuild decision evidence when a decision depends on them:

```sh
research-cockpit update-decision-evidence --root research_cockpit --id decision_x
```
