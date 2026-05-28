# Spec: Dashboard Build And Graph Refresh Performance

## Status

Implemented through Phase 5c. Phase 6 incremental build remains deferred because the measured 1k-node full build is now about 1.1-1.2s and the extra invalidation complexity is not justified yet.

## Date

2026-05-28

## Implementation Results

Representative fixture: `.test_tmp/perf_1000_phase02_marker`, 1,000 nodes, 6,220 linked-resource rows, and 7,220 search-index entries.

Measured full-build profile after implementation:

- `total_duration_ms`: about 1.1-1.2s.
- `load_nodes`: about 0.18-0.21s after switching to PyYAML C safe loader when available.
- `build_link_rows`: about 0.37-0.39s after target-resolution caching.
- `build_search_index`: about 0.28-0.29s after repeated resource skip/text caching.
- `graph_to_json`: about 0.02s with precomputed topology and slim graph payload.
- `graph_view.json`: default dashboard graph omits full `raw` node payload; profile reports slim and with-raw estimated byte sizes.

Validation completed during implementation:

- `python -m unittest discover -s tests`
- `python dev/scripts/run_skill_release_check.py --json --skip-mutating`
- `git diff --check`
- Per-slice no-context subagent review and functional testing for link rows, search indexing, and YAML I/O.

## Assumptions

- The target scale is at least 1,000 graph nodes in a real research repository, with growth toward several thousand nodes.
- Slow paths matter in both CLI `research-cockpit build` and Streamlit UI refresh.
- The current truth-source model remains file-based YAML/JSON; this plan does not introduce a database.
- Build output must stay compatible enough for existing dashboards, UI, and agent handoffs unless a task explicitly adds a migration.
- Optimizations must be measurable before and after each slice.

## Objective

Make Research Cockpit responsive for large research graphs by reducing redundant full-graph work, shrinking dashboard payloads, and making UI refresh reuse generated dashboard data where possible.

Success means:

- Users with 1k+ nodes can identify which build stage is slow.
- Multi-agent batches can defer expensive rebuilds without losing correctness.
- Dashboard refresh is dominated by reading existing dashboard files, not redoing every build step.
- Graph rendering receives the smallest payload needed for navigation and filtering.
- Each optimization is guarded by tests and before/after measurements.

## Non-Goals

- Do not replace YAML truth-source files with a database.
- Do not remove existing `build`, `validate`, `smoke`, `context`, or UI workflows.
- Do not make graph rendering depend on an always-running background service.
- Do not drop search, resources, suggestions, or baseline data from the product; only make expensive pieces cached, slimmed, optional, or incremental.
- Do not optimize by hiding correctness errors from validation.

## Commands

Baseline and verification commands:

```sh
python -m unittest discover -s tests
python -m unittest tests/test_scripts.py -k build
python -m unittest tests/test_scripts.py -k graph
python -m unittest tests/test_scripts.py -k search
research-cockpit validate --root examples/demo_research_cockpit --json
research-cockpit build --root examples/demo_research_cockpit --json
research-cockpit smoke --root examples/demo_research_cockpit --json
python dev/scripts/run_skill_release_check.py --json --skip-mutating
git diff --check
```

Proposed new profiling commands:

```sh
research-cockpit build --root <large_root> --json --profile
research-cockpit build --root <large_root> --json --profile --profile-output dashboards/build_profile.json
python dev/scripts/generate_large_cockpit_fixture.py --root .test_tmp/perf_1000 --nodes 1000 --links-per-node 2
python dev/scripts/benchmark_build.py --root .test_tmp/perf_1000 --runs 5 --json
```

## Project Structure

Likely code paths:

- `src/research_cockpit/commands/build_dashboard.py`: build orchestration, output writing, watch loop.
- `src/research_cockpit/context_packs.py`: agent/focus/current-state context builders.
- `src/research_cockpit/graph_core.py`: graph metadata, parent/child/path traversal, graph JSON.
- `src/research_cockpit/search_index.py`: node, note, and resource full-text indexing.
- `src/research_cockpit/resources.py`: linked resource rows and artifact resolution.
- `src/research_cockpit/ui/app.py`: Streamlit data load and refresh cache.
- `src/research_cockpit/ui/view_helpers.py`: graph filtering and React Flow payload creation.
- `tests/`: behavior and performance-regression smoke tests.
- `dev/scripts/`: optional synthetic fixture and benchmark helpers.

