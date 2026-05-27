# Research Cockpit Agent Rules

## Source Of Truth

- Treat the project data root `research_cockpit/current_state.yaml`, `research_cockpit/graph/nodes/*.yaml`, `research_cockpit/runs/*.yaml`, `research_cockpit/gate_results/*.yaml`, and `research_cockpit/gate_results/*.json` as the truth source for structured state.
- Treat `research_cockpit/artifacts/*` as long-lived evidence payloads, not generated dashboard context.
- Treat `research_cockpit/dashboards/*` as generated context. Regenerate it with `research-cockpit build --root <data-root>` after YAML changes.
- Do not infer current state from Markdown notes. Notes are long-form supporting records.

## Plugin Boundary

- This repository is the reusable Research Cockpit plugin.
- Runtime code lives in `src/research_cockpit/`.
- Public workflow commands live in the `research-cockpit` CLI.
- Agent-facing details are split under `capabilities/`.
- Project-specific research state belongs in the caller repository's `research_cockpit/`, not inside the plugin directory.
- For internal module boundaries, read `docs/internal-architecture.md`; for the rationale behind the layered layout, read `docs/decisions/0001-layered-plugin-architecture.md`.

## Read Order

1. Run `research-cockpit bootstrap --root <data-root> --json`.
2. Read `<data-root>/dashboards/agent_context_pack.json`.
3. Read `<data-root>/dashboards/focus_context_pack.json` for local focus.
4. If assigned a specific node id, run `research-cockpit node-context --root <data-root> --id <node_id> --json`.
5. Use `research-cockpit search --root <data-root> --query "..." --json` when more context is needed.
6. Use `research-cockpit commands --json` to choose safe workflow commands.

If the working directory is unreliable, use absolute `--root` paths.

## Write Rules

- Prefer `research-cockpit` commands over manual YAML edits for all supported operations.
- Use mutating CLI commands for focus, baseline, status, findings, decisions, notes, suggestions, run records, gate results, and lifecycle cleanup.
- Use `research-cockpit node-context` as the shortest read-only handoff when continuing work from a known node id.
- Use `effective_baseline` from `context`/`node-context` as the default inherited option, decision, and artifact bundle; do not scan all accepted history unless asked.
- Do not directly set a decision to `accepted`; use `research-cockpit accept-decision --root <data-root> --id <decision_id>`.
- Do not execute a suggested command just because it appears in Action Guidance. Queue, dismiss, or complete suggestions only when asked.

## Verification

After code or YAML changes, run:

```sh
research-cockpit validate --root <data-root>
research-cockpit build --root <data-root>
research-cockpit smoke --root <data-root> --json
```

For plugin development, run:

```sh
python -m unittest discover -s tests
python dev/scripts/run_skill_release_check.py --json --skip-mutating
```

## Environment

- Set `RESEARCH_COCKPIT_ROOT` when commands should default to a specific data root.
- If `research-cockpit bootstrap` reports missing modules, run `python -m pip install -e .` from the plugin root or use an interpreter with the listed requirements installed.
- Do not commit local absolute paths, usernames, virtual environment paths, or machine-specific interpreter paths.
