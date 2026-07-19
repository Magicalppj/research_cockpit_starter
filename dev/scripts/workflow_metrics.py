from __future__ import annotations

from typing import Any


CONTEXT_READ_COMMANDS = {
    "agent-session-context",
    "artifact-records",
    "assignment-view",
    "bootstrap",
    "check-decision-acceptance",
    "commands",
    "context",
    "node-context",
    "option-workstream-context",
    "run-context",
    "search",
    "smoke",
    "suggest-next-actions",
}

HIGH_LEVEL_COMMANDS = {
    "apply-graph-plan",
    "complete-experiment",
    "complete-experiments",
    "complete-run",
    "create-run",
    "context",
    "create-artifact",
    "create-workstream",
    "finalize-workstream",
    "ingest-artifact",
    "link-artifact",
    "option-workstream-context",
    "update-finding",
}

MUTATING_COMMANDS = {
    "accept-decision",
    "add-node",
    "apply-graph-plan",
    "apply-suggestion",
    "build",
    "claim-option",
    "cleanup-suggestion-lifecycle",
    "complete-experiment",
    "complete-experiments",
    "close-branch",
    "compact-artifacts",
    "complete-run",
    "create-followup-experiment",
    "create-run",
    "ingest-artifact",
    "ingest-gate-result",
    "migrate-interaction-log",
    "promote-artifact-record",
    "record-gate-result",
    "set-cursor",
    "start-agent-session",
    "update-run",
    "create-artifact",
    "create-note",
    "create-workstream",
    "finalize-workstream",
    "init",
    "link-artifact",
    "promote-decision",
    "record-finding",
    "report-option-workstream",
    "set-focus",
    "sync-focus-actions",
    "update-decision-checklist",
    "update-decision-evidence",
    "update-finding",
    "update-node-fields",
    "update-status",
    "update-suggestion-state",
}

TRUTH_SOURCE_MUTATION_COMMANDS = MUTATING_COMMANDS - {
    "build",
}

TRUTH_SOURCE_SUFFIXES = (".yaml", ".yml", ".md")


def command_name(command: list[str]) -> str | None:
    if not command:
        return None
    if command[0] == "research-cockpit" and len(command) > 1:
        return command[1]
    for index, item in enumerate(command[:-1]):
        if item == "-m" and command[index + 1] == "research_cockpit.cli" and len(command) > index + 2:
            return command[index + 2]
    return None



def _optional_numeric_sum(
    command_rows: list[tuple[str, dict[str, Any]]],
    field: str,
    *,
    floating: bool = False,
) -> int | float | None:
    if not command_rows or any(
        field not in check or check[field] is None for _, check in command_rows
    ):
        return None
    if floating:
        return round(
            sum(max(0.0, float(check[field])) for _, check in command_rows),
            3,
        )
    return sum(max(0, int(check[field])) for _, check in command_rows)


def _optional_output_sum(
    command_rows: list[tuple[str, dict[str, Any]]],
) -> int | None:
    if not command_rows:
        return 0
    fields = ("stdout_bytes", "stderr_bytes")
    if any(
        field not in check or check[field] is None
        for _, check in command_rows
        for field in fields
    ):
        return None
    return sum(
        max(0, int(check["stdout_bytes"]))
        + max(0, int(check["stderr_bytes"]))
        for _, check in command_rows
    )


def _internally_verified_mutation(check: dict[str, Any]) -> bool:
    if not check.get("passed", False):
        return False
    payload = check.get("json")
    if not isinstance(payload, dict):
        return False
    verification = payload.get("verification")
    if isinstance(verification, dict):
        return (
            verification.get("status") in {"internal_verify", "internally_verified"}
            and verification.get("additional_verification_required") is False
        )
    return (
        payload.get("verified") is True
        and payload.get("verification_stage") in {"internal_verify", "internally_verified"}
        and payload.get("additional_verification_required") is False
    )


