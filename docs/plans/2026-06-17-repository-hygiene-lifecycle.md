# Spec: Repository Hygiene Lifecycle

## Status

Implemented historical plan, superseded operationally by the 0.3.0 role facade. Removed command examples below are design history, not current instructions. Current operational guidance: `capabilities/maintenance.md`, `docs/worktree-branch-lifecycle.md`, `docs/artifact-retention-policy.md`, and `docs/migrations/0.3.0-cli-cutover.md`.

## Date

2026-06-17

## Source Context

This spec is based on downstream agent feedback in `dev/docs/ResearchCockpitSkillOptimizationProposal.md` and the current Research Cockpit implementation. The feedback says the multi-agent branch/worktree model is useful, but long-running ML repositories still accumulate generated outputs, caches, checkpoints, temporary worktrees, and `codex/*` or `agent/*` branches faster than agents can safely clean them up.

Current code already has several stable extension points:

- `start-agent-session` records assignment, worktree label, and git branch metadata without storing machine-local absolute paths in YAML.
- `assignments/*.yaml` stores worker-local cursors and active assignment state.
- `runs/*.yaml` stores execution sidecar records with `pid`, `tmux_session`, `log_root`, `output_root`, `progress_file`, and `config_file`.
- artifact nodes and `ingest-artifact` preserve worktree outputs in the canonical artifact store before a disposable worktree is removed.
- `lint --semantic` already reports warning-style lifecycle issues.
- `build --profile` and `build --skip-resource-search` already expose dashboard build performance hooks.
- `commands --json --compact --workflow maintenance` already exists as a command discovery path, but it does not yet cover repository hygiene audits.

## Assumptions

- The repository remains a reusable Research Cockpit plugin; project-specific experiment state still lives in the caller repository's `research_cockpit/` data root.
- Heavy payloads such as checkpoints, raw generations, dataset caches, and large audio bundles should not become default graph payloads.
- The first implementation should prefer documentation and read-only audits over destructive cleanup.
- Existing YAML roots must remain valid without migration.
- Maintenance commands should work on Windows and Unix-style repositories; path handling cannot assume POSIX separators.
- Git worktree and branch auditing may need to inspect nested repositories, but nested repos must be opt-in.
- RC-level active resource declarations are advisory safety signals; they do not replace system-level checks such as process lists, GPU tools, port checks, or user approval.

## Objective

Add a repository hygiene lifecycle to Research Cockpit so agents can answer these questions before cleaning a large multi-agent research repo:

- Which worktrees are still tied to active assignments, active runs, or unpreserved evidence?
- Which temporary branches are merged, stale, evidence-backed, or candidates for promotion to `research/*`?
- Which artifact/output/cache directories are evidence-critical, reproducible, disposable, or unknown?
- Which paths, ports, GPUs, or PIDs are still declared by active runs?
- Which dashboard/search scans are at risk of reading too much generated payload?

Success means a coordinator can start cleanup with a single read-only audit, understand blockers, preserve needed evidence, and only then execute explicit, reviewable closeout steps.

## Non-Goals

- Do not add a command that force-deletes worktrees, branches, outputs, caches, checkpoints, or artifacts by default.
- Do not turn Research Cockpit into a general disk-cleanup tool.
- Do not require all projects to adopt ML/audio-specific evidence fields.
- Do not make `research_cockpit/artifacts/**` the default home for raw large payloads.
- Do not replace existing run, gate, artifact, finding, decision, or assignment workflows.
- Do not introduce a database or long-running daemon for this feature.

## Tech Stack

- Python package under `src/research_cockpit/`.
- CLI dispatch through `src/research_cockpit/cli.py` and `src/research_cockpit/command_registry.py`.
- YAML truth-source IO through `storage.py`, `model.py`, and focused domain modules.
- Tests under `tests/`, primarily `tests/test_scripts.py` for CLI behavior and integration-style workflows.
- Documentation under `SKILL.md`, `capabilities/`, and `docs/`.

No new runtime dependency is expected for the first implementation. Git inspection should use `subprocess.run(["git", "-C", ...])` with bounded, explicit commands.

