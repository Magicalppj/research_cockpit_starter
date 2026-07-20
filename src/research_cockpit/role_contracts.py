from __future__ import annotations

from typing import Any


ROLE_CHOICES = ("worker", "reviewer", "coordinator", "maintainer")
SURFACE_CHOICES = ("core", "advanced", "maintenance")
_ROLE_FACADE_COMMANDS = {
    "work claim",
    "work close",
    "work open",
    "work release",
    "work renew",
    "work start",
    "review open",
    "review report",
    "coord review",
}

_RETAINED_REPLACEMENTS = {
    "init": "init",
    "ui": "ui",
    "commands": "commands --role <role>",
}
_MAINTENANCE_REPLACEMENTS = {
    "lint": "maintenance audit",
    "repair-interaction-log": "maintenance repair",
    "migrate-interaction-log": "maintenance migrate",
    "active-resources": "maintenance audit",
    "worktree-audit": "maintenance audit",
    "branch-audit": "maintenance audit",
    "artifact-retention-audit": "maintenance audit",
    "maintenance-audit": "maintenance audit",
    "worktree-closeout": "maintenance audit",
    "import-worktree-findings": "maintenance migrate",
    "compact-artifacts": "maintenance compact",
    "migrate-terminal-next-actions": "maintenance migrate",
    "cleanup-suggestion-lifecycle": "maintenance repair",
}
_HANDOFF_COMMANDS = {"validate", "build", "smoke"}
_WORK_OPEN_COMMANDS = {
    "context",
    "node-context",
    "agent-session-context",
    "option-workstream-context",
    "run-context",
    "artifact-records",
}
_WORK_CLAIM_COMMANDS = {"claim-option", "claim-workstream"}
_WORK_START_COMMANDS = {"create-run"}
_WORK_RECORD_COMMANDS = {
    "update-run",
    "record-gate-result",
    "ingest-gate-result",
    "ingest-artifact",
    "record-finding",
    "update-finding",
    "create-artifact",
    "link-artifact",
    "update-node-fields",
    "update-workstream-fields",
    "create-note",
}
_WORK_CLOSE_COMMANDS = {
    "complete-run",
    "complete-experiment",
    "close-current-experiment",
    "complete-experiments",
    "create-followup-experiment",
    "report-option-workstream",
    "finalize-workstream",
    "set-cursor",
}
_COORD_OVERVIEW_COMMANDS = {
    "bootstrap",
    "search",
    "suggest-next-actions",
    "assignment-view",
}
_COORD_ASSIGN_COMMANDS = {
    "add-node",
    "apply-graph-plan",
    "create-workstream",
    "close-branch",
    "update-status",
    "set-focus",
    "set-agent-focus",
    "sync-focus-actions",
    "start-agent-session",
    "apply-suggestion",
    "update-suggestion-state",
}
_COORD_REVIEW_COMMANDS = {
    "check-decision-acceptance",
    "promote-artifact-record",
}
_COORD_DECIDE_COMMANDS = {
    "promote-decision",
    "update-decision-evidence",
    "update-decision-checklist",
    "accept-decision",
    "set-baseline",
}

_AUDIENCE_OVERRIDES = {
    "work claim": ("worker", "reviewer", "coordinator"),
    "work release": ("worker", "reviewer", "coordinator"),
    "work renew": ("worker", "reviewer", "coordinator"),
    "work start": ("worker", "coordinator"),
    "work open": ("worker", "reviewer", "coordinator"),
    "validate": ("worker", "reviewer", "coordinator", "maintainer"),
    "build": ("coordinator", "maintainer"),
    "smoke": ("worker", "coordinator", "maintainer"),
    "search": ("worker", "reviewer", "coordinator"),
    "context": ("worker", "reviewer", "coordinator"),
    "node-context": ("worker", "reviewer", "coordinator"),
    "agent-session-context": ("worker", "reviewer", "coordinator"),
    "option-workstream-context": ("worker", "reviewer", "coordinator"),
    "check-decision-acceptance": ("reviewer", "coordinator"),
}

_CORE_COMMANDS_BY_ROLE = {
    "worker": {
        "work open",
        "work claim",
        "work start",
        "work close",
        "validate",
        "smoke",
        "search",
        "commands",
    },
    "reviewer": {
        "review open",
        "review report",
        "commands",
    },
    "coordinator": {
        "bootstrap",
        "search",
        "suggest-next-actions",
        "start-agent-session",
        "set-focus",
        "check-decision-acceptance",
        "coord review",
        "promote-decision",
        "accept-decision",
        "validate",
        "build",
        "smoke",
        "commands",
    },
    "maintainer": {
        "init",
        "validate",
        "lint",
        "repair-interaction-log",
        "migrate-interaction-log",
        "build",
        "smoke",
        "maintenance-audit",
        "compact-artifacts",
        "commands",
    },
}


