# Launcher Templates

These templates are optional starting points for long experiment runs. They write the standard launcher output files defined in `docs/launcher-output-conventions.md` and avoid any scheduler-specific dependency.

## Modes

| Mode | Use when | Default gate type | Typical next action |
| --- | --- | --- | --- |
| `dry-run` | Verify arguments, config paths, imports, and tiny inputs. | `dry_run` | `smoke_gate` |
| `smoke-gate` | Run the smallest useful execution before a full run. | `smoke_check` | `full_run` |
| `full-run` | Start or monitor the real long-running experiment. | `full_run` | `artifact_capture` |
| `artifact-capture` | Preserve useful files after a run finishes. | `artifact_capture` | `validate_build` |
| `validate-build` | Run `validate`, `build`, `smoke`, or project checks before handoff. | `validation_check` | `next_action_update` |
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

1. Create or update the run with `create-run` or `update-run`.
2. Preserve the output directory with `ingest-artifact`.
3. Attach `gate_result.json` with `ingest-gate-result`.
4. Record conclusions with `record-finding`, `complete-experiment`, or a next-action update.
5. Run final `validate`, `build`, and `smoke` once after batched state updates.
