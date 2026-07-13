# Manual Launcher Checklist

Use this checklist when a run is started by hand and no script owns the lifecycle.

## Before Launch

- Choose `experiment_id` and `run_id`.
- Create one run output directory.
- Copy the command, config path, expected output path, and owner into `run_record.txt`.
- Add a safe `monitor_command` if another agent can run it without side effects.
- Add a `stop_command` only when it is specific and safe, such as a tmux session kill or scheduler cancel command for this run.

## During Run

- Update `progress.json` with `status`, `last_update`, `current_stage`, and warnings.
- Keep logs under `logs/`.
- Keep reusable outputs under `outputs/`.

## Gate Handoff

- Write `gate_result.json` for dry run, smoke, full run, validation, or handoff gates.
- Set `passed: false` and fill `fatal_failures` when the next action should be blocked.
- Set `next_allowed_action` when the next step is mechanically clear.

## Artifact Handoff

- Write `artifact_manifest.json`.
- List only files that should be preserved as evidence.
- Prefer relative links such as `outputs/metrics.json`, `logs/run.log`, `progress.json`, and `gate_result.json`.

## Research Cockpit Update

```sh
research-cockpit create-run --root research_cockpit --id <run_id> --experiment <experiment_id> --status running --progress-file artifacts/<experiment_id>/<run_id>/progress.json --no-build
research-cockpit ingest-artifact --root research_cockpit --node <experiment_id> --from <launcher_output_dir> --run-id <run_id> --agent <agent_id> --link gate_result=gate_result.json --no-build --json --compact
research-cockpit complete-run --print-schema
research-cockpit complete-run --root research_cockpit --file <closeout.yaml> --assignment <assignment_id> --no-build --json --compact
```

Use `complete-run --file` for the closeout. Keep the `record_id` returned by `ingest-artifact` and set it as `artifact_record.existing_record_id` in `closeout.yaml`. Put terminal run status, gate payloads, the finding, and next actions in the same transaction. Run only the returned changed-scope `verify_commands` as a worker.

Do not pass the artifact record id to `ingest-gate-result --artifact`; that flag accepts only an explicitly promoted graph artifact id. Use separate gate/finding/status commands only for recovery or a deliberately partial update. Full `validate`, `build`, and root `smoke` belong to coordinator merge, release, or final handoff.
