# Research Cockpit Agent Rules

## Source Of Truth

- Treat the project data root `research_cockpit/agents/*.yaml`, `research_cockpit/assignments/*.yaml`, `research_cockpit/coordinator_state.yaml`, `research_cockpit/current_state.yaml`, `research_cockpit/graph/nodes/*.yaml`, `research_cockpit/graph/interaction_events/**`, `research_cockpit/runs/*.yaml`, `research_cockpit/gate_results/*.yaml`, `research_cockpit/gate_results/*.json`, and `research_cockpit/artifact_records/*.yaml` as the truth source for structured state and append-only interaction history.
- Treat `research_cockpit/assignments/*.yaml` as the worker-local cursor and next-action source in multi-agent sessions.
- Treat `research_cockpit/coordinator_state.yaml` as coordinator/UI selection state.
- Treat `research_cockpit/current_state.yaml` as legacy/coordinator compatibility state, not the default worker cursor.
- Treat `research_cockpit/artifacts/*` as long-lived evidence payloads, not generated dashboard context.
- Treat `research_cockpit/artifact_records/*.yaml` as lightweight structured evidence metadata, not generated dashboard context.
- Treat `research_cockpit/artifact_migrations/*.yaml` as artifact demotion audit reports written by `compact-artifacts`.
- Treat `graph/interaction_log.yaml` as the immutable legacy interaction prefix after `graph/interaction_events/manifest.json` exists. New events append under `graph/interaction_events/`; use `migrate-interaction-log` instead of editing either backend.
- Treat `research_cockpit/dashboards/*` as generated context. Regenerate it only when a consumer needs fresh dashboard context or before coordinator/release/milestone handoff; ordinary worker verification does not require `build`.
- Do not infer current state from Markdown notes. Notes are long-form supporting records.

## Plugin Boundary

- This repository is the reusable Research Cockpit plugin.
- Runtime code lives in `src/research_cockpit/`.
- Public workflow commands live in the `research-cockpit` CLI.
- Agent-facing details are split under `capabilities/`.
- Project-specific research state belongs in the caller repository's `research_cockpit/`, not inside the plugin directory.
- For internal module boundaries, read `docs/internal-architecture.md`; for the rationale behind the layered layout, read `docs/decisions/0001-layered-plugin-architecture.md`.

## Read Order

Load exactly one role playbook, then choose one startup path instead of chaining context commands.

1. If assigned a specific assignment id, run `research-cockpit work open --root <data-root> --assignment <assignment_id> --compact --json`; reuse `--since <revision>` for polling.
2. If assigned a specific node id without an assignment id, run `research-cockpit context --root <data-root> --id <node_id> --view execution --compact --json`.
3. If the target is unknown or the task is global triage, run `research-cockpit bootstrap --root <data-root> --coordinator --json`.
4. If continuing an older minimal handoff, use `research-cockpit node-context --root <data-root> --id <node_id> --compact --json`.
5. Read `<data-root>/dashboards/agent_context_pack.json` and `<data-root>/dashboards/focus_context_pack.json` only when generated dashboard context or a broad focus scan is needed.
6. Use bounded search such as `research-cockpit search --root <data-root> --query "..." --json --limit 5 --source node` when more context is needed.
7. If one operation is missing, use `research-cockpit commands --role <role> --json --compact --name <command>`; do not run broad discovery during normal startup.

Use `--since <revision>` for repeated known-node polling. Do not run both `bootstrap` and a wider context view for normal known-node work. If the working directory is unreliable, use absolute `--root` paths.

## Write Rules

- Prefer `research-cockpit` commands over manual YAML edits for all supported operations.
- Use assignment-scoped mutating CLI commands with `--assignment <assignment_id>` when working as a downstream agent.
- Use coordinator/global mutating commands for focus, baseline, suggestions, and lifecycle cleanup only when explicitly acting as coordinator.
- Use `work open` as the assignment handoff; use `research-cockpit context --id <node_id> --view execution --compact --json` only for a known node without an assignment.
- Use `effective_baseline` from `context`/`node-context` as the default inherited option, decision, and artifact bundle; do not scan all accepted history unless asked.
- Do not directly set a decision to `accepted`; use `research-cockpit accept-decision --root <data-root> --id <decision_id>`.
- Do not execute a suggested command just because it appears in Action Guidance. Queue, dismiss, or complete suggestions only when asked.
- For ordinary experiment run output, use `ingest-artifact --json --compact --no-build`; record mode is the default and `--record-only` remains an explicit compatibility flag. Create a graph artifact only with `--promote --promotion-reason "..."`, or promote an existing record with `promote-artifact-record --promotion-reason "..."`.
- Start a planned/queued experiment and its run together with `create-run --status running --start-experiment --json --compact --no-build`.
- Close the run with one `complete-run --file <closeout.yaml> --json --compact --no-build` transaction. Its `experiment` and optional `next_experiment` blocks record the outcome, create at most one sibling follow-up, and move an assignment cursor. After ingest, use `artifact_record.existing_record_id`; do not repeat `complete-experiment`, `create-followup-experiment`, or `set-cursor` for the same closeout.
- Use `compact-artifacts --dry-run` before artifact demotion. Execute only one `can_demote` artifact with `--execute --id <artifact_id>`; this writes an artifact record and migration report but must not delete payload files.

## Verification

After a manual known-node YAML edit, or when a compact mutation reports additional verification is required, run changed-scope verification:

```sh
research-cockpit validate --root <data-root> --changed-node <node_id> --json
research-cockpit context --root <data-root> --id <node_id> --view execution --compact --json
```

For CLI mutations, accept `verified: true` with `additional_verification_required: false` as completed worker verification; do not run another validate/context cycle. Otherwise use only the reported changed scope.

Before coordinator merge, release, or research-stage closeout (`milestone_handoff`), run the full gate. An ordinary agent turn is not a milestone handoff:

```sh
research-cockpit validate --root <data-root> --json
research-cockpit build --root <data-root>
research-cockpit smoke --root <data-root> --json --progress
```

Default root `smoke` is compact for large roots. Use `smoke --scope changed --id <node_id> --json --progress` for one-node worker checks, and use `--full` only when explicitly diagnosing the older full subprocess workflow. `--progress` emits JSON lines to stderr; stdout remains a single machine-readable JSON payload.

## Environment

- Set `RESEARCH_COCKPIT_ROOT` when commands should default to a specific data root.
- If `research-cockpit bootstrap` reports missing modules, run `python -m pip install -e .` from the plugin root or use an interpreter with the listed requirements installed.
- Markdown files are UTF-8. In Windows PowerShell, use `Get-Content -Encoding UTF8 -Path <file>` if Chinese text appears garbled.
- Do not commit local absolute paths, usernames, virtual environment paths, or machine-specific interpreter paths.