## Commands

Baseline verification commands:

```sh
python -m unittest discover -s tests
python dev/scripts/run_skill_release_check.py --json --skip-mutating
git diff --check
```

Research Cockpit verification commands:

```sh
research-cockpit validate --root examples/demo_research_cockpit --json
research-cockpit build --root examples/demo_research_cockpit --json --profile
research-cockpit smoke --root examples/demo_research_cockpit --json
research-cockpit commands --json --compact --workflow maintenance
```

Proposed read-only audit commands:

```sh
research-cockpit active-resources --root research_cockpit --json
research-cockpit worktree-audit --root research_cockpit --repo /path/to/repo --json
research-cockpit branch-audit --root research_cockpit --repo /path/to/repo --base main --json
research-cockpit artifact-retention-audit --root research_cockpit --repo /path/to/repo --min-size-gb 10 --json
research-cockpit maintenance-audit --root research_cockpit --repo /path/to/repo --min-size-gb 10 --json
```

Proposed dry-run closeout command:

```sh
research-cockpit worktree-closeout \
  --root research_cockpit \
  --repo /path/to/repo \
  --worktree /path/to/repo/.worktrees/example \
  --classification preserve_as_research_branch \
  --research-branch research/example \
  --dry-run \
  --json \
  --show-diff
```

Proposed metadata write shape, exact flags to be finalized during implementation:

```sh
research-cockpit update-run --root research_cockpit --id run_x --resources-json resources.json --no-build
research-cockpit complete-run --root research_cockpit --id run_x --status completed --output-retention-json output_retention.json --no-build
research-cockpit create-artifact --root research_cockpit --file artifact.yaml --no-build
research-cockpit update-node-fields --root research_cockpit --id artifact_x --metadata-file retention.yaml --no-build
```

The final write API should avoid many one-off flags if the metadata is naturally nested. Prefer file or JSON input for nested retention/resource payloads.

## Project Structure

Likely new or changed files:

```text
SKILL.md
capabilities/maintenance.md
capabilities/experiment-tracking.md
capabilities/integrations.md
docs/artifact-retention-policy.md
docs/worktree-branch-lifecycle.md
docs/large-repo-hygiene.md
docs/plans/2026-06-17-repository-hygiene-lifecycle.md
src/research_cockpit/maintenance.py
src/research_cockpit/commands/active_resources.py
src/research_cockpit/commands/worktree_audit.py
src/research_cockpit/commands/branch_audit.py
src/research_cockpit/commands/artifact_retention_audit.py
src/research_cockpit/commands/maintenance_audit.py
src/research_cockpit/commands/worktree_closeout.py
src/research_cockpit/commands/create_run.py
src/research_cockpit/commands/update_run.py
src/research_cockpit/commands/complete_run.py
src/research_cockpit/commands/create_artifact.py
src/research_cockpit/commands/file_schemas.py
src/research_cockpit/commands/lint_semantic.py
src/research_cockpit/search_index.py
src/research_cockpit/commands/build_dashboard.py
tests/test_scripts.py
```

The domain logic should live in focused modules such as `maintenance.py`, not directly inside command wrappers. Commands should parse arguments, call domain helpers, and emit stable JSON.

## Code Style

Use plain dictionaries for command JSON payloads and small focused helpers for normalization. Prefer explicit safety fields over free-form text.

Example shape:

```python
def audit_active_resources(root: Path) -> dict[str, Any]:
    runs = load_runs(root)
    active = []
    for run in sorted(runs.values(), key=lambda item: item.run_id):
        if run.status not in {"queued", "running"}:
            continue
        resources = run.raw.get("resources") if isinstance(run.raw.get("resources"), dict) else {}
        active.append({
            "run_id": run.run_id,
            "experiment_id": run.experiment_id,
            "status": run.status,
            "pid": run.pid,
            "tmux_session": run.tmux_session,
            "output_root": run.output_root,
            "resources": resources,
        })
    return {"schema_version": "active_resources_v1", "active": active}
```

