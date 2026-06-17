# Large Repository Hygiene

Large research repositories can become slow because generated files, checkpoints, datasets, artifacts, and temporary worktrees are watched or scanned as if they were source files.

Repository hygiene is part of Research Cockpit usability. If the dashboard, IDE, git status, or file watchers slow down, agents make worse decisions and cleanup becomes riskier.

## Recommended Layout

```text
research-repo/
  src/
  configs/
  scripts/
  tests/
  research_cockpit/
    graph/
    runs/
    gate_results/
    artifacts/          # long-lived summaries and review bundles, not every raw output
  outputs/              # git-ignored generated outputs
  data/                 # git-ignored or externally managed datasets/caches
  .worktrees/           # temporary agent worktrees
```

For very large payloads, use an external stable artifact root and store only summaries, manifests, and portable review bundles in or near `research_cockpit/artifacts/`.

## Sparse Or Minimal Worktrees

Temporary worktrees usually need:

- source code
- configs
- scripts
- tests
- minimal docs
- any required nested repo path

Temporary worktrees usually do not need:

- `research_cockpit/`
- `outputs/`
- `logs/`
- large `data/` trees
- generated dataset artifacts
- virtual environments
- bulk artifact payloads

The canonical cockpit root should still be passed as an absolute `--root` pointing at the main checkout.

## Watcher Excludes

Recommended excludes for IDEs, file watchers, and repo-wide developer scanners:

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

Do not exclude truth-source YAML directories such as `research_cockpit/graph/`, `research_cockpit/runs/`, `research_cockpit/gate_results/`, `research_cockpit/assignments/`, or `research_cockpit/agents/`.

## Dashboard Build Hygiene

Use profiling before guessing:

```sh
research-cockpit build --root research_cockpit --json --profile
```

If local linked resource full-text indexing dominates the build, use:

```sh
research-cockpit build --root research_cockpit --json --profile --skip-resource-search
```

This keeps node and note search while marking local linked resource text as disabled for that build.

## Evidence Shape

Prefer small, durable evidence:

- `metrics_summary.json`
- `report.md`
- `README.md`
- `artifact_manifest.json`
- `bundle_check.json`
- portable `index.html` review bundles with relative links

Avoid treating these as permanent evidence by default:

- raw generated samples
- every intermediate checkpoint
- optimizer and scheduler state
- precomputed dataset caches
- temporary model export directories
- local absolute paths

## Cleanup Safety

Before cleanup, check:

- active assignments
- queued/running runs
- active resource declarations
- worktree dirty state
- nested repo dirty state
- artifact retention metadata
- whether findings and decisions already link stable evidence

Research Cockpit can make these checks easier, but cleanup still needs explicit human approval for destructive operations.
