from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

from research_cockpit.paths import default_data_root

ROOT = default_data_root()

from research_cockpit.commands.apply_graph_plan import apply_graph_plan
from research_cockpit.commands.file_schemas import CREATE_WORKSTREAM_EXAMPLE
from research_cockpit.model import ValidationError, load_yaml


RESERVED_NODE_KEYS = {"id", "type", "title", "status", "summary", "parent", "fields"}


def _mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a mapping")
    return value


def _list(value: Any, field_name: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"{field_name}[{index}] must be a mapping")
    return value


def _required_text(data: dict[str, Any], field_name: str, owner: str) -> str:
    value = str(data.get(field_name) or "").strip()
    if not value:
        raise ValueError(f"{owner}.{field_name} is required")
    return value


def _fields_from_node_spec(spec: dict[str, Any]) -> dict[str, Any]:
    fields = copy.deepcopy(spec.get("fields") or {})
    if not isinstance(fields, dict):
        raise ValueError("fields must be a mapping")
    for key, value in spec.items():
        if key not in RESERVED_NODE_KEYS:
            fields[key] = value
    return fields


def _graph_node(
    spec: dict[str, Any],
    *,
    owner: str,
    node_type: str,
    parent: str | None,
    default_status: str,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": _required_text(spec, "id", owner),
        "type": node_type,
        "title": _required_text(spec, "title", owner),
        "status": str(spec.get("status") or default_status),
        "summary": str(spec.get("summary") or ""),
    }
    if parent:
        out["parent"] = parent
    elif spec.get("parent"):
        out["parent"] = str(spec["parent"])
    fields = _fields_from_node_spec(spec)
    if fields:
        out["fields"] = fields
    return out


def graph_plan_from_workstream(workstream: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(workstream, dict):
        raise ValueError("Workstream file must contain a mapping")

    problem = _mapping(workstream.get("problem"), "problem")
    active_option = _mapping(workstream.get("active_option"), "active_option")
    experiments = _list(workstream.get("experiments"), "experiments")
    followup_options = _list(workstream.get("followup_options"), "followup_options")

    problem_id = _required_text(problem, "id", "problem")
    active_option_id = _required_text(active_option, "id", "active_option")
    experiment_ids = [_required_text(item, "id", f"experiments[{index}]") for index, item in enumerate(experiments, start=1)]

    problem_node = _graph_node(problem, owner="problem", node_type="problem", parent=None, default_status="active")
    problem_fields = problem_node.setdefault("fields", {})
    problem_fields["current_best_option"] = active_option_id

    option_node = _graph_node(
        active_option,
        owner="active_option",
        node_type="option",
        parent=problem_id,
        default_status="active",
    )
    option_fields = option_node.setdefault("fields", {})
    if experiment_ids:
        option_fields.setdefault("supporting_experiments", [])
        if not isinstance(option_fields["supporting_experiments"], list):
            raise ValueError("active_option.supporting_experiments must be a list")
        option_fields["supporting_experiments"].extend(experiment_ids)

    nodes = [problem_node, option_node]
    for index, experiment in enumerate(experiments, start=1):
        nodes.append(
            _graph_node(
                experiment,
                owner=f"experiments[{index}]",
                node_type="experiment",
                parent=active_option_id,
                default_status="planned",
            )
        )
    for index, option in enumerate(followup_options, start=1):
        nodes.append(
            _graph_node(
                option,
                owner=f"followup_options[{index}]",
                node_type="option",
                parent=problem_id,
                default_status="open",
            )
        )

    return {"nodes": nodes}


def load_workstream(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Workstream file does not exist: {path}")
    data = load_yaml(path)
    if not isinstance(data, dict):
        raise ValueError("Workstream file must contain a mapping")
    return data


def create_workstream(
    root: Path,
    *,
    workstream: dict[str, Any],
    rebuild_dashboard: bool = True,
    dry_run: bool = False,
    show_diff: bool = False,
) -> dict[str, Any]:
    plan = graph_plan_from_workstream(workstream)
    result = apply_graph_plan(
        root,
        plan=plan,
        rebuild_dashboard=rebuild_dashboard,
        dry_run=dry_run,
        show_diff=show_diff,
    )
    result["workstream"] = {
        "problem_id": plan["nodes"][0]["id"],
        "active_option_id": plan["nodes"][1]["id"],
        "experiment_ids": [
            node["id"]
            for node in plan["nodes"]
            if node["type"] == "experiment"
        ],
        "followup_option_ids": [
            node["id"]
            for node in plan["nodes"][2:]
            if node["type"] == "option"
        ],
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=CREATE_WORKSTREAM_EXAMPLE,
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--file", type=Path, dest="workstream_file")
    parser.add_argument("--print-schema", action="store_true", help="Print the workstream YAML schema example and exit.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--show-diff", action="store_true")
    parser.add_argument("--no-build", action="store_true")
    args = parser.parse_args()
    if args.print_schema:
        print(CREATE_WORKSTREAM_EXAMPLE)
        return
    if args.workstream_file is None:
        parser.error("--file is required unless --print-schema is used")

    try:
        result = create_workstream(
            args.root,
            workstream=load_workstream(args.workstream_file),
            rebuild_dashboard=not args.no_build,
            dry_run=args.dry_run,
            show_diff=args.show_diff,
        )
    except (ValidationError, ValueError, FileExistsError, FileNotFoundError) as exc:
        print(str(exc))
        raise SystemExit(1) from exc

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    verb = "Would create" if args.dry_run else "Created"
    print(f"{verb} workstream {result['workstream']['problem_id']}: {len(result['changed_files'])} file(s)")
    if args.show_diff and result.get("diff"):
        print(result["diff"], end="" if str(result["diff"]).endswith("\n") else "\n")
    if not args.dry_run and not args.no_build:
        print(f"Rebuilt dashboards under {args.root / 'dashboards'}")


if __name__ == "__main__":
    main()
