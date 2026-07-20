from __future__ import annotations

from research_cockpit.role_contracts import (
    ROLE_CHOICES,
    SURFACE_CHOICES,
    command_role_contract,
    command_surface_for_role,
)


COMMAND_MODULES: dict[str, str] = {
    "bootstrap": "agent_bootstrap",
    "validate": "validate_cockpit",
    "lint": "lint_semantic",
    "repair-interaction-log": "repair_interaction_log",
    "migrate-interaction-log": "migrate_interaction_log",
    "build": "build_dashboard",
    "commands": "list_agent_commands",
    "smoke": "skill_smoke_test",
    "search": "search_knowledge",
    "suggest-next-actions": "suggest_next_actions",
    "context": "context",
    "assignment-view": "assignment_view",
    "node-context": "node_context",
    "option-workstream-context": "option_workstream_context",
    "agent-session-context": "agent_session_context",
    "check-decision-acceptance": "check_decision_acceptance",
    "add-node": "add_node",
    "apply-graph-plan": "apply_graph_plan",
    "create-workstream": "create_workstream",
    "close-branch": "close_branch",
    "update-status": "update_status",
    "set-focus": "set_focus",
    "set-agent-focus": "set_agent_focus",
    "set-cursor": "set_cursor",
    "sync-focus-actions": "sync_focus_actions",
    "claim-option": "claim_option",
    "claim-workstream": "claim_workstream",
    "start-agent-session": "start_agent_session",
    "create-run": "create_run",
    "update-run": "update_run",
    "complete-run": "complete_run",
    "list-runs": "list_runs",
    "active-resources": "active_resources",
    "worktree-audit": "worktree_audit",
    "branch-audit": "branch_audit",
    "artifact-retention-audit": "artifact_retention_audit",
    "maintenance-audit": "maintenance_audit",
    "worktree-closeout": "worktree_closeout",
    "run-context": "run_context",
    "record-gate-result": "record_gate_result",
    "ingest-gate-result": "ingest_gate_result",
    "report-option-workstream": "report_option_workstream",
    "finalize-workstream": "finalize_workstream",
    "import-worktree-findings": "import_worktree_findings",
    "ingest-artifact": "ingest_artifact",
    "artifact-records": "list_artifact_records",
    "promote-artifact-record": "promote_artifact_record",
    "compact-artifacts": "compact_artifacts",
    "record-finding": "record_finding",
    "update-finding": "update_finding",
    "create-artifact": "create_artifact",
    "link-artifact": "link_artifact",
    "complete-experiment": "complete_experiment",
    "close-current-experiment": "close_current_experiment",
    "complete-experiments": "complete_experiments",
    "create-followup-experiment": "create_followup_experiment",
    "migrate-terminal-next-actions": "migrate_terminal_next_actions",
    "promote-decision": "promote_decision",
    "update-decision-evidence": "update_decision_evidence",
    "update-decision-checklist": "update_decision_checklist",
    "accept-decision": "accept_decision",
    "set-baseline": "set_baseline",
    "update-node-fields": "update_node_fields",
    "update-workstream-fields": "update_workstream_fields",
    "apply-suggestion": "apply_suggestion",
    "update-suggestion-state": "update_suggestion_state",
    "cleanup-suggestion-lifecycle": "cleanup_suggestion_lifecycle",
    "create-note": "create_note",
}

GROUPED_COMMAND_ALIASES: dict[str, dict[str, str]] = {
    "artifact": {
        "create": "create-artifact",
        "ingest": "ingest-artifact",
        "records": "artifact-records",
        "promote-record": "promote-artifact-record",
        "compact": "compact-artifacts",
        "link": "link-artifact",
    },
    "run": {
        "create": "create-run",
        "update": "update-run",
        "complete": "complete-run",
        "list": "list-runs",
        "context": "run-context",
    },
}

COMMAND_STATUS_CHOICES = ("active", "compatibility", "deprecated")

