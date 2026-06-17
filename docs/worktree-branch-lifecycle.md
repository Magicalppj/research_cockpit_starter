# Worktree And Branch Lifecycle

Git worktrees are execution sandboxes for parallel agents. They are not long-term knowledge stores. Research Cockpit should preserve the research state and evidence needed to remove a worktree safely.

## Branch Classes

| Branch class | Purpose | Lifecycle |
| --- | --- | --- |
| `main` | Stable baseline | Receives verified, general changes |
| `codex/*` | Temporary task or experiment branch | Candidate for deletion after closeout, merge, or extraction |
| `agent/*` | Assignment-scoped parallel agent work | Close when assignment ends |
| `research/*` | Long-lived research direction | Keep when useful but not ready for main |
| `archive/*` | Rare read-only branch preservation | Prefer git history and RC evidence first |

## Worktree Closeout

Before deleting a worktree:

1. Identify the associated assignment, option, experiment, run, branch, and worktree label.
2. Read scoped context through `bootstrap --assignment`, `agent-session-context`, or `option-workstream-context`.
3. Check active assignments and queued/running runs.
4. Check active resource declarations.
5. Check outer and nested repo dirty state.
6. Classify the code changes:
   - `merge_to_main`
   - `preserve_as_research_branch`
   - `extract_partial`
   - `discard_after_recording`
7. Ingest or link useful evidence.
8. Record findings, decisions, baseline updates, or follow-up work.
9. Move assignment cursors away from terminal nodes.
10. Remove the worktree and temporary branch only after review.

## Branch Cleanup Rules

- Do not delete a checked-out branch.
- Do not delete an unmerged branch unless evidence and reusable code have been reviewed.
- Do not force-delete by default. Prefer `git branch -d` for merged inactive branches.
- Promote useful but unstable work from `codex/*` or `agent/*` to `research/*` rather than leaving it as an accidental temporary branch.
- After batch branch cleanup, `git pack-refs --all --prune` can reduce local ref overhead.

## Failed Experiments

A negative result can still contain reusable work:

- dataset builders
- evaluation scripts
- launcher fixes
- visualization or review bundles
- bug fixes
- config improvements

Record the negative finding first, then decide whether any code should be merged, extracted, or preserved on a `research/*` branch.

## Nested Repositories

Nested repositories must be audited explicitly. Do not assume the outer repository dirty state tells the full story. For nested repos, check:

- current branch
- dirty files
- untracked files
- whether the nested branch matches the outer worktree purpose
- whether generated outputs are inside the nested repo

## Research Cockpit Boundary

The canonical `research_cockpit/` root remains in the main checkout. Downstream agents should write to that root with `--root` or `RESEARCH_COCKPIT_ROOT`; they should not initialize or mutate a worktree-local cockpit root.
