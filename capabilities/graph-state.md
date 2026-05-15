# Graph State

Use this capability when reading or changing the research graph shape, saved graph views, or interaction log.

## Data Files

- `research_cockpit/current_state.yaml`: active stage/problem/option/focus path.
- `research_cockpit/graph/nodes/*.yaml`: graph nodes and their parent/children links.
- `research_cockpit/graph/edges.yaml`: optional semantic edges beyond parent/children.
- `research_cockpit/graph/graph_views.yaml`: saved dynamic view presets, not frozen snapshots.
- `research_cockpit/graph/interaction_log.yaml`: append-only operation summaries, not an immutable audit log.

The main research graph should stay focused on `stage -> problem -> option -> experiment -> decision`, with recursive child branches modeled as `option -> problem -> option -> experiment/decision`. `artifact` is still a valid node type, but it is supporting material by default and is hidden from the Streamlit graph unless the researcher explicitly enables it.

## Validation And Generated Context

```sh
research-cockpit validate --root research_cockpit --json
research-cockpit repair-interaction-log --root research_cockpit --dry-run --json --show-diff
research-cockpit build --root research_cockpit --json
research-cockpit build --root research_cockpit --watch --interval 5 --json
```

`validate` and `repair-interaction-log --dry-run` are read-only. `build --json` reports generated dashboard files and node count; it writes generated files only and does not append an interaction log event. `build --watch` polls truth-source YAML/notes and rebuilds dashboards only after changes; use `--max-iterations` in tests. `build --watch --json` prints one JSON object per iteration (JSONL-style), not one final JSON document.
Generated dashboards include `assignment_view.json`, a machine-readable queue of high-priority queued/running experiment assignments. Regenerate it with `build`; do not edit it by hand.

In multi-agent worktree runs, run `build --watch` from the main repository against the canonical shared `research_cockpit/` root. Downstream agents should not rebuild or mutate worktree-local cockpit roots.

## Saved Views

Saved graph views preserve view mode, filters, branch visibility state, and saved focus context. They intentionally do not save temporary search text or a one-off selected node.

The Streamlit UI writes saved views through model helpers. Agents should treat `graph_views.yaml` as researcher workspace state and read it through context packs.

## Interaction Log

Key mutating commands append compact events to `interaction_log.yaml`, including focus changes, experiment findings, option claims/reports, suggestion application, decision acceptance, and saved graph views. Do not treat the log as the source of truth; use graph nodes and current state for current facts.

Read-only context commands tolerate malformed log events by skipping them and returning warnings. `research-cockpit validate` treats malformed `interaction_log.yaml` as a data health error. Mutating commands, including dry-runs, strict-parse the log before returning success. Mutations must run sequentially for one data root; they use `graph/.mutation.lock`, refuse to write when the interaction log cannot be parsed safely, and fail without writing if a target truth-source file changed after command planning. Lock timeout JSON includes the lock path, owner pid, creation timestamp, wait time, and error text.

Use `repair-interaction-log` only after validation or a mutating dry-run reports interaction log schema damage. It preserves valid mapping events, drops invalid non-mapping event items, writes a backup on execution, and refuses YAML scanner errors instead of guessing a repair.
