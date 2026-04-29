# Repository Layout Reference

Use this reference when packaging, exporting, or validating the Research Cockpit plugin layout.

## Plugin Boundary

The repository root is the plugin boundary. Copy or vendor this repository into a research repo when installing it as an agent skill:

```text
research-repo/
  .agent/skills/research-cockpit/
  research_cockpit/
```

The plugin stores reusable code and tools. The research repo stores project-specific state in its own `research_cockpit/` directory.

## Plugin Contents

- `SKILL.md`: thin Codex skill entry.
- `AGENTS.md`: rules for coding agents operating inside the plugin.
- `capabilities/`: detailed agent-facing capability files.
- `src/research_cockpit/`: Python runtime, model helpers, Streamlit UI, and component wrapper.
- `scripts/`: stable wrapper scripts for agents and humans.
- `templates/`: initial state templates for new research repos.
- `examples/demo_research_cockpit/`: public demo data and generated context.
- `schemas/`: exported schema docs or machine-readable schemas as the API stabilizes.
- `docs/`: human and maintainer documentation.
- `tests/`: plugin regression tests.
- `requirements.txt`: script/runtime dependency list.
- `pyproject.toml`: installable package metadata and `research-cockpit` CLI entry point.

## Data Root Resolution

Commands resolve data root in this order:

1. Explicit `--root`.
2. `RESEARCH_COCKPIT_ROOT`.
3. Upward search from current working directory for `research_cockpit/`.
4. Plugin repo fallback `examples/demo_research_cockpit/`.

Run workflow commands from the plugin root, or call wrapper scripts by absolute path and pass `--root`.
