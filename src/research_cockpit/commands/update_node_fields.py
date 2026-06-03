from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any

from research_cockpit.paths import default_data_root

ROOT = default_data_root()

from research_cockpit.assignment_scope import AssignmentScopeError, ensure_assignment_scope
from research_cockpit.commands._assignment_scope_cli import add_assignment_scope_args, emit_assignment_scope_error
from research_cockpit.commands._runtime import compact_mutation_result, dry_run_preflight_result, finish_mutation, load_validated_state, yaml_change_diff
from research_cockpit.commands.record_finding import find_node_file
from research_cockpit.model import ResearchNode, ValidationError, load_yaml, script_command, validate_cockpit


SCALAR_FIELDS = {
    "title",
    "summary",
    "question",
    "hypothesis",
    "evidence_summary",
    "result_summary",
    "priority",
    "order",
    "rank",
    "owner",
    "handoff_context",
}

LIST_APPEND_FIELDS = {
    "tags",
    "success_criteria",
    "metrics",
    "pros",
    "cons",
    "next_actions",
    "supporting_experiments",
    "contradicting_experiments",
    "supporting_decisions",
    "linked_artifacts",
    "alternatives_considered",
    "derived_from",
    "depends_on",
    "blocked_by",
}

REFERENCE_LIST_FIELDS = {
    "supporting_experiments",
    "contradicting_experiments",
    "supporting_decisions",
    "linked_artifacts",
    "alternatives_considered",
    "derived_from",
    "depends_on",
    "blocked_by",
}

BOOL_FIELDS = {
    "ready_for_agent",
}

FIELD_ALIASES = {
    "tag": "tags",
    "success_criterion": "success_criteria",
    "metric": "metrics",
    "pro": "pros",
    "con": "cons",
    "next_action": "next_actions",
    "supporting_experiment": "supporting_experiments",
    "contradicting_experiment": "contradicting_experiments",
    "supporting_decision": "supporting_decisions",
    "linked_artifact": "linked_artifacts",
    "alternative": "alternatives_considered",
    "alternatives": "alternatives_considered",
    "alternative_considered": "alternatives_considered",
    "derived": "derived_from",
    "dependency": "depends_on",
    "blocked": "blocked_by",
}


def supported_field_names() -> list[str]:
    return sorted([
        *SCALAR_FIELDS,
        *LIST_APPEND_FIELDS,
        *BOOL_FIELDS,
        "clear_next_actions",
        "current_best_option",
        "replace_next_actions",
    ])


def _validate_current_best_option(nodes: dict[str, ResearchNode], node: ResearchNode, option_id: str) -> None:
    if node.type != "problem":
        raise ValueError(f"--current-best-option can only be used with problem nodes; {node.id} is {node.type}")
    option = nodes.get(option_id)
    if not option:
        raise ValueError(f"Current best option does not exist: {option_id}")
    if option.type != "option":
        raise ValueError(f"Current best option {option_id} must be option, got {option.type}")
    if option.parent != node.id and option_id not in node.children:
        raise ValueError(f"Current best option {option_id} must be a child option of problem {node.id}")