JSON payloads should include:

- `schema_version`
- `ok`
- `root`
- `repo` when repo inspection is involved
- `warnings`
- `blockers`
- `recommended_next_actions`

Avoid shell strings when an argument vector is safer. If a command suggests a destructive operation, output both `command_args` and a human-readable command string, but do not execute it.

## Data Contracts

All new fields are optional. Existing records without these fields must continue to validate.

### Artifact Retention

```yaml
retention:
  class: evidence_critical
  reason: "Contains metric summary and portable review bundle used by finding_x."
  delete_after: null
  reusable: true
  regenerate_command: scripts/experiments/example/run_eval.sh
  depends_on_for_future_training: false
  keep_files:
    - metrics_summary.json
    - index.html
    - comparison_data.json
  disposable_patterns:
    - raw_generations/**
    - optimizer.bin
    - scheduler.bin
```

Allowed classes:

- `evidence_critical`
- `portable_review_bundle`
- `final_checkpoint`
- `resume_state`
- `reproducible_output`
- `disposable_cache`
- `deprecated_payload`

### Run Output Retention

```yaml
output_retention:
  keep_checkpoints:
    - step10000
    - final
  keep_optimizer_state: false
  resume_planned: false
  raw_outputs_disposable: true
  portable_bundle_path: outputs/example/listening_bundle.tar.gz
  cleanup_after_completion: true
  cleanup_notes: "Metrics and bundle preserved; intermediate generations are reproducible."
```

### Active Resources

```yaml
resources:
  gpus:
    - 0
    - 1
  ports:
    - 8000
  process_ids:
    - 123456
  worktree: /repo/.worktrees/example
  output_roots:
    - /repo/outputs/example_run
  cache_roots:
    - /repo/data/example/.precomputed
  dataset_roots:
    - /repo/data/example
  model_paths:
    - /repo/outputs/example_run/checkpoint-final
```

Prefer relative or portable paths when possible. Absolute paths may be useful for active resource warnings but should not become long-lived evidence references.

### Optional Claim Discipline

These fields are useful for ML/audio projects and should remain optional:

```yaml
evidence_level: overfit_cached_init
scale_class: five_shot
claim_scope: pipeline_fit_only
effective_epochs: 3000
generation_mode: cached_init
requires_pure_noise_for_promotion: true
required_metrics:
  - outside_leakage
  - onset_offset_accuracy
  - wrong_time_counterfactual
  - wrong_text_counterfactual
```

Semantic lint rules for these fields should be configurable, not hard-coded as universal Research Cockpit policy.

## Architecture Decisions

### Decision 1: Audit Before Cleanup

Maintenance starts with read-only audit commands. Destructive cleanup remains outside the default command behavior.

Rationale: multi-agent repositories can have active runs, nested repos, and reusable failed-branch code. A direct cleanup command would make the dangerous path too easy.

### Decision 2: Extend Existing Sidecars Instead Of Adding New Graph Nodes

Run resources and output retention belong in `runs/*.yaml`. Artifact retention belongs on artifact nodes. Worktree and branch audit output is computed from git and current RC state.

Rationale: the graph should stay focused on research questions, options, experiments, decisions, and key artifacts. Operational cleanup state should not flood the node graph.

### Decision 3: Keep Metadata Optional And Warning-Based

Missing retention metadata should produce audit findings or semantic lint warnings, not hard validation failures in the first version.

Rationale: existing projects should upgrade gradually. Hard validation would block unrelated research updates.

### Decision 4: Domain Helpers First, CLI Wrappers Second

Shared logic should live in `maintenance.py` or narrowly named helpers. Command modules should remain thin.

Rationale: tests can exercise the safety logic without shelling out through every CLI path, and future UI/dashboard surfaces can reuse the read model.

### Decision 5: Dashboard Scans Must Be Bounded

Resource search should read metadata, summary files, and explicitly linked small files by default. Heavy payload traversal must be capped and visible in profile output.

Rationale: watcher performance is a feature requirement for large experiment repositories.

## Implementation Plan

