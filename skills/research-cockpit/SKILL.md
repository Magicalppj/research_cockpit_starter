---
name: research-cockpit
description: Use this skill to inspect, maintain, and update a YAML-backed research cockpit with generated dashboard context for agents.
---

# Research Cockpit Skill

Use this repository as a local research cockpit skill when a task depends on the current research graph, focus state, experiment findings, decisions, notes, or action suggestions.

Run commands from this skill directory root. In this development repository the path is `skills/research-cockpit/`; when exported, this directory is the whole package. Layout and packaging boundaries are summarized in `references/repo-layout.md`.

## Start

```powershell
python scripts\agent_bootstrap.py --json
```

If the agent cannot reliably set its working directory, invoke the script by absolute path. The script derives the package root from its own file location:

```powershell
python C:\path\to\research-cockpit\scripts\agent_bootstrap.py --json
```

If dashboard files are stale or missing:

```powershell
python scripts\agent_bootstrap.py --json --build
```

If `agent_bootstrap.py` reports missing Python modules, install dependencies with `python -m pip install -r requirements.txt` or set `RESEARCH_COCKPIT_PYTHON` to an interpreter that has the requirements installed.

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
python scripts\skill_smoke_test.py --json
python scripts\suggest_next_actions.py --json
python scripts\search_knowledge.py --query "..." --json
python scripts\record_finding.py --experiment <id> --statement "..." --confidence medium
python scripts\accept_decision.py --id <decision_id>
```

Set `RESEARCH_COCKPIT_PYTHON` if command templates should use a custom interpreter.

## Subagent Validation

When asking another agent to test this skill, pass this folder path plus a concrete cockpit task, then let it run `agent_bootstrap.py` before choosing commands.
