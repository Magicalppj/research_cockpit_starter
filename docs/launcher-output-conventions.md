# Launcher Output Conventions

Use these conventions when a shell script, Python script, tmux session, notebook, scheduler job, or manual run writes outputs for a Research Cockpit experiment. They are recommendations, not a required runtime dependency. A launcher may stage files anywhere, but the stable copy should end up under:

```text
research_cockpit/artifacts/<experiment_id>/<run_id>/
```

Starter templates live in `templates/launcher/`. Use them as copyable examples for dry runs, smoke gates, full runs, artifact capture, validate/build handoffs, and next action updates.

Keep paths in launcher files relative to the run output directory whenever possible. Convert them to data-root relative paths, such as `artifacts/<experiment_id>/<run_id>/progress.json`, when recording runs, gates, or artifacts through `research-cockpit`.

## Standard Files

| File | Purpose | Ingest path |
| --- | --- | --- |
| `run_record.txt` | Human-readable run handoff: ids, commands, process hints, and output paths. | Put initial metadata in `work start`, then use `update-run` or `complete-run` only when needed. |
| `progress.json` | Machine-readable heartbeat for long-running work. | Reference with `--progress-file artifacts/<experiment_id>/<run_id>/progress.json`. |
| `gate_result.json` | Machine-readable gate outcome for preflight, dataset, cache, smoke, training, or evaluation gates. | Attach with `ingest-gate-result` or write with `record-gate-result`. |
| `artifact_manifest.json` | Machine-readable summary of files worth preserving as evidence. | Use it to choose `ingest-artifact --link` values or `create-artifact --link` values. |

## `run_record.txt`

`run_record.txt` is intentionally line-oriented text so shell, Python, and manual launch flows can create it without a YAML or JSON library:

```text
schema_version: launcher_run_record_v1
run_id: run_x
experiment_id: experiment_x
status: running
launcher: shell
command: python train.py --config config.yaml
started_at: 2026-05-27T09:00:00Z
finished_at:
pid:
tmux_session:
log_root: logs
output_root: outputs
monitor_command: tail -f logs/run.log
stop_command:
progress_file: progress.json
gate_result_file: gate_result.json
artifact_manifest_file: artifact_manifest.json
config_file: config.yaml
notes:
```

Only `run_id`, `experiment_id`, `status`, and `command` are expected for every launcher. Fill `pid`, `tmux_session`, `monitor_command`, and `stop_command` when they are known. Manual flows can leave process fields blank and still provide useful handoff context.

Typical run ingestion:

```sh
research-cockpit work start --root research_cockpit --assignment <assignment_id> --file work_start.yaml --json --compact
research-cockpit update-run --root research_cockpit --assignment <assignment_id> --id run_x --status running --progress-file artifacts/experiment_x/run_x/progress.json --no-build
research-cockpit complete-run --print-schema
research-cockpit complete-run --root research_cockpit --file closeout.yaml --assignment <assignment_id> --json --compact --no-build
```

## `progress.json`

`progress.json` follows the standard heartbeat schema used by run summaries:

```json
{
  "status": "running",
  "completed_steps": 12,
  "total_steps": 64,
  "last_update": "2026-05-27T09:30:00Z",
  "current_stage": "synthesis",
  "latest_artifact": "outputs/partial.json",
  "warnings": []
}
```

`total_steps` may be omitted or null when the total is unknown. `last_update` should be an ISO-8601 timestamp. If the launcher stages this file outside the data root, copy or ingest it so the run record can reference the stable `artifacts/<experiment_id>/<run_id>/progress.json` path.

## `gate_result.json`

`gate_result.json` uses the standard gate result schema:

```json
{
  "gate_type": "smoke_check",
  "passed": true,
  "expected": {},
  "observed": {},
  "fatal_failures": {},
  "warnings": [],
  "next_allowed_action": "full_run",
  "experiment_id": "experiment_x",
  "run_id": "run_x"
}
```

Failed gates should set `passed` to `false` and put blocking details in `fatal_failures`. Warning-only gates keep `passed: true` and list warnings. Preserve the run output directory first so the gate file path is stable, then attach the gate file. Add `--artifact <artifact_id>` only after promoting the run output to a graph artifact node:

```sh
research-cockpit ingest-gate-result --root research_cockpit --id gate_x --file artifacts/experiment_x/run_x/gate_result.json --run run_x --json --compact --no-build
```

For long-run preflight checks, use `gate_type: "preflight"` and add a `preflight` object with disk, GPU, port, cache directory, and conflicting process observations. Failed preflight gates block `full_run` in context:

```json
{
  "gate_type": "preflight",
  "passed": false,
  "preflight": {
    "disk_available_gb": 120,
    "estimated_required_gb": 800,
    "gpu_ids": [0, 1],
    "port_available": true,
    "cache_dir": "cache/precompute",
    "cache_dir_exists": true,
    "conflicting_processes": ["python train.py"]
  },
  "fatal_failures": {
    "disk": "insufficient"
  },
  "next_allowed_action": "full_run"
}
```

## `artifact_manifest.json`

`artifact_manifest.json` tells an agent which files in the run directory are evidence rather than scratch output:

```json
{
  "schema_version": "artifact_manifest_v1",
  "artifact_id": "artifact_experiment_x_run_x",
  "title": "Run x output bundle",
  "experiment_id": "experiment_x",
  "run_id": "run_x",
  "summary": "Smoke run output and logs.",
  "path": ".",
  "links": {
    "metrics": "outputs/metrics.json",
    "config": "config.yaml",
    "log": "logs/run.log",
    "gate_result": "gate_result.json",
    "progress": "progress.json"
  },
  "warnings": []
}
```

Use link values relative to the run output directory. When the output directory is disposable, first preserve it with `ingest-artifact`; repeated `--link key=relative/path` values should come from the manifest:

```sh
research-cockpit ingest-artifact --root research_cockpit --node experiment_x --from <launcher_output_dir> --run-id run_x --agent agent_x --link metrics=outputs/metrics.json --link config=config.yaml --link gate_result=gate_result.json --json --compact --no-build
```

`ingest-artifact` returns a runtime-generated record id in `target.artifact_id`. Reuse that exact value as `artifact_record.existing_record_id`; do not derive it from experiment or run ids. Keep ordinary output as a record, and promote it with `promote-artifact-record --promotion-reason "..."` only when a graph artifact is required.

If the run directory already lives at a stable path, create or link the artifact directly:

```sh
research-cockpit create-artifact --root research_cockpit --id artifact_experiment_x_run_x --title "Run x output bundle" --path artifacts/experiment_x/run_x --link metrics=artifacts/experiment_x/run_x/outputs/metrics.json --link-to experiment_x --no-build
```

## Launcher-Neutral Flow

Shell, Python, and manual launch flows should follow the same sequence:

1. Create one output directory for the run.
2. Write `run_record.txt` before or immediately after launch.
3. Update `progress.json` during long-running work.
4. Write `gate_result.json` for each gate that should drive the next action.
5. Write `artifact_manifest.json` before handoff so an agent can preserve only useful outputs.
6. Ingest the artifact bundle once, then use one `complete-run --file <closeout.yaml>` transaction to close the run, reference `artifact_record.existing_record_id`, attach gates, record the finding, finish the experiment, and optionally create one follow-up. With an assignment, the follow-up becomes its cursor. An internally verified result needs no repeated validate/context.

Do not store machine-local absolute paths in canonical Research Cockpit records. Keep those details in local launcher logs unless they are needed as short human hints in `run_record.txt`.
