# Coordinator Loop

Load this playbook only when decomposing, assigning, reviewing, deciding, or handing off portfolio-level research work.

## Bounded Triage

Use the indexed Coordination Snapshot for unknown-target or global assignment triage:

```sh
research-cockpit coord overview --root <data-root> --json --compact --limit 20
```

Filter by status, kind, agent, root node, or review status. Continue with the returned `next_page`; poll a stable query with `--since <revision>`. For a known assignment, open its Work Packet; for a known node, use bounded execution context. Do not chain these reads or substitute full bootstrap.

Create assignments with an objective, subtree scope, dependencies, captured inputs, success criteria, deliverables, and review policy. Keep canonical-root commits short; workers may compute in parallel but same-root truth mutations remain serialized.

Use `research-cockpit commands --role coordinator --name <command> --json --compact` only when one operation contract is missing. Default coordinator work must not load the 70-command legacy inventory.

## Coordination Rules

- Coordinator state owns global/UI selection; assignment state owns worker cursors.
- New-branch proposals are reviewed before assignment creation.
- Review updates review status and refs; it does not rewrite producer evidence.
- Prefer bounded summaries and selected evidence refs over dashboard rebuilds or global scans.
- Successful internally verified mutations do not get a mechanical validate/context/build/smoke tail.

## Synthesis Assignments

A synthesis assignment is opened with the normal `work open` path. Its Work Packet embeds a bounded Synthesis Packet derived only from captured dependency result revisions and selected evidence refs. Missing, unsatisfied, or changed dependencies appear as explicit missing/stale warnings; do not scan unrelated accepted history to fill them.

Candidate options are explicit metadata when supplied, otherwise the selected dependency root nodes. Close synthesis work through the same assignment-scoped `work close` transaction.

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

`milestone_handoff` means coordinator merge, release, or research-stage closeout. Create the input once; inspect the schema only when needed:

```yaml
schema_version: coord_handoff_v1
operation_id: handoff_release_001
kind: release
summary: Release candidate handoff
strict_lifecycle: true
allow:
  pending_reviews: false
  stale_inputs: false
  active_leases: false
  unresolved_blockers: false
```

```sh
research-cockpit coord handoff --root <data-root> --file <handoff.yaml> --json --compact --progress
```

This one command captures the target revision, runs full validation once, reuses that state for one dashboard build and one compact smoke, computes Coordination Snapshot blockers, rechecks truth, and commits one `handoffs/*.yaml` report plus audit event. It does not hold the canonical mutation lock while gates run.

Defaults block pending reviews, stale inputs, active leases, unresolved/expired work, and scope overlaps. Set an `allow` value only as an explicit coordinator policy decision. A blocked report is durable: after state changes, use a new operation id. Reuse the same operation id only for an exact retry of the same request; mismatch is rejected before gates run.

Do not run standalone full `validate`, `build`, or `smoke` before this command. Those routes remain for diagnosis, not the milestone recipe.

An ordinary worker or reviewer completion is not a milestone handoff. Use `maintenance.md` only for explicit migration, repair, retention, or repository hygiene work.

Load one deeper capability only for the active coordination condition: `focus-context.md` for focus ownership, `node-management.md` for graph decomposition, `decision-adr.md` for acceptance, `graph-state.md` for structural changes, or `ui-dashboard.md` for an explicit UI handoff.
