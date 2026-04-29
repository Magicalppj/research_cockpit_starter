# Integrations

Use this capability for installation shape, CLI entry points, wrapper scripts, and agent integration.

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

## CLI And Wrappers

Installed CLI:

```powershell
research-cockpit bootstrap --root research_cockpit --json
research-cockpit validate --root research_cockpit
research-cockpit build --root research_cockpit
research-cockpit ui --root research_cockpit
```

Wrapper scripts remain available for agents:

```powershell
python .agent\skills\research-cockpit\scripts\agent_bootstrap.py --root research_cockpit --json
```

If an agent is already executing from the plugin root, the shorter `python scripts\agent_bootstrap.py --root <path> --json` form is equivalent. From the research repo root, prefer the `.agent\skills\research-cockpit\scripts\...` form or the installed `research-cockpit` CLI.

Use `RESEARCH_COCKPIT_PYTHON` when command templates should prefer a specific interpreter.
