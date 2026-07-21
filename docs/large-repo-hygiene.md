# Large Repository Hygiene

Large research repositories slow down when generated outputs, checkpoints, datasets, artifacts, and temporary worktrees are scanned as source files. Keep Research Cockpit truth small and keep bulk payloads outside normal source and watcher paths.

## Recommended Layout

```text
research-repo/
  src/
  configs/
  scripts/
  tests/
  research_cockpit/
    graph/
    assignments/
    runs/
    gate_results/
    artifact_records/
    artifacts/          # durable summaries and review bundles
  outputs/              # ignored generated outputs
  data/                 # ignored or externally managed data
  .worktrees/           # temporary agent worktrees
```

For large payloads, use an external stable artifact root and store bounded summaries, manifests, and portable review bundles under `research_cockpit/artifacts/`.

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

Review active assignments and runs, resource declarations, outer and nested repository state, retention metadata, and stable evidence links. The audit reports candidates; destructive filesystem, worktree, and branch operations remain explicit human actions.
