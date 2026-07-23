from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from research_cockpit.commands.cleanup_suggestion_lifecycle import (
    cleanup_suggestion_lifecycle,
)
from research_cockpit.commands.compact_artifacts import compact_artifacts_payload
from research_cockpit.commands.import_worktree_findings import import_worktree_findings
from research_cockpit.commands.migrate_interaction_log import migrate_interaction_log
from research_cockpit.commands.migrate_terminal_next_actions import (
    migrate_terminal_next_actions,
)
from research_cockpit.commands.repair_interaction_log import repair_interaction_log
from research_cockpit.artifact_migration import migrate_legacy_artifact


_ACTIONS_BY_COMMAND = {
    "repair": {"interaction_log", "suggestion_lifecycle"},
    "migrate": {
        "artifact_storage",
        "interaction_log",
        "worktree_findings",
        "terminal_next_actions",
    },
    "compact": {"artifact"},
}

_PARAMETER_FIELDS = {
    ("repair", "interaction_log"): {"show_diff"},
    ("repair", "suggestion_lifecycle"): {
        "state",
        "older_than_days",
        "show_diff",
    },
    ("migrate", "interaction_log"): set(),
    ("migrate", "artifact_storage"): {"record_id", "operation_id"},
    ("migrate", "worktree_findings"): {
        "from_root",
        "agent_id",
        "option_id",
        "show_diff",
    },
    ("migrate", "terminal_next_actions"): {
        "node_id",
        "followup_id",
        "title",
        "parent",
        "priority",
        "set_focus_to_created",
        "show_diff",
    },
    ("compact", "artifact"): {"artifact_id", "show_diff"},
}

_REQUIRED_PARAMETERS = {
    ("migrate", "artifact_storage"): {"record_id", "operation_id"},
    ("migrate", "worktree_findings"): {"from_root", "agent_id", "option_id"},
    ("migrate", "terminal_next_actions"): {"node_id"},
}


def parse_maintenance_action(
    plan: dict[str, Any],
    *,
    command: str,
    input_path: Path | None = None,
) -> dict[str, Any]:
    if command not in _ACTIONS_BY_COMMAND:
        raise ValueError(f"Unknown maintenance command: {command}")
    if plan.get("schema_version") != "maintenance_action_v1":
        raise ValueError(
            f"maintenance {command} input requires schema_version: maintenance_action_v1"
        )
    unknown = sorted(set(plan) - {"schema_version", "action", "execute", "parameters"})
    if unknown:
        raise ValueError(
            f"maintenance {command} input contains unknown fields: " + ", ".join(unknown)
        )
    action = plan.get("action")
    if action not in _ACTIONS_BY_COMMAND[command]:
        allowed = ", ".join(sorted(_ACTIONS_BY_COMMAND[command]))
        raise ValueError(
            f"action {action!r} is not supported by maintenance {command}; allowed: {allowed}"
        )
    execute = plan.get("execute", False)
    if not isinstance(execute, bool):
        raise ValueError(f"maintenance {command} execute must be a boolean")
    parameters = plan.get("parameters", {})
    if not isinstance(parameters, dict):
        raise ValueError(f"maintenance {command} parameters must be a mapping")
    allowed_parameters = _PARAMETER_FIELDS[(command, action)]
    unknown_parameters = sorted(set(parameters) - allowed_parameters)
    if unknown_parameters:
        raise ValueError(
            f"maintenance {command} {action} contains unknown parameters: "
            + ", ".join(unknown_parameters)
        )
    missing = sorted(_REQUIRED_PARAMETERS.get((command, action), set()) - set(parameters))
    if missing:
        raise ValueError(
            f"maintenance {command} {action} is missing parameters: " + ", ".join(missing)
        )

    normalized = deepcopy(parameters)
    for field in _REQUIRED_PARAMETERS.get((command, action), set()):
        value = normalized[field]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                f"maintenance {command} {action} {field} must be a non-empty string"
            )
        normalized[field] = value.strip()
    for field in ("show_diff", "set_focus_to_created"):
        if field in normalized and not isinstance(normalized[field], bool):
            raise ValueError(f"maintenance {command} {action} {field} must be a boolean")
    if "older_than_days" in normalized:
        age = normalized["older_than_days"]
        if isinstance(age, bool) or not isinstance(age, int) or age < 0:
            raise ValueError(
                f"maintenance {command} {action} older_than_days must be an integer >= 0"
            )
    if "from_root" in normalized:
        from_root = Path(normalized["from_root"])
        if not from_root.is_absolute() and input_path is not None:
            from_root = input_path.parent / from_root
        normalized["from_root"] = from_root
    return {
        "schema_version": "maintenance_action_v1",
        "action": action,
        "execute": execute,
        "parameters": normalized,
    }


def _repair(root: Path, action: str, execute: bool, parameters: dict[str, Any]) -> dict[str, Any]:
    if action == "interaction_log":
        return repair_interaction_log(
            root,
            dry_run=not execute,
            show_diff=bool(parameters.get("show_diff", False)),
        )
    return cleanup_suggestion_lifecycle(
        root,
        state=str(parameters.get("state", "all")),
        older_than_days=parameters.get("older_than_days"),
        dry_run=not execute,
        rebuild_dashboard=False,
        show_diff=bool(parameters.get("show_diff", False)),
    )


def _migrate(root: Path, action: str, execute: bool, parameters: dict[str, Any]) -> dict[str, Any]:
    if action == "artifact_storage":
        return migrate_legacy_artifact(
            root,
            record_id=parameters["record_id"],
            operation_id=parameters["operation_id"],
            execute=execute,
        )
    if action == "interaction_log":
        return migrate_interaction_log(root, dry_run=not execute)
    if action == "worktree_findings":
        return import_worktree_findings(
            root,
            from_root=parameters["from_root"],
            agent_id=parameters["agent_id"],
            option_id=parameters["option_id"],
            rebuild_dashboard=False,
            dry_run=not execute,
            show_diff=bool(parameters.get("show_diff", False)),
        )
    return migrate_terminal_next_actions(
        root,
        node_id=parameters["node_id"],
        followup_id=parameters.get("followup_id"),
        title=parameters.get("title"),
        parent=parameters.get("parent"),
        priority=parameters.get("priority"),
        set_focus_to_created=bool(parameters.get("set_focus_to_created", False)),
        rebuild_dashboard=False,
        dry_run=not execute,
        show_diff=bool(parameters.get("show_diff", False)),
        coordinator=True,
    )


def _compact(root: Path, action: str, execute: bool, parameters: dict[str, Any]) -> dict[str, Any]:
    del action
    return compact_artifacts_payload(
        root,
        artifact_id=parameters.get("artifact_id"),
        dry_run=not execute,
        execute=execute,
        show_diff=bool(parameters.get("show_diff", False)),
        rebuild_dashboard=False,
    )


_HANDLERS: dict[str, Callable[[Path, str, bool, dict[str, Any]], dict[str, Any]]] = {
    "repair": _repair,
    "migrate": _migrate,
    "compact": _compact,
}


def apply_maintenance_action(
    root: Path,
    *,
    command: str,
    plan: dict[str, Any],
    input_path: Path | None = None,
) -> dict[str, Any]:
    parsed = parse_maintenance_action(plan, command=command, input_path=input_path)
    result = _HANDLERS[command](
        root,
        parsed["action"],
        parsed["execute"],
        parsed["parameters"],
    )
    return {
        "schema_version": "maintenance_result_v1",
        "command": command,
        "action": parsed["action"],
        "executed": parsed["execute"],
        "result": result,
    }
