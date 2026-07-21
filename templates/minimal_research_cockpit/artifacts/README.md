# Artifact Store

This directory is the default stable store for Research Cockpit evidence copied out of disposable agent worktrees.

Recommended layout:

```text
artifacts/
  <node_id>/
    <run_id>/
      _research_cockpit_ingest.json
      metrics.json
      report.md
      figures/
```

Use final `work_close_v1.evidence_inputs`, or `research-cockpit work record` when evidence must be durable before close, to copy a worktree output directory here and create a linked artifact record. The ingest manifest stores the source path as a portable path relative to the canonical root parent when possible; external source directories are recorded only as a short hint. Source directories containing symlinks are rejected. Small files can live in Git. Large files should use Git LFS or stable external storage, but the recorded artifact path or link must remain valid after the worktree is deleted.
