# Research Cockpit Starter

A repo-native, Python-based research dashboard for graph-structured research planning.

It is designed for remote GPU servers and can be launched with Streamlit.

## Quick Start

```bash
cd skills/research-cockpit
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python scripts/build_dashboard.py
streamlit run ui/app.py --server.address 0.0.0.0 --server.port 8501
```

Then open the forwarded port in your browser.
The UI opens on the Research Graph in Focus Mode by default.
The graph workbench supports Focus Depth, Current Branch, Option Workstream, and Global views, plus filters for node type, status, stage, focus role, workstream, blockers, next actions, and missing evidence. The graph detail panel can set any visible node as the current focus and records that durable operation in `research_cockpit/graph/interaction_log.yaml`. The UI also includes Branch Comparison, Decision Trace, Action Guidance, Search, Resources, and node search views for research review.
Researchers can save reusable graph filter presets from the graph workbench. Saved presets are stored in `research_cockpit/graph/graph_views.yaml` as dynamic views over the current graph data, not immutable graph snapshots. Saving a view appends a `save_graph_view` event to `interaction_log.yaml` and refreshes dashboard/context outputs so agents can read `saved_graph_views`.

By default commands use `python`. If your environment needs a specific interpreter, set `RESEARCH_COCKPIT_PYTHON`; generated command templates will use that value.
If `agent_bootstrap.py` reports missing modules, install this package's dependencies with `python -m pip install -r requirements.txt` or use an interpreter that already has them.

```powershell
python scripts\build_dashboard.py
python scripts\skill_smoke_test.py --json
streamlit run ui\app.py --server.address 0.0.0.0 --server.port 8501
```

Example SSH forwarding:

```bash
ssh -L 8501:localhost:8501 user@remote-gpu-server
```

## Core Idea

- YAML nodes are the source of truth.
- Markdown notes contain detailed reasoning.
- Python scripts build dashboard JSON/Markdown.
- Streamlit + PyVis renders an interactive graph.
- Saved graph views live in `research_cockpit/graph/graph_views.yaml` and preserve reusable scopes/filters for long-running research review.
- Agents read `research_cockpit/dashboards/agent_context_pack.json` first, then use
  `research_cockpit/dashboards/focus_context_pack.json` for the current local focus.

## Project Status

Development status logs, planning specs, and tests are intentionally outside this package in the source workspace's `dev/` directory. They are not required when the skill package is copied or installed on its own.

## Maintenance Commands

Validate cockpit data:

```powershell
python scripts\validate_cockpit.py
python scripts\validate_cockpit.py --json
```

Run a read-only agent smoke test:

```powershell
python scripts\skill_smoke_test.py
python scripts\skill_smoke_test.py --json
```

`skill_smoke_test.py` invokes workflow scripts by absolute path, so it is a good first check when a subagent or sandbox cannot reliably set the working directory.

For scripts that accept `--root`, pass the `research_cockpit` data directory, not the package root. When omitted, scripts use the bundled `research_cockpit/` directory.

Add a node:

```powershell
python scripts\add_node.py --id experiment_new --type experiment --title "New Experiment" --parent option_demo_prompt_refinement
```

Update status:

```powershell
python scripts\update_status.py --id experiment_demo_prompt_refinement --status running
```

Record an experiment finding:

```powershell
python scripts\record_finding.py --experiment experiment_demo_prompt_refinement --statement "The refined prompt improves consistency on the demo cases." --confidence medium --outcome positive --metric consistency_score --summary "Improved answer consistency."
```

Follow an option as an agent workstream:

```powershell
python scripts\claim_option.py --option option_demo_prompt_refinement --agent agent_demo --objective "Evaluate the prompt refinement branch."
python scripts\option_workstream_context.py --option option_demo_prompt_refinement --json
python scripts\report_option_workstream.py --option option_demo_prompt_refinement --agent agent_demo --recommend continue --summary "Promising evidence, but more evaluation is needed."
```

`claim_option.py` enforces a single active owner for an option while its workstream status is `claimed`, `in_progress`, or `blocked`. Use `--force` only when intentionally transferring ownership. Agents can branch under the claimed option by adding child `problem` nodes, then child `option` / `experiment` / `decision` nodes. `report_option_workstream.py` writes a report back to the option but does not accept a decision or close the upstream problem.

Promote an option into a decision:

```powershell
python scripts\promote_decision.py --id decision_demo_prompt_refinement_v2 --option option_demo_prompt_refinement --title "Adopt prompt refinement branch" --summary "Use the refined prompt as the default demo workflow." --status proposed --supporting-experiment experiment_demo_prompt_refinement --evidence-strength medium
python scripts\promote_decision.py --id decision_demo_prompt_refinement_v3 --option option_demo_prompt_refinement --title "Adopt prompt refinement branch" --summary "Use the refined prompt as the default demo workflow." --status proposed --auto-evidence
```

