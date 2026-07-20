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

## Three Mutations

Mutate one canonical data root sequentially. Pass `--assignment <assignment_id>` for worker writes.

### 1. Start The Run

Create the run and move a planned/queued experiment to `running` in one transaction:

```sh
research-cockpit create-run --root <data-root> --assignment <assignment_id> --id <run_id> --experiment <experiment_id> --status running --start-experiment --json --compact --no-build
```

Do not issue a separate experiment status command. Add launcher, progress, resource, or retention flags only when that metadata exists.

### 2. Preserve Output When Needed

Skip this step when the run has no payload that must survive. Otherwise ingest the run directory once:

```sh
research-cockpit ingest-artifact --root <data-root> --assignment <assignment_id> --node <experiment_id> --from <output_dir> --run-id <run_id> --json --compact --no-build
```

The default is a lightweight artifact record. Keep the returned record id; do not list records or promote a graph artifact unless another task explicitly needs that result.

### 3. Close Atomically

Write one small `run_closeout_v1` file:

```yaml
schema_version: run_closeout_v1
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
next_experiment:
  id: experiment_x_followup
  title: Scale the verified configuration
  success_criteria:
    - Complete the full evaluation.
  next_action: Start the full run.
```

After ingest, add this block; otherwise omit it:

```yaml
artifact_record:
  existing_record_id: artifact_experiment_x_run_x
```

Then apply one transaction:

```sh
research-cockpit complete-run --root <data-root> --assignment <assignment_id> --file closeout.yaml --json --compact --no-build
```

`experiment` is optional for a status-only run closeout. A `done` experiment requires a finding. `next_experiment` is optional and limited to one planned/queued sibling; with assignment scope it also advances the worker cursor and inherits `next_action`. If `next_action` is omitted, the assignment's old actions are cleared. Include gate rows only for gate payloads that already exist.

Do not repeat the experiment conclusion, follow-up creation, artifact-record link, or cursor movement with standalone commands after this transaction.

## Long-Running Jobs

Use `update-run` only when operational metadata actually changes, such as heartbeat path, status, process id, or output root:

```sh
research-cockpit update-run --root <data-root> --assignment <assignment_id> --id <run_id> --status running --progress-file artifacts/<experiment_id>/<run_id>/progress.json --no-build
```

Use `run-context` only when full operational details are required. Launcher output conventions and templates are in `docs/launcher-output-conventions.md` and `templates/launcher/`.

## Verification Contract

For successful non-dry-run `create-run`, `ingest-artifact`, and structured `complete-run`, compact output should report:

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

On a stale-write conflict, reread one bounded assignment/execution context and retry the rejected transaction. Never parallelize mutations against the same data root.