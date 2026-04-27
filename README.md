# Research Cockpit Starter

A repo-native, Python-based research dashboard for graph-structured research planning.

It is designed for remote GPU servers and can be launched with Streamlit.

## Quick Start

```bash
cd research_cockpit_starter
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python scripts/build_dashboard.py
streamlit run ui/app.py --server.address 0.0.0.0 --server.port 8501
```

Then open the forwarded port in your browser.
The UI opens on the Research Graph in Focus Mode by default.
The graph detail panel can set any visible node as the current focus. The UI also includes Branch Comparison, Decision Trace, Action Guidance, Resources, and node search views for read-only research review.

On the current Windows workstation, the verified interpreter is:

```powershell
D:\Tools\miniconda3\envs\aigc\python.exe scripts\build_dashboard.py
D:\Tools\miniconda3\envs\aigc\python.exe -m unittest discover -s tests
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
- Agents read `research_cockpit/dashboards/agent_context_pack.json` first, then use
  `research_cockpit/dashboards/focus_context_pack.json` for the current local focus.

## Project Status

See `docs_development_status.md` for the current development phase, completed MVP capabilities, known limitations, and recommended next features.

## Maintenance Commands

Validate cockpit data:

```powershell
D:\Tools\miniconda3\envs\aigc\python.exe scripts\validate_cockpit.py
D:\Tools\miniconda3\envs\aigc\python.exe scripts\validate_cockpit.py --json
```

Add a node:

```powershell
D:\Tools\miniconda3\envs\aigc\python.exe scripts\add_node.py --id exp_new --type experiment --title "New Experiment" --parent option_flan_t5_clap
```

Update status:

```powershell
D:\Tools\miniconda3\envs\aigc\python.exe scripts\update_status.py --id exp_041_flan_t5_only --status running
```

Record an experiment finding:

```powershell
D:\Tools\miniconda3\envs\aigc\python.exe scripts\record_finding.py --experiment exp_042_flan_t5_clap --statement "FLAN-T5 + CLAP improves replace following." --confidence medium --outcome positive --metric replace_following --summary "Improved edit following."
```

Promote an option into a decision:

```powershell
D:\Tools\miniconda3\envs\aigc\python.exe scripts\promote_decision.py --id decision_flan_t5_clap_v2 --option option_flan_t5_clap --title "Adopt FLAN-T5 + CLAP" --summary "Use FLAN-T5 event tokens with CLAP anchor." --status proposed --supporting-experiment exp_042_flan_t5_clap --evidence-strength medium
```

Review read-only action suggestions:

```powershell
D:\Tools\miniconda3\envs\aigc\python.exe scripts\suggest_next_actions.py
D:\Tools\miniconda3\envs\aigc\python.exe scripts\suggest_next_actions.py --json --focus-only
```

Suggestions are generated from current focus actions, blockers, planned experiments, completed experiments without findings, proposed decisions, and missing local resources. They are read-only and do not update YAML.

Queue a suggestion into an action list:

```powershell
D:\Tools\miniconda3\envs\aigc\python.exe scripts\apply_suggestion.py --id next_action_001
D:\Tools\miniconda3\envs\aigc\python.exe scripts\apply_suggestion.py --id next_action_001 --target node
```

`apply_suggestion.py` only appends the suggestion text to `current_state.next_actions` or the source node's `next_actions`. It does not run experiments, update statuses, record findings, or create decisions.

Create a linked Markdown note:

```powershell
D:\Tools\miniconda3\envs\aigc\python.exe scripts\create_note.py --node problem_event_text_weak
```

`create_note.py` supports `problem`, `option`, `experiment`, and `decision` nodes. It writes a template under `research_cockpit/notes/<type>/<node_id>.md`, links it back through `links.notes`, and rebuilds dashboard/context files by default. Existing notes are not overwritten unless you pass `--overwrite`.

Set current focus:

```powershell
D:\Tools\miniconda3\envs\aigc\python.exe scripts\set_focus.py --focus-node problem_event_text_weak
```

`set_focus.py` derives `current_focus_path` from parent links when `--focus-node` is supplied. You can still pass `--stage`, `--problem`, `--option`, or `--path` to override the derived values. The script rebuilds dashboard/context files by default; add `--no-build` only when you intentionally want to update `current_state.yaml` without regenerating outputs.

Optional explicit graph edges:

```yaml
edges:
  - source: problem_event_text_weak
    target: option_flan_t5_clap
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

Agents should read `agent_context_pack.json` first, then `focus_context_pack.json`. The focus context now includes a `knowledge_index` with nearby linked note/config/file paths so agents can decide which long-form notes or artifacts to open next.
Both context packs also include `suggested_next_actions` for read-only planning.

## Directory Layout

```text
research_cockpit/
  current_state.yaml
  graph/
    nodes/*.yaml
    edges.yaml
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
  create_note.py
  suggest_next_actions.py
  apply_suggestion.py
ui/
  app.py
```
