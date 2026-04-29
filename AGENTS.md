# Research Cockpit Agent Rules

## Source Of Truth

- Treat the project data root `research_cockpit/current_state.yaml` and `research_cockpit/graph/nodes/*.yaml` as the truth source.
- Treat `research_cockpit/dashboards/*` as generated context. Regenerate it with `python scripts\build_dashboard.py --root <data-root>` after YAML changes.
- Do not infer current state from Markdown notes. Notes are long-form supporting records.

## Plugin Boundary

- This repository is the reusable Research Cockpit plugin.
- Runtime code lives in `src/research_cockpit/`.
- Thin command wrappers live in `scripts/`.
- Agent-facing details are split under `capabilities/`.
- Project-specific research state belongs in the caller repository's `research_cockpit/`, not inside the plugin directory.

## Read Order

1. Run `python scripts\agent_bootstrap.py --root <data-root> --json`.
2. Read `<data-root>/dashboards/agent_context_pack.json`.
3. Read `<data-root>/dashboards/focus_context_pack.json` for local focus.
4. Use `python scripts\search_knowledge.py --root <data-root> --query "..." --json` when more context is needed.
5. Use `python scripts\list_agent_commands.py --json` to choose safe workflow scripts.

If the working directory is unreliable, call wrapper scripts by absolute path and always pass `--root`.

## Write Rules

- Prefer scripts over manual YAML edits for all supported operations.
- Use mutating scripts for focus, status, findings, decisions, notes, suggestions, and lifecycle cleanup.
- Do not directly set a decision to `accepted`; use `python scripts\accept_decision.py --root <data-root> --id <decision_id>`.
- Do not execute a suggested command just because it appears in Action Guidance. Queue, dismiss, or complete suggestions only when asked.

## Verification

After code or YAML changes, run:

```powershell
python scripts\validate_cockpit.py --root <data-root>
python scripts\build_dashboard.py --root <data-root>
python scripts\skill_smoke_test.py --root <data-root> --json
```

For plugin development, run:

```powershell
python -m unittest discover -s tests
python dev\scripts\run_skill_release_check.py --json --skip-mutating
```

## Environment

- Default command templates use `python`.
- Set `RESEARCH_COCKPIT_ROOT` when commands should default to a specific data root.
- Set `RESEARCH_COCKPIT_PYTHON` to override the interpreter in generated command templates.
- If `agent_bootstrap.py` reports missing modules, run `python -m pip install -r requirements.txt` from the plugin root or use an interpreter with the listed requirements installed.
- Do not commit local absolute paths, usernames, virtual environment paths, or machine-specific interpreter paths.