def workflow_metrics(
    checks: list[dict[str, Any]],
    *,
    files_changed: list[str] | None = None,
    documentation_bytes: int | None = None,
) -> dict[str, Any]:
    command_rows: list[tuple[str, dict[str, Any]]] = []
    for check in checks:
        name = command_name(check.get("command", []))
        if name:
            command_rows.append((name, check))
    commands = [name for name, _ in command_rows if name]
    failed = [name for name, check in command_rows if name and not check.get("passed", False)]
    high_level = sorted({name for name in commands if name in HIGH_LEVEL_COMMANDS})
    mutating_count = sum(1 for name in commands if name in MUTATING_COMMANDS)
    model_visible_output_bytes = _optional_output_sum(command_rows)
    control_plane_wall_time_ms = _optional_numeric_sum(
        command_rows,
        "duration_ms",
        floating=True,
    )
    state_load_count = _optional_numeric_sum(command_rows, "state_load_count")
    nested_subprocess_count = _optional_numeric_sum(
        command_rows,
        "nested_subprocess_count",
    )
    documentation_input_bytes = (
        max(0, int(documentation_bytes))
        if documentation_bytes is not None
        else None
    )
    changed = files_changed or []
    truth_changes = [
        path
        for path in changed
        if path.startswith("research_cockpit/")
        and not path.startswith("research_cockpit/dashboards/")
        and path.endswith(TRUTH_SOURCE_SUFFIXES)
    ]
    has_truth_mutation = any(name in TRUTH_SOURCE_MUTATION_COMMANDS for name in commands)
    explained_truth_source_changes = truth_changes if has_truth_mutation else []
    extra_verification_after_mutation_count = 0
    internally_verified_mutation_seen = False
    verification_commands = CONTEXT_READ_COMMANDS | {"validate", "build"}
    for name, check in command_rows:
        if internally_verified_mutation_seen and name in verification_commands:
            extra_verification_after_mutation_count += 1
        if name in TRUTH_SOURCE_MUTATION_COMMANDS:
            internally_verified_mutation_seen = _internally_verified_mutation(check)

    return {
        "command_count": len(commands),
        "failed_command_count": len(failed),
        "context_read_count": sum(1 for name in commands if name in CONTEXT_READ_COMMANDS),
        "mutating_count": mutating_count,
        "dry_run_count": sum(1 for _, check in command_rows if "--dry-run" in check.get("command", [])),
        "build_count": sum(1 for name, check in command_rows if name == "build" or "--build" in check.get("command", [])),
        "validate_count": sum(1 for name in commands if name == "validate"),
        "manual_yaml_patch_detected": bool(truth_changes and not has_truth_mutation),
        "truth_source_changed_files": truth_changes,
        "explained_truth_source_changes": explained_truth_source_changes,
        "high_level_commands_used": high_level,
        "model_visible_output_bytes": model_visible_output_bytes,
        "documentation_input_bytes": documentation_input_bytes,
        "estimated_output_tokens": (
            (model_visible_output_bytes + 3) // 4
            if model_visible_output_bytes is not None
            else None
        ),
        "estimated_visible_tokens": (
            (model_visible_output_bytes + documentation_input_bytes + 3) // 4
            if (
                model_visible_output_bytes is not None
                and documentation_input_bytes is not None
            )
            else None
        ),
        "token_estimation": {
            "method": "utf8_bytes_div_4",
            "tokenizer": None,
            "measured": False,
        },
        "control_plane_wall_time_ms": control_plane_wall_time_ms,
        "state_load_count": state_load_count,
        "nested_subprocess_count": nested_subprocess_count,
        "measurements": {
            "model_visible_output_bytes": model_visible_output_bytes is not None,
            "documentation_input_bytes": documentation_input_bytes is not None,
            "control_plane_wall_time_ms": control_plane_wall_time_ms is not None,
            "state_load_count": state_load_count is not None,
            "nested_subprocess_count": nested_subprocess_count is not None,
        },
        "extra_verification_after_mutation_count": extra_verification_after_mutation_count,
    }
