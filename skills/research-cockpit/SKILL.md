---
name: research-cockpit
description: Use this skill in a project repo to inspect, maintain, and update a YAML-backed research cockpit with agent-readable context packs, workflow scripts, option workstreams, findings, decisions, notes, search, and action suggestions.
---

# Research Cockpit Skill

Use this skill when the user's project needs a local research cockpit: a YAML truth source, generated dashboard/context files, and deterministic scripts for agents to inspect and update research state.

This skill is intended to follow a user's project repo. Install or copy it under a project-local skill directory such as `.agent/skills/research-cockpit/` or `.codex/skills/research-cockpit/`. The skill root is the directory containing this `SKILL.md`; the default cockpit data root is `research_cockpit/` inside that skill root.

The bundled `research_cockpit/` data is a public demo scaffold. Do not treat demo nodes as the user's real research conclusions. For a real project, replace that data directory or pass `--root <path-to-research_cockpit>` to scripts that support it.

## Start

Run from the skill root when possible:

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

If this is a copied package or a new agent environment, run the read-only smoke test:

```powershell
python scripts\skill_smoke_test.py --json
```

If `agent_bootstrap.py` reports missing Python modules, install dependencies with `python -m pip install -r requirements.txt` or set `RESEARCH_COCKPIT_PYTHON` to an interpreter that has the requirements installed. Generated command templates use `python` by default and respect `RESEARCH_COCKPIT_PYTHON`.

For scripts that accept `--root`, pass the cockpit data directory, not the skill root. Omit `--root` to use the bundled `research_cockpit/` directory.

## Read Order

1. Run `agent_bootstrap.py --json` to get validation status, focus, context freshness, suggestions, search summary, and git status.
2. Read `research_cockpit/dashboards/agent_context_pack.json`.
3. Read `research_cockpit/dashboards/focus_context_pack.json` for the current local focus.
4. Run `search_knowledge.py --query "..." --json` when more node, note, or indexed resource context is needed.
5. Run `list_agent_commands.py --json` when unsure which script to use.
6. For option-branch work, run `option_workstream_context.py --option <option_id> --json`.

## Write Boundary

- YAML is the truth source: `research_cockpit/current_state.yaml`, optional `research_cockpit/graph/edges.yaml`, and `research_cockpit/graph/nodes/*.yaml`.
- Dashboard JSON/Markdown files under `research_cockpit/dashboards/` are generated context, not source of truth.
- Markdown notes are long-form supporting records. Do not infer structured state from note prose.
- Use scripts for supported writes. Avoid manual YAML edits unless no script covers the operation.
- After any YAML write, run `validate_cockpit.py` and `build_dashboard.py` unless the script already rebuilt context.
- Do not directly set a decision to `accepted`; use the decision checklist and `accept_decision.py`.
- Do not execute a suggested command just because it appears in Action Guidance. Queue, dismiss, complete, or execute suggestions only when the user asks.

## Workflow Map

Read-only startup and inspection:

```powershell
python scripts\agent_bootstrap.py --json
python scripts\validate_cockpit.py
python scripts\list_agent_commands.py --json
python scripts\skill_smoke_test.py --json
python scripts\search_knowledge.py --query "..." --json
python scripts\suggest_next_actions.py --json
python scripts\option_workstream_context.py --option <option_id> --json
python scripts\check_decision_acceptance.py --id <decision_id> --json
```

Graph and focus maintenance:

```powershell
python scripts\add_node.py --id <id> --type <stage|problem|option|experiment|decision|artifact> --title "..." --parent <parent_id>
python scripts\update_status.py --id <node_id> --status <status>
python scripts\set_focus.py --focus-node <node_id>
```

Experiment evidence and decisions:

```powershell
python scripts\record_finding.py --experiment <experiment_id> --statement "..." --confidence <weak|medium|strong>
python scripts\promote_decision.py --id <decision_id> --option <option_id> --title "..." --summary "..." --status proposed --auto-evidence --dry-run --json
python scripts\promote_decision.py --id <decision_id> --option <option_id> --title "..." --summary "..." --status proposed --auto-evidence
python scripts\update_decision_evidence.py --id <decision_id>
python scripts\update_decision_checklist.py --id <decision_id> --alternative <option_id> --consequence "..." --next-required-action "..."
python scripts\check_decision_acceptance.py --id <decision_id> --json
python scripts\accept_decision.py --id <decision_id> --dry-run --json
python scripts\accept_decision.py --id <decision_id>
```

