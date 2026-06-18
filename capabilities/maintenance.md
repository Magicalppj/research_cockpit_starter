# Maintenance

Use this capability when cleaning up temporary worktrees, branches, generated outputs, caches, checkpoints, or large artifact payloads in a long-running Research Cockpit repository.

Maintenance is audit-first. Research Cockpit should help agents decide what is safe, what is blocked, and what evidence must be preserved. It should not silently delete user files, git branches, worktrees, outputs, or caches.

See also:

- `docs/artifact-retention-policy.md`
- `docs/worktree-branch-lifecycle.md`
- `docs/large-repo-hygiene.md`
- `docs/plans/2026-06-17-repository-hygiene-lifecycle.md`

## Core Rules

- Keep the canonical `research_cockpit/` root in the main checkout. Do not mutate a worktree-local cockpit root.
- Preserve conclusions before cleanup: record findings, decisions, baseline changes, and linked artifacts before deleting a worktree or output directory.
- Treat worktree outputs as temporary until copied or summarized into a stable evidence location.
- Treat `research_cockpit/artifacts/**` as long-lived evidence, not a default store for raw large payloads.
- Prefer read-only audits before cleanup. If a cleanup step is destructive, present explicit commands for human review instead of running them automatically.
- In multi-agent runs, check active assignments, queued/running runs, and declared resources before moving or deleting paths.

## Worktree Closeout Checklist

Before deleting a worktree:

1. Identify the associated assignment, option, experiment, run, branch, and worktree label.
2. Generate a read-only plan:

```sh
research-cockpit worktree-closeout --root research_cockpit --repo . --worktree ../worktrees/<label> --classification discard_after_recording --dry-run --json
```

3. Read scoped context with `agent-session-context` or `option-workstream-context`.
4. Check for queued or running runs that reference the worktree, output root, cache root, GPU, port, PID, or model path.
5. Check the outer repo and any explicitly relevant nested repos for dirty state.
6. Classify the code changes:
   - `merge_to_main`: verified, general-purpose, and ready for the main branch.
   - `preserve_as_research_branch`: useful long-term research line, but not ready for main.
   - `extract_partial`: keep only reusable scripts, evals, dataset builders, or bug fixes.
   - `discard_after_recording`: experiment-specific code with no reusable value after findings are recorded.
7. Ingest or link useful evidence before cleanup.
8. Record findings and any follow-up work.
9. Close or move the assignment cursor when the current node is terminal.
10. Only then remove the worktree and clean up the temporary branch.
11. Run `validate`, `build`, and `smoke` after structured state changes.

`worktree-closeout` is always a planner. It reports blockers, Research Cockpit updates still needed, and shell command drafts for human review. It does not delete worktrees, delete branches, merge branches, or edit YAML.

## Branch Lifecycle

Recommended branch classes:

| Branch class | Purpose | Lifecycle |
| --- | --- | --- |
| `main` | Stable baseline | Receives verified, general changes |
| `codex/*` | Temporary task or experiment branch | Candidate for deletion after closeout, merge, or extraction |
| `agent/*` | Assignment-scoped parallel agent work | Close when assignment ends |
| `research/*` | Long-lived research direction | Keep when useful but not ready for main |
| `archive/*` | Rare read-only branch preservation | Prefer git history and RC evidence first |

Do not discard a failed experiment branch until reusable code has been checked. Failed results can still contain useful dataset builders, evaluation scripts, launcher fixes, or bug fixes.

For merged, non-checked-out, inactive `codex/*` or `agent/*` branches, prefer normal git safety (`git branch -d`) over force deletion. Use force deletion only after explicit human approval.

## Artifact Retention Classes

Use retention metadata to separate evidence from disposable payload:

| Class | Meaning | Default action |
| --- | --- | --- |
| `evidence_critical` | Supports a finding, decision, or baseline | Preserve |
| `portable_review_bundle` | Small review/listening bundle with relative links | Preserve or archive |
| `final_checkpoint` | Best or final checkpoint needed for reproduction | Preserve |
| `resume_state` | Optimizer/scheduler state for planned resume | Preserve only while resume is planned |
| `reproducible_output` | Large output that can be regenerated | Keep summary and command; payload can be cleaned |
| `disposable_cache` | Precompute/cache/intermediate data | Clean after conclusion is recorded |
| `deprecated_payload` | Superseded by newer evidence | Archive or delete after review |

Full schema examples live in `docs/artifact-retention-policy.md`.

Use `create-artifact --file` or `update-node-fields --metadata-file` to persist artifact retention metadata.

## Run Closeout

A completed run should answer what can be cleaned:

```yaml
output_retention:
  keep_checkpoints:
    - final
  keep_optimizer_state: false
  resume_planned: false
  raw_outputs_disposable: true
  portable_bundle_path: outputs/example/listening_bundle.tar.gz
  cleanup_after_completion: true
  cleanup_notes: "Metrics and bundle preserved; intermediate generations are reproducible."
```

This metadata is advisory. A missing retention policy should be treated as a warning, not a hard validation failure, until a project explicitly opts into stricter rules.
Use `create-run`, `update-run`, or `complete-run` with `--output-retention-json` / `--output-retention-file` to persist run retention metadata.

## Active Resource Declaration

Runs can optionally declare resources that cleanup must respect:

```yaml
resources:
  gpus:
    - 0
    - 1
  ports:
    - 8000
  process_ids:
    - 123456
  worktree: /repo/.worktrees/example
  output_roots:
    - /repo/outputs/example_run
  cache_roots:
    - /repo/data/example/.precomputed
  dataset_roots:
    - /repo/data/example
  model_paths:
    - /repo/outputs/example_run/checkpoint-final
```

These declarations are the Research Cockpit layer of safety. They do not replace system checks such as process lists, GPU tools, port checks, open-file checks, or user approval.

## Large Repository Hygiene

For large experiment repositories:

- Use sparse worktrees for temporary agent branches when possible.
- Keep generated outputs, caches, logs, and bulk datasets out of temporary worktree checkouts.
- Keep large artifact roots in git-ignored or external storage when payloads are too large for normal repository operations.
- Prefer small summary files and portable review bundles as long-lived evidence.
- Exclude `.worktrees/`, `outputs/`, `logs/`, `data/`, `datasets/**/artifacts/`, and `research_cockpit/artifacts/**` from IDE or repo watchers when they contain large generated files.
- Use `research-cockpit build --json --profile` to find build/search pressure.
- Use `research-cockpit build --skip-resource-search` when linked resource full-text indexing is too expensive for a large payload tree.

## Cleanup Boundaries

Always:

- Validate that findings and evidence are preserved before cleanup.
- Keep cleanup recommendations explicit and reviewable.
- Use assignment-scoped context for downstream agents.

Ask first:

- Deleting branches, worktrees, checkpoints, caches, datasets, or artifact payloads.
- Promoting a temporary branch to `research/*`.
- Moving large artifact roots outside the repository.

Never:

- Delete files based only on branch name.
- Use worktree-local evidence paths as long-lived findings.
- Mutate a worktree-local `research_cockpit/` root as the normal path.
- Treat RC-declared resources as proof that a path is unused.