### Phase 0: Documentation And Routing

#### Task 0.1: Add Maintenance Capability Documentation

Description: Add `capabilities/maintenance.md` covering cleanup, retention, worktree closeout, branch lifecycle, active resources, and watcher hygiene.

Acceptance:

- Documents the safe closeout sequence before deleting a worktree.
- Defines branch classes: `main`, `codex/*`, `agent/*`, `research/*`, `archive/*`.
- Defines retention classes and when each is safe to delete or preserve.
- States that cleanup is audit-first and never automatic.

Verify:

```sh
git diff --check
python dev/scripts/run_skill_release_check.py --json --skip-mutating
```

Files:

- `capabilities/maintenance.md`
- `SKILL.md`

Dependencies: none.

#### Task 0.2: Update Existing Capability Routing

Description: Add short references from `SKILL.md`, `capabilities/experiment-tracking.md`, and `capabilities/integrations.md` to the maintenance workflow.

Acceptance:

- `SKILL.md` stays thin and only routes maintenance tasks to `capabilities/maintenance.md`.
- `experiment-tracking.md` references run `resources` and `output_retention`.
- `integrations.md` references sparse worktrees, external artifact roots, and watcher excludes.

Verify:

```sh
python dev/scripts/run_skill_release_check.py --json --skip-mutating
```

Files:

- `SKILL.md`
- `capabilities/experiment-tracking.md`
- `capabilities/integrations.md`

Dependencies: Task 0.1.

#### Task 0.3: Add Human-Facing Policy Docs

Description: Add durable docs for artifact retention, worktree/branch lifecycle, and large repo hygiene.

Acceptance:

- Each doc explains intent, defaults, and examples.
- Docs do not duplicate every CLI example from capability files.
- Docs clearly distinguish RC evidence metadata from heavy payload storage.

Verify:

```sh
git diff --check
```

Files:

- `docs/artifact-retention-policy.md`
- `docs/worktree-branch-lifecycle.md`
- `docs/large-repo-hygiene.md`

Dependencies: Task 0.1.

### Phase 1: Read-Only Audit Foundation

#### Task 1.1: Add `active-resources`

Description: Read `runs/*.yaml` and report active RC-declared resources from queued/running runs.

Acceptance:

- Reports existing fields such as `pid`, `tmux_session`, `output_root`, `log_root`, and `progress_file`.
- Also reports optional `resources` when present.
- Ignores completed, failed, and cancelled runs unless an `--include-terminal` flag is added.
- Does not inspect system processes in v1.

Verify:

```sh
python -m unittest tests.test_scripts -k active_resources
research-cockpit active-resources --root examples/demo_research_cockpit --json
```

Files:

- `src/research_cockpit/maintenance.py`
- `src/research_cockpit/commands/active_resources.py`
- `src/research_cockpit/command_registry.py`
- `src/research_cockpit/commands/list_agent_commands.py`
- `tests/test_scripts.py`

Dependencies: none.

#### Task 1.2: Add `worktree-audit`

Description: Inspect git worktrees and join them with assignment/workstream metadata.

Acceptance:

- Runs `git -C <repo> worktree list --porcelain`.
- Maps worktree branch and label to `assignment.worktree` and option `agent_workstream`.
- Reports checked-out branch, path, commit, active assignment ids, active node ids, run statuses, and blockers.
- Optional `--include-nested <path>` repeats the same audit for nested repos.
- Read-only only.

Verify:

```sh
python -m unittest tests.test_scripts -k worktree_audit
research-cockpit worktree-audit --root examples/demo_research_cockpit --repo . --json
```

Files:

- `src/research_cockpit/maintenance.py`
- `src/research_cockpit/commands/worktree_audit.py`
- `tests/test_scripts.py`

Dependencies: Task 1.1 for active run/resource blockers.

#### Task 1.3: Add `branch-audit`

Description: Classify local branches by lifecycle state.

Acceptance:

- Reports branches checked out by any worktree.
- Reports branches merged into `--base`.
- Reports unmerged temporary branches with or without RC assignment/evidence.
- Flags likely `research/*` candidates when a branch is unmerged but linked to findings/artifacts or active option workstream records.
- Does not delete branches.

Verify:

```sh
python -m unittest tests.test_scripts -k branch_audit
research-cockpit branch-audit --root examples/demo_research_cockpit --repo . --base main --json
```

Files:

- `src/research_cockpit/maintenance.py`
- `src/research_cockpit/commands/branch_audit.py`
- `tests/test_scripts.py`

Dependencies: Task 1.2 for checked-out branch detection.

#### Task 1.4: Add `artifact-retention-audit`

Description: Report artifact paths and large output candidates with retention status.

Acceptance:

- Reads artifact nodes from `graph/nodes/*.yaml`.
- Resolves artifact `path` and `links` through existing resource resolution behavior.
- Computes bounded size and file-count summaries for paths under the repo/root.
- Flags large paths missing `retention`.
- Flags `disposable_cache`, `reproducible_output`, and `deprecated_payload` as cleanup candidates only when no active resource references them.
- Avoids expensive recursive scans beyond configured thresholds.

Verify:

```sh
python -m unittest tests.test_scripts -k artifact_retention_audit
research-cockpit artifact-retention-audit --root examples/demo_research_cockpit --repo . --min-size-gb 10 --json
```

Files:

- `src/research_cockpit/maintenance.py`
- `src/research_cockpit/commands/artifact_retention_audit.py`
- `tests/test_scripts.py`

Dependencies: Task 1.1.

#### Task 1.5: Add `maintenance-audit`

Description: Aggregate active resources, worktree audit, branch audit, artifact retention audit, and dashboard scan warnings into one read-only payload.

Acceptance:

- Output sections include `active_assignments`, `running_runs`, `active_resources`, `worktree_candidates`, `branch_candidates`, `large_artifact_candidates`, `large_output_candidates`, `dashboard_performance_warnings`, `unsafe_cleanup_blockers`, and `recommended_next_actions`.
- Payload has a stable schema version.
- The command is discoverable under `--workflow maintenance`.

Verify:

```sh
python -m unittest tests.test_scripts -k maintenance_audit
research-cockpit commands --json --compact --workflow maintenance
research-cockpit maintenance-audit --root examples/demo_research_cockpit --repo . --json
```

Files:

- `src/research_cockpit/commands/maintenance_audit.py`
- `src/research_cockpit/command_registry.py`
- `src/research_cockpit/commands/list_agent_commands.py`
- `tests/test_scripts.py`

Dependencies: Tasks 1.1 to 1.4.

### Phase 2: Optional Metadata Write Support

#### Task 2.1: Add Run `resources` And `output_retention` Write Support

Description: Allow run commands to write nested resource and output retention metadata.

Acceptance:

- `create-run`, `update-run`, and `complete-run` can write nested metadata from JSON or file input.
- Existing scalar run fields continue to work unchanged.
- Invalid nested payload shapes produce clear CLI errors.
- Old run records without these fields still validate.

Verify:

```sh
python -m unittest tests.test_scripts -k run_retention
python -m unittest tests.test_scripts -k active_resources
```

Files:

- `src/research_cockpit/commands/_runs.py`
- `src/research_cockpit/commands/create_run.py`
- `src/research_cockpit/commands/update_run.py`
- `src/research_cockpit/commands/complete_run.py`
- `src/research_cockpit/model.py`
- `tests/test_scripts.py`

Dependencies: Task 1.1.

#### Task 2.2: Add Artifact Retention Write Support

Description: Allow artifact commands to create and update `retention` and `artifact_kind` metadata.

Acceptance:

- `create-artifact --file` schema supports `retention` and `artifact_kind`.
- Existing `create-artifact` CLI flags remain compatible.
- There is a safe update path for retention metadata on existing artifact nodes.
- Retention class values are validated when present.

Verify:

```sh
python -m unittest tests.test_scripts -k artifact_retention
research-cockpit create-artifact --print-schema
```

Files:

