# Graph State

`current_state.yaml` is legacy/coordinator compatibility state, not the default worker cursor in multi-agent sessions. For assigned workers, `assignments/*.yaml` is the source of truth for the assignment-local cursor and next actions; `coordinator_state.yaml` is the source of truth for coordinator/UI selection. Treat `agents/*.yaml`, `assignments/*.yaml`, and `coordinator_state.yaml` as structured truth-source files alongside graph nodes, runs, gate results, and artifacts.

Use this capability when reading or changing the research graph shape, saved graph views, or interaction log.

## Data Files

- `research_cockpit/agents/*.yaml`: generated agent identities, display names, and active assignment ids.
- `research_cockpit/assignments/*.yaml`: worker-local assignment roots, cursors, next actions, and status.
- `research_cockpit/coordinator_state.yaml`: coordinator/UI selected node, selected assignment, global next actions, and dashboard filters.
- `research_cockpit/current_state.yaml`: legacy/coordinator compatibility focus, baseline, and global next-action state; not the default worker cursor.
- `research_cockpit/graph/nodes/*.yaml`: graph nodes and their parent/children links.
- `research_cockpit/graph/edges.yaml`: optional semantic edges beyond parent/children.
- `research_cockpit/graph/graph_views.yaml`: saved dynamic view presets, not frozen snapshots.
- `research_cockpit/graph/interaction_log.yaml`: legacy operation-history prefix. Commands may append here only before event-backend migration; after `graph/interaction_events/manifest.json` exists, this file is immutable and new events append under `graph/interaction_events/**`.

The main research graph should stay focused on `stage -> problem -> option -> experiment -> decision`, with recursive child branches modeled as `option -> problem -> option -> experiment/decision`. `artifact` is still a valid node type, but it is supporting material by default and is hidden from the Streamlit graph unless the researcher explicitly enables it.

## Validation And Generated Context

```sh
research-cockpit validate --root research_cockpit --json
research-cockpit repair-interaction-log --root research_cockpit --dry-run --json --show-diff
research-cockpit build --root research_cockpit --json
research-cockpit build --root research_cockpit --json --profile
research-cockpit build --root research_cockpit --watch --interval 5 --json
```

`validate` and `repair-interaction-log --dry-run` are read-only. `build --json` reports generated dashboard files and node count; it writes generated files only and does not append an interaction log event. `build --json --profile` adds a `build_profile_v1` payload with per-stage duration, total duration, node/edge counts, dashboard output sizes, search index counts, `resource_scan_settings`, and resource-scan warnings. Pass `--profile-output dashboards/build_profile.json` to persist the same profile under the data root; `maintenance-audit` reads that file when reporting dashboard performance warnings. Profile output paths must stay under `<root>/dashboards`, must end in `.json`, and must not overwrite the standard dashboard output files. Resource scans are bounded: generated payload patterns are skipped with `resource_scan_skip_pattern`, directory resources prefer configured summary files, and profile warnings can include `resource_scan_skipped_payload`, `resource_directory_without_summary`, or `resource_scan_truncated`. If resource full-text indexing dominates a large build, use `build --skip-resource-search` to keep node and note search while marking local linked resources as `resource_search_disabled` instead of reading their text. `build --watch` polls truth-source YAML/notes plus time-sensitive run progress, run staleness, and gate-result signatures; use `--max-iterations` in tests. `build --watch --json` prints one JSON object per iteration (JSONL-style), not one final JSON document. Watch events include `build_attempted`, `last_build_at`, `last_build_status`, and `last_build_error`; build failures are reported as `ok: false` events and the watcher keeps polling.
Generated dashboards include `assignment_view.json`, a machine-readable queue of high-priority queued/running experiment assignments. Regenerate it with `build`; do not edit it by hand.

In multi-agent worktree runs, run `build --watch` from the main repository against the canonical shared `research_cockpit/` root. Downstream agents should not rebuild or mutate worktree-local cockpit roots.

## Saved Views

Saved graph views preserve view mode, filters, branch visibility state, and saved focus context. They intentionally do not save temporary search text or a one-off selected node.

The Streamlit UI writes saved views through model helpers. Agents should treat `graph_views.yaml` as researcher workspace state and read it through context packs.

## Interaction Log

Key mutating commands append compact JSONL events to the active `graph/interaction_events/` backend, including focus changes, experiment findings, option claims/reports, suggestion application, decision acceptance, and saved graph views. `graph/interaction_log.yaml` is the immutable legacy prefix after migration. Neither backend is the source of current facts; use graph nodes plus the assignment, coordinator, or compatibility state appropriate to the caller.

Read-only context commands tolerate malformed events by skipping them and returning warnings. `research-cockpit validate` treats malformed active JSONL segments or a malformed legacy prefix as data health errors. Mutating commands, including dry-runs, strict-parse the active backend before returning success. Mutations run sequentially under `graph/.mutation.lock`, refuse unsafe log state, and fail without writing if a target truth-source file changed after planning. Lock timeout JSON includes only lock metadata, wait time, and error text; it does not expose another agent command.

Use `repair-interaction-log` only for schema damage in the legacy YAML prefix. For a valid but large legacy prefix, run `migrate-interaction-log --dry-run` before `--execute`; do not edit activated JSONL segments by hand.
