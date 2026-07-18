from __future__ import annotations

from typing import Any


CONTEXT_READ_COMMANDS = {
    "bootstrap",
    "check-decision-acceptance",
    "commands",
    "context",
    "node-context",
    "option-workstream-context",
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


def workflow_metrics(
    checks: list[dict[str, Any]],
    *,
    files_changed: list[str] | None = None,
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
    }
