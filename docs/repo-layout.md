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
- `research-cockpit` CLI: stable command surface for agents and humans.
- `templates/`: initial state templates for new research repos.
- `examples/demo_research_cockpit/`: public demo data and generated context.
- `schemas/`: exported schema docs or machine-readable schemas as the API stabilizes.
- `docs/`: human and maintainer documentation.
- `requirements.txt`: script/runtime dependency list.
- `pyproject.toml`: installable package metadata and `research-cockpit` CLI entry point.

## Maintainer References

- `docs/internal-architecture.md`: internal Python module boundaries and dependency rules.
- `docs/decisions/0001-layered-plugin-architecture.md`: rationale for the layered plugin architecture and the temporary `model.py` compatibility facade.

## Source Module Map

`src/research_cockpit/` is organized around stable internal boundaries:

- `cli.py` and `command_registry.py`: public CLI dispatch and command metadata.
- `commands/`: command implementations. Commands validate input, call domain helpers, and perform controlled writes.
- `commands/_runtime.py`: shared command runtime for validated state loading and mutation finalization.
- `types.py`: core dataclasses, validation error type, node/status constants, and search constants.
- `storage.py`: YAML IO and path normalization helpers.
- `paths.py`: plugin and data-root discovery.
- `graph_core.py`: node loading, explicit edge loading, graph traversal, focus path derivation, and graph JSON.
- `resources.py`: node links, linked artifacts, and local resource row extraction.
- `interaction_log.py` and `graph_views.py`: sidecar state helpers.
- `decisions.py`, `option_workstreams.py`, and `suggestions.py`: domain logic.
- `search_index.py`: search index construction and query helpers.
- `node_onboarding.py`: single-node onboarding payloads for new agents.
- `context_packs.py`: agent/focus/current-state context payload builders and dashboard Markdown writer.
- `ui/`: Streamlit UI, view helpers, PyVis fallback, and React Flow component wrapper.
- `model.py`: compatibility facade for older imports; new code should prefer the focused modules above.

## Data Root Resolution

Commands resolve data root in this order:

1. Explicit `--root`.
2. `RESEARCH_COCKPIT_ROOT`.
3. Upward search from current working directory for `research_cockpit/`.
4. Plugin repo fallback `examples/demo_research_cockpit/`.

Run workflow commands with `research-cockpit` and pass `--root` when the data root is not obvious.
