# Artifact Retention Policy

Research Cockpit preserves conclusions, provenance, and bounded evidence bundles. It is not the default store for every generated file in a research repository.

## Goals

- Keep findings, decisions, and baselines reviewable after a worktree is removed.
- Preserve reusable evidence while allowing reproducible or disposable payloads to be cleaned.
- Keep legacy nodes, artifact records, manifests, and payloads readable while keeping new managed payloads outside the state root.
- Avoid creating a graph node for every run output.

## What Must Be Preserved

Preserve enough information to understand and reproduce a conclusion:

- code commit, branch, or patch reference
- configuration and launch command
- metric summary and relevant gates
- manifest path and content hash
- key plots, reports, tables, or portable review bundle
- linked finding, decision, option, baseline, experiment, and run ids

The full raw payload does not need to remain in the state root. `research_cockpit/artifacts/**` is a legacy location, not a destination for new evidence writes.

## Retention Classes

| Class | Meaning | Default action |
| --- | --- | --- |
| `evidence_critical` | Supports a finding, decision, or baseline | Preserve |
| `portable_review_bundle` | Small self-contained review bundle | Preserve or archive |
| `final_checkpoint` | Final checkpoint needed for reproduction | Preserve |
| `resume_state` | State required by a planned resume | Preserve while needed |
| `reproducible_output` | Output that can be regenerated | Keep summary and command; payload may be cleaned |
| `disposable_cache` | Cache or intermediate data | Clean after review |
| `deprecated_payload` | Superseded evidence | Archive or delete after review |

New evidence records default to `reproducible_output`. Existing 0.2.x retention metadata remains valid and is preserved on round-trip writes.

## Canonical Evidence Intake

Final output belongs in the assignment closeout. Put a source directory and only the useful relative links in `work_close_v1.evidence_inputs`:

```yaml
evidence_inputs:
  source: ../outputs/run_x
  title: Run x evidence
  summary: Bounded result summary.
  links:
    metrics: metrics_summary.json
    config: config.yaml
    report: report.md
```

```sh
research-cockpit work close --root <data-root> --assignment <assignment_id> --file <closeout.yaml> --json --compact
```

The default `reference` mode writes source URI, selected links, bounded inventory, and declared integrity into the artifact record without copying payload bytes. The source remains owned by the launcher or external system.

Set `mode: managed` only when Cockpit must own a copy. Managed mode requires an external artifact root configured through `storage.yaml` or `RESEARCH_COCKPIT_ARTIFACT_ROOT`; it copies and hashes in one stream before atomically publishing the artifact record. It never falls back to `research_cockpit/artifacts/**`. Both modes reject symlinks, junctions, unsupported file types, and links outside the source directory.

Use `work record` only when evidence must be durable before close because of crash recovery, shared consumption, or a long streaming run:

```sh
research-cockpit work record --root <data-root> --assignment <assignment_id> --file <record.yaml> --json --compact
```

Obtain the exact incremental schema with `work record --print-schema`. A later closeout may reference its returned record id through `artifact_record.existing_record_id`; it must not also provide `evidence_inputs`.

## Graph Artifacts And Compaction

Artifact records are the normal representation for run outputs, logs, metrics, caches, and checkpoints. A graph artifact is reserved for durable portfolio navigation and is a coordinator-owned graph change, not an automatic consequence of recording evidence.

Existing graph artifact nodes and their retention metadata remain supported. Audit before demotion:

```yaml
schema_version: maintenance_action_v1
action: artifact
execute: false
parameters:
  artifact_id: artifact_x
  show_diff: true
```

```sh
research-cockpit maintenance compact --root <data-root> --file <compaction.yaml> --json --compact
```

Only a single artifact classified as `can_demote` may be executed. Change `execute` to `true` only after reviewing the dry-run. Demotion writes an artifact record and migration report, updates safe references, and removes the graph node; it does not delete payload bytes.

## Managed Storage Migration And GC

Legacy payloads remain readable. To move one record into configured external managed storage, use `maintenance migrate` with `action: artifact_storage`; the default is dry-run and the same stable `operation_id` resumes an interrupted migration. See [0.3.1 storage migration](migrations/0.3.1-storage-boundaries-and-workstream-tracking.md) for the exact file contract.

Physical cleanup is separate from graph compaction. `maintenance compact` with `action: artifact_gc` plans and executes one revision-bound transition for one verified Cockpit-managed record:

```text
dry-run plan -> verify -> quarantine -> delayed purge
```

It rejects active, must-keep, external, legacy, weak-integrity, incomplete-inventory, or unsafe payloads. Quarantine is an atomic rename inside the managed artifact filesystem; every prepared and final transition has an immutable manifest. Purge must use a fresh plan revision after the delay. Do not manually delete managed or quarantine directories.

## Cleanup Rules

Preserve evidence linked from accepted decisions, effective baselines, strong findings, or active follow-up work. Preserve final checkpoints and planned resume state.

Clean only after review:

- reproducible generated samples
- precompute caches and temporary exports
- intermediate checkpoints
- optimizer or scheduler state after resume is no longer planned
- payloads superseded by stronger evidence

Do not clean paths referenced by active assignments, queued or running runs, active resources, or unresolved retention warnings. Use `maintenance audit` for bounded candidates. Only `artifact_gc` may physically clean eligible Cockpit-managed payloads; external and legacy sources remain outside this mechanism.

For very large payloads, use reference evidence by default or an explicit stable external managed root. Keep only metadata, provenance, and portable review bundles in the state root.
