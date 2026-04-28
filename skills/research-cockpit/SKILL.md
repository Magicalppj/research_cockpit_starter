---
name: research-cockpit
description: Use this skill in a project repo to inspect, maintain, and update a YAML-backed research cockpit with agent-readable context packs, workflow scripts, option workstreams, findings, decisions, notes, search, action suggestions, and a Streamlit/React Flow graph workbench.
---

# Research Cockpit Skill

This file is the agent-facing operating contract for Research Cockpit. Use it when a project repo contains this skill under a project-local location such as `.codex/skills/research-cockpit/` or `.agent/skills/research-cockpit/`.

The skill root is the directory containing this `SKILL.md`. The default data root is `research_cockpit/` inside the skill root. The bundled data is public demo data, not the user's real research record.

## Mission

Maintain a local, YAML-backed research graph for long-running research. Agents should:

- inspect current research state before acting;
- preserve YAML as the structured truth source;
- use deterministic scripts for writes;
- record evidence before decisions;
- keep generated context fresh for future agents;
- avoid treating generated suggestions as completed facts.

## Data Model

Primary source files:

- `research_cockpit/current_state.yaml`: current stage, problem, option, focus node, focus path, and current next actions.
- `research_cockpit/graph/nodes/*.yaml`: graph nodes.
- `research_cockpit/graph/edges.yaml`: optional explicit semantic edges beyond parent/child links.
- `research_cockpit/graph/graph_views.yaml`: saved graph view presets created by the researcher.
- `research_cockpit/graph/interaction_log.yaml`: append-only operation summaries for recent human/agent actions.
- `research_cockpit/notes/**/*.md`: long-form notes linked from nodes.

Generated outputs:

- `research_cockpit/dashboards/agent_context_pack.json`
- `research_cockpit/dashboards/focus_context_pack.json`
- `research_cockpit/dashboards/graph_view.json`
- `research_cockpit/dashboards/search_index.json`
- other dashboard Markdown/JSON files

Generated dashboard files are useful context but are not the truth source. Rebuild them after supported writes unless the script already does it.

Node types:

- `stage`: high-level research phase.
- `problem`: research question, quality gap, blocker, or subproblem.
- `option`: candidate approach or branch.
- `experiment`: evidence-producing work.
- `decision`: proposed or accepted conclusion.
- `artifact`: linked output or supporting asset.

Preferred hierarchy:

```text
stage -> problem -> option -> experiment
stage -> problem -> option -> decision
option -> problem -> option -> experiment/decision
```

## Startup Protocol

Start every agent session from the skill root when possible:

```powershell
python scripts\agent_bootstrap.py --json
```

If dashboard/context files are stale or missing:

```powershell
python scripts\agent_bootstrap.py --json --build
```

If cwd handling is unreliable, invoke scripts by absolute path. Scripts derive the package root from their own file location:

```powershell
python C:\path\to\research-cockpit\scripts\agent_bootstrap.py --json
```

If `agent_bootstrap.py` reports missing Python modules, install dependencies:

```powershell
python -m pip install -r requirements.txt
```

Generated command templates use `python` by default and respect `RESEARCH_COCKPIT_PYTHON` when it is set.

For scripts with `--root`, pass the cockpit data directory, not the skill root:

```powershell
python scripts\validate_cockpit.py --root C:\path\to\research_cockpit
```

Omit `--root` to use the bundled `research_cockpit/`.

## Read Order

1. Run `agent_bootstrap.py --json`.
2. If copied package or new environment, run `skill_smoke_test.py --json`.
3. Read `research_cockpit/dashboards/agent_context_pack.json`.
4. Read `research_cockpit/dashboards/focus_context_pack.json`.
5. Run `list_agent_commands.py --json` when selecting a script.
6. Use `search_knowledge.py --query "..." --json` for targeted context.
7. For option branch work, run `option_workstream_context.py --option <option_id> --json`.

Context packs include metadata:

- `metadata.schema_version`
- `metadata.generated_at`
- `metadata.source_git_commit`
- `metadata.worktree_dirty`
- `metadata.current_state_updated_at`

Use these fields to judge freshness. If generated context is stale, run `build_dashboard.py`.

Context packs can also include:

- `suggested_next_actions`
- `recent_interactions`
- `saved_graph_views`
- `search_index_summary`
- `knowledge_index`
- `option_workstream_context` when focus is inside an option branch

