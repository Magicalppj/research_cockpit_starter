# Coordinator Loop

Load this playbook only when decomposing, assigning, reviewing, deciding, or handing off portfolio-level research work.

## Bounded Triage

Use coordinator bootstrap only when the target is unknown or global triage is required. For a known assignment, open its Work Packet; for a known node, use bounded execution context. Do not chain these reads.

Create assignments with an objective, subtree scope, dependencies, captured inputs, success criteria, deliverables, and review policy. Keep canonical-root commits short; workers may compute in parallel but same-root truth mutations remain serialized.

Use `research-cockpit commands --role coordinator --name <command> --json --compact` only when one operation contract is missing. Default coordinator work must not load the 70-command legacy inventory.

## Coordination Rules

- Coordinator state owns global/UI selection; assignment state owns worker cursors.
- New-branch proposals are reviewed before assignment creation.
- Review updates review status and refs; it does not rewrite producer evidence.
- Prefer bounded summaries and selected evidence refs over dashboard rebuilds or global scans.
- Successful internally verified mutations do not get a mechanical validate/context/build/smoke tail.

## Apply A Review

Consume a completed reviewer result by exact producer/review revisions:

```yaml
schema_version: coord_review_v1
operation_id: op_apply_review_x
review_assignment_id: assign_review_x
review_result_revision: result-v1:review
producer_result_revision: result-v1:producer
```

```sh
research-cockpit coord review --root <data-root> --assignment <producer_assignment_id> --file <verdict.yaml> --json --compact
```

This command updates only producer review metadata; it does not rewrite the producer Evidence Bundle or reviewer result. `approved` and `changes_requested` become terminal review states and store the reviewer result revision. `inconclusive` keeps producer review pending with a null result revision; the immutable audit event still records the inspected reviewer revision. Do not create an assignment merely because a worker Evidence Bundle contains a `new_branch` proposal; evaluate and assign it separately.

## Milestone Handoff

`milestone_handoff` means coordinator merge, release, or research-stage closeout. Only this boundary runs the full gate once:

```sh
research-cockpit validate --root <data-root> --json
research-cockpit build --root <data-root>
research-cockpit smoke --root <data-root> --json --progress
```

An ordinary worker or reviewer completion is not a milestone handoff. Use `maintenance.md` only for explicit migration, repair, retention, or repository hygiene work.

Load one deeper capability only for the active coordination condition: `focus-context.md` for focus ownership, `node-management.md` for graph decomposition, `decision-adr.md` for acceptance, `graph-state.md` for structural changes, or `ui-dashboard.md` for an explicit UI handoff.
