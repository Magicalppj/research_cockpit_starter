from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from research_cockpit.paths import default_data_root

ROOT = default_data_root()

from research_cockpit.model import (
    ValidationError,
    load_explicit_edges,
    load_nodes,
    load_yaml,
    validate_cockpit,
)
from research_cockpit.option_workstreams import build_option_workstream_context
from research_cockpit.resources import node_artifact_ids


def option_workstream_context_payload(root: Path, *, option_id: str) -> dict:
    nodes = load_nodes(root)
    current = load_yaml(root / "current_state.yaml")
    explicit_edges = load_explicit_edges(root)
    validate_cockpit(root, nodes, current, explicit_edges, raise_on_error=True)
    return build_option_workstream_context(root, nodes, current, option_id)


def _brief_node(node: dict[str, Any] | None) -> dict[str, Any] | None:
    if not node:
        return None
    out = {
        "id": node.get("id"),
        "type": node.get("type"),
        "title": node.get("title"),
        "status": node.get("status"),
    }
    if node.get("summary"):
        out["summary"] = node.get("summary")
    if node.get("current_best_option"):
        out["current_best_option"] = node.get("current_best_option")
    return out


def _list_count(value: Any) -> int:
    if isinstance(value, list):
        return len([item for item in value if str(item).strip()])
    if isinstance(value, dict):
        return len(value)
    if isinstance(value, str):
        return 1 if value.strip() else 0
    return 0


def _first_text(value: Any, *, limit: int = 120) -> str | None:
    first: Any = None
    if isinstance(value, list) and value:
        first = value[0]
    elif isinstance(value, str):
        first = value
    if first is None:
        return None
    text = str(first).strip()
    if not text:
        return None
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _experiment_summaries(payload: dict[str, Any], raw_nodes: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    raw_nodes = raw_nodes or {}
    by_id = {
        str(node.get("id")): node
        for node in payload.get("subtree_nodes", [])
        if node.get("type") == "experiment"
    }
    summaries: list[dict[str, Any]] = []
    for experiment_id in payload["subtree"].get("experiment_ids", []):
        node = by_id.get(str(experiment_id))
        if not node:
            continue
        raw_node = raw_nodes.get(str(experiment_id))
        raw = getattr(raw_node, "raw", {}) if raw_node is not None else {}
        metrics = raw.get("metrics") if isinstance(raw, dict) else None
        success_criteria = node.get("success_criteria", []) or []
        linked_artifact_count = (
            _list_count(node_artifact_ids(raw_node))
            if raw_node is not None
            else _list_count(node.get("linked_artifacts", []))
        )
        summaries.append({
            "id": node.get("id"),
            "title": node.get("title"),
            "status": node.get("status"),
            "result_summary": node.get("result_summary"),
            "success_criteria_count": _list_count(success_criteria),
            "first_success_criterion": _first_text(success_criteria),
            "metric_count": _list_count(metrics),
            "finding_count": _list_count(node.get("findings", [])),
            "linked_artifact_count": linked_artifact_count,
        })
    return summaries


def compact_option_workstream_context(payload: dict[str, Any], raw_nodes: dict[str, Any] | None = None) -> dict[str, Any]:
    subtree = payload["subtree"]
    subtree_nodes = payload.get("subtree_nodes", [])
    linked_artifacts: list[str] = []
    for node in subtree_nodes:
        raw_node = raw_nodes.get(str(node.get("id"))) if raw_nodes else None
        if raw_node is not None:
            linked_artifacts.extend(node_artifact_ids(raw_node))
        else:
            linked_artifacts.extend(str(item) for item in node.get("linked_artifacts", []) or [] if str(item).strip())
    artifact_ids = sorted(set([*subtree.get("artifact_ids", []), *linked_artifacts]))
    evidence = payload["evidence_summary"]
    return {
        "option": _brief_node(payload["option"]),
        "upstream_problem": _brief_node(payload.get("upstream_problem")),
        "current_focus_related": payload.get("current_focus_related"),
        "focus_next_actions": payload.get("focus_next_actions", []),
        "open_next_actions": payload.get("open_next_actions", []),
        "blockers": payload.get("blockers", []),
        "subtree": {
            "root_option_id": subtree.get("root_option_id"),
            "node_count": len(subtree.get("node_ids", [])),
            "node_ids": subtree.get("node_ids", []),
            "problem_ids": subtree.get("problem_ids", []),
            "option_ids": subtree.get("option_ids", []),
            "experiment_ids": subtree.get("experiment_ids", []),
            "decision_ids": subtree.get("decision_ids", []),
            "artifact_ids": artifact_ids,
        },
        "evidence_summary": {
            "experiment_count": evidence.get("experiment_count", 0),
            "finding_count": evidence.get("findings_count", 0),
            "findings_count": evidence.get("findings_count", 0),
            "artifact_count": len(artifact_ids),
            "evidence_strength": evidence.get("evidence_strength"),
            "latest_finding": evidence.get("latest_finding"),
            "outcome_counts": evidence.get("outcome_counts", {}),
        },
        "experiment_summaries": _experiment_summaries(payload, raw_nodes),
        "hierarchy_policy": payload.get("hierarchy_policy", {}),
        "suggested_commands": payload.get("suggested_commands", {}),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--option", "--id", required=True, dest="option_id")
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        payload = option_workstream_context_payload(args.root, option_id=args.option_id)
    except (ValidationError, ValueError) as exc:
        print(str(exc))
        raise SystemExit(1) from exc

    if args.json:
        if args.compact:
            payload = compact_option_workstream_context(payload, load_nodes(args.root))
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    option = payload["option"]
    problem = payload.get("upstream_problem") or {}
    evidence = payload["evidence_summary"]
    print(f"Option: {option['id']} - {option['title']}")
    print(f"Upstream problem: {problem.get('id') or '(none)'}")
    print(f"Subtree nodes: {len(payload['subtree']['node_ids'])}")
    print(f"Experiments: {evidence['experiment_count']}; findings: {evidence['findings_count']}")
    if evidence.get("latest_finding"):
        print(f"Latest finding: {evidence['latest_finding']}")


if __name__ == "__main__":
    main()
