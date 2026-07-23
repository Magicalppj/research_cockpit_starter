# Large Repository Hygiene

Large research repositories slow down when generated outputs, checkpoints, datasets, artifacts, and temporary worktrees are scanned as source files. Keep Research Cockpit truth small and keep bulk payloads outside normal source and watcher paths.

## Recommended Layout

```text
research-repo/
  .research-cockpit.yaml # portable project locator only
  src/
  configs/
  scripts/
  tests/
  .worktrees/           # temporary agent worktrees

external-state/<project-id>/
  storage.yaml
  graph/
  assignments/
  runs/
  gate_results/
  artifact_records/
  handoffs/

external-managed-artifacts/<project-id>/
  .quarantine/

legacy-state-root/      # only for existing in-repository projects
  research_cockpit/
    graph/
    assignments/
    runs/
    gate_results/
    artifact_records/
    artifacts/          # readable legacy payloads only
```

In a Git worktree, `research-cockpit init` without `--root` creates external state and writes only `.research-cockpit.yaml` into the repository. `--root research_cockpit` remains the explicit legacy/in-place mode. Configure managed payload storage separately in `<state-root>/storage.yaml` or through `RESEARCH_COCKPIT_ARTIFACT_ROOT`; it must not overlap the state root or source worktree.

New evidence is reference-only by default. Store metadata, provenance, and selected links in the state root; use an explicit external managed root only when Cockpit must own a copy. Do not place new managed payloads under `research_cockpit/artifacts/`.

## Minimal Worktrees

Temporary worktrees usually need source, configs, scripts, tests, minimal docs, and any required nested repository. They usually do not need the canonical `research_cockpit/`, bulk outputs, logs, datasets, artifact payloads, or virtual environments.

Create sparse or minimal worktrees with Git tooling appropriate to the host platform. Then register the session through a `coord_assign_v1` session request using the canonical data root and the resulting worktree path:

```sh
research-cockpit coord assign --print-schema --action session
research-cockpit coord assign --root <absolute-data-root> --file <session.yaml> --json --compact
```

Set `session.create_worktree: false` when the worktree already exists. A worktree must not initialize or mutate a second cockpit root.

## Watcher Excludes

Exclude generated and local-only paths from IDEs, file watchers, and repository-wide scanners:

```text
.worktrees/
outputs/
logs/
data/
datasets/**/artifacts/
research_cockpit/artifacts/**
.venv/
.venvs/
```

Do not exclude truth directories such as `research_cockpit/graph/`, `assignments/`, `agents/`, `runs/`, `gate_results/`, or `artifact_records/`.

## Dashboard Diagnostics

Normal worker mutations do not build dashboards. Profile a standalone diagnostic build only when dashboard generation itself is under investigation:

```sh
research-cockpit build --root <data-root> --json --profile
research-cockpit build --root <data-root> --json --profile --profile-output <profile.json>
```

The profile reports stage timings, output sizes, search index counts, resource scan settings, and warnings. If linked resource full-text indexing dominates, diagnose with:

```sh
research-cockpit build --root <data-root> --json --profile --skip-resource-search
```

This keeps node and note search but disables local linked-resource text for that diagnostic build. Do not turn a profiling workaround into a default without reviewing search requirements.

## Evidence Shape

Prefer small durable files such as `metrics_summary.json`, `report.md`, `artifact_manifest.json`, `bundle_check.json`, and portable `index.html` bundles with relative links. Treat raw samples, intermediate checkpoints, optimizer state, caches, temporary exports, and machine-local absolute paths as disposable unless an explicit retention decision says otherwise.

## Cleanup Safety

Use a bounded maintenance audit before cleanup:

```sh
research-cockpit maintenance audit --root <data-root> --repo <repo-root> --json --compact
```

Review active assignments and runs, resource declarations, outer and nested repository state, retention metadata, and stable evidence links. The audit reports candidates. For a verified Cockpit-managed payload, use the revision-bound `maintenance compact` `artifact_gc` transition instead of manually deleting files: it quarantines first and purges only after the recorded delay. External and legacy evidence remain outside GC.

See [0.3.1 storage boundaries](migrations/0.3.1-storage-boundaries-and-workstream-tracking.md) for migration and recovery details.
