from __future__ import annotations


COMMAND_MODULES: dict[str, str] = {
    "bootstrap": "agent_bootstrap",
    "validate": "validate_cockpit",
    "repair-interaction-log": "repair_interaction_log",
    "build": "build_dashboard",
    "commands": "list_agent_commands",
    "smoke": "skill_smoke_test",
    "search": "search_knowledge",
    "suggest-next-actions": "suggest_next_actions",
    "context": "context",
    "node-context": "node_context",
    "option-workstream-context": "option_workstream_context",
    "agent-session-context": "agent_session_context",
    "check-decision-acceptance": "check_decision_acceptance",
    "add-node": "add_node",
    "apply-graph-plan": "apply_graph_plan",
    "create-workstream": "create_workstream",
    "update-status": "update_status",
    "set-focus": "set_focus",
    "set-agent-focus": "set_agent_focus",
    "sync-focus-actions": "sync_focus_actions",
    "claim-option": "claim_option",
    "claim-workstream": "claim_workstream",
    "start-agent-session": "start_agent_session",
    "report-option-workstream": "report_option_workstream",
    "finalize-workstream": "finalize_workstream",
    "import-worktree-findings": "import_worktree_findings",
    "record-finding": "record_finding",
    "update-finding": "update_finding",
    "create-artifact": "create_artifact",
    "link-artifact": "link_artifact",
    "complete-experiment": "complete_experiment",
    "complete-experiments": "complete_experiments",
    "promote-decision": "promote_decision",
    "update-decision-evidence": "update_decision_evidence",
    "update-decision-checklist": "update_decision_checklist",
    "accept-decision": "accept_decision",
    "update-node-fields": "update_node_fields",
    "apply-suggestion": "apply_suggestion",
    "update-suggestion-state": "update_suggestion_state",
    "cleanup-suggestion-lifecycle": "cleanup_suggestion_lifecycle",
    "create-note": "create_note",
}

SCRIPT_TO_SUBCOMMAND = {
    f"{module_name}.py": command_name
    for command_name, module_name in COMMAND_MODULES.items()
}


def subcommand_for_script(script_name: str) -> str:
    try:
        return SCRIPT_TO_SUBCOMMAND[script_name]
    except KeyError as exc:
        raise ValueError(f"Unknown Research Cockpit command script: {script_name}") from exc


def cli_command_for_script(script_name: str, *parts: str) -> str:
    command = ["research-cockpit", subcommand_for_script(script_name)]
    command.extend(parts)
    return " ".join(str(part) for part in command if part not in ("", None))
