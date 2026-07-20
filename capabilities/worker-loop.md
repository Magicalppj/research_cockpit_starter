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

The no-payload path is three agent-visible CLI calls including open:

```sh
research-cockpit create-run --root <data-root> --assignment <assignment_id> --id <run_id> --experiment <experiment_id> --status running --start-experiment --json --compact --no-build
research-cockpit complete-run --root <data-root> --assignment <assignment_id> --file <closeout.yaml> --json --compact --no-build
```

Use one additional `ingest-artifact --json --compact --no-build` only when a durable payload exists before close. In closeout, reference its returned `artifact_record.existing_record_id`; do not repeat experiment completion, follow-up creation, or cursor movement already included by `complete-run`.

Use assignment-scoped mutations. Do not mutate coordinator focus, accept decisions, or run lifecycle cleanup unless the assignment explicitly delegates that authority.

## Verification

Read the mutation receipt. When `verified: true` and `additional_verification_required: false`, do not run another validate, context reread, build, or smoke command.

When additional verification is required, run only the reported changed scope. A normal worker turn is not `milestone_handoff`; full validate/build/smoke belongs to coordinator handoff.

## Conditional Recovery

- `waiting_dependencies`: stop and report the bounded blockers.
- `stale_inputs`: reopen from the latest packet before producing a result.
- `unknown_inputs`: continue only for explicitly supported legacy work; otherwise ask the coordinator to refresh the index or assignment inputs.
- expired lease or rejected scope: do not retry a mutation blindly.
- missing operation details: query one command with `research-cockpit commands --role worker --name <command> --json --compact`; do not request broad discovery.

Read a deeper capability only when the packet requires it: `experiment-cycle.md` for current run closeout, `experiment-tracking.md` for advanced evidence, `integrations.md` for external payload ingestion, `node-management.md` for an explicitly delegated graph mutation, and `troubleshooting.md` for a reported recovery condition.
