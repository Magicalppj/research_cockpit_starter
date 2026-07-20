# Experiment Cycle

Use this capability for the ordinary lifecycle of one assigned experiment: start its run, optionally preserve output, and close the result plus one follow-up atomically.

Use `capabilities/experiment-tracking.md` only for advanced workstream planning, standalone gates/findings, promoted graph artifacts, retention, or legacy recovery.

## Read Once

Start from the assignment when one exists:

```sh
research-cockpit work open --root <data-root> --assignment <assignment_id> --compact --json
```

Without an assignment, read the known experiment directly:

```sh
research-cockpit context --root <data-root> --id <experiment_id> --view execution --compact --json
```

Do not prepend command discovery, bootstrap, artifact inventory, or a generated context pack unless the target or required command is unknown.

## Default Assigned Path

Mutate one canonical data root sequentially. The normal path is one packet read, one `work start`, and one `work close`.

### 1. Start The Run

Use the `work_start_v1` file from `worker-loop.md` to create a runtime-named run, move a planned/queued experiment to `running`, and renew its lease in one transaction:

```sh
research-cockpit work start --root <data-root> --assignment <assignment_id> --file <start.yaml> --json --compact
```

Use the returned `entities.run_id` in later evidence and closeout input. Do not issue a separate lease renewal or experiment status command. Add launcher, progress, resource, or retention fields under `run` only when that metadata exists.

### 2. Preserve Incremental Evidence Only

Skip this step unless evidence must be durable before the run closes, such as streaming output or a shared intermediate result. In that exceptional case, ingest once:

```sh
research-cockpit ingest-artifact --root <data-root> --assignment <assignment_id> --node <experiment_id> --from <output_dir> --run-id <run_id> --json --compact --no-build
```

Keep the returned record id for closeout. Final payload available at close belongs in `work_close_v1.evidence_inputs` and does not need this extra invocation.

### 3. Close Atomically

Write one small `work_close_v1` file using lease and revision values from the packet:

```yaml
schema_version: work_close_v1
agent_id: agent_x
lease_id: lease_x
lease_epoch: 1
operation_id: op_close_x
input_revision: input-v1:x
run:
  id: run_x
  status: completed
experiment:
  status: done
  result_summary: The bounded experiment passed.
finding:
  statement: The tested configuration met the acceptance criterion.
  confidence: strong
  outcome: positive
assignment_result:
  outcome: positive
  summary: The bounded experiment passed.
  delivery:
    git_commit: null
    changed_files: []
    tests:
      status: passed
      summary: Targeted checks passed.
  proposals: []
next_experiment:
  id: experiment_x_followup
  title: Scale the verified configuration
  success_criteria:
    - Complete the full evaluation.
  next_action: Start the full run.
```

After an earlier standalone ingest, add this block:

```yaml
artifact_record:
  existing_record_id: artifact_experiment_x_run_x
```

For final payload available at close, use this block instead; `source` is a directory and links are source-relative:

```yaml
evidence_inputs:
  source: ../worktree/.agent_runs/run_x
  title: Final run evidence
  summary: Metrics and logs retained at close.
  links:
    metrics: outputs/metrics.json
```

Do not combine `evidence_inputs` with `artifact_record`.

Then apply one transaction:

```sh
research-cockpit work close --root <data-root> --assignment <assignment_id> --file closeout.yaml --json --compact
```

A `done` experiment requires a finding. `next_experiment` is optional and limited to one same-scope sibling; it keeps the assignment active and advances its cursor. Without it, close completes the assignment, releases its lease, and records pending/not-required review state. Cross-scope follow-ups are `assignment_result.proposals` and never create assignments automatically.

Do not repeat the experiment conclusion, follow-up creation, artifact-record link, or cursor movement with standalone commands after this transaction.

## Long-Running Jobs

Use `update-run` only when operational metadata actually changes, such as heartbeat path, status, process id, or output root:

```sh
research-cockpit update-run --root <data-root> --assignment <assignment_id> --id <run_id> --status running --progress-file artifacts/<experiment_id>/<run_id>/progress.json --no-build
```

Use `run-context` only when full operational details are required. Launcher output conventions and templates are in `docs/launcher-output-conventions.md` and `templates/launcher/`.

## Verification Contract

Successful `work start` and `work close` receipts report `verification.status: internally_verified` and `additional_verification_required: false`. A standalone compatibility `ingest-artifact` reports:

```json
{"verified":true,"additional_verification_required":false,"verification_stage":"internal_verify","verify_commands":[]}
```

When those fields match, the worker is done. Do not run validate, context, build, smoke, or a dry-run replay.

Only when compact output requests additional verification, or after a manual truth-source edit, verify the reported changed scope:

```sh
research-cockpit validate --root <data-root> --changed-node <node_id> --json
research-cockpit context --root <data-root> --id <node_id> --view execution --compact --json
```

If validation falls back, follow only its `fallback.recommended_commands` and retry. Full validate/build/root smoke belongs to coordinator merge, release, or research-stage milestone handoff.

On a stale-write conflict, reread one bounded assignment/execution context and retry the rejected transaction with a new operation id. Agents may compute in parallel; the runtime serializes short truth commits. Do not submit duplicate mutations for the same assignment concurrently.