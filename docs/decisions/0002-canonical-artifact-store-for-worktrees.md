# ADR-0002: Use Canonical Artifact Store For Worktree Outputs

## Status
Accepted

## Date
2026-05-09

## Context
Research Cockpit supports parallel agents working in separate git worktrees. Worktrees are useful for isolating code changes and temporary experiment outputs, but they are also intentionally disposable. If findings or artifact paths point into a deleted worktree, later users can still see the YAML record but cannot inspect the evidence.

We need a default rule that lets users delete worktrees without losing the evidence behind findings, decisions, and baselines.

## Decision
Use the canonical data root as the long-lived artifact store:

```text
research_cockpit/artifacts/<node_id>/<run_id>/
```

Agents must copy useful worktree outputs into this store with `research-cockpit ingest-artifact` before recording long-lived findings. The command creates a linked `artifact` node and writes `_research_cockpit_ingest.json` beside the copied files. Findings should then refer to the artifact id, not the original worktree path.

## Alternatives Considered

### Keep Worktrees Long Term
- Pros: No copy step.
- Cons: Worktrees accumulate local branches, build outputs, caches, and machine-specific paths.
- Rejected: It makes repository cleanup conflict with evidence preservation.

### Use Worktree-Local Research Cockpit Roots
- Pros: Each agent can mutate independently.
- Cons: Merging YAML truth sources is harder than merging code, and `import-worktree-findings` must reject many unsafe changes.
- Rejected: This remains a recovery path, not the default workflow.

### Store Only External URIs
- Pros: Works well for large artifacts.
- Cons: New users and demos need a local path that works without extra infrastructure.
- Rejected as the default. External URIs can still be recorded as artifact links when needed.

## Consequences
- Deleting a worktree is safe only after useful outputs are ingested and findings/decisions/baselines reference canonical artifacts.
- Small artifacts can be tracked in Git; large artifacts should use Git LFS or an external store while keeping a stable artifact node/path.
- Ingested run directories are copied as regular files and directories; symlinked files or directories are rejected to avoid pulling in data outside the run bundle.
- Agent handoffs include `stable_artifact_root` and an `ingest-artifact` command template.
