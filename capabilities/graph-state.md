# Graph State

Use this capability when reading or changing the research graph shape, saved graph views, or interaction log.

## Data Files

- `research_cockpit/current_state.yaml`: active stage/problem/option/focus path.
- `research_cockpit/graph/nodes/*.yaml`: graph nodes and their parent/children links.
- `research_cockpit/graph/edges.yaml`: optional semantic edges beyond parent/children.
- `research_cockpit/graph/graph_views.yaml`: saved dynamic view presets, not frozen snapshots.
- `research_cockpit/graph/interaction_log.yaml`: append-only operation summaries, not an immutable audit log.

## Read Commands

```powershell
python .agent\skills\research-cockpit\scripts\validate_cockpit.py --root research_cockpit --json
python .agent\skills\research-cockpit\scripts\build_dashboard.py --root research_cockpit
```

## Saved Views

Saved graph views preserve view mode, filters, and saved focus context. They intentionally do not save temporary search text or a one-off selected node.

The Streamlit UI writes saved views through model helpers. Agents should treat `graph_views.yaml` as researcher workspace state and read it through context packs.

## Interaction Log

Key mutating scripts append compact events to `interaction_log.yaml`, including focus changes, option claims/reports, suggestion application, decision acceptance, and saved graph views. Do not treat the log as the source of truth; use graph nodes and current state for current facts.
