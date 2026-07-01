# Integrations

Use this capability for installation shape, CLI entry points, and agent integration.

## Research Repo Installation

Recommended layout:

```text
research-repo/
  research_cockpit/
    artifacts/
  .agent/skills/research-cockpit/
```

The plugin must not store project-specific research state inside the skill directory. Keep state in the research repo root `research_cockpit/`.

## Canonical Root And Disposable Worktrees

For multi-agent development, git worktrees are execution sandboxes. They may contain code changes and temporary `.agent_runs/<run_id>/` outputs, but they are not long-lived Research Cockpit truth sources.

- Canonical truth source: `<main_repo>/research_cockpit/`.
- Stable artifact store: `<main_repo>/research_cockpit/artifacts/<node_id>/<run_id>/`.
- Disposable worktree: `../worktrees/<agent_or_node>/`.

Agents should use `start-agent-session` to receive a handoff with `RESEARCH_COCKPIT_ROOT`, `stable_artifact_root`, and an `ingest-artifact` command template. Before a worktree can be deleted, any useful output must be copied with `ingest-artifact`, then referenced by `complete-experiment --artifact-id` or `update-finding --artifact-id`.

`import-worktree-findings` is a recovery tool for accidental worktree-local cockpit writes. It is not the normal path for preserving outputs.

For large experiment repositories, prefer sparse or minimal temporary worktrees. A temporary worktree usually needs source code, configs, scripts, tests, and minimal docs; it usually does not need a full checkout of `research_cockpit/`, `outputs/`, `logs/`, large `data/` trees, generated dataset artifacts, or virtual environments. The canonical `--root` should still point to the main checkout's `research_cockpit/` directory.

Use sparse planning before starting a heavy temporary branch:

```sh
research-cockpit start-agent-session --root D:/main_repo/research_cockpit --option option_x --label branch_probe --objective "Run branch experiments" --branch agent/option_x-branch_probe --worktree ../worktrees/branch_probe --base main --dry-run --json --sparse --sparse-profile ml-experiment
```

The sparse output is a dry-run command plan. Dry-run generated ids are preview-only; pass explicit `--agent` and `--assignment` / `--assignment-id` if the execute step must reuse them. Review the `sparse_worktree.commands` sequence, create the sparse worktree manually, then run `start-agent-session` normally without `--sparse` to record the assignment. The `ml-experiment` profile excludes `research_cockpit/`, `outputs/`, `logs/`, `data/`, generated dataset artifact directories, and common virtual environments from the temporary checkout. Downstream agents still mutate the main checkout's canonical `research_cockpit/` root through `--root` or `RESEARCH_COCKPIT_ROOT`.

Recommended watcher excludes for IDEs and repo watchers:

```text
.worktrees/
outputs/
logs/
data/
datasets/**/artifacts/
research_cockpit/artifacts/**
.venv/
.venvs/
```

When a repository has large generated payloads, keep bulk artifacts in git-ignored or external stable storage and link only small summaries, manifests, metrics, or portable review bundles from Research Cockpit. See `capabilities/maintenance.md` and `docs/large-repo-hygiene.md`.

## Data Root Resolution

Commands resolve data root in this order:

1. Explicit `--root`.
2. `RESEARCH_COCKPIT_ROOT`.
3. Upward search from current working directory for `research_cockpit/`.
4. Plugin repo fallback `examples/demo_research_cockpit/`.

## CLI

Installed CLI:

```sh
research-cockpit init --root research_cockpit
research-cockpit init --root research_cockpit --build --json
research-cockpit bootstrap --root research_cockpit --json
research-cockpit validate --root research_cockpit
research-cockpit build --root research_cockpit
research-cockpit ui --root research_cockpit
```

Agents should use the installed `research-cockpit` CLI from the research repo root. Do not call files inside the plugin package directly.

Use `init --build --json` for a new data root when the next step needs generated context packs immediately. Plain `init` keeps the old behavior and only copies the template.

If the console script is not on `PATH`, use the same installed Python interpreter as a deterministic fallback:

```sh
python -m research_cockpit.cli bootstrap --root /absolute/path/to/research_cockpit --json
```

When running from a constrained agent shell, prefer an explicit absolute `--root`. Relative roots depend on the shell's current directory and can fail if directory switching is restricted.