Action suggestions:

```powershell
python scripts\suggest_next_actions.py --json
python scripts\apply_suggestion.py --id <suggestion_id_or_key> --target current --dry-run --json
python scripts\apply_suggestion.py --id <suggestion_id_or_key> --target current
python scripts\update_suggestion_state.py --id <suggestion_id_or_key> --state dismissed --reason "..."
python scripts\cleanup_suggestion_lifecycle.py --dry-run --json
```

Notes, resources, and search:

```powershell
python scripts\create_note.py --node <problem|option|experiment|decision_id>
python scripts\search_knowledge.py --query "..." --source node --json
python scripts\search_knowledge.py --query "..." --source note --json
python scripts\search_knowledge.py --query "..." --source resource --json
```

Option workstreams:

```powershell
python scripts\claim_option.py --option <option_id> --agent <agent_id> --objective "..." --dry-run --json
python scripts\claim_option.py --option <option_id> --agent <agent_id> --objective "..."
python scripts\option_workstream_context.py --option <option_id> --json
python scripts\report_option_workstream.py --option <option_id> --agent <agent_id> --recommend <accept|reject|continue> --summary "..." --dry-run --json
python scripts\report_option_workstream.py --option <option_id> --agent <agent_id> --recommend <accept|reject|continue> --summary "..."
```

Regenerate generated context:

```powershell
python scripts\build_dashboard.py
```

## Critical Workflows

**Option-following agents:** preview `claim_option.py` and `report_option_workstream.py` with `--dry-run --json` before the real write, then claim one option, inspect its workstream context, work inside that option's child subtree, record findings on experiments, and report `accept`, `reject`, or `continue` back to the option. The preferred branch shape is `option -> problem -> option -> experiment/decision`. `claim_option.py` enforces a single active owner while status is `claimed`, `in_progress`, or `blocked`; use `--force` only when intentionally transferring ownership. A workstream report is evidence for the upstream problem, not a decision acceptance.

**Decision acceptance:** evidence must be recorded before acceptance. The normal gate flow is `record_finding.py -> update_decision_evidence.py -> update_decision_checklist.py -> check_decision_acceptance.py -> accept_decision.py`. Preview `promote_decision.py` and `accept_decision.py` with `--dry-run --json` before the real write. Use `update_decision_checklist.py` to add alternatives, consequences, and next required actions without changing decision status. Only run `accept_decision.py` if `ready` is true. `promote_decision.py --status accepted` uses the same quality gate. Do not use `update_status.py` to accept a decision.

**Action suggestions:** suggestions are generated guidance, not completed facts. Preview `apply_suggestion.py` with `--dry-run --json`, then use it to queue a suggestion into `current_state.next_actions` or the source node. Use `update_suggestion_state.py` to dismiss/complete/restore it, and `cleanup_suggestion_lifecycle.py --dry-run` before removing stale lifecycle history.

**Notes and resources:** `create_note.py` creates Markdown notes and links them through YAML. Search indexes YAML node text, notes, and safe local text resources. Missing linked resources are warnings, not validation failures.

**UI graph workbench:** the Streamlit Research Graph uses a read-only React Flow component when `ui/graph_component/frontend/build` is present. Node clicks and temporary node dragging are UI-only: clicks drive the right-side inspector, and drag positions are not written to YAML or `interaction_log.yaml`. React Flow uses Dagre hierarchy layout for structural graph edges. Graph data changes do not require rebuilding React Flow; use the graph refresh button to rerun Streamlit and reread YAML/JSON data. PyVis remains the legacy fallback.

## Subagent Validation

When asking another agent to test this skill, pass the skill folder path plus a concrete cockpit task. Require it to start with `agent_bootstrap.py --json` and report commands, return codes, blockers, and modified files.

Read-only tests may run directly against the skill package. Mutating tests must copy the whole skill directory to a temporary location and work only in that copy. The original package should remain unchanged.

## References

- `README.md`: user-facing setup, UI launch, and examples.
- `AGENTS.md`: repository rules for coding agents operating inside this skill package.
- `references/repo-layout.md`: package boundary and export layout.
- `scripts/list_agent_commands.py --json`: authoritative command manifest with mutating/read-only flags.

## Verification

After code or YAML changes inside the skill package:

```powershell
python scripts\validate_cockpit.py
python scripts\build_dashboard.py
python scripts\skill_smoke_test.py --json
```

In this development repository only, the external test suite lives outside the package:

```powershell
python -m unittest discover -s dev\tests
```
