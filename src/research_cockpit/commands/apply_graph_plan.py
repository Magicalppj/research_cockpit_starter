from __future__ import annotations

import argparse
import copy
from datetime import date
from pathlib import Path
from typing import Any

from research_cockpit.paths import default_data_root

ROOT = default_data_root()

from research_cockpit.commands._runtime import (
    compact_mutation_result,
    dry_run_preflight_result,
    emit_json,
    finish_mutation,
    load_validated_state,
    safe_print,
    yaml_change_diff,
)
from research_cockpit.commands.file_schemas import APPLY_GRAPH_PLAN_EXAMPLE
from research_cockpit.commands.record_finding import find_node_file
from research_cockpit.commands.update_node_fields import apply_node_field_updates, field_updates_from_mapping
from research_cockpit.lifecycle_guards import LifecycleGuardError, raise_for_terminal_parent_transitions
from research_cockpit.model import (
    ResearchNode,
    ValidationError,
    default_status_for_type,
    load_yaml,
    script_command,
    validate_cockpit,
    validate_status,
)


STATUS_ALIASES_BY_TYPE = {
    "option": {"planned": "open"},
}

UPDATE_TOP_LEVEL_FIELDS = {"id", "status", "fields"}


def _as_list(value: Any, field_name: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    return value


def _required_text(data: dict[str, Any], field_name: str, owner: str) -> str:
    value = str(data.get(field_name) or "").strip()
    if not value:
        raise ValueError(f"{owner}.{field_name} is required")
    return value


def _node_path(root: Path, node_id: str) -> Path:
    return root / "graph" / "nodes" / f"{node_id}.yaml"


def _normalize_input_status(node_type: str, status: str) -> tuple[str, str | None]:
    normalized = STATUS_ALIASES_BY_TYPE.get(node_type, {}).get(status, status)
    return normalized, status if normalized != status else None


class GraphPlanBuilder:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.state = load_validated_state(root)
        self.candidate = dict(self.state.nodes)
        self.data_by_id: dict[str, dict[str, Any]] = {}
        self.before_by_id: dict[str, dict[str, Any] | None] = {}
        self.path_by_id: dict[str, Path] = {}
        self.created_ids: list[str] = []
        self.updated_ids: set[str] = set()
        self.status_changed_ids: set[str] = set()
        self.status_aliases: list[dict[str, str]] = []
        self.normalized_statuses: list[dict[str, str]] = []

    def mutable_data(self, node_id: str) -> dict[str, Any]:
        if node_id in self.data_by_id:
            return self.data_by_id[node_id]
        if node_id not in self.candidate:
            raise ValueError(f"Node does not exist: {node_id}")
        path = find_node_file(self.root, node_id)
        before = load_yaml(path)
        self.before_by_id[node_id] = copy.deepcopy(before)
        self.data_by_id[node_id] = copy.deepcopy(before)
        self.path_by_id[node_id] = path
        return self.data_by_id[node_id]

    def add_node(self, entry: dict[str, Any], known_new_ids: set[str], index: int) -> None:
        owner = f"nodes[{index}]"
        node_id = _required_text(entry, "id", owner)
        node_type = _required_text(entry, "type", owner)
        title = _required_text(entry, "title", owner)
        if node_id in self.candidate:
            raise FileExistsError(_node_path(self.root, node_id))
        raw_status = str(entry.get("status") or default_status_for_type(node_type))
        status, alias_from = _normalize_input_status(node_type, raw_status)
        validate_status(node_type, status)
        if node_type == "decision" and status == "accepted":
            raise ValueError(
                "Use `research-cockpit accept-decision` to accept a decision so option/problem state stays synchronized."
            )
        if alias_from:
            self.normalized_statuses.append({
                "node_id": node_id,
                "node_type": node_type,
                "input_status": alias_from,
                "stored_status": status,
            })
            self.status_aliases.append({
                "node_id": node_id,
                "type": node_type,
                "from": alias_from,
                "to": status,
            })
        parent = str(entry.get("parent") or "").strip()
        if parent and parent not in self.candidate and parent not in known_new_ids:
            raise ValueError(f"{owner}.parent references missing node {parent!r}")

        today = str(date.today())
        data: dict[str, Any] = {
            "id": node_id,
            "type": node_type,
            "title": title,
            "status": status,
            "summary": str(entry.get("summary") or ""),
            "created_at": today,
            "updated_at": today,
        }
        if parent:
            data["parent"] = parent

        self.before_by_id[node_id] = None
        self.data_by_id[node_id] = data
        self.path_by_id[node_id] = _node_path(self.root, node_id)
        self.candidate[node_id] = ResearchNode.from_dict(data)
        self.created_ids.append(node_id)
        self.status_changed_ids.add(node_id)

    def apply_fields(self, node_id: str, fields: dict[str, Any], *, owner: str) -> None:
        updates = field_updates_from_mapping(fields)
        if not any(
            [
                updates["current_best_option"],
                updates["replace_next_actions"] is not None,
                updates["scalar_updates"],
                updates["list_appends"],
                updates["bool_updates"],
            ]
        ):
            raise ValueError(f"{owner}.fields must include at least one supported field")
        data = self.mutable_data(node_id)
        apply_node_field_updates(
            self.candidate,
            node_id=node_id,
            data=data,
            current_best_option=updates["current_best_option"],
            replace_next_actions=updates["replace_next_actions"],
            scalar_updates=updates["scalar_updates"],
            list_appends=updates["list_appends"],
            bool_updates=updates["bool_updates"],
        )
        data["updated_at"] = str(date.today())
        self.candidate[node_id] = ResearchNode.from_dict(data)
        if node_id not in self.created_ids:
            self.updated_ids.add(node_id)

    def apply_status(self, node_id: str, raw_status: Any, *, owner: str) -> None:
        status_text = str(raw_status or "").strip()
        if not status_text:
            raise ValueError(f"{owner}.status is required when present")
        node = self.candidate[node_id]
        status, alias_from = _normalize_input_status(node.type, status_text)
        validate_status(node.type, status)
        if node.type == "decision" and status == "accepted":
            raise ValueError(
                "Use `research-cockpit accept-decision` to accept a decision so option/problem state stays synchronized."
            )
        if alias_from:
            self.normalized_statuses.append({
                "node_id": node_id,
                "node_type": node.type,
                "input_status": alias_from,
                "stored_status": status,
            })
            self.status_aliases.append({
                "node_id": node_id,
                "type": node.type,
                "from": alias_from,
                "to": status,
            })
        data = self.mutable_data(node_id)
        data["status"] = status
        data["updated_at"] = str(date.today())
        self.candidate[node_id] = ResearchNode.from_dict(data)
        if node.status != status:
            self.status_changed_ids.add(node_id)
        if node_id not in self.created_ids:
            self.updated_ids.add(node_id)

    def sync_parent_children(self) -> None:
        for node_id in self.created_ids:
            node_data = self.data_by_id[node_id]
            parent = str(node_data.get("parent") or "").strip()
            if not parent:
                continue
            parent_data = self.mutable_data(parent)
            children = parent_data.get("children", []) or []
            if not isinstance(children, list):
                raise ValueError(f"{parent}: children must be a list")
            child_ids = [str(child) for child in children]
            if node_id not in child_ids:
                child_ids.append(node_id)
                parent_data["children"] = child_ids
                parent_data["updated_at"] = str(date.today())
                self.candidate[parent] = ResearchNode.from_dict(parent_data)
                if parent not in self.created_ids:
                    self.updated_ids.add(parent)

    def changes(self) -> list[tuple[str, Path, dict[str, Any] | None, dict[str, Any]]]:
        out: list[tuple[str, Path, dict[str, Any] | None, dict[str, Any]]] = []
        for node_id in sorted(self.data_by_id):
            before = self.before_by_id.get(node_id)
            after = self.data_by_id[node_id]
            if before != after:
                out.append((node_id, self.path_by_id[node_id], before, after))
        return out


def apply_graph_plan(
    root: Path,
    *,
    plan: dict[str, Any],
    rebuild_dashboard: bool = True,
    dry_run: bool = False,
    show_diff: bool = False,
) -> dict[str, Any]:
    if not isinstance(plan, dict):
        raise ValueError("Graph plan must be a mapping")

    node_entries = _as_list(plan.get("nodes", []), "nodes")
    update_entries = _as_list(plan.get("updates", []), "updates")
    builder = GraphPlanBuilder(root)

    known_new_ids: set[str] = set()
    for index, raw_entry in enumerate(node_entries, start=1):
        if not isinstance(raw_entry, dict):
            raise ValueError(f"nodes[{index}] must be a mapping")
        node_id = _required_text(raw_entry, "id", f"nodes[{index}]")
        if node_id in known_new_ids:
            raise ValueError(f"Duplicate node id in graph plan: {node_id}")
        known_new_ids.add(node_id)

    for index, raw_entry in enumerate(node_entries, start=1):
        builder.add_node(raw_entry, known_new_ids, index)

    for index, raw_entry in enumerate(node_entries, start=1):
        fields = raw_entry.get("fields") or {}
        if fields:
            node_id = _required_text(raw_entry, "id", f"nodes[{index}]")
            if not isinstance(fields, dict):
                raise ValueError(f"nodes[{index}].fields must be a mapping")
            builder.apply_fields(node_id, fields, owner=f"nodes[{index}]")

    for index, raw_update in enumerate(update_entries, start=1):
        if not isinstance(raw_update, dict):
            raise ValueError(f"updates[{index}] must be a mapping")
        unsupported = sorted(set(raw_update) - UPDATE_TOP_LEVEL_FIELDS)
        if unsupported:
            field_text = ", ".join(unsupported)
            raise ValueError(
                f"updates[{index}] unsupported top-level field(s): {field_text}; "
                "use updates[*].fields for node field updates"
            )
        node_id = _required_text(raw_update, "id", f"updates[{index}]")
        if node_id not in builder.candidate:
            raise ValueError(f"updates[{index}].id references missing node {node_id!r}")
        has_status = "status" in raw_update
        fields = raw_update.get("fields")
        if not has_status and fields is None:
            raise ValueError(f"updates[{index}] must include status or fields")
        if fields is not None and not isinstance(fields, dict):
            raise ValueError(f"updates[{index}].fields must be a mapping")
        if has_status:
            builder.apply_status(node_id, raw_update.get("status"), owner=f"updates[{index}]")
        if fields is not None:
            builder.apply_fields(node_id, fields, owner=f"updates[{index}]")

    builder.sync_parent_children()
    raise_for_terminal_parent_transitions(root, builder.state.nodes, builder.candidate, builder.status_changed_ids)
    validate_cockpit(root, builder.candidate, builder.state.current, builder.state.explicit_edges, raise_on_error=True)

    changes = builder.changes()
    yaml_changes = [(path, before, after) for _, path, before, after in changes]
    changed = bool(changes)
    result: dict[str, Any] = {
        "dry_run": dry_run,
        "changed": False if dry_run else changed,
        "would_change": changed,
        "created_nodes": builder.created_ids,
        "updated_nodes": sorted(builder.updated_ids),
        "changed_files": [str(path) for _, path, _, _ in changes],
        "status_aliases": builder.status_aliases,
        "normalized_statuses": builder.normalized_statuses,
    }
    if show_diff:
        result["diff"] = yaml_change_diff([(path, before, after) for _, path, before, after in changes])
    if dry_run:
        return dry_run_preflight_result(root, result)
    if not changed:
        return result

    finish_mutation(
        root,
        yaml_changes,
        interaction={
            "kind": "apply_graph_plan",
            "actor": "researcher",
            "command": script_command("apply_graph_plan.py"),
            "after": {
                "created_nodes": builder.created_ids,
                "updated_nodes": sorted(builder.updated_ids),
                "changed_file_count": len(changes),
            },
        },
        rebuild_dashboard=rebuild_dashboard,
    )
    return result


def load_graph_plan(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Graph plan file does not exist: {path}")
    plan = load_yaml(path)
    if not isinstance(plan, dict):
        raise ValueError("Graph plan file must contain a mapping")
    return plan


def main() -> None:
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=APPLY_GRAPH_PLAN_EXAMPLE,
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--file", type=Path, dest="plan_file")
    parser.add_argument("--print-schema", action="store_true", help="Print the graph plan YAML schema example and exit.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--show-diff", action="store_true")
    parser.add_argument("--validate", action="store_true", help="Accepted for readability; validation always runs.")
    parser.add_argument("--build", action="store_true", help="Accepted for readability; build is the default.")
    parser.add_argument("--no-build", action="store_true")
    args = parser.parse_args()
    if args.print_schema:
        safe_print(APPLY_GRAPH_PLAN_EXAMPLE)
        return
    if args.plan_file is None:
        parser.error("--file is required unless --print-schema is used")

    try:
        result = apply_graph_plan(
            args.root,
            plan=load_graph_plan(args.plan_file),
            rebuild_dashboard=not args.no_build,
            dry_run=args.dry_run,
            show_diff=args.show_diff,
        )
    except LifecycleGuardError as exc:
        if args.json:
            emit_json(exc.payload)
        else:
            safe_print(str(exc))
        raise SystemExit(1) from exc
    except (ValidationError, ValueError, FileExistsError, FileNotFoundError) as exc:
        safe_print(str(exc))
        raise SystemExit(1) from exc

    if args.json:
        emit_json(
            compact_mutation_result(
                result,
                command="apply-graph-plan",
                target=str(args.plan_file),
                root=args.root,
            ) if args.compact else result
        )
        return
    verb = "Would apply" if args.dry_run else "Applied"
    safe_print(f"{verb} graph plan {args.plan_file}: {len(result['changed_files'])} file(s)")
    if args.show_diff and result.get("diff"):
        safe_print(result["diff"], end="" if str(result["diff"]).endswith("\n") else "\n")
    if not args.dry_run and not args.no_build:
        safe_print(f"Rebuilt dashboards under {args.root / 'dashboards'}")


if __name__ == "__main__":
    main()
