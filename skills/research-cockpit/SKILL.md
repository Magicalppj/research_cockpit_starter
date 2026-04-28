---
name: research-cockpit
description: Use this skill to inspect, maintain, and update a YAML-backed research cockpit with generated dashboard context for agents.
---

# Research Cockpit Skill

Use this repository as a local research cockpit skill when a task depends on the current research graph, focus state, experiment findings, decisions, notes, or action suggestions.

Run commands from the repository root. This skill package is stored at `skills/research-cockpit/`; repository layout and packaging boundaries are summarized in `references/repo-layout.md`.

## Start

```powershell
python scripts\agent_bootstrap.py --json
```

If dashboard files are stale or missing:

```powershell
python scripts\agent_bootstrap.py --json --build
```

## Read Order

1. Bootstrap JSON.
2. `research_cockpit/dashboards/agent_context_pack.json`.
3. `research_cockpit/dashboards/focus_context_pack.json`.
4. `python scripts\search_knowledge.py --query "..." --json`.
5. `python scripts\list_agent_commands.py --json`.

## Write Boundary

- YAML is the truth source.
- Use scripts for supported writes.
- Regenerate dashboard/context after writes.
- Do not treat dashboard JSON or Markdown notes as authoritative state.

## Common Commands

```powershell
python scripts\validate_cockpit.py
python scripts\build_dashboard.py
python scripts\suggest_next_actions.py --json
python scripts\search_knowledge.py --query "..." --json
python scripts\record_finding.py --experiment <id> --statement "..." --confidence medium
python scripts\accept_decision.py --id <decision_id>
```

Set `RESEARCH_COCKPIT_PYTHON` if command templates should use a custom interpreter.

## Subagent Validation

When asking another agent to test this skill, pass the folder path `skills/research-cockpit/` plus a concrete cockpit task, then let it run `agent_bootstrap.py` before choosing commands.
