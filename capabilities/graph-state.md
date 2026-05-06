# Graph State

Use this capability when reading or changing the research graph shape, saved graph views, or interaction log.

## Data Files

- `research_cockpit/current_state.yaml`: active stage/problem/option/focus path.
- `research_cockpit/graph/nodes/*.yaml`: graph nodes and their parent/children links.
- `research_cockpit/graph/edges.yaml`: optional semantic edges beyond parent/children.
- `research_cockpit/graph/graph_views.yaml`: saved dynamic view presets, not frozen snapshots.
- `research_cockpit/graph/interaction_log.yaml`: append-only operation summaries, not an immutable audit log.

The main research graph should stay focused on `stage -> problem -> option -> experiment -> decision`. `artifact` is still a valid node type, but it is supporting material by default and is hidden from the Streamlit graph unless the researcher explicitly enables it.

## Read Commands

```sh
research-cockpit validate --root research_cockpit --json
research-cockpit build --root research_cockpit --json
research-cockpit repair-interaction-log --root research_cockpit --dry-run --json --show-diff
```

`build --json` reports generated dashboard files and node count. It writes generated files only and does not append an interaction log event.

## Saved Views

Saved graph views preserve view mode, filters, and saved focus context. They intentionally do not save temporary search text or a one-off selected node.

The Streamlit UI writes saved views through model helpers. Agents should treat `graph_views.yaml` as researcher workspace state and read it through context packs.

## Interaction Log

Key mutating commands append compact events to `interaction_log.yaml`, including focus changes, experiment findings, option claims/reports, suggestion application, decision acceptance, and saved graph views. Do not treat the log as the source of truth; use graph nodes and current state for current facts.

Read-only context commands tolerate malformed log events by skipping them and returning warnings. `research-cockpit validate` treats malformed `interaction_log.yaml` as a data health error. Mutating commands must run sequentially for one data root; they use `graph/.mutation.lock` and refuse to write when the interaction log cannot be parsed safely. Lock timeout JSON includes the lock path, owner pid, creation timestamp, and wait time.

Use `repair-interaction-log` only after validation reports interaction log schema damage. It preserves valid mapping events, drops invalid non-mapping event items, writes a backup on execution, and refuses YAML scanner errors instead of guessing a repair.
