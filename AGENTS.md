# Research Cockpit Agent Rules

## Source Of Truth

- Treat the project data root `research_cockpit/agents/*.yaml`, `research_cockpit/assignments/*.yaml`, `research_cockpit/coordinator_state.yaml`, `research_cockpit/current_state.yaml`, `research_cockpit/graph/nodes/*.yaml`, `research_cockpit/graph/interaction_events/**`, `research_cockpit/runs/*.yaml`, `research_cockpit/gate_results/*.yaml`, `research_cockpit/gate_results/*.json`, `research_cockpit/artifact_records/*.yaml`, and `research_cockpit/handoffs/*.yaml` as the truth source for structured state and append-only interaction history.
- Treat `research_cockpit/assignments/*.yaml` as the worker-local cursor and next-action source in multi-agent sessions.
- Treat `research_cockpit/coordinator_state.yaml` as coordinator/UI selection state.
- Treat `research_cockpit/current_state.yaml` as legacy/coordinator compatibility state, not the default worker cursor.
- Treat `research_cockpit/artifacts/*` as long-lived evidence payloads, not generated dashboard context.
- Treat `research_cockpit/artifact_records/*.yaml` as lightweight structured evidence metadata, not generated dashboard context.
- Treat `research_cockpit/artifact_migrations/*.yaml` as artifact demotion audit reports written by `compact-artifacts`.
- Treat `research_cockpit/handoffs/*.yaml` as immutable operation-id-scoped milestone reports. Retry the same request with the same operation id; use a new operation id after truth or blocker state changes.
- Treat `graph/interaction_log.yaml` as the immutable legacy interaction prefix after `graph/interaction_events/manifest.json` exists. New events append under `graph/interaction_events/`; use `migrate-interaction-log` instead of editing either backend.
- Treat `research_cockpit/dashboards/*` as generated context. Regenerate it only when a consumer explicitly needs fresh dashboard context; `coord handoff` performs its own single build and ordinary worker verification does not require `build`.
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

1. If explicitly assigned a review assignment, run `research-cockpit review open --root <data-root> --assignment <review_id> --compact --json`; do not separately open producer context.
2. If assigned worker execution, run `research-cockpit work open --root <data-root> --assignment <assignment_id> --compact --json`; reuse `--since <revision>` for polling.
3. If assigned a specific node id without an assignment id, run `research-cockpit context --root <data-root> --id <node_id> --view execution --compact --json`.
4. If the target is unknown or the task is global assignment triage, run `research-cockpit coord overview --root <data-root> --json --compact --limit 20`; use its filters, `next_page`, and `--since` revision instead of full bootstrap.
5. If continuing an older minimal handoff, use `research-cockpit node-context --root <data-root> --id <node_id> --compact --json`.
6. Read `<data-root>/dashboards/agent_context_pack.json` and `<data-root>/dashboards/focus_context_pack.json` only when generated dashboard context or a broad focus scan is needed.
7. Use bounded search such as `research-cockpit search --root <data-root> --query "..." --json --limit 5 --source node` when more context is needed.
8. If one operation is missing, use `research-cockpit commands --role <role> --json --compact --name <command>`; do not run broad discovery during normal startup.

Use `--since <revision>` for repeated known-node polling. Do not run both `bootstrap` and a wider context view for normal known-node work. If the working directory is unreliable, use absolute `--root` paths.

## Write Rules