## Code Style

Prefer small read-model objects and explicit dependencies over hidden global caches.

Example style for a build-local cache:

```python
@dataclass(frozen=True)
class DashboardReadModels:
    link_rows: list[dict[str, Any]]
    search_index: list[dict[str, Any]]
    action_suggestions: list[dict[str, Any]]


def build_agent_context(
    root: Path,
    nodes: dict[str, ResearchNode],
    *,
    read_models: DashboardReadModels | None = None,
) -> dict[str, Any]:
    models = read_models or build_dashboard_read_models(root, nodes)
    ...
```

Guidelines:

- Keep public CLI output backward-compatible unless a task defines a migration.
- Add opt-in flags first for potentially disruptive behavior.
- Keep caches local to a build or keyed by file signatures; do not use process-global mutable caches for truth-source state.
- Prefer measured algorithmic improvements over broad rewrites.

## Testing Strategy

- Unit tests verify payload compatibility, command flags, profile JSON schema, and cache invalidation.
- Synthetic large-fixture tests should be deterministic and small enough for normal CI when possible.
- Expensive benchmark scripts should live under `dev/scripts/` and be opt-in.
- Each optimization task must record before/after timing on at least one representative fixture.
- UI changes should preserve existing graph filters, saved views, focus modes, and selected node behavior.

## Boundaries

Always:

- Preserve truth-source correctness and generated dashboard semantics.
- Run targeted unit tests plus `git diff --check` for every slice.
- Add or update docs when command behavior or recommended workflow changes.
- Keep mutations sequential and compatible with `--no-build` batch workflows.

Ask first:

- Removing fields from existing dashboard JSON files without a compatibility path.
- Introducing a persistent background daemon or external dependency.
- Changing default search coverage for existing users.
- Changing React Flow behavior or graph layout defaults.

Never:

- Commit benchmark output, machine-local paths, or generated `.test_tmp` data.
- Skip validation to make build appear faster.
- Make UI depend on stale dashboard files without a visible stale/rebuild indication.
- Rewrite unrelated UI or command behavior while doing performance work.

## Performance Budget

Initial targets should be validated against the user's real 1k+ node repository and a synthetic fixture.

- `build --profile` reports stage timing, node/edge/resource counts, and dashboard output sizes.
- Repeated no-change UI refresh should avoid rebuilding graph/context/search data.
- Phase 2 should remove duplicate `search_index`, `link_rows`, and `action_suggestions` construction from a single build.
- Phase 3 should reduce graph topology/path traversal cost from repeated scans toward linear or near-linear behavior.
- Phase 4 should reduce `graph_view.json` size materially by removing full `raw` payload from the default graph rendering file.

Target numbers should be set from baseline measurements:

- Good: at least 50% build-time reduction on the representative large root after phases 2-4.
- Good: UI refresh reads cached/generated data without recomputing `build_search_index`.
- Guardrail: generated files remain readable by `research-cockpit smoke`.

## Recommended Implementation Plan

### Phase 0: Add Build Profiling

Add a profiling mode before optimizing.

Tasks:

- [x] Add `--profile` to `research-cockpit build`.
  - Acceptance: JSON output includes per-stage duration, total duration, node count, edge count, and output file sizes.
  - Verify: `research-cockpit build --root examples/demo_research_cockpit --json --profile`.
  - Files: `src/research_cockpit/commands/build_dashboard.py`, `tests/test_scripts.py`, `capabilities/graph-state.md`, `SKILL.md`.

- [x] Add a synthetic large fixture generator.
  - Acceptance: creates deterministic data roots with configurable node counts, parent chains, linked artifacts, notes, and search resources.
  - Verify: generated fixture passes `validate`, `build`, and `smoke`.
  - Files: `dev/scripts/generate_large_cockpit_fixture.py`, `tests/`.

- [x] Add an opt-in benchmark script.
  - Acceptance: runs build several times and emits min/median/max stage timings as JSON.
  - Verify: `python dev/scripts/benchmark_build.py --root .test_tmp/perf_1000 --runs 3 --json`.
  - Files: `dev/scripts/benchmark_build.py`.

### Phase 1: Reuse Build Read Models

Remove redundant full-data computations inside a single dashboard build.

Current issue:

- `build_dashboard()` computes `linked_resources`, `action_suggestions`, and `search_index`.
- `build_agent_context()` and `build_focus_context()` also compute overlapping data internally.