`--auto-evidence` collects structured experiment `findings`, `result_summary`, and `outcome` from the option branch, then fills `supporting_experiments`, `evidence_strength`, and `evidence_summary`. Explicit `--evidence-strength` values other than `none` are preserved.

Refresh evidence for an existing decision:

```powershell
python scripts\update_decision_evidence.py --id decision_demo_prompt_refinement
```

`update_decision_evidence.py` only updates evidence-related fields on an existing decision. It does not accept or reject the decision, and it does not close the parent option/problem.

Update checklist metadata for an existing decision:

```powershell
python scripts\update_decision_checklist.py --id decision_demo_prompt_refinement --alternative option_demo_retrieval_branch --consequence "Keep the retrieval branch available as a fallback." --next-required-action "Run a follow-up smoke test after acceptance."
```

`update_decision_checklist.py` appends alternatives, consequences, and next required actions without changing decision status. Duplicate values are ignored. Use `--evidence-summary` only for a deliberate manual evidence summary override; the preferred evidence path is still `update_decision_evidence.py`.

Check whether a decision is ready to accept:

```powershell
python scripts\check_decision_acceptance.py --id decision_demo_prompt_refinement
python scripts\check_decision_acceptance.py --id decision_demo_prompt_refinement --json
```

Accept an existing decision:

```powershell
python scripts\accept_decision.py --id decision_demo_prompt_refinement
```

Acceptance is guarded by the decision checklist. A decision needs valid supporting experiments, evidence content, non-`none` evidence strength, an evidence summary, alternatives, consequences, and next required actions. `promote_decision.py --status accepted` uses the same gate. Use `--force-accept` only for reviewed migration/import exceptions.

Review read-only action suggestions:

```powershell
python scripts\suggest_next_actions.py
python scripts\suggest_next_actions.py --json --focus-only
python scripts\suggest_next_actions.py --include-inactive --state dismissed --json
```

Suggestions are generated from current focus actions, blockers, planned experiments, completed experiments without findings, proposed decisions, and missing local resources. They are read-only and do not update YAML.

Queue a suggestion into an action list:

```powershell
python scripts\apply_suggestion.py --id next_action_001
python scripts\apply_suggestion.py --id next_action_001 --target node
```

`apply_suggestion.py` only appends the suggestion text to `current_state.next_actions` or the source node's `next_actions`. It does not run experiments, update statuses, record findings, or create decisions.

Update suggestion lifecycle state:

```powershell
python scripts\update_suggestion_state.py --id next_action_011 --state dismissed --reason "Not relevant this week."
python scripts\update_suggestion_state.py --id sg_example_key --state active
```

`dismissed` and `completed` only affect suggestion visibility. They do not execute the suggested command or change the underlying experiment, decision, or resource state. `active` restores a suggestion by removing its lifecycle record.

Clean up orphan suggestion lifecycle records:

```powershell
python scripts\cleanup_suggestion_lifecycle.py --dry-run --json
python scripts\cleanup_suggestion_lifecycle.py --state completed --older-than-days 30
```

`cleanup_suggestion_lifecycle.py` only removes lifecycle records whose stable suggestion key no longer matches current generated suggestions. `--dry-run` previews candidates without writing YAML. `--older-than-days` ignores records with missing or invalid dates.

Search notes, YAML node fields, and indexed local resources:

```powershell
python scripts\search_knowledge.py --query demo
python scripts\search_knowledge.py --query "answer quality" --json --focus-only
python scripts\search_knowledge.py --query cache --source note --limit 5
python scripts\search_knowledge.py --query cache --source resource --json
```

`search_knowledge.py` validates cockpit data first, then searches Markdown notes under `research_cockpit/notes/**/*.md`, selected YAML node text fields, and safe local linked resources. Resource indexing is limited to repo-relative paths under `research_cockpit/` with these suffixes: `.md`, `.txt`, `.yaml`, `.yml`, `.json`, `.toml`, `.csv`, `.tsv`. Each resource is capped at 128KB and marked as truncated when larger. URLs, run ids, absolute paths, missing files, unsupported suffixes, and note files already indexed as notes are skipped for resource full-text search.

Create a linked Markdown note:

```powershell
python scripts\create_note.py --node problem_demo_quality_gap
```

`create_note.py` supports `problem`, `option`, `experiment`, and `decision` nodes. It writes a template under `research_cockpit/notes/<type>/<node_id>.md`, links it back through `links.notes`, and rebuilds dashboard/context files by default. Existing notes are not overwritten unless you pass `--overwrite`.

Set current focus:

```powershell
python scripts\set_focus.py --focus-node problem_demo_quality_gap
```

`set_focus.py` derives `current_focus_path` from parent links when `--focus-node` is supplied. You can still pass `--stage`, `--problem`, `--option`, or `--path` to override the derived values. The script rebuilds dashboard/context files by default; add `--no-build` only when you intentionally want to update `current_state.yaml` without regenerating outputs.

