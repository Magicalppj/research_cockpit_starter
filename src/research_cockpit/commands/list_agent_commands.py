from __future__ import annotations

import argparse
import json

from research_cockpit.command_registry import subcommand_for_script


CAPABILITY_BY_COMMAND = {
    "init": "capabilities/integrations.md",
    "ui": "capabilities/ui-dashboard.md",
    "agent_bootstrap.py": "capabilities/focus-context.md",
    "validate_cockpit.py": "capabilities/troubleshooting.md",
    "build_dashboard.py": "capabilities/graph-state.md",
    "skill_smoke_test.py": "capabilities/integrations.md",
    "search_knowledge.py": "capabilities/focus-context.md",
    "suggest_next_actions.py": "capabilities/focus-context.md",
    "node_context.py": "capabilities/focus-context.md",
    "option_workstream_context.py": "capabilities/experiment-tracking.md",
    "check_decision_acceptance.py": "capabilities/decision-adr.md",
    "add_node.py": "capabilities/node-management.md",
    "update_status.py": "capabilities/node-management.md",
    "set_focus.py": "capabilities/focus-context.md",
    "claim_option.py": "capabilities/experiment-tracking.md",
    "report_option_workstream.py": "capabilities/experiment-tracking.md",
    "record_finding.py": "capabilities/experiment-tracking.md",
    "promote_decision.py": "capabilities/decision-adr.md",
    "update_decision_evidence.py": "capabilities/decision-adr.md",
    "update_decision_checklist.py": "capabilities/decision-adr.md",
    "accept_decision.py": "capabilities/decision-adr.md",
    "apply_suggestion.py": "capabilities/node-management.md",
    "update_suggestion_state.py": "capabilities/node-management.md",
    "cleanup_suggestion_lifecycle.py": "capabilities/node-management.md",
    "create_note.py": "capabilities/node-management.md",
}