Tasks:

- [x] Introduce a build-local read model object.
  - Acceptance: `build_dashboard()` computes shared read models once and passes them to context builders.
  - Verify: existing `build`, `context`, `bootstrap`, and `smoke` tests pass.
  - Files: `src/research_cockpit/context_packs.py`, `src/research_cockpit/commands/build_dashboard.py`, `tests/`.

- [x] Keep standalone context commands compatible.
  - Acceptance: when no read model is supplied, `build_agent_context()` and `build_focus_context()` still compute their own data.
  - Verify: `research-cockpit context --root examples/demo_research_cockpit --id experiment_demo_prompt_refinement --with-bootstrap --with-artifacts --compact --json`.
  - Files: `src/research_cockpit/context_packs.py`, `tests/`.

### Phase 2: Precompute Graph Topology

Avoid repeated scans for children, descendants, and parent paths.

Current issue:

- `child_ids()` scans all nodes when explicit `children` fields are incomplete.
- Graph metadata computes node paths repeatedly.
- Focus and interaction metadata repeat parent/child traversal.

Tasks:

- [x] Add a `GraphTopology` helper.
  - Acceptance: it exposes `children_by_parent`, `parent_by_node`, `path_by_node`, and safe child/path lookup.
  - Verify: graph JSON and focus context are unchanged for demo data.
  - Files: `src/research_cockpit/graph_core.py`, `tests/`.

- [x] Refactor graph metadata builders to use topology.
  - Acceptance: `graph_to_json()` does not repeatedly scan all nodes for common child/path lookups.
  - Verify: benchmark shows reduced `graph_to_json` stage time on synthetic 1k+ fixture.
  - Files: `src/research_cockpit/graph_core.py`, `tests/`.

- [x] Reuse topology in context pack focus helpers where practical.
  - Acceptance: focus context preserves behavior while avoiding repeated parent/child scans.
  - Verify: node-context, context, and option-workstream tests pass.
  - Files: `src/research_cockpit/context_packs.py`, `src/research_cockpit/graph_core.py`, `tests/`.

### Phase 3: Slim Graph Dashboard Payload

Reduce graph payload size and UI transfer cost.

Current issue:

- Each graph node includes full `raw` node data in `graph_view.json`, even though graph rendering needs only slim metadata.

Tasks:

- [x] Add `include_raw` support to `graph_to_json()`.
  - Acceptance: default dashboard graph payload omits `raw`; compatibility path can still request it for tests or debug.
  - Verify: graph rendering and detail panel still work from loaded nodes.
  - Files: `src/research_cockpit/graph_core.py`, `src/research_cockpit/commands/build_dashboard.py`, `src/research_cockpit/ui/app.py`, `tests/`.

- [x] Write payload-size metrics in `--profile`.
  - Acceptance: profile reports `graph_view.json` bytes before and after slim mode.
  - Verify: synthetic fixture profile shows a material size reduction.
  - Files: `src/research_cockpit/commands/build_dashboard.py`, `tests/`.

### Phase 4: Make UI Prefer Generated Dashboard Data

Avoid recomputing all build read models during ordinary UI refresh.

Current issue:

- Streamlit `_load_graph_data_cached()` reloads nodes and rebuilds graph/context/search/read models.
- This duplicates `research-cockpit build` work and makes UI refresh expensive for large repositories.

Tasks:

- [x] Add a dashboard-file loader for UI.
  - Acceptance: UI can load `graph_view.json`, `agent_context_pack.json`, `linked_resources.json`, `search_index.json`, and other existing dashboard files.
  - Verify: UI smoke tests or helper tests load demo dashboards without recomputing search index.
  - Files: `src/research_cockpit/ui/app.py`, `src/research_cockpit/ui/view_helpers.py`, `tests/`.

- [x] Add stale dashboard detection.
  - Acceptance: UI can warn when truth-source files are newer than dashboard files and offer rebuild guidance.
  - Verify: modifying a YAML file causes stale warning in helper-level test.
  - Files: `src/research_cockpit/ui/app.py`, `src/research_cockpit/commands/build_dashboard.py`, `tests/`.

- [x] Keep manual rebuild path.
  - Acceptance: existing `Refresh` / rebuild behavior still works for users who want fresh generated dashboards.
  - Verify: UI cache clear still invalidates loaded data.
  - Files: `src/research_cockpit/ui/app.py`.

