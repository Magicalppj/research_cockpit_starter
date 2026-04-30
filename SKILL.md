---
name: research-cockpit
description: Use this skill to read, validate, update, and summarize project-local Research Cockpit state stored in a repository `research_cockpit/` directory.
---

# Research Cockpit

Research Cockpit 是一个项目本地研究状态插件。插件代码位于本目录，研究数据默认位于调用方仓库根目录的 `research_cockpit/`。

## Startup Contract

1. Resolve the data root:
   - Prefer an explicit `--root <path-to-research_cockpit>`.
   - Use an absolute `--root` when the agent shell may not preserve the expected current working directory.
   - If omitted, commands search from the current working directory upward for `research_cockpit/`.
   - In this plugin repo only, commands fall back to `examples/demo_research_cockpit/`.
   - If the caller repository has no data root yet, initialize one with `research-cockpit init --root research_cockpit` from the caller repository root.
2. Run bootstrap before making decisions:

```sh
research-cockpit bootstrap --root research_cockpit --build --json
```

If the current working directory is already the plugin root, use `research-cockpit bootstrap --root <path> --build --json` instead.

If the `research-cockpit` console script is unavailable but the package is installed, use `python -m research_cockpit.cli <command>` with the same Python environment.

3. Read generated context before editing:
   - `research_cockpit/dashboards/agent_context_pack.json`
   - `research_cockpit/dashboards/focus_context_pack.json`
4. Use `research-cockpit` commands for mutating operations. Do not bypass helpers by hand-editing YAML unless the relevant capability explicitly says YAML repair is the right path.

Default research graph reasoning centers on `stage`, `problem`, `option`, `experiment`, and `decision`. Treat `artifact` nodes as supporting evidence/resources by default; do not create an artifact node for an ordinary file, config, JSON, or result unless that artifact is itself a long-lived research object or key deliverable.

Status semantics:

- `stage`: `planned`, `active`, `blocked`, `done`.
- `problem`: `open`, `active`, `blocked`, `resolved`, `parked`.
- `option`: `open`, `active`, `promising`, `rejected`, `accepted`, `paused`, `parked`.
- `experiment`: `planned`, `queued`, `running`, `done`, `failed`, `cancelled`.
- `decision`: `proposed`, `accepted`, `superseded`, `rejected`.
- `artifact`: `draft`, `planned`, `active`, `done`, `superseded`, `deprecated`, `archived`.

Use `promising` only for an `option` that has positive signal but is not yet accepted. Do not set decisions to `accepted` directly; use `research-cockpit accept-decision`.

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

```sh
research-cockpit validate --root research_cockpit
research-cockpit build --root research_cockpit
research-cockpit search --root research_cockpit --query "..." --json
research-cockpit suggest-next-actions --root research_cockpit --json
research-cockpit commands --json
```

After mutating state, validate and rebuild unless the script already did so:

```sh
research-cockpit validate --root research_cockpit
research-cockpit build --root research_cockpit
```

## Write Boundary

Allowed truth-source writes are under:

- `research_cockpit/current_state.yaml`
- `research_cockpit/graph/nodes/*.yaml`
- `research_cockpit/graph/edges.yaml`
- `research_cockpit/graph/graph_views.yaml`
- `research_cockpit/graph/interaction_log.yaml`
- `research_cockpit/notes/**/*.md`

Agents should normally write these files through `research-cockpit` CLI commands. Direct YAML repair is a last-resort structural fix and must be followed by validation and dashboard rebuild.

Generated files under `research_cockpit/dashboards/` must be rebuilt, not hand-authored.

Never create or update project research state inside the plugin directory itself unless you are intentionally editing `examples/demo_research_cockpit/` for plugin development.
