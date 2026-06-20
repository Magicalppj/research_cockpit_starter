# Research Cockpit Agent Rules

## Source Of Truth

- Treat the project data root `research_cockpit/agents/*.yaml`, `research_cockpit/assignments/*.yaml`, `research_cockpit/coordinator_state.yaml`, `research_cockpit/current_state.yaml`, `research_cockpit/graph/nodes/*.yaml`, `research_cockpit/runs/*.yaml`, `research_cockpit/gate_results/*.yaml`, and `research_cockpit/gate_results/*.json` as the truth source for structured state.
- Treat `research_cockpit/assignments/*.yaml` as the worker-local cursor and next-action source in multi-agent sessions.
- Treat `research_cockpit/coordinator_state.yaml` as coordinator/UI selection state.
- Treat `research_cockpit/current_state.yaml` as legacy/coordinator compatibility state, not the default worker cursor.
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

Choose one startup path instead of chaining every context command.

1. If assigned a specific assignment id, run `research-cockpit bootstrap --root <data-root> --assignment <assignment_id> --json`.
2. If assigned a specific node id without an assignment id, run `research-cockpit context --root <data-root> --id <node_id> --with-bootstrap --with-artifacts --compact --json`.
3. If the target is unknown or the task is global triage, run `research-cockpit bootstrap --root <data-root> --coordinator --json`.
4. If continuing an older minimal handoff, use `research-cockpit node-context --root <data-root> --id <node_id> --compact --json`.
5. Read `<data-root>/dashboards/agent_context_pack.json` and `<data-root>/dashboards/focus_context_pack.json` only when generated dashboard context or a broad focus scan is needed.
6. Use bounded search such as `research-cockpit search --root <data-root> --query "..." --json --limit 5 --source node` when more context is needed.
7. Use `research-cockpit commands --json --compact` to choose safe workflow commands.

Do not run both `bootstrap` and `context --with-bootstrap` for normal known-node work. If the working directory is unreliable, use absolute `--root` paths.

## Write Rules

- Prefer `research-cockpit` commands over manual YAML edits for all supported operations.
- Use assignment-scoped mutating CLI commands with `--assignment <assignment_id>` when working as a downstream agent.
- Use coordinator/global mutating commands for focus, baseline, suggestions, and lifecycle cleanup only when explicitly acting as coordinator.
- Use `research-cockpit context --id <node_id> --with-bootstrap --with-artifacts --compact --json` as the default read-only handoff when continuing work from a known node id.
- Use `effective_baseline` from `context`/`node-context` as the default inherited option, decision, and artifact bundle; do not scan all accepted history unless asked.
- Do not directly set a decision to `accepted`; use `research-cockpit accept-decision --root <data-root> --id <decision_id>`.
- Do not execute a suggested command just because it appears in Action Guidance. Queue, dismiss, or complete suggestions only when asked.

## Verification

After code or YAML changes, run:

```sh
research-cockpit validate --root <data-root>
research-cockpit build --root <data-root>
research-cockpit smoke --root <data-root> --json --progress
```

Default `smoke` is compact for large roots. Use `--full` only when explicitly diagnosing the older full subprocess workflow.

For plugin development, run:

```sh
python -m unittest discover -s tests
python dev/scripts/run_skill_release_check.py --json --skip-mutating
```

## Environment

- Set `RESEARCH_COCKPIT_ROOT` when commands should default to a specific data root.
- If `research-cockpit bootstrap` reports missing modules, run `python -m pip install -e .` from the plugin root or use an interpreter with the listed requirements installed.
- Markdown files are UTF-8. In Windows PowerShell, use `Get-Content -Encoding UTF8 -Path <file>` if Chinese text appears garbled.
- Do not commit local absolute paths, usernames, virtual environment paths, or machine-specific interpreter paths.