COMMANDS: list[dict[str, object]] = [
    {
        "name": "init",
        "purpose": "Initialize a project-local research_cockpit state directory from a template.",
        "mutating": True,
        "supports_json": False,
        "supports_dry_run": False,
        "supports_no_build": False,
        "recommended_when": "Start a new research repo before recording project-specific state.",
    },
    {
        "name": "ui",
        "purpose": "Launch the Streamlit researcher dashboard for a data root.",
        "mutating": False,
        "supports_json": False,
        "supports_dry_run": False,
        "supports_no_build": False,
        "recommended_when": "Inspect graph state, saved views, decisions, search, and data health interactively.",
    },
    {
        "name": "agent_bootstrap.py",
        "purpose": "Inspect validation status, focus, context paths, suggestions, search summary, and git state.",
        "mutating": False,
        "supports_json": True,
        "supports_dry_run": False,
        "supports_no_build": False,
        "supports_build": True,
        "recommended_when": "Start every agent session here.",
    },
    {
        "name": "validate_cockpit.py",
        "purpose": "Validate YAML nodes, focus state, edges, and lifecycle structure.",
        "mutating": False,
        "supports_json": True,
        "supports_dry_run": False,
        "supports_no_build": False,
        "recommended_when": "Run before and after mutating cockpit data.",
    },
    {
        "name": "build_dashboard.py",
        "purpose": "Regenerate dashboard and context JSON from YAML truth source.",
        "mutating": True,
        "writes_dashboard": True,
        "writes_generated_files": True,
        "supports_json": False,
        "supports_dry_run": False,
        "supports_no_build": False,
        "recommended_when": "Refresh generated context after YAML changes.",
    },
    {
        "name": "skill_smoke_test.py",
        "purpose": "Run a read-only agent workflow smoke test through the package CLI.",
        "mutating": False,
        "writes_generated_files": False,
        "supports_json": True,
        "supports_dry_run": False,
        "supports_no_build": False,
        "recommended_when": "Check whether a copied skill package is usable by an agent.",
    },
    {
        "name": "search_knowledge.py",
        "purpose": "Search YAML nodes, notes, and indexed local text resources.",
        "mutating": False,
        "supports_json": True,
        "supports_dry_run": False,
        "supports_no_build": False,
        "recommended_when": "Find relevant research context before editing.",
    },
    {
        "name": "suggest_next_actions.py",
        "purpose": "List active or inactive action suggestions.",
        "mutating": False,
        "supports_json": True,
        "supports_dry_run": False,
        "supports_no_build": False,
        "recommended_when": "Decide what work should happen next.",
    },
    {
        "name": "node_context.py",
        "purpose": "Read a single node onboarding context with parent chain, blockers, evidence, and safe next commands.",
        "mutating": False,
        "supports_json": True,
        "supports_dry_run": False,
        "supports_no_build": False,
        "recommended_when": "Start work from a specific research node id.",
    },
    {
        "name": "option_workstream_context.py",
        "purpose": "Read context for one option workstream, including recursive child problems, options, experiments, and evidence.",
        "mutating": False,
        "supports_json": True,
        "supports_dry_run": False,
        "supports_no_build": False,
        "recommended_when": "Before an agent follows an option branch.",
    },
    {
        "name": "check_decision_acceptance.py",
        "purpose": "Check whether a decision satisfies acceptance quality gates.",
        "mutating": False,
        "supports_json": True,
        "supports_dry_run": False,
        "supports_no_build": False,
        "recommended_when": "Before accepting a proposed decision.",
    },
    {
        "name": "add_node.py",
        "purpose": "Create a new research node YAML file.",
        "mutating": True,
        "supports_json": False,
        "supports_dry_run": False,
        "supports_no_build": False,
        "recommended_when": "Add a stage, problem, option, experiment, decision, or long-lived artifact.",
    },
    {
        "name": "update_status.py",
        "purpose": "Update a node status and optional summaries.",
        "mutating": True,
        "supports_json": False,
        "supports_dry_run": False,
        "supports_no_build": False,
        "recommended_when": "Move experiment/problem/option state forward without accepting a decision.",
    },
    {
        "name": "set_focus.py",
        "purpose": "Update current_state focus fields.",
        "mutating": True,
        "supports_json": False,
        "supports_dry_run": False,
        "supports_no_build": True,
        "recommended_when": "Change the current research focus.",
    },
    {
        "name": "claim_option.py",
        "purpose": "Claim an option for a single active agent workstream.",
        "mutating": True,
        "supports_json": True,
        "supports_dry_run": True,
        "supports_no_build": True,
        "recommended_when": "Start agent work on one candidate option branch.",
    },
    {
        "name": "report_option_workstream.py",
        "purpose": "Write an option workstream report with recommendation and evidence summary.",
        "mutating": True,
        "supports_json": True,
        "supports_dry_run": True,
        "supports_no_build": True,
        "recommended_when": "Return findings from an option-following agent to the upstream problem.",
    },
    {
        "name": "record_finding.py",
        "purpose": "Append a structured finding to an experiment.",
        "mutating": True,
        "supports_json": False,
        "supports_dry_run": False,
        "supports_no_build": True,
        "recommended_when": "Record evidence after an experiment finishes.",
    },
    {
        "name": "promote_decision.py",
        "purpose": "Create a decision from an option and optional evidence.",
        "mutating": True,
        "supports_json": True,
        "supports_dry_run": True,
        "supports_no_build": True,
        "recommended_when": "Create a proposed or accepted decision from an option.",
    },
    {
        "name": "update_decision_evidence.py",
        "purpose": "Refresh structured evidence fields for an existing decision.",
        "mutating": True,
        "supports_json": False,
        "supports_dry_run": False,
        "supports_no_build": True,
        "recommended_when": "Update evidence before reviewing a proposed decision.",
    },
    {
        "name": "update_decision_checklist.py",
        "purpose": "Append checklist metadata for an existing decision without changing decision status.",
        "mutating": True,
        "supports_json": False,
        "supports_dry_run": False,
        "supports_no_build": True,
        "recommended_when": "Fill alternatives, consequences, and next actions before accepting a decision.",
    },
    {
        "name": "accept_decision.py",
        "purpose": "Accept an existing decision and sync parent option/problem state.",
        "mutating": True,
        "supports_json": True,
        "supports_dry_run": True,
        "supports_no_build": True,
        "recommended_when": "Accept a decision after checklist passes.",
    },
    {
        "name": "apply_suggestion.py",
        "purpose": "Queue an action suggestion into current_state or the source node.",
        "mutating": True,
        "supports_json": True,
        "supports_dry_run": True,
        "supports_no_build": True,
        "recommended_when": "Turn a suggestion into an explicit next action.",
    },
    {
        "name": "update_suggestion_state.py",
        "purpose": "Mark a suggestion active, dismissed, or completed.",
        "mutating": True,
        "supports_json": False,
        "supports_dry_run": False,
        "supports_no_build": True,
        "recommended_when": "Hide or restore action suggestions.",
    },
    {
        "name": "cleanup_suggestion_lifecycle.py",
        "purpose": "Remove orphan suggestion lifecycle records.",
        "mutating": True,
        "supports_json": True,
        "supports_dry_run": True,
        "supports_no_build": True,
        "recommended_when": "Clean stale lifecycle history after reviewing Data Health.",
    },
    {
        "name": "create_note.py",
        "purpose": "Create and link a Markdown note for a supported node.",
        "mutating": True,
        "supports_json": False,
        "supports_dry_run": False,
        "supports_no_build": True,
        "recommended_when": "Create long-form notes for problem, option, experiment, or decision nodes.",
    },
]


def agent_command_manifest() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for command in COMMANDS:
        command_name = str(command["name"])
        subcommand = subcommand_for_script(command_name) if command_name.endswith(".py") else command_name
        row = {
            **command,
            "name": subcommand,
            "capability_file": CAPABILITY_BY_COMMAND[command_name],
            "command": f"research-cockpit {subcommand}",
            "python_module_command": f"python -m research_cockpit.cli {subcommand}",
            "cwd": "research_repo_root",
        }
        rows.append(row)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="Print machine-readable command manifest")
    args = parser.parse_args()

    commands = agent_command_manifest()
    if args.json:
        print(json.dumps({"commands": commands}, indent=2, ensure_ascii=False))
        return

    for command in commands:
        marker = "write" if command["mutating"] else "read"
        print(f"{command['name']} [{marker}]: {command['purpose']}")
        print(f"  {command['command']}")


if __name__ == "__main__":
    main()
