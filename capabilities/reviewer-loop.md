# Reviewer Loop

Load this playbook only for an explicit review assignment. Review is read-only with respect to the producer assignment and its evidence.

## Open The Review Packet

```sh
research-cockpit review open --root <data-root> --assignment <review_assignment_id> --json --compact
```

The response contains the review assignment Work Packet, the exact producer Evidence Bundle and result revision, and bounded artifact-record links. Confirm `assignment.kind: review`, `scope.write_policy: review_read_only`, and `allowed_operations: [review]`. Do not separately open producer context or scan all accepted history/artifacts.

## Review Invariants

- Bind findings to the producer result revision that was actually inspected.
- Order findings by severity and identify the affected file, node, or evidence ref.
- Do not change producer results, runs, findings, or artifact payloads.
- A negative or inconclusive producer result is reviewable; outcome polarity is not a validity failure.
- If producer inputs changed, return a stale-review result instead of silently reviewing mixed revisions.

Write one `review_report_v1` file using the lease and revisions from `review open`:

```yaml
schema_version: review_report_v1
agent_id: reviewer_x
lease_id: lease_x
lease_epoch: 1
operation_id: op_review_x
input_revision: input-v1:review
producer_result_revision: result-v1:producer
verdict: approved
summary: The result is reproducible within the assigned scope.
findings: []
evidence_inspected:
  - artifact_record_x
validation_performed:
  - Targeted producer tests
```

Valid verdicts are `approved`, `changes_requested`, and `inconclusive`. Findings use `P0` through `P3`; the persisted result is severity ordered and bound to `producer_result_revision`.

```sh
research-cockpit review report --root <data-root> --assignment <review_assignment_id> --file <review.yaml> --json --compact
```

The transaction writes only the reviewer assignment result, releases its lease, and returns an internally verified receipt. It never updates producer truth. On `stale_producer_result` or `stale_inputs`, reopen the review packet and submit a new request with a new operation id.

Use `check-decision-acceptance` only for an explicitly delegated legacy decision acceptance review. Query `research-cockpit commands --role reviewer --name <command> --json --compact` only when one operation contract is missing.

Do not start experiments/runs, mutate producer results, apply the verdict to producer review state, change coordinator focus, or invoke maintenance workflows.

Read `decision-adr.md` only for acceptance criteria, `experiment-tracking.md` only for referenced run evidence, and `graph-state.md` only for a scoped structural invariant. These are conditional reads, not a startup bundle.