- `src/research_cockpit/commands/create_artifact.py`
- `src/research_cockpit/commands/update_node_fields.py`
- `src/research_cockpit/commands/file_schemas.py`
- `src/research_cockpit/model.py`
- `tests/test_scripts.py`

Dependencies: Task 1.4.

#### Task 2.3: Surface Metadata In Context Outputs

Description: Add compact retention/resource summaries to relevant read paths.

Acceptance:

- `run-context` shows `resources` and `output_retention` when present.
- `node-context --with-artifacts` shows artifact retention summary when present.
- Compact outputs do not become noisy; detailed nested data remains in full mode where appropriate.

Verify:

```sh
python -m unittest tests.test_scripts -k run_context
python -m unittest tests.test_scripts -k node_context
```

Files:

- `src/research_cockpit/commands/run_context.py`
- `src/research_cockpit/node_onboarding.py`
- `src/research_cockpit/context_packs.py`
- `tests/test_scripts.py`

Dependencies: Tasks 2.1 and 2.2.

### Phase 3: Semantic Lint

#### Task 3.1: Warn On Missing Retention For Completed Large Runs

Description: Extend `lint --semantic` with warning-style retention checks.

Acceptance:

- Completed runs with `output_root` but no `output_retention` get `run_completed_without_retention_policy`.
- Large artifact nodes with no `retention` get `artifact_missing_retention_policy`.
- Warnings do not make normal `validate` fail.

Verify:

```sh
python -m unittest tests.test_scripts -k semantic_lint
research-cockpit lint --root examples/demo_research_cockpit --semantic --json
```

Files:

- `src/research_cockpit/commands/lint_semantic.py`
- `tests/test_scripts.py`

Dependencies: Phase 2.

#### Task 3.2: Add Optional ML/Audio Claim Discipline Rules

Description: Add configurable warnings for evidence level and claim scope mismatch.

Acceptance:

- Rules are disabled unless configured or explicitly requested.
- Warnings include `tiny_sample_claims_generalization`, `teacher_forced_claims_generation`, `missing_effective_epochs`, and related IDs.
- Rules work from optional node/finding fields and do not require schema migration.

Verify:

```sh
python -m unittest tests.test_scripts -k claim_discipline
```

Files:

- `src/research_cockpit/commands/lint_semantic.py`
- optional config helper if introduced
- `tests/test_scripts.py`

Dependencies: Task 2.3.

### Phase 4: Dry-Run Worktree Closeout

#### Task 4.1: Add `worktree-closeout --dry-run`

Description: Generate a closeout plan for a specific worktree.

Acceptance:

- Default execution is dry-run.
- Reports RC state updates needed before cleanup.
- Reports shell command drafts for branch rename/delete and worktree removal, but does not execute them.
- Blocks on active resources, active assignment, dirty outer repo, dirty nested repo, missing finding/evidence, or missing retention policy.
- Requires explicit classification: `merge_to_main`, `preserve_as_research_branch`, `extract_partial`, or `discard_after_recording`.

Verify:

```sh
python -m unittest tests.test_scripts -k worktree_closeout
research-cockpit worktree-closeout --root examples/demo_research_cockpit --repo . --worktree <path> --classification discard_after_recording --dry-run --json
```

Files:

- `src/research_cockpit/commands/worktree_closeout.py`
- `src/research_cockpit/maintenance.py`
- `tests/test_scripts.py`

Dependencies: Phase 1 and Phase 2.

#### Task 4.2: Add Closeout Documentation And Command Discovery

Description: Make the dry-run closeout workflow discoverable.

Acceptance:

- `commands --json --compact --workflow maintenance` lists `worktree-closeout`.
- `capabilities/maintenance.md` documents the required review steps.
- The command's compact output is small enough for agent handoff.

Verify:

```sh
research-cockpit commands --json --compact --workflow maintenance
python dev/scripts/run_skill_release_check.py --json --skip-mutating
```

Files:

- `src/research_cockpit/commands/list_agent_commands.py`
- `capabilities/maintenance.md`
- `SKILL.md`
- `tests/test_scripts.py`

