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
   - If the caller repository has no data root yet, initialize one with `research-cockpit init --root research_cockpit` from the caller repository root. Use `research-cockpit init --root research_cockpit --build --json` when the next step will read generated context packs.
2. Run read-only bootstrap before making decisions:

```sh
research-cockpit bootstrap --root research_cockpit --json
```

If the current working directory is already the plugin root, use `research-cockpit bootstrap --root <path> --json` instead.

If the `research-cockpit` console script is unavailable but the package is installed, use `python -m research_cockpit.cli <command>` with the same Python environment. For `node-context`, add `--command-style python` so returned command drafts use the module entrypoint too.

3. If the task names a specific node id, run the shortest handoff before broader reads:

```sh
research-cockpit context --root research_cockpit --node <node_id> --with-bootstrap --with-artifacts --compact --json
```

For known-node continuation, prefer compact `context` when you need node, focus, artifact, and validation context in one payload. Use `bootstrap --json` plus compact `node-context` when you only need the minimal older handoff. Read `agent_context_pack.json` or `focus_context_pack.json` only when you need global state, generated dashboard context, or a broader focus scan.
4. If generated dashboards are missing or stale and the task allows generated-file writes, run `research-cockpit build --root research_cockpit`. Do not run `bootstrap --build` or `build` for read-only onboarding tasks.
5. Use `research-cockpit` commands for mutating operations. Do not bypass helpers by hand-editing YAML unless the relevant capability explicitly says YAML repair is the right path.

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
research-cockpit add-node --root research_cockpit --id <node_id> --type <type> --title "..." --parent <parent_id> --dry-run --json --show-diff
research-cockpit apply-graph-plan --print-schema
research-cockpit apply-graph-plan --root research_cockpit --file graph_update.yaml --dry-run --json --show-diff
research-cockpit create-workstream --print-schema
research-cockpit create-workstream --root research_cockpit --file workstream.yaml --dry-run --json --show-diff
research-cockpit context --root research_cockpit --node <node_id> --with-bootstrap --with-artifacts --compact --json
research-cockpit create-artifact --root research_cockpit --id <artifact_id> --title "..." --path <path> --link-to <node_id> --dry-run --json --show-diff
research-cockpit create-artifact --print-schema
research-cockpit create-artifact --root research_cockpit --file artifact.yaml --dry-run --json --show-diff
research-cockpit link-artifact --root research_cockpit --artifact <artifact_id> --to <node_id> --dry-run --json --show-diff
research-cockpit complete-experiment --root research_cockpit --id <experiment_id> --finding "..." --confidence medium --no-build
research-cockpit complete-experiments --print-schema
research-cockpit complete-experiments --root research_cockpit --file findings.yaml --dry-run --json --show-diff
research-cockpit update-finding --root research_cockpit --experiment <experiment_id> --finding-id <finding_id> --statement "..." --dry-run --json --show-diff
research-cockpit finalize-workstream --print-schema
research-cockpit finalize-workstream --root research_cockpit --file finalize.yaml --dry-run --json --show-diff
research-cockpit finalize-workstream --root research_cockpit --option <option_id> --status accepted --problem-status resolved --report --dry-run --json --show-diff
research-cockpit option-workstream-context --root research_cockpit --id <option_id> --compact --json
research-cockpit update-node-fields --root research_cockpit --id <node_id> --question "..." --tag <tag> --no-build
research-cockpit sync-focus-actions --root research_cockpit --from-node <node_id> --dry-run --json --show-diff
research-cockpit node-context --root research_cockpit --id <node_id> --compact --json
research-cockpit search --root research_cockpit --query "..." --json
research-cockpit suggest-next-actions --root research_cockpit --json
research-cockpit commands --json --compact
```

`node-context` is read-only and computed from truth-source YAML. Use `--compact --json` as the shortest onboarding path when a human asks you to continue from one node; the full `--json` output remains available when you need parent chain, relations, resources, recent interactions, and type-specific traces. Command drafts include `--root`; add `--command-style python` when the console script is unavailable.
The combined `context` payload separates `target_context` from `current_global_focus`; use `context_boundary.warning` to notice when a target node differs from the global focus.

When making several related state changes, pass `--no-build` to each supported mutating command, then validate and rebuild once:

```sh
research-cockpit validate --root research_cockpit
research-cockpit build --root research_cockpit
```

For several node creations or rich field edits, prefer a single plan file:

```sh
research-cockpit apply-graph-plan --root research_cockpit --file graph_update.yaml --dry-run --json --show-diff
research-cockpit apply-graph-plan --root research_cockpit --file graph_update.yaml --no-build
research-cockpit validate --root research_cockpit --json
research-cockpit build --root research_cockpit
```

Use `create-workstream` for the common `problem -> active option -> experiments + follow-up options` shape. It creates the branch and sets the new problem `current_best_option`, but it does not change focus or pause old options.
Follow-up options should use status `open`; file-based graph commands accept option status `planned` only as an input alias and write `open` to truth-source YAML.
After creating a workstream, use `option-workstream-context --id <option_id> --compact --json` to verify experiment ids, statuses, success criteria count, metric count, finding count, and linked artifact count. Read per-experiment `node-context` only when you need the full criterion text or other detailed fields.

Use `complete-experiments` for sweeps or multi-backend experiment sets. Use `create-artifact --file artifact.yaml` for result folders with several links or target nodes, and use `link-artifact` for attaching existing artifacts, so agents do not patch `path`, `links`, or `linked_artifacts` by hand. Use `update-finding` when revising an existing finding statement, confidence, outcome, metrics, or evidence artifacts.

Use `finalize-workstream --file finalize.yaml` when the close-out needs several flags. `--file` supports `option`, `status`, `problem_status`, `stage_status`, `summary_file`, `summary_target`, `artifacts`, `sync_focus`, `report`, `agent`, and `locale`; explicit CLI flags override file values. A relative `summary_file` in `finalize.yaml` resolves against the finalize file directory, then the data root, then the current working directory. Use `finalize-workstream` only for explicit close-out. It updates the named option/problem/stage statuses and optional report/artifact/focus fields that you pass; it does not accept decisions, pause old options, delete branches, or invent next actions.

For terse machine-readable mutation feedback, add `--compact` with `--json` on high-level commands such as `apply-graph-plan`, `create-workstream`, `create-artifact`, `complete-experiments`, `update-finding`, and `finalize-workstream`. Compact output keeps only target, changed status, created/updated ids, changed file count, resolved inputs where useful, and final verify commands. `--show-diff` still includes the full diff; use it only when reviewing write content.

Run `suggest-next-actions` once before choosing work. Re-run it only after you changed `next_actions` or suggestion lifecycle state.

## Write Boundary

Allowed truth-source writes are under:

- `research_cockpit/current_state.yaml`
- `research_cockpit/graph/nodes/*.yaml`
- `research_cockpit/graph/edges.yaml`
- `research_cockpit/graph/graph_views.yaml`
- `research_cockpit/graph/interaction_log.yaml`
- `research_cockpit/notes/**/*.md`

Agents should normally write YAML truth-source files through `research-cockpit` CLI commands. Direct YAML repair is a last-resort structural fix and must be followed by validation and dashboard rebuild.

Markdown notes under `research_cockpit/notes/**/*.md` may be edited directly for human-readable detail. Keep structured findings, status, focus, decision state, `current_best_option`, and `next_actions` in YAML via CLI where a command exists.

Generated files under `research_cockpit/dashboards/` must be rebuilt, not hand-authored.

Never create or update project research state inside the plugin directory itself unless you are intentionally editing `examples/demo_research_cockpit/` for plugin development.
