# ADR-0002: Use Canonical Artifact Store For Worktree Outputs

## Status

Accepted. Amended by the record-first policy on 2026-07-01 and by the 0.3.0 role-facade cutover on 2026-07-21. The storage decision remains active; the old standalone ingest and promotion commands are historical interfaces.

## Date

2026-05-09

## Context

Parallel agents work in disposable Git worktrees. Evidence that remains only inside a deleted worktree cannot support later review, decisions, or baselines even when its YAML reference survives.

## Decision

Use the canonical data root as the long-lived local artifact store:

```text
research_cockpit/artifacts/<experiment_id>/<run_id>/
```

Ordinary final output is staged through `work_close_v1.evidence_inputs`. Evidence that must be durable earlier is staged through `work record`. Both paths copy selected output to the canonical store, create lightweight artifact-record metadata and provenance, reject linked filesystem objects, and avoid creating a graph artifact node by default.

Agents must not leave findings, decisions, or baselines dependent on an original worktree path. Large evidence may remain in a stable external store when canonical metadata, hashes, summaries, and links are sufficient.

The 0.3.0 CLI does not expose the former standalone ingest or record-promotion routes. Existing 0.2.x artifact records, graph artifact nodes, manifests, payload bytes, retention metadata, and provenance remain readable and writable by current role and maintenance workflows.

## Alternatives Considered

### Keep Worktrees Long Term

- Pros: no staging step.
- Cons: worktrees accumulate branches, outputs, caches, and machine-specific paths.
- Rejected: cleanup would conflict with evidence preservation.

### Use Worktree-Local Research Cockpit Roots

- Pros: each agent can mutate independently.
- Cons: merging shared truth is harder than merging code and weakens assignment concurrency controls.
- Rejected: explicit maintenance migration remains a recovery path, not the normal workflow.

### Store Only External URIs

- Pros: suitable for very large artifacts.
- Cons: local review and portable demonstrations require a self-contained option.
- Rejected as the only default; stable external references remain supported where appropriate.

## Consequences

- A worktree is removable only after useful evidence is staged or linked durably and the assignment is closed.
- Final evidence adds no extra CLI invocation to the three-call worker fast path.
- `work record` is exceptional because it adds a control-plane round trip and an intermediate durable record.
- New run output defaults to artifact records with `reproducible_output` retention.
- Symlinked files, junctions, and unsupported file types are rejected to prevent copying data outside the intended bundle.
- Destructive cleanup remains an explicit operator action after `maintenance audit` or artifact compaction dry-run.
