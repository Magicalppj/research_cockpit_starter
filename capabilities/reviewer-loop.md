# Reviewer Loop

Load this playbook only for an explicit review assignment. Review is read-only with respect to the producer assignment and its evidence.

## Open The Review Packet

```sh
research-cockpit work open --root <data-root> --assignment <review_assignment_id> --json --compact
```

Confirm the packet kind, producer assignment reference, producer result revision, review criteria, and allowed operations. Read only the referenced evidence and bounded context; do not scan all accepted history or all artifacts.

## Review Invariants

- Bind findings to the producer result revision that was actually inspected.
- Order findings by severity and identify the affected file, node, or evidence ref.
- Do not change producer results, runs, findings, or artifact payloads.
- A negative or inconclusive producer result is reviewable; outcome polarity is not a validity failure.
- If producer inputs changed, return a stale-review result instead of silently reviewing mixed revisions.

Use `check-decision-acceptance` only for a decision acceptance review. Query a specific command contract with `research-cockpit commands --role reviewer --name <command> --json --compact` only when the packet lacks the operation detail.

Do not start experiments or runs, mutate coordinator focus, or invoke maintenance workflows. Reviewer result persistence moves to the canonical review facade when that phase is available; until then, use only the explicit assignment-authorized command.

Read `decision-adr.md` only for acceptance criteria, `experiment-tracking.md` only for referenced run evidence, and `graph-state.md` only for a scoped structural invariant. These are conditional reads, not a startup bundle.