### Phase 5: Make Search Indexing Configurable Or Incremental

Reduce cost from notes/resources full-text indexing.

Current issue:

- `build_search_index()` reads all notes and searchable resources every build.
- Each resource may read up to `RESOURCE_SEARCH_MAX_BYTES`.

Tasks:

- [x] Add search profile metrics.
  - Acceptance: profile reports note count, resource count, resource bytes read, skipped resources, and search index build time.
  - Verify: `build --profile` output includes search details.
  - Files: `src/research_cockpit/search_index.py`, `src/research_cockpit/commands/build_dashboard.py`, `tests/`.

- [x] Add a lightweight build mode only if profiling proves search dominates.
  - Acceptance: `build --light` or `build --skip-resource-search` skips expensive resource text reads while preserving node and note search.
  - Verify: generated dashboard remains valid and documents reduced search coverage.
  - Files: `src/research_cockpit/commands/build_dashboard.py`, `src/research_cockpit/search_index.py`, `capabilities/graph-state.md`, `tests/`.

- [x] Consider a search manifest for incremental resource reads.
  - Outcome: not implemented as a persistent manifest. Repeated resource skip/text work is now cached within each build, reducing `build_search_index` from about 2.5s to about 0.28-0.29s on the 1k fixture. A persistent manifest can be revisited if a real repository still shows search-index time dominating.
  - Files: `src/research_cockpit/search_index.py`, `src/research_cockpit/storage.py`, `tests/`.

### Phase 5c: Use PyYAML C Safe Loader When Available

Reduce YAML truth-source parse time without changing storage format.

Tasks:

- [x] Switch storage helpers to `CSafeLoader` / `CSafeDumper` when available.
  - Acceptance: unsafe Python YAML tags are still rejected; pure-Python SafeLoader fallback stays compatible.
  - Verify: `test_load_yaml_rejects_unsafe_python_tags`, `test_load_yaml_safe_loader_fallback_rejects_unsafe_python_tags`, and 1k fixture benchmark.
  - Files: `src/research_cockpit/storage.py`, `tests/test_model.py`.

### Phase 6: Optional Incremental Build

Only pursue after phases 0-5 show remaining pain.

Tasks:

- [ ] Define dashboard output dependencies. Deferred.
  - Acceptance: each generated file declares which truth-source signatures affect it.
  - Verify: changing one node only rebuilds affected outputs when safe.
  - Files: `src/research_cockpit/commands/build_dashboard.py`, docs, tests.

- [ ] Add an incremental build mode behind an opt-in flag. Deferred.
  - Acceptance: `build --incremental` never serves stale files and falls back to full build when dependency safety is uncertain.
  - Verify: mutation/build/smoke tests cover stale and fallback cases.
  - Files: build command, storage helpers, tests.

## Risks And Mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Slim graph payload breaks UI detail panels | High | Keep full `nodes` loaded in UI, add compatibility tests, and make `include_raw` opt-in during transition. |
| Caches hide stale truth-source changes | High | Key caches by file signature and surface stale dashboard warnings. |
| Incremental build becomes complex | Medium | Defer until simpler profiling, deduplication, topology, and slim payload changes are measured. |
| Benchmarks become flaky in CI | Medium | Keep performance scripts opt-in; normal tests verify schema and relative behavior, not strict wall-clock thresholds. |
| Search coverage reduction surprises users | Medium | Make reduced search coverage explicit with flags and dashboard metadata. |

## Success Criteria

- A user can run one profiling command and see where build time is spent.
- A large synthetic fixture exists for repeatable local performance checks.
- `build_dashboard()` no longer constructs the same expensive read models multiple times.
- Graph traversal code has a reusable topology/index layer.
- Default graph dashboard payload is slim enough for large graph rendering.
- UI refresh can use generated dashboard files and warn when they are stale.
- All existing plugin verification commands still pass after each implementation slice.

## Open Questions

- What are acceptable build-time targets on the user's real 1k+ node repository?
- Should `build --light` skip only resource full-text reads, or also skip suggestions/search summaries?
- If future real repositories exceed the current performance budget, should UI expose a setting to force live rebuilds instead of using fresh generated dashboard files?
- Should `graph_view_full.json` be generated for debugging, or should `graph_to_json(include_raw=True)` stay internal only?
- How large can `search_index.json` become before it should move to a split or paged format?