- Prefer `research-cockpit` commands over manual YAML edits for all supported operations.
- Use assignment-scoped mutating CLI commands with `--assignment <assignment_id>` when working as a downstream agent.
- If an opened packet is unclaimed, use one `work claim --return-packet` call and continue from its returned packet; do not reopen it immediately.
- Use coordinator/global mutating commands for focus, baseline, suggestions, and lifecycle cleanup only when explicitly acting as coordinator.
- Use `review open` for review assignments and `work open` for worker assignments; use `research-cockpit context --id <node_id> --view execution --compact --json` only for a known node without an assignment.
- Use `effective_baseline` from `context`/`node-context` as the default inherited option, decision, and artifact bundle; do not scan all accepted history unless asked.
- Do not directly set a decision to `accepted`; use `research-cockpit accept-decision --root <data-root> --id <decision_id>`.
- Do not execute a suggested command just because it appears in Action Guidance. Queue, dismiss, or complete suggestions only when asked.
- Give every mutating role-facade call a stable operation id in its flags or structured input; reuse it only for an exact retry of the same request.
- Treat `work renew` as recovery/diagnostic only. Normal mutations and launcher heartbeat renew the lease without another model-visible command.
- Start a claimed assignment with `work start`; it generates the run id and atomically starts the experiment while renewing the lease.
- Use `create-run --status running --start-experiment` only as a compatibility route for legacy data without an active lease.
- Close assigned work with one `work close --file <closeout.yaml> --json --compact` transaction. It records run/finding/result, optional same-scope `next_experiment`, assignment cursor, and lease transition. Do not repeat `complete-run`, `complete-experiment`, `create-followup-experiment`, or `set-cursor` for that closeout.
- Put a final payload directory in `work_close_v1.evidence_inputs` so staging, hash, artifact record, and closeout remain one invocation. Use standalone `ingest-artifact --json --compact --no-build` only for incremental/streaming evidence that must be durable before close; reference its record with `artifact_record.existing_record_id`.
- Create a graph artifact only with `--promote --promotion-reason "..."`, or promote an existing record with `promote-artifact-record --promotion-reason "..."`.
- A `new_branch` closeout proposal never creates an assignment automatically; only the coordinator evaluates and assigns it.
- Reviewers use `review open` then `review report`; report writes only the reviewer assignment. Coordinators use `coord review` to update producer review metadata without rewriting either Evidence Bundle.
- Use `compact-artifacts --dry-run` before artifact demotion. Execute only one `can_demote` artifact with `--execute --id <artifact_id>`; this writes an artifact record and migration report but must not delete payload files.

## Verification

After a manual known-node YAML edit, or when a compact mutation reports additional verification is required, run changed-scope verification:

```sh
research-cockpit validate --root <data-root> --changed-node <node_id> --json
research-cockpit context --root <data-root> --id <node_id> --view execution --compact --json
```

For role-facade mutations, accept `verification.status: internally_verified` with `additional_verification_required: false`; compatibility mutations may report `verified: true` with the same flag false. Do not run another validate/context cycle after either receipt. Otherwise use only the reported changed scope.

Before coordinator merge, release, or research-stage closeout, create one `coord_handoff_v1` input and run the single milestone entry point:

```sh
research-cockpit coord handoff --print-schema
research-cockpit coord handoff --root <data-root> --file <handoff.yaml> --json --compact --progress
```

`coord handoff` captures one root revision, reuses one full validation state for build and compact smoke, checks lifecycle blockers, and writes one revision-bound report. Do not run standalone full `validate`, `build`, or `smoke` before it. A blocked report is durable; resolve blockers and retry with a new operation id. An exact transport retry reuses the original operation id and receipt.

Standalone full `validate`, `build`, and `smoke` remain diagnostic commands. Default root `smoke` is compact for large roots. Use `smoke --scope changed --id <node_id> --json --progress` for one-node diagnostics, and use `--full` only when explicitly diagnosing the older full subprocess workflow. `--progress` emits JSON lines to stderr; stdout remains a single machine-readable JSON payload.

## Environment

- Set `RESEARCH_COCKPIT_ROOT` when commands should default to a specific data root.
- If `research-cockpit bootstrap` reports missing modules, run `python -m pip install -e .` from the plugin root or use an interpreter with the listed requirements installed.
- Markdown files are UTF-8. In Windows PowerShell, use `Get-Content -Encoding UTF8 -Path <file>` if Chinese text appears garbled.
- Do not commit local absolute paths, usernames, virtual environment paths, or machine-specific interpreter paths.