## Write Boundary

Use scripts for supported writes. Avoid manual YAML edits unless no script covers the operation.

Rules:

- Validate before and after meaningful writes.
- Prefer `--dry-run --json` when supported.
- Do not write dashboard JSON directly.
- Do not infer structured state from Markdown prose.
- Do not directly mark a decision `accepted`; use `accept_decision.py`.
- Do not execute a suggested command just because it appears in Action Guidance.
- Do not persist React Flow drag positions; they are UI-only.
- Do not record temporary node clicks in YAML or `interaction_log.yaml`.
- Do not use `--force` or `--force-accept` unless the user explicitly accepts the tradeoff or this is a reviewed migration/import exception.

After writes, run:

```powershell
python scripts\validate_cockpit.py
python scripts\build_dashboard.py
```

Many mutating scripts rebuild context by default. `--no-build` skips dashboard rebuild but should not be treated as skipping the logical write.

## Command Manifest

Use this as the authoritative script inventory:

```powershell
python scripts\list_agent_commands.py --json
```

Important read-only commands:

```powershell
python scripts\agent_bootstrap.py --json
python scripts\validate_cockpit.py --json
python scripts\skill_smoke_test.py --json
python scripts\search_knowledge.py --query "..." --json
python scripts\suggest_next_actions.py --json
python scripts\option_workstream_context.py --option <option_id> --json
python scripts\check_decision_acceptance.py --id <decision_id> --json
```

Important mutating commands:

```powershell
python scripts\add_node.py --id <id> --type <stage|problem|option|experiment|decision|artifact> --title "..." --parent <parent_id>
python scripts\update_status.py --id <node_id> --status <status>
python scripts\set_focus.py --focus-node <node_id>
python scripts\claim_option.py --option <option_id> --agent <agent_id> --objective "..." --dry-run --json
python scripts\claim_option.py --option <option_id> --agent <agent_id> --objective "..."
python scripts\report_option_workstream.py --option <option_id> --agent <agent_id> --recommend <accept|reject|continue> --summary "..." --dry-run --json
python scripts\report_option_workstream.py --option <option_id> --agent <agent_id> --recommend <accept|reject|continue> --summary "..."
python scripts\record_finding.py --experiment <experiment_id> --statement "..." --confidence <weak|medium|strong>
python scripts\promote_decision.py --id <decision_id> --option <option_id> --title "..." --summary "..." --status proposed --auto-evidence --dry-run --json
python scripts\promote_decision.py --id <decision_id> --option <option_id> --title "..." --summary "..." --status proposed --auto-evidence
python scripts\update_decision_evidence.py --id <decision_id>
python scripts\update_decision_checklist.py --id <decision_id> --alternative <option_id> --consequence "..." --next-required-action "..."
python scripts\accept_decision.py --id <decision_id> --dry-run --json
python scripts\accept_decision.py --id <decision_id>
python scripts\apply_suggestion.py --id <suggestion_id_or_key> --target current --dry-run --json
python scripts\apply_suggestion.py --id <suggestion_id_or_key> --target current
python scripts\update_suggestion_state.py --id <suggestion_id_or_key> --state dismissed --reason "..."
python scripts\cleanup_suggestion_lifecycle.py --dry-run --json
python scripts\create_note.py --node <node_id>
```

Dry-run support currently exists for:

- `claim_option.py`
- `report_option_workstream.py`
- `promote_decision.py`
- `accept_decision.py`
- `apply_suggestion.py`
- `cleanup_suggestion_lifecycle.py`

## Critical Workflows

### 1. Read-only context gathering

Use this when the user asks for status, next steps, review, or planning:

```powershell
python scripts\agent_bootstrap.py --json
python scripts\search_knowledge.py --query "<topic>" --json --focus-only
python scripts\suggest_next_actions.py --json
```

Do not mutate YAML during a read-only request.

### 2. Adding graph nodes

Use `add_node.py` for new structured research items:

```powershell
python scripts\add_node.py --id problem_new --type problem --title "..." --parent stage_demo_research
```

Then validate and rebuild if the script did not already do so.

Use stable ASCII ids. Keep titles human-readable; titles may contain non-ASCII text if the user's project uses it.

### 3. Focus changes

Use `set_focus.py`:

