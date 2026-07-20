# Worker Loop

Load this playbook only when acting on one assignment. The assignment is the concurrency and write-scope boundary; do not infer a worker cursor from global focus or Markdown notes.

## Open Once

```sh
research-cockpit work open --root <data-root> --assignment <assignment_id> --json --compact
```

The Work Packet is the default execution context. Check `readiness`, `scope`, `dependencies`, `lease`, `allowed_operations`, `success_criteria`, `deliverables`, and `revision`. Do not add bootstrap, dashboard packs, or global search when this packet is sufficient.

For resume or polling, reuse the opaque revision:

```sh
research-cockpit work open --root <data-root> --assignment <assignment_id> --since <revision> --json --compact
```

Stop when the receipt says `changed: false`. A revision is a freshness token, not an ordered business version.

## Default Experiment Path

When `allowed_operations` contains `claim`, claim once and continue from the returned packet; do not add another `work open`:

```sh
research-cockpit work claim --root <data-root> --assignment <assignment_id> --agent <agent_id> --operation-id <operation_id> --return-packet --json --compact
```

Create `start.yaml` once from the packet lease. Add entries under `run` only when launcher metadata exists:

```yaml
schema_version: work_start_v1
agent_id: agent_x
lease_id: lease_x
lease_epoch: 1
operation_id: op_start_x
slug: trial
run:
  launcher: shell
  command: python train.py
  progress_file: artifacts/{experiment_id}/{run_id}/progress.json
```

Strings under `run` may use `{run_id}`, `{experiment_id}`, and `{assignment_id}`; the transaction expands them after generating the runtime id.

Then start and close; the start receipt supplies the generated `entities.run_id` used by closeout:

```sh
research-cockpit work start --root <data-root> --assignment <assignment_id> --file <start.yaml> --json --compact
research-cockpit complete-run --root <data-root> --assignment <assignment_id> --file <closeout.yaml> --json --compact --no-build
```

This is three agent-visible CLI calls including open, or three calls starting with claim for initially unowned work. `work start` creates the run, starts the experiment, and renews the lease in one transaction. Reuse one operation id only for an exact retry of the unchanged file; use a new id when its request changes. Normal mutations renew the lease, and a long-running launcher should call the runtime heartbeat hook outside model turns. Do not add `work renew` to the normal recipe.

Use one additional `ingest-artifact --json --compact --no-build` only when a durable payload exists before close. In closeout, reference its returned `artifact_record.existing_record_id`; do not repeat experiment completion, follow-up creation, or cursor movement already included by `complete-run`.

Use assignment-scoped mutations. Do not mutate coordinator focus, accept decisions, or run lifecycle cleanup unless the assignment explicitly delegates that authority.

## Verification

Read the mutation receipt. When `verification.status` is `internally_verified` with `additional_verification_required: false`, or a compatibility receipt reports `verified: true` with the same flag false, do not run another validate, context reread, build, or smoke command.

When additional verification is required, run only the reported changed scope. A normal worker turn is not `milestone_handoff`; full validate/build/smoke belongs to coordinator handoff.

## Conditional Recovery

- `waiting_dependencies`: stop and report the bounded blockers.
- `stale_inputs`: reopen from the latest packet before producing a result.
- `unknown_inputs`: continue only for explicitly supported legacy work; otherwise ask the coordinator to refresh the index or assignment inputs.
- expired lease with no active run or heartbeat: ask the coordinator to evaluate explicit reassignment.
- lease mismatch, idempotency conflict, or rejected scope: reopen the packet; do not change parameters and retry under the same operation id.
- missing operation details: query one command with `research-cockpit commands --role worker --name <command> --json --compact`; do not request broad discovery.

Read a deeper capability only when the packet requires it: `experiment-cycle.md` for current run closeout, `experiment-tracking.md` for advanced evidence, `integrations.md` for external payload ingestion, `node-management.md` for an explicitly delegated graph mutation, and `troubleshooting.md` for a reported recovery condition.
