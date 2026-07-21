#!/usr/bin/env sh
set -eu

RUN_DIR="${RUN_DIR:-./launcher_run}"
EXPERIMENT_ID="${EXPERIMENT_ID:-experiment_x}"
RUN_ID="${RUN_ID:-run_x}"
# Supported MODE values: dry-run, smoke-gate, full-run, artifact-capture, validate-build, next-action-update.
MODE="${MODE:-smoke-gate}"
LAUNCHER="${LAUNCHER:-shell}"
COMMAND="${COMMAND:-}"
MONITOR_COMMAND="${MONITOR_COMMAND:-}"
STOP_COMMAND="${STOP_COMMAND:-}"
TIMESTAMP="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

case "$MODE" in
  dry-run)
    DEFAULT_STATUS="completed"
    DEFAULT_GATE_TYPE="dry_run"
    DEFAULT_NEXT_ALLOWED_ACTION="smoke_gate"
    ;;
  smoke-gate)
    DEFAULT_STATUS="completed"
    DEFAULT_GATE_TYPE="smoke_check"
    DEFAULT_NEXT_ALLOWED_ACTION="full_run"
    ;;
  full-run)
    DEFAULT_STATUS="running"
    DEFAULT_GATE_TYPE="full_run"
    DEFAULT_NEXT_ALLOWED_ACTION="artifact_capture"
    ;;
  artifact-capture)
    DEFAULT_STATUS="completed"
    DEFAULT_GATE_TYPE="artifact_capture"
    DEFAULT_NEXT_ALLOWED_ACTION="validate_build"
    ;;
  validate-build)
    DEFAULT_STATUS="completed"
    DEFAULT_GATE_TYPE="validation_check"
    DEFAULT_NEXT_ALLOWED_ACTION="next_action_update"
    ;;
  next-action-update)
    DEFAULT_STATUS="completed"
    DEFAULT_GATE_TYPE="handoff_check"
    DEFAULT_NEXT_ALLOWED_ACTION="done"
    ;;
  *)
    printf 'Unsupported MODE: %s\n' "$MODE" >&2
    exit 2
    ;;
esac

STATUS="${STATUS:-$DEFAULT_STATUS}"
NEXT_ALLOWED_ACTION="${NEXT_ALLOWED_ACTION:-$DEFAULT_NEXT_ALLOWED_ACTION}"
GATE_TYPE="${GATE_TYPE:-$DEFAULT_GATE_TYPE}"
GATE_PASSED="${GATE_PASSED:-true}"
COMPLETED_STEPS="0"
if [ "$STATUS" = "completed" ] || [ "$STATUS" = "failed" ] || [ "$STATUS" = "cancelled" ]; then
  COMPLETED_STEPS="1"
fi
FATAL_FAILURES="{}"
if [ "$GATE_PASSED" != "true" ]; then
  FATAL_FAILURES="{\"reason\":\"gate failed\"}"
fi

mkdir -p "$RUN_DIR/logs" "$RUN_DIR/outputs"
printf '%s mode=%s status=%s command=%s\n' "$TIMESTAMP" "$MODE" "$STATUS" "$COMMAND" > "$RUN_DIR/logs/run.log"

cat > "$RUN_DIR/run_record.txt" <<EOF
schema_version: launcher_run_record_v1
run_id: $RUN_ID
experiment_id: $EXPERIMENT_ID
status: $STATUS
launcher: $LAUNCHER
mode: $MODE
command: $COMMAND
started_at: $TIMESTAMP
finished_at:
pid:
tmux_session:
log_root: logs
output_root: outputs
monitor_command: $MONITOR_COMMAND
stop_command: $STOP_COMMAND
progress_file: progress.json
gate_result_file: gate_result.json
artifact_manifest_file: artifact_manifest.json
config_file:
next_allowed_action: $NEXT_ALLOWED_ACTION
notes:
EOF

cat > "$RUN_DIR/progress.json" <<EOF
{
  "status": "$STATUS",
  "completed_steps": $COMPLETED_STEPS,
  "total_steps": 1,
  "last_update": "$TIMESTAMP",
  "current_stage": "$MODE",
  "latest_artifact": "artifact_manifest.json",
  "warnings": []
}
EOF

cat > "$RUN_DIR/gate_result.json" <<EOF
{
  "gate_type": "$GATE_TYPE",
  "passed": $GATE_PASSED,
  "expected": {},
  "observed": {
    "mode": "$MODE",
    "status": "$STATUS"
  },
  "fatal_failures": $FATAL_FAILURES,
  "warnings": [],
  "next_allowed_action": "$NEXT_ALLOWED_ACTION",
  "experiment_id": "$EXPERIMENT_ID",
  "run_id": "$RUN_ID"
}
EOF

cat > "$RUN_DIR/artifact_manifest.json" <<EOF
{
  "schema_version": "artifact_manifest_v1",
  "artifact_id": "artifact_${EXPERIMENT_ID}_${RUN_ID}",
  "title": "$RUN_ID output bundle",
  "experiment_id": "$EXPERIMENT_ID",
  "run_id": "$RUN_ID",
  "summary": "$MODE launcher output.",
  "path": ".",
  "links": {
    "run_record": "run_record.txt",
    "progress": "progress.json",
    "gate_result": "gate_result.json",
    "log": "logs/run.log"
  },
  "warnings": []
}
EOF

printf '{"run_dir":"%s","mode":"%s","status":"%s"}\n' "$RUN_DIR" "$MODE" "$STATUS"
