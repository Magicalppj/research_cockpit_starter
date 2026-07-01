# Artifact Retention Policy

Research Cockpit records conclusions, evidence links, and small reviewable bundles. It should not become the default home for every raw generated file in a large experiment repository.

## Goals

- Keep findings, decisions, and baselines traceable.
- Make later cleanup decisions mechanical instead of memory-based.
- Preserve reusable evidence while allowing reproducible or disposable payloads to be cleaned.
- Keep old Research Cockpit data roots valid without requiring migration.

## What Must Be Preserved

A conclusion should remain reviewable after a worktree or output directory is removed. Preserve enough information to understand and reproduce the result:

- code commit, branch, or patch reference
- config copy or config path
- launcher command or regenerate command
- metric summary
- manifest path
- key plots, reports, or tables
- portable review/listening bundle when subjective review matters
- linked finding, decision, option, or baseline ids

The full raw payload does not always need to remain in `research_cockpit/artifacts/**`.

## Retention Classes

| Class | Meaning | Default action |
| --- | --- | --- |
| `evidence_critical` | Supports a finding, decision, or baseline | Preserve |
| `portable_review_bundle` | Small review/listening bundle with relative links | Preserve or archive |
| `final_checkpoint` | Best or final checkpoint needed for reproduction | Preserve |
| `resume_state` | Optimizer/scheduler state for planned resume | Preserve only while resume is planned |
| `reproducible_output` | Large output that can be regenerated | Keep summary and command; payload can be cleaned |
| `disposable_cache` | Precompute/cache/intermediate data | Clean after conclusion is recorded |
| `deprecated_payload` | Superseded by newer evidence | Archive or delete after review |

## Suggested Metadata

For promoted graph artifact nodes, persist artifact retention metadata through `create-artifact --file` when creating the node, or `update-node-fields --metadata-file` when adding or changing retention later. For ordinary run output, prefer `ingest-artifact --record-only`; the artifact record carries the same retention class without adding another `graph/nodes/artifact_*.yaml` file. The same information can also appear in launcher output, artifact manifests, portable review bundles, or notes for review, but graph artifact metadata and artifact record metadata are the structured sources used by audits and context payloads.

```yaml
retention:
  class: evidence_critical
  reason: "Contains metric summary and portable review bundle used by finding_x."
  delete_after: null
  reusable: true
  regenerate_command: scripts/experiments/example/run_eval.sh
  depends_on_for_future_training: false
  keep_files:
    - metrics_summary.json
    - index.html
    - comparison_data.json
  disposable_patterns:
    - raw_generations/**
    - optimizer.bin
    - scheduler.bin
```

This metadata is optional in older data roots. Audit and lint commands should treat missing metadata as a warning until a project opts into stricter rules.

## Cleanup Decision Rules

Preserve:

- artifacts linked from accepted decisions, current baselines, or strong findings
- portable review bundles used by a decision or report
- final checkpoints needed for reproduction or downstream training
- resume states while a follow-up run is planned

Clean after review:

- generated samples that can be recreated from a launcher command
- precompute caches
- intermediate checkpoints
- optimizer/scheduler states after resume is no longer planned
- payloads superseded by stronger evidence

Do not clean:

- paths referenced by queued or running runs
- paths declared in active `resources`
- paths whose retention class is unknown and whose linked finding/decision status has not been reviewed

## Artifact Records, Promotion, And Demotion

Use artifact records for ordinary run outputs, logs, metrics, reproducible outputs, disposable caches, and intermediate checkpoints:

```sh
research-cockpit ingest-artifact --root research_cockpit --node <experiment_id> --from <run_dir> --run-id <run_id> --record-only --json --compact --no-build
research-cockpit artifact-records --root research_cockpit --experiment <experiment_id> --json --compact
```

Promote a record to a graph artifact node only when it needs durable navigation or must support a decision, baseline, strong finding, portable review bundle, or final checkpoint:

```sh
research-cockpit promote-artifact-record --root research_cockpit --id <record_id> --artifact-id <artifact_id> --link-to <node_id> --json --compact
```

Use graph artifact demotion as an audit-first maintenance workflow. Start with a dry-run:

```sh
research-cockpit compact-artifacts --root research_cockpit --dry-run --json --show-diff
```

Only rows classified as `can_demote` are automatic execution candidates. A safe execution command is explicit and single-artifact scoped:

```sh
research-cockpit compact-artifacts --root research_cockpit --id <artifact_id> --execute --no-build --json --show-diff
research-cockpit validate --root research_cockpit --json
```

Demotion writes an artifact record, updates safe experiment `linked_artifacts` references to `linked_artifact_records`, removes the graph artifact node, and writes `artifact_migrations/<artifact_id>.yaml`. It does not delete payload files. Rows classified as `must_keep_node`, `needs_review`, or `cannot_demote` require human review or a different command path.
## Relationship To `ingest-artifact`

`ingest-artifact --record-only` copies worktree output into the canonical artifact store and records lightweight metadata without creating a graph artifact node. The older graph-node ingest path remains compatible, but worker agents should normally use record-only for run output. Copying files into the artifact store does not mean every copied file must be kept forever. After the evidence is summarized and retention metadata exists, bulky reproducible files may still be candidates for cleanup, demotion, or external archival.

For very large payloads, prefer a stable external or git-ignored artifact root plus small Research Cockpit artifact nodes that point to manifests, summaries, and review bundles.