def canonical_replacement_for_command(name: str, group: str) -> str:
    if name in _ROLE_FACADE_COMMANDS:
        return name
    if name in _RETAINED_REPLACEMENTS:
        return _RETAINED_REPLACEMENTS[name]
    if name in _MAINTENANCE_REPLACEMENTS:
        return _MAINTENANCE_REPLACEMENTS[name]
    if name in _HANDOFF_COMMANDS:
        return "coord handoff"
    if name in _WORK_OPEN_COMMANDS:
        return "work open"
    if name in _WORK_CLAIM_COMMANDS:
        return "work claim"
    if name in _WORK_START_COMMANDS:
        return "work start"
    if name in _WORK_RECORD_COMMANDS:
        return "work record"
    if name in _WORK_CLOSE_COMMANDS:
        return "work close"
    if name in _COORD_OVERVIEW_COMMANDS:
        return "coord overview"
    if name in _COORD_ASSIGN_COMMANDS:
        return "coord assign"
    if name in _COORD_REVIEW_COMMANDS:
        return "coord review"
    if name in _COORD_DECIDE_COMMANDS:
        return "coord decide"
    fallback = {
        "artifact": "work record",
        "build": "coord handoff",
        "context": "work open",
        "decision": "coord decide",
        "focus": "coord overview",
        "graph": "coord assign",
        "maintenance": "maintenance audit",
        "run": "work record",
        "ui": "ui",
    }
    return fallback[group]


def _audiences(name: str, replacement: str) -> tuple[str, ...]:
    if name in _AUDIENCE_OVERRIDES:
        return _AUDIENCE_OVERRIDES[name]
    if replacement.startswith("work "):
        return ("worker", "coordinator")
    if replacement.startswith("review "):
        return ("reviewer", "coordinator")
    if replacement.startswith("coord "):
        return ("coordinator",)
    if replacement.startswith("maintenance ") or name == "init":
        return ("maintainer",)
    if replacement.startswith("commands"):
        return ROLE_CHOICES
    if replacement == "ui":
        return ("coordinator",)
    return ("maintainer",)


def _surface(name: str, audiences: tuple[str, ...], replacement: str) -> str:
    if any(name in names for names in _CORE_COMMANDS_BY_ROLE.values()):
        return "core"
    if replacement.startswith("maintenance ") or audiences == ("maintainer",):
        return "maintenance"
    return "advanced"


def command_surface_for_role(name: str, role: str, default_surface: str) -> str:
    if name in _CORE_COMMANDS_BY_ROLE[role]:
        return "core"
    if role == "maintainer" and default_surface == "maintenance":
        return "maintenance"
    return "advanced"


def _intent(name: str, replacement: str) -> str:
    if name == "commands":
        return "discover"
    if name in {"init", "ui"}:
        return "maintain" if name == "init" else "open"
    parts = replacement.split()
    intent = parts[1] if len(parts) > 1 else parts[0]
    return {
        "overview": "open",
        "assign": "assign",
        "audit": "maintain",
        "repair": "maintain",
        "migrate": "maintain",
        "compact": "maintain",
        "report": "review",
    }.get(intent, intent)


def command_role_contract(
    name: str,
    *,
    group: str,
    mutating: bool,
    verification_mode: str,
) -> dict[str, Any]:
    replacement = canonical_replacement_for_command(name, group)
    audiences = _audiences(name, replacement)
    if name.startswith("coord "):
        scope_policy = "coordinator"
    elif name == "review open":
        scope_policy = "read_only"
    elif name.startswith(("work ", "review ")) or replacement.startswith("work "):
        scope_policy = "assignment"
    elif not mutating:
        scope_policy = "read_only"
    elif replacement.startswith("coord "):
        scope_policy = "coordinator"
    else:
        scope_policy = "root"

    if name == "build":
        verification_policy = "milestone"
    elif name in {"validate", "smoke"}:
        verification_policy = "conditional"
    elif not mutating or verification_mode in {
        "internal_non_dry_run",
        "structured_file_non_dry_run",
    }:
        verification_policy = "internal"
    elif verification_mode == "changed_scope_after_write":
        verification_policy = "changed-scope"
    else:
        verification_policy = "conditional"

    route_kind = "facade" if name in _ROLE_FACADE_COMMANDS else "legacy"
    if route_kind == "facade" or name in _RETAINED_REPLACEMENTS:
        disposition = "retain_unique"
    elif replacement.startswith("maintenance "):
        disposition = "move_to_role_group"
    else:
        disposition = "remove_after_facade"

    if name == "work open":
        input_schema_version = "work_open_v1"
        output_schema_version = "work_packet_v1"
    elif name == "work start":
        input_schema_version = "work_start_v1"
        output_schema_version = "work_operation_v1"
    elif name == "work close":
        input_schema_version = "work_close_v1"
        output_schema_version = "work_operation_v1"
    elif name == "review open":
        input_schema_version = "review_open_v1"
        output_schema_version = "review_open_v1"
    elif name == "review report":
        input_schema_version = "review_report_v1"
        output_schema_version = "work_operation_v1"
    elif name == "coord review":
        input_schema_version = "coord_review_v1"
        output_schema_version = "work_operation_v1"
    elif route_kind == "facade":
        input_schema_version = "work_operation_input_v1"
        output_schema_version = "work_operation_v1"
    else:
        input_schema_version = "legacy_flags_v1"
        output_schema_version = "legacy_result_v1"

    return {
        "audiences": list(audiences),
        "surface": _surface(name, audiences, replacement),
        "core_roles": [role for role, names in _CORE_COMMANDS_BY_ROLE.items() if name in names],
        "intent": _intent(name, replacement),
        "work_packet_kinds": (
            ["review"]
            if name.startswith("review ")
            else (["*"] if {"worker", "reviewer"} & set(audiences) else [])
        ),
        "scope_policy": scope_policy,
        "idempotency": "required" if route_kind == "facade" and mutating else "unsupported",
        "verification_policy": verification_policy,
        "input_schema_version": input_schema_version,
        "output_schema_version": output_schema_version,
        "canonical_replacement": replacement,
        "removal_disposition": disposition,
        "route_kind": route_kind,
    }