```powershell
python scripts\set_focus.py --focus-node <node_id>
```

When `--focus-node` is supplied, the script derives `current_focus_path` from parent links. It also appends an interaction event and rebuilds dashboards unless `--no-build` is passed.

### 4. Option-following agents

Use this flow for a subagent assigned to evaluate one option:

```powershell
python scripts\claim_option.py --option <option_id> --agent <agent_id> --objective "..." --dry-run --json
python scripts\claim_option.py --option <option_id> --agent <agent_id> --objective "..."
python scripts\option_workstream_context.py --option <option_id> --json
```

The agent should work inside the option's child subtree, usually by adding child problems, options, experiments, and decisions. Record evidence on experiments.

Report back:

```powershell
python scripts\report_option_workstream.py --option <option_id> --agent <agent_id> --recommend <accept|reject|continue> --summary "..." --dry-run --json
python scripts\report_option_workstream.py --option <option_id> --agent <agent_id> --recommend <accept|reject|continue> --summary "..."
```

`claim_option.py` enforces one active owner while a workstream is `claimed`, `in_progress`, or `blocked`. Use `--force` only for intentional ownership transfer.

A workstream report is not a decision acceptance. It is evidence for the upstream research problem.

### 5. Experiment evidence

Record evidence with `record_finding.py`:

```powershell
python scripts\record_finding.py --experiment <experiment_id> --statement "..." --confidence medium --outcome positive --metric <metric_name> --summary "..."
```

Findings should be concise, evidence-like statements. Do not record a conclusion as a finding unless it is backed by an experiment outcome.

### 6. Decision promotion and acceptance

Normal path:

```text
record_finding.py
-> promote_decision.py or update_decision_evidence.py
-> update_decision_checklist.py
-> check_decision_acceptance.py
-> accept_decision.py
```

Create a proposed decision:

```powershell
python scripts\promote_decision.py --id <decision_id> --option <option_id> --title "..." --summary "..." --status proposed --auto-evidence --dry-run --json
python scripts\promote_decision.py --id <decision_id> --option <option_id> --title "..." --summary "..." --status proposed --auto-evidence
```

Refresh existing decision evidence:

```powershell
python scripts\update_decision_evidence.py --id <decision_id>
```

Fill checklist metadata:

```powershell
python scripts\update_decision_checklist.py --id <decision_id> --alternative <option_id> --consequence "..." --next-required-action "..."
```

Check gate:

```powershell
python scripts\check_decision_acceptance.py --id <decision_id> --json
```

Accept only after the gate is ready:

```powershell
python scripts\accept_decision.py --id <decision_id> --dry-run --json
python scripts\accept_decision.py --id <decision_id>
```

Acceptance checklist requires:

- valid decision parent and problem parent;
- supporting experiments;
- evidence content in supporting experiments;
- non-`none` `evidence_strength`;
- non-empty `evidence_summary`;
- alternatives considered;
- consequences recorded;
- next required actions recorded.

Use `--force-accept` only for reviewed migration/import exceptions. Do not use `update_status.py` to accept a decision.

### 7. Action suggestions

Generate suggestions:

```powershell
python scripts\suggest_next_actions.py --json
```

Queue a suggestion:

```powershell
python scripts\apply_suggestion.py --id <suggestion_id_or_key> --target current --dry-run --json
python scripts\apply_suggestion.py --id <suggestion_id_or_key> --target current
```

Lifecycle:

```powershell
python scripts\update_suggestion_state.py --id <suggestion_id_or_key> --state dismissed --reason "..."
python scripts\update_suggestion_state.py --id <suggestion_id_or_key> --state active
python scripts\cleanup_suggestion_lifecycle.py --dry-run --json
```

`dismissed` and `completed` only affect suggestion visibility. They do not execute work or update underlying experiment/decision/resource state.

### 8. Notes, resources, and search

Create a linked note:

```powershell
python scripts\create_note.py --node <node_id>
```

Search:

```powershell
python scripts\search_knowledge.py --query "..." --json
python scripts\search_knowledge.py --query "..." --source node --json
python scripts\search_knowledge.py --query "..." --source note --json
python scripts\search_knowledge.py --query "..." --source resource --json
```

Search covers:

- selected YAML node text fields;
- Markdown notes under `research_cockpit/notes/**/*.md`;
- safe local linked text resources under `research_cockpit/`.

