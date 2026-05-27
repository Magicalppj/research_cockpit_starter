# ADR-0003: Keep Run And Gate Records As Sidecar Execution State

## Status

Accepted

## Date

2026-05-27

## Context

Long experiments need operational state that is more specific than an experiment node: launcher command, tmux session, pid, log paths, progress heartbeat, stop command, and gate outcomes. Folding every execution into the graph as another node would make the research graph noisy, while leaving this state only in logs would make multi-agent handoffs hard to audit.

The system also needs standard machine-readable gates for dataset checks, cache checks, smoke runs, training runs, evaluation, and preflight resource checks.

## Decision

Store concrete executions as sidecar run records under:

```text
research_cockpit/runs/*.yaml
```

Store gate metadata records under:

```text
research_cockpit/gate_results/*.yaml
```

`record-gate-result` may also write the gate payload itself under `research_cockpit/gate_results/*.json`; `ingest-gate-result` links an existing payload under `research_cockpit/artifacts/**`.

Run records and gate records reference experiment nodes, runs, artifacts, and stable files in the canonical artifact store. They are structured state, but they are not graph nodes by default. Context builders surface short summaries in bootstrap, node context, option workstream context, and dashboards; agents can use `run-context` when they need monitor or stop details.

Launcher output files use stable conventions:

- `run_record.txt` for human handoff.
- `progress.json` for heartbeat state.
- `gate_result.json` for gate outcome and blocking semantics.
- `artifact_manifest.json` for evidence links.

Frequent run and gate updates should use `--no-build` and one final validate/build/smoke pass, or a separate dashboard watcher.

## Alternatives Considered

### Make Runs And Gates Graph Nodes

- Pros: every execution appears directly in the graph.
- Cons: long experiments and retries would clutter the research hierarchy and make option/problem structure harder to read.
- Rejected because run/job state is operational detail attached to experiments, not a research question or conclusion.

### Keep Run State Only In Artifacts Or Logs

- Pros: no new sidecar directories.
- Cons: agents would need to parse ad hoc files and could not mechanically identify stale, failed, blocking, or completed executions.
- Rejected because multi-agent handoff needs a stable read model.

### Embed Runs Inside Experiment Nodes

- Pros: one YAML file per experiment.
- Cons: frequent heartbeat/status updates would churn graph node files and increase mutation conflicts with finding and lifecycle edits.
- Rejected in favor of smaller sidecar records that can be updated independently.

## Consequences

- Experiments remain the graph-level unit of research intent and conclusion.
- Runs are the execution-level unit for launch, monitor, progress, and stop details.
- Gate results are machine-readable records that can block or allow next actions.
- `research-cockpit` commands, not manual YAML edits, remain the stable write surface for run and gate records.
- Documentation and agent rules must treat `runs/*.yaml`, `gate_results/*.yaml`, and recorded gate JSON payloads as structured truth-source state alongside graph YAML.
