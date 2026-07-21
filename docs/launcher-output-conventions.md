# Launcher Output Conventions

These conventions apply to shell scripts, Python launchers, notebooks, scheduler jobs, and manual runs. Launcher files are local execution state until `work record` or `work close` stages selected evidence into the canonical data root.

Starter templates live in `templates/launcher/`. Keep paths inside launcher files relative to one run output directory so the bundle remains portable across platforms and worktrees.

## Standard Files

| File | Purpose | Research Cockpit boundary |
| --- | --- | --- |
| `run_record.txt` | Human-readable ids, command, process hints, and paths | Summarize immutable run fields in `work start`; terminal state belongs in `work close`. |
| `progress.json` | Launcher-owned heartbeat and progress | Keep local during execution; include it in evidence links only when useful for review. |
| `gate_result.json` | Gate outcome and observations | Include required gate data in `work_close_v1.gates`; preserve the file as evidence when useful. |
| `artifact_manifest.json` | Files worth preserving | Translate selected relative paths into `evidence_inputs.links`. |

## Run Record

`run_record.txt` is line-oriented so any launcher can write it:

```text
schema_version: launcher_run_record_v1
run_id: <runtime_generated_after_work_start>
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
monitor_command:
stop_command:
progress_file: progress.json
gate_result_file: gate_result.json
artifact_manifest_file: artifact_manifest.json
config_file: config.yaml
notes:
```

`monitor_command` 与 `stop_command` 都是可选 launcher metadata。只有存在跨平台或明确限定平台、无副作用的命令时才填写；通用模板不假设 `tail`、tmux 或特定 shell。

Start through the assignment facade and retain the runtime-generated run id from its receipt:

```sh
research-cockpit work start --root <data-root> --assignment <assignment_id> --file <work_start.yaml> --json --compact
```

Do not add a control-plane command for each progress update. The launcher owns `progress.json`; normal mutations and launcher heartbeat renew the lease outside model turns.

## Progress And Gates

Recommended progress shape:

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

Recommended gate shape:

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

Failed gates set `passed: false` and record blockers in `fatal_failures`. Gate files do not mutate Research Cockpit by themselves; submit the structured gate result once through closeout.

## Artifact Manifest

`artifact_manifest.json` identifies evidence rather than scratch output:

```json
{
  "schema_version": "artifact_manifest_v1",
  "title": "Run x output bundle",
  "experiment_id": "experiment_x",
  "run_id": "run_x",
  "summary": "Smoke run output and logs.",
  "links": {
    "metrics": "outputs/metrics.json",
    "config": "config.yaml",
    "log": "logs/run.log",
    "gate_result": "gate_result.json"
  },
  "warnings": []
}
```

Link values must be relative to the source directory. Final output is staged once through closeout:

```yaml
evidence_inputs:
  source: <launcher_output_dir>
  title: Run x output bundle
  summary: Smoke run output and logs.
  links:
    metrics: outputs/metrics.json
    config: config.yaml
    gate_result: gate_result.json
```

```sh
research-cockpit work close --root <data-root> --assignment <assignment_id> --file <work_close.yaml> --json --compact
```

Use `work record --file <record.yaml>` only when output must be durable before close. Reuse the returned id as `artifact_record.existing_record_id` in closeout; do not combine that field with `evidence_inputs`.

```sh
research-cockpit work record --root <data-root> --assignment <assignment_id> --file <record.yaml> --json --compact
```



## Launcher-Neutral Flow

1. Open the assignment packet and start one run.
2. Create one output directory and update launcher-local progress during execution.
3. Write gate results and a bounded artifact manifest.
4. Close once with terminal run, experiment, finding, assignment result, gates, and final evidence.
5. Stop after an internally verified receipt; do not repeat validate, context, build, or smoke.

Do not store machine-local absolute paths in canonical records. Relative input paths are resolved from the structured input file, and staged links remain data-root relative.