Dependencies: Task 4.1.

### Phase 5: Dashboard And Resource Scan Guardrails

#### Task 5.1: Add Resource Scan Limits

Description: Introduce bounded resource scan settings for dashboard/search/maintenance paths.

Acceptance:

- Scan settings include `max_files_per_artifact`, `max_bytes_per_artifact`, `skip_patterns`, and `summary_files`.
- Defaults avoid recursively reading generated audio, checkpoints, optimizer state, and precompute caches.
- Existing `--skip-resource-search` behavior remains available.

Verify:

```sh
python -m unittest tests.test_scripts -k build
python dev/scripts/benchmark_build.py --root .test_tmp/perf_1000_phase02_marker --runs 1 --json
```

Files:

- `src/research_cockpit/search_index.py`
- `src/research_cockpit/commands/build_dashboard.py`
- `src/research_cockpit/types.py`
- `tests/test_scripts.py`

Dependencies: Task 1.4.

#### Task 5.2: Extend Build Profile With Heavy Payload Warnings

Description: Report skipped heavy paths, file counts, bytes read, and slow scan candidates in build profile output.

Acceptance:

- `build --profile` includes dashboard performance warnings.
- `maintenance-audit` can reuse or reproduce the same warning logic.
- Large directories are summarized rather than fully indexed.

Verify:

```sh
research-cockpit build --root examples/demo_research_cockpit --json --profile
python -m unittest tests.test_scripts -k build_profile
```

Files:

- `src/research_cockpit/commands/build_dashboard.py`
- `src/research_cockpit/search_index.py`
- `src/research_cockpit/maintenance.py`
- `tests/test_scripts.py`

Dependencies: Task 5.1.

### Phase 6: Sparse Worktree Support

#### Task 6.1: Document Sparse Worktree Guidance

Description: Define a sparse checkout profile for large experiment repos.

Acceptance:

- Docs recommend excluding `research_cockpit/`, `outputs/`, `logs/`, `data/`, generated dataset artifacts, and virtual environments from temporary worktrees.
- Docs preserve the canonical root rule: downstream agents mutate the main checkout's `research_cockpit/`, not a worktree-local root.

Verify:

```sh
git diff --check
```

Files:

- `capabilities/integrations.md`
- `docs/large-repo-hygiene.md`

Dependencies: Phase 0.

#### Task 6.2: Add Optional `start-agent-session --sparse` Planning Support

Description: Extend session startup to generate sparse worktree commands or dry-run guidance.

Acceptance:

- `--sparse --sparse-profile ml-experiment` is opt-in.
- Dry-run output shows the git command sequence.
- Non-sparse behavior remains unchanged.
- Exact implementation may create the worktree normally, then initialize sparse checkout, or may output a helper command plan if direct execution is too platform-sensitive.

Verify:

```sh
python -m unittest tests.test_scripts -k start_agent_session
```

Files:

- `src/research_cockpit/commands/start_agent_session.py`
- `src/research_cockpit/agent_sessions.py`
- `tests/test_scripts.py`

Dependencies: Task 6.1.

## Parallelization Plan

Safe parallel work after this spec is accepted:

- Agent A: Phase 0 documentation and command routing docs.
- Agent B: Phase 1 read-only audit domain helpers and `active-resources`.
- Agent C: Phase 2 metadata write schema and tests.
- Agent D: Phase 5 dashboard scan/profile guardrails.

Must be sequential or coordinated:

- Command names, JSON schema versions, and retention class names should be agreed before multiple agents implement commands.
- `maintenance.py` shared helper contracts should land before audit command wrappers diverge.
- Mutating metadata write commands should not land before read-only audits define how the fields are consumed.
- Dry-run closeout should wait until read-only audits and metadata warnings are stable.

## Testing Strategy

Use layered tests:

- Domain helper tests for branch/worktree/resource classification.
- CLI tests for JSON payload schema, command discovery, dry-run behavior, and error messages.
- Temp git repo tests for worktree and branch audit behavior.
- Regression tests proving old demo/minimal data roots validate without new metadata.
- Build/profile tests for bounded resource scanning.

