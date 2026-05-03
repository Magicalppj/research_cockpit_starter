# Subagent Forward Test Cases

This note records the two manual subagent workflow cases that informed
`dev/scripts/run_subagent_forward_check.py`. They are kept in `dev/` because
they are project-development verification material, not agent-facing skill
instructions.

## Scope

- Use isolated temporary data roots under `.test_tmp/`.
- Do not write `src/`, `tests/`, `SKILL.md`, `README.md`, or `capabilities/`
  during the subagent run.
- Prefer `research-cockpit` CLI commands over direct YAML edits.
- Record command count, failed commands, context reads, build/validate count,
  manual YAML patches, and unclear CLI affordances.
- Finish each run with `validate --json` and `build`.

## Case A: New Workstream Creation

Goal: test whether an agent with no implementation context can create a new
problem, active option, planned experiments, and a follow-up option without
hand-editing YAML or rebuilding after every node.

Suggested isolated root:

```text
.test_tmp/agent_flow_a/research_cockpit
```

Required graph:

- `problem_agent_flow_latency_budget`
  - `status: active`
  - question: how to reduce command count and repeated context reads
  - hypothesis: high-level batch graph commands reduce YAML patches and rebuilds
- `option_batch_graph_mutation_api`
  - `status: active`
  - should be the problem `current_best_option`
- Planned experiments under the active option:
  - `experiment_agent_flow_command_count`
  - `experiment_agent_flow_no_yaml_patch`
  - `experiment_agent_flow_single_build`
- Follow-up option under the same problem:
  - `option_agent_flow_context_command`
  - `status: planned` input is acceptable when the CLI normalizes to stored
    `open`

Expected short path:

```sh
research-cockpit init --root <root> --build --json
research-cockpit create-workstream --root <root> --file workstream.yaml --dry-run --json --show-diff
research-cockpit create-workstream --root <root> --file workstream.yaml --no-build
research-cockpit option-workstream-context --root <root> --id option_batch_graph_mutation_api --compact --json
research-cockpit validate --root <root> --json
research-cockpit build --root <root>
```

Pass signals:

- The agent naturally discovers `create-workstream` or `apply-graph-plan`.
- No direct YAML patch is needed for `children`, `current_best_option`, or
  `supporting_experiments`.
- Compact option context includes experiment summaries with criteria and metric
  counts, so per-experiment `node-context` is not needed for quick verification.

## Case B: Evidence Close-Out

Goal: test whether an agent can create minimal initial graph state, record an
artifact, batch-complete experiments, revise a finding, and finalize an option
workstream without low-level YAML edits.

Suggested isolated root:

```text
.test_tmp/agent_flow_b/research_cockpit
```

Required initial graph:

- `problem_agent_flow_overfit_gate`
- `option_small_batch_overfit_gate`
- `experiment_overfit_gate_cached_init`
- `experiment_overfit_gate_fresh_init`

Required evidence:

- `artifact_overfit_gate_results_20260503`
  - `status: done`
  - `path: research_cockpit/notes/options/option_small_batch_overfit_gate.md`
  - links:
    - `cached_init=outputs/cached_init_metrics.json`
    - `fresh_init=outputs/fresh_init_metrics.json`
  - linked to the option or related experiments

Required experiment completion:

- `experiment_overfit_gate_cached_init`
  - finding: cached initialization passes the overfit gate with stable loss
    decrease
  - later revise this finding statement to Chinese
- `experiment_overfit_gate_fresh_init`
  - finding: fresh initialization is slower and should remain a diagnostic
    baseline
- Both findings link to the artifact.
- Each experiment records `result_summary` and `next_actions`.

Expected short path:

```sh
research-cockpit init --root <root> --build --json
research-cockpit create-workstream --root <root> --file workstream.yaml --no-build
research-cockpit create-artifact --root <root> --file artifact.yaml --dry-run --json --show-diff
research-cockpit create-artifact --root <root> --file artifact.yaml --no-build
research-cockpit complete-experiments --root <root> --file findings.yaml --dry-run --json --show-diff
research-cockpit complete-experiments --root <root> --file findings.yaml --no-build
research-cockpit update-finding --root <root> --experiment experiment_overfit_gate_cached_init --finding-id <finding_id> --statement "cached initialization passes the overfit gate with stable loss decrease." --dry-run --json --show-diff
research-cockpit update-finding --root <root> --experiment experiment_overfit_gate_cached_init --finding-id <finding_id> --statement "缓存初始化通过 overfit gate，loss 下降稳定。" --no-build
research-cockpit finalize-workstream --root <root> --file finalize.yaml --dry-run --json --compact
research-cockpit finalize-workstream --root <root> --file finalize.yaml --no-build
research-cockpit validate --root <root> --json
research-cockpit build --root <root>
```

Pass signals:

- Artifact `path`, `links`, and reverse `linked_artifacts` are written through
  CLI commands.
- `complete-experiments --file` avoids repeated `complete-experiment` calls.
- `update-finding` preserves `created_at` and writes `updated_at`.
- `finalize-workstream --file` writes the report by default without replacing
  node summaries unless `summary_target` explicitly requests it.
- No focus switch, decision acceptance, old-option pause, or branch deletion
  happens implicitly.

## Automated Coverage

`dev/scripts/run_subagent_forward_check.py` covers the same friction points in a
repeatable harness: cwd-independent reads, isolated workstream mutation,
retrieval branch expansion, compact option context verification, file-based
finalization, portable startup, and package hygiene. Run it with:

```powershell
python dev\scripts\run_subagent_forward_check.py --json
```
