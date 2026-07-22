from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
from typing import Any

from research_cockpit.paths import default_data_root, display_path, plugin_root

PLUGIN_ROOT = plugin_root()
ROOT = default_data_root()

REQUIRED_MODULES = {
    "networkx": "networkx",
    "yaml": "PyYAML",
}
BATCH_WORKER_VERIFY_COMMANDS = [
    "research-cockpit validate --root <root> --changed-node <node_id> --json",
    "research-cockpit context --root <root> --id <node_id> --view execution --compact --json",
]
BATCH_FINAL_HANDOFF_COMMANDS = [
    "research-cockpit coord handoff --root <root> --file handoff.yaml --json --compact --progress",
]
ASSIGNMENT_CURSOR_EXAMPLE_TEMPLATES = [
    "research-cockpit work close --root <root>{scope} --file closeout.yaml --json --compact",
]
NEXT_ACTION_EXAMPLE_TEMPLATES = [
    "research-cockpit work record --root <root>{scope} --file record.yaml --json --compact",
]
GLOBAL_NEXT_ACTION_EXAMPLE_TEMPLATES = [
    "research-cockpit coord assign --root <root> --file coord_assign.yaml --json --compact",
]
MUTATION_COMMAND_SKELETON_TEMPLATES = [
    "research-cockpit context --root <root> --id <node_id> --view execution --compact --json",
    "research-cockpit work start --root <root>{scope} --file work_start.yaml --json --compact",
    "research-cockpit work record --root <root>{scope} --file record.yaml --json --compact",
    "research-cockpit work close --root <root>{scope} --file closeout.yaml --json --compact",
]
ASSIGNMENT_START_COMMAND_SKELETON_TEMPLATES = [
    "research-cockpit work claim --root <root>{scope} --agent <agent_id> --operation-id <operation_id> --return-packet --json --compact",
    "research-cockpit work start --root <root>{scope} --file work_start.yaml --json --compact",
]
GLOBAL_COMMAND_SKELETON_PREFIX: list[str] = []
GLOBAL_COMMAND_SKELETON_SUFFIX: list[str] = []
BATCH_EXAMPLE_TEMPLATES = {
    "findings": [
        "research-cockpit work record --root <root>{scope} --file record.yaml --json --compact",
    ],
    "artifacts": [
        "research-cockpit work record --root <root>{scope} --file record.yaml --json --compact",
    ],
    "runs": [
        "research-cockpit work start --root <root>{scope} --file work_start.yaml --json --compact",
        "research-cockpit work record --root <root>{scope} --file record.yaml --json --compact",
        "research-cockpit work close --root <root>{scope} --file closeout.yaml --json --compact",
    ],
    "gates": [
        "research-cockpit work record --root <root>{scope} --file record.yaml --json --compact",
    ],
}

def missing_runtime_dependencies(required: dict[str, str] = REQUIRED_MODULES) -> list[str]:
    return [module for module in required if importlib.util.find_spec(module) is None]


def format_dependency_error(missing: list[str]) -> str:
    packages = ", ".join(REQUIRED_MODULES.get(module, module) for module in missing)
    modules = ", ".join(missing)
    return (
        f"Missing Python modules: {modules}. "
        f"From the Research Cockpit plugin root, install the package with `python -m pip install -e .` "
        f"or rerun with an interpreter that already has: {packages}."
    )


_MISSING_DEPENDENCIES = missing_runtime_dependencies()

if not _MISSING_DEPENDENCIES:
    from research_cockpit.agent_sessions import build_agent_session_context
    from research_cockpit.baselines import resolve_current_effective_baseline
    from research_cockpit.context_packs import build_context_metadata
    from research_cockpit.gate_result_records import build_gate_overview
    from research_cockpit.hierarchy_policy import hierarchy_policy
    from research_cockpit.commands.option_workstream_context import compact_option_workstream_context
    from research_cockpit.model import (
        ACTIVE_ASSIGNMENT_STATUSES,
        ValidationError,
        build_search_index,
        build_search_index_summary,
        focus_node_id_from_current,
        load_assignments,
        load_coordinator_state,
        load_nodes,
        load_yaml,
        validate_cockpit,
    )
    from research_cockpit.resources import build_link_rows
    from research_cockpit.run_summaries import build_run_overview
    from research_cockpit.suggestions import build_action_suggestions
    from research_cockpit.commands.build_dashboard import build_dashboard
    from research_cockpit.commands.lint_semantic import semantic_lint


