# Decision ADR

Use this capability when checking, repairing, promoting, or accepting decisions.

## Checklist Flow

```sh
research-cockpit check-decision-acceptance --root research_cockpit --id decision_x --json
research-cockpit update-decision-evidence --root research_cockpit --id decision_x --dry-run --json --show-diff
research-cockpit update-decision-evidence --root research_cockpit --id decision_x --no-build
research-cockpit update-decision-checklist --root research_cockpit --id decision_x --alternative option_a --consequence "..." --next-required-action "..." --dry-run --json --show-diff
research-cockpit update-decision-checklist --root research_cockpit --id decision_x --alternative option_a --consequence "..." --next-required-action "..." --no-build
research-cockpit accept-decision --root research_cockpit --id decision_x --dry-run --json
research-cockpit accept-decision --root research_cockpit --id decision_x
```

`check-decision-acceptance --json` returns a non-zero exit code when the gate is not ready, but stdout is still a valid JSON checklist report. Treat that as an expected gate failure, not as a command crash.

`--alternative` must be an existing `option` node id. To repair invalid alternatives, replace them with valid option ids before accepting the decision.
Use `--dry-run --json --show-diff` before evidence/checklist repair when you need to review generated evidence summaries or appended checklist fields. These commands support JSON/dry-run but do not use `--compact`.

## Repair Hints

The UI maps blocking checklist failures to CLI or YAML-field hints. Structural failures such as invalid parents should be repaired in YAML only after validating the intended graph relationship. After a structural YAML repair, run changed-scope `research-cockpit validate`; run `build` only when generated dashboards are needed or at milestone handoff.

## Promotion

Use `research-cockpit promote-decision` to create proposed decision nodes from a promising option:

```sh
research-cockpit promote-decision --root research_cockpit --id decision_x --option option_x --title "..." --summary "..." --dry-run --json
research-cockpit promote-decision --root research_cockpit --id decision_x --option option_x --title "..." --summary "..."
```

Decision acceptance writes a compact event through the active interaction backend; migrated roots keep `interaction_log.yaml` as an immutable legacy prefix.

Accepted decisions are historical adoption records. They do not automatically become the default context for every downstream agent. After acceptance, set the default baseline on the relevant problem, option, experiment, or stage when future work should inherit it:

```sh
research-cockpit set-baseline --root research_cockpit --node problem_x --option option_x --decision decision_x --dry-run --json --show-diff
research-cockpit set-baseline --root research_cockpit --node problem_x --option option_x --decision decision_x --no-build
```
