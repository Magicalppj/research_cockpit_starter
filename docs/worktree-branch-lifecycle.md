# Worktree And Branch Lifecycle

Git worktrees isolate parallel code and experiment execution. They are disposable execution environments, while the canonical Research Cockpit root preserves assignment state, provenance, and selected evidence.

## Branch Classes

| Branch class | Purpose | Lifecycle |
| --- | --- | --- |
| `main` | Stable baseline | Receives reviewed general changes |
| `codex/*` | Temporary task or experiment | Delete after merge, extraction, or recorded discard |
| `agent/*` | Assignment-scoped parallel work | Close when the assignment ends |
| `research/*` | Deliberately retained research direction | Keep while the direction remains active |
| `archive/*` | Exceptional read-only preservation | Prefer Git history and durable evidence first |

## Session Creation

The coordinator creates the assignment/session record through one canonical request:

```sh
research-cockpit coord assign --print-schema --action session
research-cockpit coord assign --root <absolute-data-root> --file <session.yaml> --json --compact
```

Use `session.create_worktree: true` for runtime-managed worktree creation. For a manually created sparse worktree, use `false` and pass its path. All agents still use the canonical main-checkout data root through absolute `--root` or `RESEARCH_COCKPIT_ROOT`.

## Assignment Closeout

Before removing a worktree:

1. Open the assignment with `work open` and verify its current lease, run, and required deliverables.
2. Preserve final evidence through one `work close`; use `work record` only if evidence had to become durable earlier.
3. Complete required review through `review open`, `review report`, and `coord review`.
4. Record coordinator decisions or baselines through `coord decide` when applicable.
5. Run `maintenance audit` to inspect worktree, branch, active-run, resource, nested-repository, and artifact blockers.
6. Classify code as merge, retain as research, extract partially, or discard after recording.
7. Perform Git merge, extraction, worktree removal, and branch deletion explicitly after review.

```sh
research-cockpit maintenance audit --root <absolute-data-root> --repo <repo-root> --json --compact
```

Research Cockpit reports state and cleanup candidates. It does not implicitly merge branches or delete worktrees, branches, or payload files.

## Branch Cleanup Rules

- Do not delete a checked-out branch.
- Do not delete an unmerged branch until evidence and reusable code have been reviewed.
- Prefer normal merged-branch deletion; force deletion requires explicit human judgment.
- Move useful but intentionally unfinished work to `research/*` instead of retaining accidental temporary branches.
- Treat repository maintenance such as ref packing as a separate operator action.

## Failed Experiments

A negative result can still produce reusable dataset builders, evaluators, launcher fixes, review bundles, configuration improvements, or bug fixes. Record the evidence-backed negative finding before deciding whether code should be merged, extracted, retained, or discarded.

## Nested Repositories

Audit nested repositories explicitly. Check their branch, dirty and untracked files, purpose alignment, and generated outputs; the outer repository state is not sufficient evidence.

## Data Boundary

Never initialize a worktree-local cockpit root for normal parallel work. Shared structured state belongs to the canonical root; worktree-local recovery data may enter it only through an explicit `maintenance migrate` action and a reviewed dry-run.