def _display_path(path: Path) -> str:
    return display_path(path, base=Path.cwd())


def _context_paths(root: Path) -> dict[str, dict[str, Any]]:
    dash = root / "dashboards"
    files = {
        "agent_context_pack": dash / "agent_context_pack.json",
        "focus_context_pack": dash / "focus_context_pack.json",
        "graph_view": dash / "graph_view.json",
        "assignment_view": dash / "assignment_view.json",
        "search_index": dash / "search_index.json",
        "next_action_suggestions": dash / "next_action_suggestions.json",
    }
    return {
        name: {
            "path": _display_path(path),
            "exists": path.exists(),
        }
        for name, path in files.items()
    }


class BootstrapIdentityError(ValueError):
    def __init__(self, error: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.payload: dict[str, Any] = {
            "ok": False,
            "error": error,
            "message": message,
            "validation": {"ok": False, "errors": [message]},
        }
        self.payload.update(extra)


def _active_assignment_ids(assignments: dict[str, Any]) -> list[str]:
    return sorted(
        assignment.assignment_id
        for assignment in assignments.values()
        if assignment.status in ACTIVE_ASSIGNMENT_STATUSES
    )


def _active_assignment_for_agent(assignments: dict[str, Any], agent_id: str) -> str | None:
    assignment_ids = sorted(
        assignment.assignment_id
        for assignment in assignments.values()
        if assignment.agent_id == agent_id and assignment.status in ACTIVE_ASSIGNMENT_STATUSES
    )
    if len(assignment_ids) > 1:
        raise BootstrapIdentityError(
            "assignment_identity_required",
            (
                f"Multiple active assignments exist for agent {agent_id!r}. "
                "Pass --assignment or set RESEARCH_COCKPIT_ASSIGNMENT_ID."
            ),
            agent_id=agent_id,
            assignment_ids=assignment_ids,
        )
    return assignment_ids[0] if assignment_ids else None


def _session_file_identity(root: Path) -> dict[str, str]:
    candidates = []
    cwd_file = Path.cwd() / ".research_cockpit_session.yaml"
    candidates.append(cwd_file)
    repo_file = root.resolve().parent / ".research_cockpit_session.yaml"
    if repo_file not in candidates:
        candidates.append(repo_file)
    for path in candidates:
        if not path.exists():
            continue
        data = load_yaml(path)
        if not isinstance(data, dict):
            continue
        result: dict[str, str] = {}
        if data.get("assignment_id"):
            result["assignment_id"] = str(data["assignment_id"])
        if data.get("agent_id"):
            result["agent_id"] = str(data["agent_id"])
        return result
    return {}


def _resolve_bootstrap_identity(
    root: Path,
    assignments: dict[str, Any],
    *,
    agent_id: str | None,
    assignment_id: str | None,
) -> tuple[str | None, str | None, str]:
    if assignment_id:
        return assignment_id, agent_id, "explicit_assignment"
    if agent_id:
        resolved = _active_assignment_for_agent(assignments, agent_id)
        return resolved, agent_id, "explicit_agent" if resolved else "explicit_agent_legacy"

    env_assignment_id = os.environ.get("RESEARCH_COCKPIT_ASSIGNMENT_ID")
    env_agent_id = os.environ.get("RESEARCH_COCKPIT_AGENT_ID")
    if env_assignment_id:
        return env_assignment_id, env_agent_id or agent_id, "env_assignment"
    if env_agent_id:
        resolved = _active_assignment_for_agent(assignments, env_agent_id)
        return resolved, env_agent_id, "env_agent" if resolved else "env_agent_legacy"

    session = _session_file_identity(root)
    if session.get("assignment_id"):
        return session["assignment_id"], session.get("agent_id"), "session_file_assignment"
    if session.get("agent_id"):
        session_agent_id = session["agent_id"]
        resolved = _active_assignment_for_agent(assignments, session_agent_id)
        return resolved, session_agent_id, "session_file_agent" if resolved else "session_file_agent_legacy"

    active_ids = _active_assignment_ids(assignments)
    if len(active_ids) == 1:
        return active_ids[0], agent_id, "single_active_assignment"
    if len(active_ids) > 1:
        raise BootstrapIdentityError(
            "assignment_identity_required",
            "Multiple active assignments exist. Pass --assignment or set RESEARCH_COCKPIT_ASSIGNMENT_ID.",
            assignment_ids=active_ids,
        )
    return None, agent_id, "global"


def _render_scoped_templates(templates: list[str], assignment_flag: str) -> list[str]:
    return [template.format(scope=assignment_flag) for template in templates]


def _mutation_guidance(
    nodes: dict[str, Any],
    current: dict[str, Any],
    *,
    scope_option_id: str | None = None,
    assignment_id: str | None = None,
) -> dict[str, Any]:
    focus_node_id = focus_node_id_from_current(current, nodes)
    focus_node = nodes.get(focus_node_id) if focus_node_id else None
    problem_id = current.get("current_problem")
    problem = nodes.get(str(problem_id)) if problem_id else None
    current_best_option = scope_option_id
    if current_best_option is None and problem:
        current_best_option = problem.raw.get("current_best_option") or current.get("current_option")
    elif current_best_option is None and focus_node:
        current_best_option = focus_node.raw.get("current_best_option") or current.get("current_option")
    assignment_flag = f" --assignment {assignment_id}" if assignment_id else ""
    next_action_examples = _render_scoped_templates(NEXT_ACTION_EXAMPLE_TEMPLATES, assignment_flag)
    if assignment_id:
        next_action_examples.extend(_render_scoped_templates(ASSIGNMENT_CURSOR_EXAMPLE_TEMPLATES, assignment_flag))
    else:
        next_action_examples.extend(GLOBAL_NEXT_ACTION_EXAMPLE_TEMPLATES)
    command_skeletons: list[str] = []
    for template in MUTATION_COMMAND_SKELETON_TEMPLATES:
        if assignment_id and "research-cockpit work start " in template:
            command_skeletons.extend(
                _render_scoped_templates(ASSIGNMENT_START_COMMAND_SKELETON_TEMPLATES, assignment_flag)
            )
        else:
            command_skeletons.extend(_render_scoped_templates([template], assignment_flag))
    if assignment_id:
        batch_examples = {
            name: _render_scoped_templates(templates, assignment_flag)
            for name, templates in BATCH_EXAMPLE_TEMPLATES.items()
        }
        batch_examples["runs"] = [command for command in batch_examples["runs"] if "work start" not in command]
        batch_examples["runs"].insert(0, _render_scoped_templates(ASSIGNMENT_START_COMMAND_SKELETON_TEMPLATES[1:], assignment_flag)[0])
    else:
        command_skeletons = [
            "research-cockpit context --root <root> --id <node_id> --view execution --compact --json",
            "research-cockpit coord overview --root <root> --json --compact --limit 20",
            "research-cockpit coord assign --root <root> --file coord_assign.yaml --json --compact",
            "research-cockpit coord decide --root <root> --file coord_decide.yaml --json --compact",
        ]
        batch_examples = {name: [] for name in BATCH_EXAMPLE_TEMPLATES}
    batch_examples["next_actions"] = next_action_examples

    pause_candidates: list[str] = []
    if problem:
        for node in nodes.values():
            if node.type == "option" and node.parent == problem.id and node.id != current_best_option:
                if node.status in {"active", "promising", "open"}:
                    pause_candidates.append(node.id)

    return {
        "current_focus_node": focus_node_id,
        "current_best_option": current_best_option,
        "pause_candidate_options": sorted(pause_candidates),
        "batching": "Use one canonical role request per lifecycle transition. Reuse internally verified receipts without extra validation; run changed-scope diagnostics only when the receipt requires them. A coordinator runs milestone handoff once through coord handoff.",
        "multi_agent_batch_mode": {
            "default": "Agents compute in parallel and submit role-facade requests to the canonical root. Runtime locks serialize commits, leases reject conflicting ownership, and internally verified receipts require no follow-up command. A coordinator runs milestone handoff once through coord handoff.",
            "rules": [
                "Parallelize compute across assignments; let runtime locks serialize canonical-root commits and handle explicit lease or revision conflicts.",
                "Use commands --role <role> --name <command> --json --compact only when one operation is unknown.",
                "Use maintenance dry-run defaults before explicitly setting execute: true.",
                "Read verification.status and additional_verification_required before running a changed-scope diagnostic.",
                "On mutation conflict, reopen the assignment packet and retry with a new operation_id.",
            ],
            "finish_commands": [],
            "worker_verify_commands": BATCH_WORKER_VERIFY_COMMANDS,
            "final_handoff_commands": BATCH_FINAL_HANDOFF_COMMANDS,
            "examples": batch_examples,
        },
        "hierarchy_policy": hierarchy_policy(parent_option_id=scope_option_id or current.get("current_option") or current_best_option),
        "command_skeletons": command_skeletons,
    }


def _agent_scope_payload(
    root: Path,
    nodes: dict[str, Any],
    current: dict[str, Any],
    *,
    agent_id: str | None,
    option_id: str | None,
    assignment_id: str | None = None,
) -> dict[str, Any] | None:
    if not agent_id and not option_id:
        return None
    payload = build_agent_session_context(
        root,
        nodes,
        current,
        agent_id=agent_id,
        assignment_id=assignment_id,
        option_id=option_id,
    )
    return {
        "assignment_id": assignment_id,
        "agent_id": payload.get("agent_id"),
        "option_id": payload.get("option_id"),
        "assignment": payload.get("assignment"),
        "assignment_cursor": payload.get("assignment_cursor"),
        "root_node": (payload.get("assignment") or {}).get("root_node") if payload.get("assignment") else payload.get("option_id"),
        "current_node": (payload.get("assignment") or {}).get("current_node") if payload.get("assignment") else None,
        "current_path": (payload.get("assignment_cursor") or {}).get("current_path", []),
        "next_actions": (payload.get("assignment") or {}).get("next_actions", []) if payload.get("assignment") else [],
        "matching_options": payload.get("matching_options", []),
        "session": payload.get("session", {}),
        "canonical_root": payload.get("canonical_root"),
        "required_root": payload.get("required_root"),
        "do_not_mutate_worktree_root": True,
        "storage_policy": payload.get("storage_policy", {}),
        "worktree_boundary": payload.get("worktree_boundary", {}),
        "agent_focus": payload.get("agent_focus"),
        "option_context": compact_option_workstream_context(payload["option_context"], nodes),
        "handoff": payload.get("handoff", {}),
        "uses_global_current_state": False,
        "current_state_policy": (
            "Treat current_state global focus as coordinator metadata only. "
            "Use this agent_scope and its option_context unless the user explicitly assigns another branch."
        ),
    }


def agent_bootstrap_payload(
    root: Path = ROOT,
    *,
    build: bool = False,
    agent_id: str | None = None,
    assignment_id: str | None = None,
    option_id: str | None = None,
    coordinator: bool = False,
    nodes: dict[str, Any] | None = None,
    current: dict[str, Any] | None = None,
    validation_errors: list[str] | None = None,
    link_rows: list[dict[str, Any]] | None = None,
    semantic_warnings: list[dict[str, Any]] | None = None,
    compact_runtime: bool = False,
) -> dict[str, Any]:
    if _MISSING_DEPENDENCIES:
        raise RuntimeError(format_dependency_error(_MISSING_DEPENDENCIES))

    if build:
        build_dashboard(root)

    nodes = nodes if nodes is not None else load_nodes(root)
    current = current if current is not None else load_yaml(root / "current_state.yaml")
    errors = list(validation_errors) if validation_errors is not None else validate_cockpit(root, nodes, current)
    link_rows = link_rows if link_rows is not None else build_link_rows(root, nodes)
    suggestions = [] if compact_runtime else build_action_suggestions(root, nodes, current, link_rows)
    search_index = [] if compact_runtime else build_search_index(root, nodes, current)
    focus_node_id = focus_node_id_from_current(current, nodes)
    effective_baseline = resolve_current_effective_baseline(nodes, current)
    metadata = build_context_metadata(root, current)
    semantic = {
        "warnings": list(semantic_warnings)
        if semantic_warnings is not None
        else ([] if compact_runtime else semantic_lint(root)["warnings"])
    }
    try:
        coordinator_state = load_coordinator_state(root)
    except ValidationError:
        coordinator_state = None
    try:
        assignments = load_assignments(root)
    except ValidationError:
        assignments = {}
    if coordinator:
        assignment_id, agent_id, identity_source = None, None, "explicit_coordinator"
    else:
        assignment_id, agent_id, identity_source = _resolve_bootstrap_identity(
            root,
            assignments,
            agent_id=agent_id,
            assignment_id=assignment_id,
        )
    if assignment_id:
        assignment = assignments.get(assignment_id)
        if assignment is None:
            raise BootstrapIdentityError(
                "assignment_not_found",
                f"Assignment does not exist: {assignment_id}",
                assignment_id=assignment_id,
            )
        if agent_id and agent_id != assignment.agent_id:
            raise BootstrapIdentityError(
                "assignment_identity_mismatch",
                f"Assignment {assignment_id} is owned by {assignment.agent_id!r}, not {agent_id!r}",
                assignment_id=assignment_id,
                assignment_agent_id=assignment.agent_id,
                agent_id=agent_id,
            )
        if option_id and option_id != assignment.root_node:
            raise BootstrapIdentityError(
                "assignment_identity_mismatch",
                f"Assignment {assignment_id} root_node is {assignment.root_node!r}, not {option_id!r}",
                assignment_id=assignment_id,
                assignment_root_node=assignment.root_node,
                option_id=option_id,
            )
        agent_id = assignment.agent_id
        option_id = assignment.root_node
    agent_scope = _agent_scope_payload(
        root,
        nodes,
        current,
        agent_id=agent_id,
        option_id=option_id,
        assignment_id=assignment_id,
    )
    scope_option_id = str(agent_scope["option_id"]) if agent_scope and agent_scope.get("option_id") else None
    if agent_scope and assignment_id:
        scope_mode = "assignment"
        primary_context = "assignment_scope"
    elif agent_scope:
        scope_mode = "agent"
        primary_context = "agent_session"
    else:
        scope_mode = "global"
        primary_context = "global_current_state"
    scope = {
        "mode": scope_mode,
        "primary_context": primary_context,
    }
    if agent_scope:
        scope.update({
            "assignment_id": agent_scope.get("assignment_id"),
            "agent_id": agent_scope.get("agent_id"),
            "option_id": agent_scope.get("option_id"),
            "identity_source": identity_source,
            "global_focus_is_coordinator_only": True,
        })
    else:
        scope["identity_source"] = identity_source

    payload: dict[str, Any] = {
        "root": _display_path(root),
        "scope": scope,
        "validation": {
            "ok": not errors,
            "errors": errors,
            "node_count": len(nodes),
        },
        "focus": {
            "current_stage": current.get("current_stage"),
            "current_problem": current.get("current_problem"),
            "current_option": current.get("current_option"),
            "current_focus_node": focus_node_id,
            "current_focus_path": current.get("current_focus_path", []) or [],
            "effective_baseline": effective_baseline,
            "coordinator_only": bool(agent_scope),
        },
        "coordinator_state": {
            "coordinator_only": True,
            "selected_node": coordinator_state.selected_node if coordinator_state else None,
            "selected_assignment": coordinator_state.selected_assignment if coordinator_state else None,
            "global_next_actions": list(coordinator_state.global_next_actions) if coordinator_state else [],
            "dashboard_filters": dict(coordinator_state.dashboard_filters) if coordinator_state else {},
        },
        "context_paths": _context_paths(root),
        "skill": {
            "path": _display_path(PLUGIN_ROOT),
            "exists": PLUGIN_ROOT.exists(),
        },
        "plugin": {
            "path": _display_path(PLUGIN_ROOT),
            "exists": PLUGIN_ROOT.exists(),
        },
        "top_suggestions": suggestions[:3],
        "semantic_warnings": semantic["warnings"],
        "mutation_guidance": _mutation_guidance(
            nodes,
            current,
            scope_option_id=scope_option_id,
            assignment_id=assignment_id,
        ),
        "run_overview": {} if compact_runtime else build_run_overview(root, nodes),
        "gate_overview": {} if compact_runtime else build_gate_overview(root),
        "search_summary": build_search_index_summary(search_index),
        "git": {
            "source_git_commit": metadata["source_git_commit"],
            "worktree_dirty": metadata["worktree_dirty"],
        },
        "metadata": metadata,
    }
    if agent_scope:
        if assignment_id:
            payload["assignment_scope"] = agent_scope
        payload["agent_scope"] = agent_scope
    return payload


def _print_text(payload: dict[str, Any]) -> None:
    validation = payload["validation"]
    state = "OK" if validation["ok"] else "FAILED"
    print(f"Validation: {state} ({validation['node_count']} nodes)")
    for error in validation.get("errors", []):
        print(f"- {error}")
    focus = payload["focus"]
    scope = payload.get("scope", {})
    if scope.get("mode") in {"agent", "assignment"}:
        print(f"Scope: {scope.get('mode')} {scope.get('agent_id')} / option {scope.get('option_id')}")
    print(f"Focus: {focus.get('current_focus_node')}")
    print("Context paths:")
    for name, item in payload["context_paths"].items():
        exists = "exists" if item["exists"] else "missing"
        print(f"- {name}: {item['path']} ({exists})")
    if payload["top_suggestions"]:
        print("Top suggestions:")
        for suggestion in payload["top_suggestions"]:
            print(f"- {suggestion['id']} {suggestion['kind']}: {suggestion['action']}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--agent", dest="agent_id", help="Return an agent-scoped bootstrap payload for this active session")
    parser.add_argument("--assignment", dest="assignment_id", help="Return an assignment-scoped bootstrap payload")
    parser.add_argument("--option", dest="option_id", help="Return an option-scoped bootstrap payload for this active session")
    parser.add_argument("--coordinator", action="store_true", help="Return coordinator/global bootstrap even when active assignments exist")
    parser.add_argument("--json", action="store_true", help="Print machine-readable bootstrap payload")
    parser.add_argument("--build", action="store_true", help="Refresh dashboard/context files before reporting")
    args = parser.parse_args()

    try:
        payload = agent_bootstrap_payload(
            args.root,
            build=args.build,
            agent_id=args.agent_id,
            assignment_id=args.assignment_id,
            option_id=args.option_id,
            coordinator=args.coordinator,
        )
    except BootstrapIdentityError as exc:
        error_payload = exc.payload
        if args.json:
            print(json.dumps(error_payload, indent=2, ensure_ascii=False))
        else:
            print(f"FAILED: {exc}")
        raise SystemExit(1)
    except (OSError, RuntimeError, ValueError) as exc:
        error_payload = {
            "validation": {"ok": False, "errors": [str(exc)]},
            "error": str(exc),
        }
        if args.json:
            print(json.dumps(error_payload, indent=2, ensure_ascii=False))
        else:
            print(f"FAILED: {exc}")
        raise SystemExit(1)

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        _print_text(payload)


if __name__ == "__main__":
    main()
