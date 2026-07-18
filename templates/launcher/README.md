# Launcher Templates

These templates are optional starting points for long experiment runs. They write the standard launcher output files defined in `docs/launcher-output-conventions.md` and avoid any scheduler-specific dependency.

## Modes

| Mode | Use when | Default gate type | Typical next action |
| --- | --- | --- | --- |
| `dry-run` | Verify arguments, config paths, imports, and tiny inputs. | `dry_run` | `smoke_gate` |
| `smoke-gate` | Run the smallest useful execution before a full run. | `smoke_check` | `full_run` |
| `full-run` | Start or monitor the real long-running experiment. | `full_run` | `artifact_capture` |
| `artifact-capture` | Preserve useful files after a run finishes. | `artifact_capture` | `validate_build` |
| `validate-build` | Run changed-scope worker checks, project checks, or the coordinator/final full gate. | `validation_check` | `next_action_update` |
| `next-action-update` | Record findings, gates, artifacts, and the next focus/action. | `handoff_check` | `done` |

## Python Template

```sh
python templates/launcher/run_launcher.py --run-dir .agent_runs/run_x --experiment-id experiment_x --run-id run_x --mode smoke-gate --command "python train.py --smoke"
```

The Python template creates:

- `run_record.txt`
- `progress.json`
- `gate_result.json`
- `artifact_manifest.json`
- `logs/run.log`

Pass repeated `--link key=relative/path` values for files that should become artifact links.

## Shell Template

```sh
RUN_DIR=.agent_runs/run_x EXPERIMENT_ID=experiment_x RUN_ID=run_x MODE=smoke-gate COMMAND="python train.py --smoke" sh templates/launcher/run_launcher.sh
```

The shell template uses environment variables so it can be copied into a tmux pane, CI step, scheduler wrapper, or local terminal. Set `MONITOR_COMMAND` and `STOP_COMMAND` only when the command is safe for another agent to run.

## Manual Template

Use `manual_run_checklist.md` when no script owns the run. Fill the same fields by hand, then use the ingestion commands from `docs/launcher-output-conventions.md`.

## Handoff Order

Use `--no-build` for handoff mutations. Default `ingest-artifact` creates an artifact record, and the structured closeout references that returned record through `artifact_record.existing_record_id`.

1. Start the run and experiment together with `create-run --status running --start-experiment`. 2. Preserve output once with `ingest-artifact` when canonical evidence is needed. 3. Use one `complete-run --file closeout.yaml` transaction for run, gates, record link, finding, experiment terminal state, and one optional follow-up. 4. Skip repeated validate/context when compact output is internally verified; otherwise verify changed scope only. 5. Run full gates only at coordinator merge, release, or research-stage milestone handoff.  Use a separate `ingest-gate-result` only for recovery or an intentionally partial update. Pass `--artifact <artifact_id>` only when that id is an explicitly promoted graph artifact.