Optional explicit graph edges:

```yaml
edges:
  - source: problem_demo_quality_gap
    target: option_demo_prompt_refinement
    type: supports
    label: supports
    strength: 0.8
```

Save this as `research_cockpit/graph/edges.yaml` when you need semantic edges beyond `parent` / `children`. Supported edge fields are `source`, `target`, `type`, `label`, and `strength`.

## Notes, Resources, and Search

- Node detail pages show linked resources as a readable table instead of raw `links` YAML.
- The Resources tab lists notes, config paths, artifact paths, run ids, and linked artifacts, with filters for node type, resource type, and existence status.
- Missing local resource paths are shown as warnings in Data Health; they do not fail validation.
- URLs, run ids, and absolute/external paths are marked as unknown because they are not checked on the local filesystem.
- The Research Graph detail selector has a search box over node id, title, summary, tags, status, and type.
- The Research Graph has Current Focus, Current Branch, Option Workstream, and Global views. Use these with graph filters to inspect long-running research branches without rendering every unrelated node.
- Setting a node as current focus from the UI updates `current_state.yaml`, rebuilds dashboard/context files, and appends an event to `research_cockpit/graph/interaction_log.yaml` so agents can inspect recent human actions.
- The Search tab performs lightweight full-text search over Markdown notes, YAML node text fields, and indexed local linked resources, with filters for source, node type, focus-only results, and result limit.
- The Resources tab shows whether a linked resource was indexed, truncated, or skipped.
- `research_cockpit/dashboards/search_index.json` stores the generated search entries. Context packs only include `search_index_summary`, so agents see counts and nearby entries without loading full note text by default.

Agents should read `agent_context_pack.json` first, then `focus_context_pack.json`. The focus context now includes a `knowledge_index` with nearby linked note/config/file paths so agents can decide which long-form notes or artifacts to open next.
Both context packs also include `suggested_next_actions`, `recent_interactions`, and `search_index_summary` for deciding whether to run `search_knowledge.py`. When current focus is inside an option branch, `focus_context_pack.json` also includes `option_workstream_context` so a subagent can inspect the branch it should follow.

## Using as Agent Skill

This directory is the installable skill package. In the development workspace its path is `skills/research-cockpit/`. For another project repo, copy this directory under a project-local skill location such as `.agent/skills/research-cockpit/` or `.codex/skills/research-cockpit/`.

Agent sessions should start from the skill package root with:

```powershell
python scripts\agent_bootstrap.py --json
python scripts\list_agent_commands.py --json
```

If cwd handling is unreliable, invoke scripts with an absolute path. The scripts derive the package root from their own location:

```powershell
python C:\path\to\research-cockpit\scripts\agent_bootstrap.py --json
```

Recommended agent read order:

1. Run `agent_bootstrap.py --json` to inspect validation, focus, context freshness, top suggestions, and git status.
2. Run `skill_smoke_test.py --json` if this is a copied package or a new agent environment.
3. Read `research_cockpit/dashboards/agent_context_pack.json`, then `research_cockpit/dashboards/focus_context_pack.json`.
4. Use `search_knowledge.py` for targeted note/YAML/resource search.
5. Use mutating scripts for YAML writes; avoid manual edits unless a script does not cover the operation.
6. Run `validate_cockpit.py` and `build_dashboard.py` after mutating data.

Context packs include `metadata.schema_version`, `metadata.generated_at`, `metadata.source_git_commit`, `metadata.worktree_dirty`, and `metadata.current_state_updated_at` so agents can judge whether generated context is fresh. They also include `saved_graph_views`, which are reusable graph scope/filter presets created by the researcher.

For subagent validation, pass the skill folder path and a concrete cockpit task. In the development workspace that path is `skills/research-cockpit`; in an exported package it is the exported directory itself. The bootstrap payload also exposes this path under `skill.path`.

## Directory Layout

```text
research_cockpit/
  current_state.yaml
  graph/
    nodes/*.yaml
    edges.yaml
    graph_views.yaml
    interaction_log.yaml
  notes/
    problems/*.md
    options/*.md
    experiments/*.md
    decisions/*.md
  dashboards/
cockpit/
  model.py
scripts/
  build_dashboard.py
  add_node.py
  update_status.py
  set_focus.py
  validate_cockpit.py
  record_finding.py
  promote_decision.py
  update_decision_evidence.py
  update_decision_checklist.py
  check_decision_acceptance.py
  accept_decision.py
  create_note.py
  suggest_next_actions.py
  apply_suggestion.py
  update_suggestion_state.py
  cleanup_suggestion_lifecycle.py
  search_knowledge.py
  agent_bootstrap.py
  list_agent_commands.py
ui/
  app.py
SKILL.md
AGENTS.md
README.md
requirements.txt
agents/
  openai.yaml
references/
```