COMMAND_GROUP_BY_COMMAND = {
    "init": "maintenance",
    "ui": "ui",
    "bootstrap": "context",
    "validate": "maintenance",
    "lint": "maintenance",
    "repair-interaction-log": "maintenance",
    "migrate-interaction-log": "maintenance",
    "build": "build",
    "smoke": "maintenance",
    "search": "context",
    "suggest-next-actions": "focus",
    "commands": "maintenance",
    "node-context": "context",
    "context": "context",
    "assignment-view": "context",
    "option-workstream-context": "context",
    "agent-session-context": "context",
    "check-decision-acceptance": "decision",
    "add-node": "graph",
    "apply-graph-plan": "graph",
    "create-workstream": "graph",
    "close-branch": "graph",
    "update-status": "graph",
    "set-focus": "focus",
    "set-agent-focus": "focus",
    "set-cursor": "focus",
    "sync-focus-actions": "focus",
    "claim-option": "context",
    "claim-workstream": "context",
    "start-agent-session": "context",
    "create-run": "run",
    "update-run": "run",
    "complete-run": "run",
    "list-runs": "run",
    "active-resources": "maintenance",
    "worktree-audit": "maintenance",
    "branch-audit": "maintenance",
    "artifact-retention-audit": "maintenance",
    "maintenance-audit": "maintenance",
    "worktree-closeout": "maintenance",
    "run-context": "run",
    "record-gate-result": "run",
    "ingest-gate-result": "run",
    "report-option-workstream": "context",
    "finalize-workstream": "context",
    "import-worktree-findings": "maintenance",
    "ingest-artifact": "artifact",
    "artifact-records": "artifact",
    "promote-artifact-record": "artifact",
    "compact-artifacts": "artifact",
    "record-finding": "run",
    "update-finding": "run",
    "create-artifact": "artifact",
    "link-artifact": "artifact",
    "complete-experiment": "run",
    "close-current-experiment": "run",
    "complete-experiments": "run",
    "create-followup-experiment": "graph",
    "migrate-terminal-next-actions": "graph",
    "promote-decision": "decision",
    "update-decision-evidence": "decision",
    "update-decision-checklist": "decision",
    "accept-decision": "decision",
    "set-baseline": "decision",
    "update-node-fields": "graph",
    "update-workstream-fields": "context",
    "apply-suggestion": "focus",
    "update-suggestion-state": "focus",
    "cleanup-suggestion-lifecycle": "focus",
    "create-note": "graph",
}

COMMAND_GROUP_CHOICES = tuple(sorted(set(COMMAND_GROUP_BY_COMMAND.values())))

AUDIT_COMMANDS = {
    "active-resources",
    "artifact-retention-audit",
    "branch-audit",
    "check-decision-acceptance",
    "maintenance-audit",
    "suggest-next-actions",
    "worktree-audit",
    "worktree-closeout",
}

SCRIPT_TO_SUBCOMMAND = {
    f"{module_name}.py": command_name
    for command_name, module_name in COMMAND_MODULES.items()
}

GROUPED_ALIASES_BY_COMMAND: dict[str, list[str]] = {}
for group_name, actions in GROUPED_COMMAND_ALIASES.items():
    for action_name, command_name in actions.items():
        GROUPED_ALIASES_BY_COMMAND.setdefault(command_name, []).append(f"{group_name} {action_name}")


def subcommand_for_script(script_name: str) -> str:
    try:
        return SCRIPT_TO_SUBCOMMAND[script_name]
    except KeyError as exc:
        raise ValueError(f"Unknown Research Cockpit command script: {script_name}") from exc


def cli_command_for_script(script_name: str, *parts: str) -> str:
    command = ["research-cockpit", subcommand_for_script(script_name)]
    command.extend(parts)
    return " ".join(str(part) for part in command if part not in ("", None))


def grouped_aliases_for_command(command_name: str) -> list[str]:
    return sorted(GROUPED_ALIASES_BY_COMMAND.get(command_name, []))


def command_group_for_command(command_name: str) -> str:
    try:
        return COMMAND_GROUP_BY_COMMAND[command_name]
    except KeyError as exc:
        raise ValueError(f"Unknown Research Cockpit command group target: {command_name}") from exc


def command_lifecycle_for_command(command_name: str, *, mutating: bool) -> str:
    if command_name == "build":
        return "build"
    if command_name in AUDIT_COMMANDS:
        return "audit"
    if mutating:
        return "mutate"
    return "read"

ROLE_COMMAND_MODULES: dict[str, dict[str, str]] = {
    "work": {
        "claim": "work_claim",
        "open": "work_open",
        "release": "work_release",
        "renew": "work_renew",
        "start": "work_start",
        "close": "work_close",
    },
    "review": {
        "open": "review_open",
        "report": "review_report",
    },
    "coord": {
        "handoff": "coord_handoff",
        "overview": "coord_overview",
        "review": "coord_review",
    },
}