Resource search excludes URLs, run ids, absolute paths, unsupported suffixes, binary files, and oversized files beyond the configured cap.

## UI Workbench Contract

The Streamlit UI is for human researchers. It can be launched with:

```powershell
python scripts\build_dashboard.py
streamlit run ui\app.py --server.address 0.0.0.0 --server.port 8501
```

Research Graph behavior:

- default renderer is React Flow when `ui/graph_component/frontend/build` exists;
- React Flow uses Dagre for hierarchy layout;
- PyVis remains a legacy fallback;
- node clicks drive the right-side inspector;
- node dragging is temporary UI state only;
- graph filters and saved views are dynamic query presets;
- `刷新图谱 / Refresh` reruns Streamlit and rereads YAML/JSON data;
- graph data changes do not require rebuilding React Flow;
- React Flow source changes require `npm.cmd run build`.

The UI should not be treated as the only write path. For durable research state, scripts remain the safe boundary.

## Interaction Log

`research_cockpit/graph/interaction_log.yaml` is an append-only operation summary, not a cryptographic audit log.

Operations that can write interaction events include:

- setting focus;
- saving graph views;
- claiming option workstreams;
- reporting option workstreams;
- applying suggestions;
- accepting decisions.

Use `recent_interactions` in context packs to understand recent human/agent actions before making new writes.

## Graph Views

Saved graph views live in:

```text
research_cockpit/graph/graph_views.yaml
```

They preserve:

- view scope;
- filters;
- saved focus node id;
- saved focus path;
- timestamps.

They do not preserve temporary search text, selected inspector node, React Flow drag positions, or a frozen graph snapshot.

## Frontend Dependencies

Normal graph data updates do not require Node.js. The committed `frontend/build/` assets are enough for Streamlit to load the React Flow component.

Only rebuild when editing `ui/graph_component/frontend/src/` or frontend dependency files:

```powershell
cd ui\graph_component\frontend
npm.cmd install
npm.cmd run build
```

Primary frontend packages:

- `@xyflow/react`
- `dagre`
- `react`
- `react-dom`
- `streamlit-component-lib`
- `vite`
- `typescript`

Do not commit `node_modules/`.

## Subagent Rules

When delegating to another agent:

- pass the skill folder path and the concrete task;
- require `agent_bootstrap.py --json` first;
- require command outputs, return codes, blockers, and modified paths in the final report;
- for read-only validation, operate directly on the package;
- for mutating validation, copy the package to a temporary location and mutate only the copy;
- do not let subagents revert unrelated user or agent changes.

For option workstreams, assign a unique `agent_id` and a single option branch.

## Failure Handling

If validation fails:

1. Read the validation error exactly.
2. Identify the source YAML node or state file.
3. Prefer a script-based repair.
4. Re-run `validate_cockpit.py`.
5. Rebuild dashboard/context after repair.

If decision acceptance fails:

1. Run `check_decision_acceptance.py --id <decision_id> --json`.
2. Inspect `blocking_failures`.
3. Use `record_finding.py`, `update_decision_evidence.py`, or `update_decision_checklist.py` as appropriate.
4. Do not bypass with `--force-accept` unless explicitly approved.

If React Flow does not load:

1. Confirm `ui/graph_component/frontend/build/index.html` exists.
2. Confirm a JS file exists under `ui/graph_component/frontend/build/assets/`.
3. Use PyVis fallback if needed.
4. Rebuild frontend only if source or dependency files changed.

## Verification

After code or data changes inside the skill package:

```powershell
python scripts\validate_cockpit.py
python scripts\build_dashboard.py
python scripts\skill_smoke_test.py --json
```

In the development repository only:

```powershell
python -m unittest discover -s dev\tests
python dev\scripts\run_skill_release_check.py --json --skip-mutating
```

After frontend source changes:

```powershell
cd ui\graph_component\frontend
npm.cmd run build
```

## References

- `README.md`: human-facing project overview, installation, UI usage, and example workflows.
- `AGENTS.md`: coding-agent rules for this repository.
- `references/repo-layout.md`: package boundary and export layout.
- `scripts/list_agent_commands.py --json`: authoritative command manifest.
- `research_cockpit/dashboards/agent_context_pack.json`: first agent context file to read.
- `research_cockpit/dashboards/focus_context_pack.json`: current focus context.