def _as_str_list(value: Any, *, field_name: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    raise ValueError(f"{field_name} must be a string or list")


def _as_bool(value: Any, *, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "1", "yes", "y"}:
            return True
        if text in {"false", "0", "no", "n"}:
            return False
    raise ValueError(f"{field_name} must be a boolean")


def _append_unique(data: dict[str, Any], field_name: str, values: list[str]) -> None:
    existing = data.get(field_name, []) or []
    if not isinstance(existing, list):
        raise ValueError(f"{field_name} must be a list")
    out = [str(item) for item in existing]
    seen = set(out)
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        out.append(text)
        seen.add(text)
    if out:
        data[field_name] = out
    else:
        data.pop(field_name, None)


def field_updates_from_mapping(fields: dict[str, Any] | None) -> dict[str, Any]:
    fields = fields or {}
    if not isinstance(fields, dict):
        raise ValueError("fields must be a mapping")

    scalar_updates: dict[str, str] = {}
    list_appends: dict[str, list[str]] = {}
    bool_updates: dict[str, bool] = {}
    current_best_option: str | None = None
    replace_next_actions: list[str] | None = None

    for raw_key, value in fields.items():
        key = str(raw_key).replace("-", "_")
        field_name = FIELD_ALIASES.get(key, key)
        if field_name == "current_best_option":
            current_best_option = str(value).strip()
            continue
        if field_name == "replace_next_actions":
            replace_next_actions = _as_str_list(value, field_name=field_name)
            continue
        if field_name in SCALAR_FIELDS:
            scalar_updates[field_name] = "" if value is None else str(value)
            continue
        if field_name in LIST_APPEND_FIELDS:
            list_appends.setdefault(field_name, []).extend(_as_str_list(value, field_name=field_name))
            continue
        if field_name in BOOL_FIELDS:
            bool_updates[field_name] = _as_bool(value, field_name=field_name)
            continue
        allowed = ", ".join(supported_field_names())
        raise ValueError(f"Unsupported node field {raw_key!r}; supported fields: {allowed}")

    return {
        "current_best_option": current_best_option,
        "replace_next_actions": replace_next_actions,
        "scalar_updates": scalar_updates,
        "list_appends": list_appends,
        "bool_updates": bool_updates,
    }


def referenced_node_ids_from_field_updates(
    *,
    current_best_option: str | None = None,
    list_appends: dict[str, list[str]] | None = None,
) -> list[str]:
    out: list[str] = []
    if current_best_option:
        out.append(current_best_option)
    for field_name, values in (list_appends or {}).items():
        if field_name in REFERENCE_LIST_FIELDS:
            out.extend(str(value) for value in values if str(value).strip())
    return out


def apply_node_field_updates(
    nodes: dict[str, ResearchNode],
    *,
    node_id: str,
    data: dict[str, Any],
    current_best_option: str | None = None,
    replace_next_actions: list[str] | None = None,
    scalar_updates: dict[str, Any] | None = None,
    list_appends: dict[str, list[str]] | None = None,
    bool_updates: dict[str, bool] | None = None,
) -> list[str]:
    scalar_updates = scalar_updates or {}
    list_appends = list_appends or {}
    bool_updates = bool_updates or {}
    if replace_next_actions is not None and list_appends.get("next_actions"):
        raise ValueError("--next-action cannot be used together with --replace-next-actions")

    for field_name in scalar_updates:
        if field_name not in SCALAR_FIELDS:
            raise ValueError(f"Unsupported scalar node field: {field_name}")
    for field_name in list_appends:
        if field_name not in LIST_APPEND_FIELDS:
            raise ValueError(f"Unsupported list node field: {field_name}")
    for field_name in bool_updates:
        if field_name not in BOOL_FIELDS:
            raise ValueError(f"Unsupported boolean node field: {field_name}")

    node = nodes[node_id]
    touched: list[str] = []
    if current_best_option is not None:
        _validate_current_best_option(nodes, node, current_best_option)
        data["current_best_option"] = current_best_option
        touched.append("current_best_option")
    if replace_next_actions is not None:
        actions = [str(action) for action in replace_next_actions if str(action).strip()]
        if actions:
            data["next_actions"] = actions
        else:
            data.pop("next_actions", None)
        touched.append("next_actions")
    for field_name, value in scalar_updates.items():
        data[field_name] = "" if value is None else str(value)
        touched.append(field_name)
    for field_name, values in list_appends.items():
        _append_unique(data, field_name, values)
        touched.append(field_name)
    for field_name, value in bool_updates.items():
        data[field_name] = bool(value)
        touched.append(field_name)
    return sorted(set(touched))


def _field_snapshot(data: dict[str, Any], field_names: list[str]) -> dict[str, Any]:
    return {field_name: data.get(field_name) for field_name in field_names}


def update_node_fields(
    root: Path,
    *,
    node_id: str,
    current_best_option: str | None = None,
    replace_next_actions: list[str] | None = None,
    clear_next_actions: bool = False,
    scalar_updates: dict[str, Any] | None = None,
    list_appends: dict[str, list[str]] | None = None,
    bool_updates: dict[str, bool] | None = None,
    rebuild_dashboard: bool = True,
    dry_run: bool = False,
    show_diff: bool = False,
    assignment_id: str | None = None,
    coordinator: bool = False,
) -> dict[str, Any]:
    scalar_updates = scalar_updates or {}
    list_appends = list_appends or {}
    bool_updates = bool_updates or {}
    if clear_next_actions and replace_next_actions is not None:
        raise ValueError("--clear-next-actions cannot be used together with --replace-next-actions")
    if clear_next_actions:
        replace_next_actions = list_appends.pop("next_actions", [])
    if current_best_option is None and replace_next_actions is None and not scalar_updates and not list_appends and not bool_updates:
        raise ValueError("At least one field update is required")
    if replace_next_actions is not None and list_appends.get("next_actions"):
        raise ValueError("--next-action cannot be used together with --replace-next-actions")

    state = load_validated_state(root)
    nodes = state.nodes
    if node_id not in nodes:
        raise ValueError(f"Node does not exist: {node_id}")
    referenced_node_ids = referenced_node_ids_from_field_updates(
        current_best_option=current_best_option,
        list_appends=list_appends,
    )
    ensure_assignment_scope(
        root,
        nodes,
        assignment_id=assignment_id,
        coordinator=coordinator,
        target_node_ids=[node_id, *referenced_node_ids],
    )

    path = find_node_file(root, node_id)
    data = load_yaml(path)
    before_data = dict(data)
    touched_fields = apply_node_field_updates(
        nodes,
        node_id=node_id,
        data=data,
        current_best_option=current_best_option,
        replace_next_actions=replace_next_actions,
        scalar_updates=scalar_updates,
        list_appends=list_appends,
        bool_updates=bool_updates,
    )
    data["updated_at"] = str(date.today())

    candidate = dict(nodes)
    candidate[node_id] = ResearchNode.from_dict(data)
    ensure_assignment_scope(
        root,
        candidate,
        assignment_id=assignment_id,
        coordinator=coordinator,
        target_node_ids=[node_id],
    )
    validate_cockpit(root, candidate, state.current, state.explicit_edges, raise_on_error=True)

    before = _field_snapshot(before_data, touched_fields)
    after = _field_snapshot(data, touched_fields)
    changed = before != after
    result: dict[str, Any] = {
        "node_id": node_id,
        "dry_run": dry_run,
        "changed": False if dry_run else changed,
        "would_change": changed,
        "path": str(path),
        "before": before,
        "after": after,
        "fields": touched_fields,
    }
    if show_diff:
        result["diff"] = yaml_change_diff([(path, before_data, data)]) if changed else ""
    if dry_run:
        return dry_run_preflight_result(root, result)
    if not changed:
        return result

    finish_mutation(
        root,
        [(path, before_data, data)],
        interaction={
            "kind": "update_node_fields",
            "actor": "researcher",
            "node_id": node_id,
            "command": f"{script_command('update_node_fields.py')} --id {node_id}",
            "before": before,
            "after": after,
            "extra": {
                "current_best_option": current_best_option,
                "replaced_next_actions": replace_next_actions is not None,
                "fields": touched_fields,
            },
        },
        rebuild_dashboard=rebuild_dashboard,
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--id", required=True, dest="node_id")
    parser.add_argument("--current-best-option")
    parser.add_argument("--clear-next-actions", action="store_true")
    parser.add_argument("--replace-next-actions", action="append", dest="replace_next_actions")
    parser.add_argument("--title")
    parser.add_argument("--summary")
    parser.add_argument("--question")
    parser.add_argument("--hypothesis")
    parser.add_argument("--evidence-summary")
    parser.add_argument("--result-summary")
    parser.add_argument("--priority")
    parser.add_argument("--order")
    parser.add_argument("--rank")
    parser.add_argument("--owner")
    parser.add_argument("--handoff-context")
    ready_group = parser.add_mutually_exclusive_group()
    ready_group.add_argument("--ready-for-agent", action="store_true", dest="ready_for_agent")
    ready_group.add_argument("--not-ready-for-agent", action="store_true", dest="not_ready_for_agent")
    parser.add_argument("--tag", action="append", dest="tags")
    parser.add_argument("--success-criterion", action="append", dest="success_criteria")
    parser.add_argument("--metric", action="append", dest="metrics")
    parser.add_argument("--pro", action="append", dest="pros")
    parser.add_argument("--con", action="append", dest="cons")
    parser.add_argument("--next-action", action="append", dest="next_actions")
    parser.add_argument("--supporting-experiment", action="append", dest="supporting_experiments")
    parser.add_argument("--contradicting-experiment", action="append", dest="contradicting_experiments")
    parser.add_argument("--supporting-decision", action="append", dest="supporting_decisions")
    parser.add_argument("--linked-artifact", action="append", dest="linked_artifacts")
    parser.add_argument("--alternative", action="append", dest="alternatives_considered")
    parser.add_argument("--derived-from", action="append", dest="derived_from")
    parser.add_argument("--depends-on", action="append", dest="depends_on")
    parser.add_argument("--blocked-by", action="append", dest="blocked_by")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--show-diff", action="store_true")
    parser.add_argument("--no-build", action="store_true")
    add_assignment_scope_args(parser)
    args = parser.parse_args()
    scalar_updates = {
        key: value
        for key, value in {
            "title": args.title,
            "summary": args.summary,
            "question": args.question,
            "hypothesis": args.hypothesis,
            "evidence_summary": args.evidence_summary,
            "result_summary": args.result_summary,
            "priority": args.priority,
            "order": args.order,
            "rank": args.rank,
            "owner": args.owner,
            "handoff_context": args.handoff_context,
        }.items()
        if value is not None
    }
    list_appends = {
        key: value
        for key, value in {
            "tags": args.tags,
            "success_criteria": args.success_criteria,
            "metrics": args.metrics,
            "pros": args.pros,
            "cons": args.cons,
            "next_actions": args.next_actions,
            "supporting_experiments": args.supporting_experiments,
            "contradicting_experiments": args.contradicting_experiments,
            "supporting_decisions": args.supporting_decisions,
            "linked_artifacts": args.linked_artifacts,
            "alternatives_considered": args.alternatives_considered,
            "derived_from": args.derived_from,
            "depends_on": args.depends_on,
            "blocked_by": args.blocked_by,
        }.items()
        if value is not None
    }
    bool_updates = {}
    if args.ready_for_agent:
        bool_updates["ready_for_agent"] = True
    elif args.not_ready_for_agent:
        bool_updates["ready_for_agent"] = False

    try:
        result = update_node_fields(
            args.root,
            node_id=args.node_id,
            current_best_option=args.current_best_option,
            replace_next_actions=args.replace_next_actions,
            clear_next_actions=args.clear_next_actions,
            scalar_updates=scalar_updates,
            list_appends=list_appends,
            bool_updates=bool_updates,
            rebuild_dashboard=not args.no_build,
            dry_run=args.dry_run,
            show_diff=args.show_diff,
            assignment_id=args.assignment,
            coordinator=args.coordinator,
        )
    except AssignmentScopeError as exc:
        emit_assignment_scope_error(args, exc)
        raise SystemExit(1) from exc
    except (ValidationError, ValueError, FileNotFoundError) as exc:
        print(str(exc))
        raise SystemExit(1) from exc

    if args.json:
        payload = compact_mutation_result(
            result,
            command="update-node-fields",
            target=args.node_id,
            root=args.root,
            updated=[args.node_id],
        ) if args.compact else result
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if args.dry_run:
        print(f"Would update node fields for {args.node_id}")
        if args.show_diff and result.get("diff"):
            print(result["diff"], end="" if str(result["diff"]).endswith("\n") else "\n")
        return
    if result["changed"]:
        print(f"Updated node fields for {args.node_id}: {result['path']}")
        if not args.no_build:
            print(f"Rebuilt dashboards under {args.root / 'dashboards'}")
    else:
        print(f"No node field changes for {args.node_id}")


if __name__ == "__main__":
    main()
