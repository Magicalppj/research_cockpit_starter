from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


COMMANDS: list[dict[str, object]] = [
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
        "mutating": False,
        "writes_dashboard": True,
        "writes_generated_files": True,
        "supports_json": False,
        "supports_dry_run": False,
        "supports_no_build": False,
        "recommended_when": "Refresh generated context after YAML changes.",
    },
    {
        "name": "skill_smoke_test.py",
        "purpose": "Run a read-only agent workflow smoke test with absolute script paths.",
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
        "recommended_when": "Add a stage, problem, option, experiment, decision, or artifact.",
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
        "supports_json": False,
        "supports_dry_run": False,
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
        "supports_json": False,
        "supports_dry_run": False,
        "supports_no_build": True,
        "recommended_when": "Accept a decision after checklist passes.",
    },
    {
        "name": "apply_suggestion.py",
        "purpose": "Queue an action suggestion into current_state or the source node.",
        "mutating": True,
        "supports_json": False,
        "supports_dry_run": False,
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


def python_command() -> str:
    return os.environ.get("RESEARCH_COCKPIT_PYTHON", "").strip() or "python"


def script_command(script_name: str) -> str:
    return f"{python_command()} scripts\\{script_name}"


def agent_command_manifest() -> list[dict[str, object]]:
    return [
        {
            **command,
            "command": script_command(str(command["name"])),
        }
        for command in COMMANDS
    ]


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
