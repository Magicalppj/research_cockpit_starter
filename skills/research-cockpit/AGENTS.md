# Research Cockpit Agent Rules

## Source Of Truth

- Treat `research_cockpit/current_state.yaml` and `research_cockpit/graph/nodes/*.yaml` as the truth source.
- Treat `research_cockpit/dashboards/*` as generated context. Regenerate it with `python scripts\build_dashboard.py` after YAML changes.
- Do not infer state from Markdown notes. Notes are long-form supporting records.

## Package Boundaries

- This directory is the reusable Codex skill package.
- Runtime code, scripts, UI, cockpit data, generated context, requirements, skill metadata, and agent rules stay inside this directory.
- Development logs, design notes, historical requirements, specs, and tests stay outside the package under the development repository's `dev/` directory.
- When preparing a pure skill export, copy this directory as-is.

## Read Order

1. Run `python scripts\agent_bootstrap.py --json`.
2. Read `research_cockpit/dashboards/agent_context_pack.json`.
3. Read `research_cockpit/dashboards/focus_context_pack.json` for local focus.
4. Use `python scripts\search_knowledge.py --query "..." --json` when more context is needed.
5. Use `python scripts\list_agent_commands.py --json` to choose safe workflow scripts.

If the working directory is unreliable, call scripts by absolute path. Scripts derive the package root from their own location, so `python C:\path\to\research-cockpit\scripts\agent_bootstrap.py --json` is valid.

For subagent validation, pass this directory as the skill path and run the task from this directory root.

## Write Rules

- Prefer scripts over manual YAML edits for all supported operations.
- Use mutating scripts for focus, status, findings, decisions, notes, suggestions, and lifecycle cleanup.
- Do not directly set a decision to `accepted`; use `python scripts\accept_decision.py --id <decision_id>`.
- Do not execute a suggested command just because it appears in Action Guidance. Queue, dismiss, or complete suggestions only when asked.

## Verification

After code or YAML changes, run:

```powershell
python scripts\skill_smoke_test.py --json
python scripts\validate_cockpit.py
python scripts\build_dashboard.py
```

In the development repository, run the external test suite from the repository root with `python -m unittest discover -s dev\tests`.

## Environment

- Default command templates use `python`.
- Set `RESEARCH_COCKPIT_PYTHON` to override the interpreter in generated command templates.
- If `agent_bootstrap.py` reports missing modules, run `python -m pip install -r requirements.txt` or use an interpreter with the listed requirements installed.
- Do not commit local absolute paths, usernames, virtual environment paths, or machine-specific interpreter paths.
