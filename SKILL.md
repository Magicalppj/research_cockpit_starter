---
name: research-cockpit
description: Use this skill to read, validate, update, and summarize project-local Research Cockpit state stored in a repository `research_cockpit/` directory.
---

# Research Cockpit

Research Cockpit 是一个项目本地研究状态插件。插件代码位于本目录，研究数据默认位于调用方仓库根目录的 `research_cockpit/`。

## Startup Contract

1. Resolve the data root:
   - Prefer an explicit `--root <path-to-research_cockpit>`.
   - If omitted, commands search from the current working directory upward for `research_cockpit/`.
   - In this plugin repo only, commands fall back to `examples/demo_research_cockpit/`.
2. Run bootstrap before making decisions:

```powershell
python .agent\skills\research-cockpit\scripts\agent_bootstrap.py --root research_cockpit --build --json
```

If the current working directory is already the plugin root, use `python scripts\agent_bootstrap.py --root <path> --build --json` instead.

3. Read generated context before editing:
   - `research_cockpit/dashboards/agent_context_pack.json`
   - `research_cockpit/dashboards/focus_context_pack.json`
4. Use scripts for mutating operations. Do not bypass helpers by hand-editing YAML unless the relevant capability explicitly says YAML repair is the right path.

## Capability Routing

- Graph state, data files, saved graph views, and interaction log: `capabilities/graph-state.md`
- Current focus, context packs, search, and startup read order: `capabilities/focus-context.md`
- Creating nodes, status updates, suggestions, and safe YAML boundaries: `capabilities/node-management.md`
- Experiments, findings, evidence, and option workstream reports: `capabilities/experiment-tracking.md`
- Decisions, ADR-style acceptance, checklist repair, promote/accept flows: `capabilities/decision-adr.md`
- Streamlit UI, React Flow graph, refresh behavior, and frontend build rules: `capabilities/ui-dashboard.md`
- Installation shape, CLI, wrappers, environment variables, and agent integration: `capabilities/integrations.md`
- Validation failures, release checks, dependency issues, and recovery: `capabilities/troubleshooting.md`

Read only the capability files needed for the current task.

## Core Commands

```powershell
python .agent\skills\research-cockpit\scripts\validate_cockpit.py --root research_cockpit
python .agent\skills\research-cockpit\scripts\build_dashboard.py --root research_cockpit
python .agent\skills\research-cockpit\scripts\search_knowledge.py --root research_cockpit --query "..." --json
python .agent\skills\research-cockpit\scripts\suggest_next_actions.py --root research_cockpit --json
python .agent\skills\research-cockpit\scripts\list_agent_commands.py --json
```

After mutating state, validate and rebuild unless the script already did so:

```powershell
python .agent\skills\research-cockpit\scripts\validate_cockpit.py --root research_cockpit
python .agent\skills\research-cockpit\scripts\build_dashboard.py --root research_cockpit
```

## Write Boundary

Allowed truth-source writes are under:

- `research_cockpit/current_state.yaml`
- `research_cockpit/graph/nodes/*.yaml`
- `research_cockpit/graph/edges.yaml`
- `research_cockpit/graph/graph_views.yaml`
- `research_cockpit/graph/interaction_log.yaml`
- `research_cockpit/notes/**/*.md`

Agents should normally write these files through `scripts/` commands. Direct YAML repair is a last-resort structural fix and must be followed by validation and dashboard rebuild.

Generated files under `research_cockpit/dashboards/` must be rebuilt, not hand-authored.
