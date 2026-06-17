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

P0 note: this policy documents the intended metadata shape. Until CLI write support is implemented, do not assume existing commands will persist nested retention fields. Preserve the same information in launcher output, artifact manifests, portable review bundles, or notes instead of hand-editing YAML.

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

## Relationship To `ingest-artifact`

`ingest-artifact` copies worktree output into the canonical artifact store so a disposable worktree can be removed. That does not mean every copied file must be kept forever. After the evidence is summarized and retention metadata exists, bulky reproducible files may still be candidates for cleanup or external archival.

For very large payloads, prefer a stable external or git-ignored artifact root plus small Research Cockpit artifact nodes that point to manifests, summaries, and review bundles.
