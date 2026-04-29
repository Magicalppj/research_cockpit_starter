# Decision ADR

Use this capability when checking, repairing, promoting, or accepting decisions.

## Checklist Flow

```powershell
python .agent\skills\research-cockpit\scripts\check_decision_acceptance.py --root research_cockpit --id decision_x --json
python .agent\skills\research-cockpit\scripts\update_decision_evidence.py --root research_cockpit --id decision_x
python .agent\skills\research-cockpit\scripts\update_decision_checklist.py --root research_cockpit --id decision_x --alternative option_a --consequence "..." --next-required-action "..."
python .agent\skills\research-cockpit\scripts\accept_decision.py --root research_cockpit --id decision_x --dry-run --json
python .agent\skills\research-cockpit\scripts\accept_decision.py --root research_cockpit --id decision_x
```

## Repair Hints

The UI maps blocking checklist failures to script or YAML-field hints. Structural failures such as invalid parents should be repaired in YAML only after validating the intended graph relationship. After any structural YAML repair, run `validate_cockpit.py` and `build_dashboard.py`.

## Promotion

Use `promote_decision.py` to create proposed decision nodes from a promising option:

```powershell
python .agent\skills\research-cockpit\scripts\promote_decision.py --root research_cockpit --id decision_x --option option_x --title "..." --summary "..." --dry-run --json
python .agent\skills\research-cockpit\scripts\promote_decision.py --root research_cockpit --id decision_x --option option_x --title "..." --summary "..."
```

Decision acceptance writes compact events to `interaction_log.yaml`.
