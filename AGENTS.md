# Research Cockpit Agent Rules

## Source Of Truth

- Treat `research_cockpit/current_state.yaml` and `research_cockpit/graph/nodes/*.yaml` as the truth source.
- Treat `research_cockpit/dashboards/*` as generated context. Regenerate it with `python scripts\build_dashboard.py` after YAML changes.
- Do not infer state from Markdown notes. Notes are long-form supporting records.

## Package Boundaries

- The reusable Codex skill package lives in `skills/research-cockpit/`.
- Development logs, design notes, historical requirements, and v2 planning specs live in `dev/`.
- Runtime code remains at the repository root in `cockpit/`, `scripts/`, `ui/`, and `research_cockpit/`.
- When preparing a pure skill export, start from `skills/research-cockpit/` and include only the referenced runtime files intentionally.

## Read Order

1. Run `python scripts\agent_bootstrap.py --json`.
2. Read `research_cockpit/dashboards/agent_context_pack.json`.
3. Read `research_cockpit/dashboards/focus_context_pack.json` for local focus.
4. Use `python scripts\search_knowledge.py --query "..." --json` when more context is needed.
5. Use `python scripts\list_agent_commands.py --json` to choose safe workflow scripts.

For subagent validation, pass `skills/research-cockpit/` as the skill path and run the task from repository root.

## Write Rules

- Prefer scripts over manual YAML edits for all supported operations.
- Use mutating scripts for focus, status, findings, decisions, notes, suggestions, and lifecycle cleanup.
- Do not directly set a decision to `accepted`; use `python scripts\accept_decision.py --id <decision_id>`.
- Do not execute a suggested command just because it appears in Action Guidance. Queue, dismiss, or complete suggestions only when asked.

## Verification

After code or YAML changes, run:

```powershell
python -m unittest discover -s tests
python scripts\validate_cockpit.py
python scripts\build_dashboard.py
```

## Environment

- Default command templates use `python`.
- Set `RESEARCH_COCKPIT_PYTHON` to override the interpreter in generated command templates.
- Do not commit local absolute paths, usernames, virtual environment paths, or machine-specific interpreter paths.
