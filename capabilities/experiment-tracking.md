# Experiment Tracking

Use this capability for experiments, findings, and option workstreams.

## Option Workstreams

Claim an option branch:

```powershell
python .agent\skills\research-cockpit\scripts\claim_option.py --root research_cockpit --option option_x --agent agent_id --objective "..." --dry-run --json
python .agent\skills\research-cockpit\scripts\claim_option.py --root research_cockpit --option option_x --agent agent_id --objective "..."
```

Read workstream context:

```powershell
python .agent\skills\research-cockpit\scripts\option_workstream_context.py --root research_cockpit --option option_x --json
```

Report a workstream:

```powershell
python .agent\skills\research-cockpit\scripts\report_option_workstream.py --root research_cockpit --option option_x --agent agent_id --recommend continue --summary "..." --dry-run --json
python .agent\skills\research-cockpit\scripts\report_option_workstream.py --root research_cockpit --option option_x --agent agent_id --recommend continue --summary "..."
```

## Findings

Record experiment findings through `record_finding.py`:

```powershell
python .agent\skills\research-cockpit\scripts\record_finding.py --root research_cockpit --experiment experiment_x --statement "..." --confidence medium --outcome positive --summary "..."
```

After findings change, rebuild decision evidence when a decision depends on them:

```powershell
python .agent\skills\research-cockpit\scripts\update_decision_evidence.py --root research_cockpit --id decision_x
```