Minimum full verification before merging a phase:

```sh
python -m unittest discover -s tests
python dev/scripts/run_skill_release_check.py --json --skip-mutating
git diff --check
```

For phases that change dashboard behavior, also run:

```sh
research-cockpit build --root examples/demo_research_cockpit --json --profile
research-cockpit smoke --root examples/demo_research_cockpit --json
```

## Boundaries

Always:

- Keep cleanup audit-first.
- Keep new metadata optional and backward-compatible.
- Use `git -C <repo>` with explicit repo paths for git inspection.
- Keep destructive actions as dry-run recommendations unless a later approved spec explicitly changes that.
- Preserve assignment-scoped mutation rules and canonical root boundaries.
- Prefer compact JSON for agent handoffs.

Ask first:

- Adding a persistent config file to the data root.
- Changing default dashboard/search coverage for all users.
- Introducing new dependencies.
- Making missing retention metadata a hard validation error.
- Executing any command that deletes branches, worktrees, outputs, caches, or artifacts.

Never:

- Delete or move user files as part of audit commands.
- Store worktree-local output paths as long-lived evidence.
- Treat a failed experiment branch as disposable before checking for reusable code, eval, data builder, or bugfix changes.
- Scan arbitrary large directories without bounded limits.
- Mutate worktree-local `research_cockpit/` roots as the normal path.

## Success Criteria

- A user can run one read-only `maintenance-audit` and see active resources, branch/worktree candidates, large payload candidates, blockers, and next actions.
- Worktree closeout has a dry-run checklist that prevents removing a worktree with active runs, dirty nested repos, or unpreserved evidence.
- Artifact and run metadata can express what to keep, what is reproducible, and what is safe to clean later.
- Existing roots without retention metadata still validate and build.
- Dashboard/search performance does not depend on recursively reading heavy generated payloads.
- New commands are discoverable through `research-cockpit commands --json --compact --workflow maintenance`.
- The feature is documented in `SKILL.md`, capability docs, and human-facing docs without bloating startup instructions.

## Risks And Mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Audit output suggests unsafe cleanup | High | Include blockers, avoid destructive execution, require explicit classification, and keep commands dry-run first. |
| Metadata schema becomes too ML/audio-specific | Medium | Keep ML/audio fields optional and configurable; core retention/resource fields stay generic. |
| Git audit is brittle across platforms | Medium | Use porcelain git output, temp-repo tests, explicit paths, and nested repo opt-in. |
| Dashboard guardrails hide useful evidence from search | Medium | Preserve summary files and explicit links; report skipped resources in profile. |
| Context payloads become too large | Medium | Add compact summaries first; expose nested details only in full context or targeted commands. |
| Agents confuse audit with permission to delete | High | Capability docs and JSON payloads must state that audit is advisory and cleanup needs explicit user approval. |

## Open Questions

- Should retention metadata live only on artifact nodes, or should findings also support a lightweight retention/evidence policy block?
- Should `resource_scan` configuration live in `current_state.yaml`, a new `settings.yaml`, or only as CLI defaults in v1?
- Should `worktree-closeout` eventually execute safe `git worktree remove` and `git branch -d`, or should it remain a command-plan generator permanently?
- What exact threshold should define a "large" artifact/output candidate by default: 1 GB, 10 GB, or caller-configured only?
- Should `research/*` branch promotion be a git-only recommendation, or should RC record the promoted branch in assignment/workstream metadata?
- How much system-level process inspection should RC attempt itself versus documenting `ps`, `nvidia-smi`, `ss`, and platform-specific checks?

## Recommended First Slice

Implement Phase 0 and Task 1.1 first:

1. Add `capabilities/maintenance.md` and routing docs.
2. Add `active-resources` as the smallest useful read-only command.
3. Register `active-resources` in command discovery under `maintenance`.
4. Add tests for existing run fields plus optional `resources`.

This gives downstream agents immediate cleanup safety value without touching branch deletion, worktree deletion, or artifact retention writes.
