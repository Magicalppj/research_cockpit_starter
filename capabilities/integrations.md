# Integrations

Use this capability for installation shape, CLI entry points, and agent integration.

## Research Repo Installation

Recommended layout:

```text
research-repo/
  research_cockpit/
  .agent/skills/research-cockpit/
```

The plugin must not store project-specific research state inside the skill directory. Keep state in the research repo root `research_cockpit/`.

## Data Root Resolution

Commands resolve data root in this order:

1. Explicit `--root`.
2. `RESEARCH_COCKPIT_ROOT`.
3. Upward search from current working directory for `research_cockpit/`.
4. Plugin repo fallback `examples/demo_research_cockpit/`.

## CLI

Installed CLI:

```sh
research-cockpit bootstrap --root research_cockpit --json
research-cockpit validate --root research_cockpit
research-cockpit build --root research_cockpit
research-cockpit ui --root research_cockpit
```

Agents should use the installed `research-cockpit` CLI from the research repo root. Do not call files inside the plugin package directly.

If the console script is not on `PATH`, use the same installed Python interpreter as a deterministic fallback:

```sh
python -m research_cockpit.cli bootstrap --root /absolute/path/to/research_cockpit --json
```

When running from a constrained agent shell, prefer an explicit absolute `--root`. Relative roots depend on the shell's current directory and can fail if directory switching is restricted.
