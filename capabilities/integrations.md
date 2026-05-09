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
