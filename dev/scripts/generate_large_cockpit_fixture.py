from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from research_cockpit.model import load_nodes
from research_cockpit.resources import build_link_rows
from research_cockpit.storage import save_yaml


FIXTURE_DATE = "2026-05-28"
MARKER_FILE = ".synthetic_research_cockpit_fixture.json"
MARKER_KIND = "research_cockpit_synthetic_fixture"


def _resource_path(index: int) -> str:
    return f"artifacts/search_resources/resource_{index:04d}.md"


def _base_links(node_index: int, *, links_per_node: int, resource_count: int) -> dict[str, str]:
    if links_per_node <= 0 or resource_count <= 0:
        return {}
    return {
        f"resource_{link_index:02d}": _resource_path((node_index * links_per_node + link_index) % resource_count)
        for link_index in range(links_per_node)
    }


def _artifact_resource_links(artifact_index: int, *, artifact_count: int, resource_count: int) -> dict[str, str]:
    return {
        f"artifact_resource_{resource_index:04d}": _resource_path(resource_index)
        for resource_index in range(artifact_index, resource_count, artifact_count)
    }


def _add_child(nodes: dict[str, dict[str, Any]], parent_id: str, child_id: str) -> None:
    nodes[parent_id].setdefault("children", []).append(child_id)


def _node(
    node_id: str,
    node_type: str,
    title: str,
    status: str,
    *,
    summary: str,
    parent: str | None = None,
    links: dict[str, str] | None = None,
    children: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": node_id,
        "type": node_type,
        "title": title,
        "status": status,
        "summary": summary,
        "updated_at": FIXTURE_DATE,
    }
    if parent:
        data["parent"] = parent
    if children:
        data["children"] = children
    if links:
        data["links"] = links
    if extra:
        data.update(extra)
    return data


def _layout_counts(node_count: int, resource_count: int) -> tuple[int, int, int]:
    if node_count < 5:
        raise ValueError("--nodes must be at least 5")
    level_count = max(1, min(50, node_count // 20 + 1))
    base_count = 1 + 2 * level_count
    if base_count > node_count - 2:
        level_count = 1
        base_count = 3
    remaining = node_count - base_count
    artifact_count = min(max(1, resource_count), max(1, remaining // 5), remaining - 1)
    experiment_count = node_count - base_count - artifact_count
    return level_count, artifact_count, experiment_count


def _is_synthetic_fixture_root(root: Path) -> bool:
    marker = root / MARKER_FILE
    if not marker.is_file():
        return False
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    if not isinstance(data, dict):
        return False
    return data.get("kind") == MARKER_KIND


def _prepare_root(root: Path, *, force: bool) -> None:
    if root.exists() and not root.is_dir():
        raise FileExistsError(f"Target root exists and is not a directory: {root}")
    if root.exists() and any(root.iterdir()):
        if not force:
            raise FileExistsError(f"Target root already exists and is not empty: {root}")
        if not _is_synthetic_fixture_root(root):
            raise FileExistsError(
                "Refusing to --force a non-synthetic fixture root. "
                f"Expected marker file: {MARKER_FILE}"
            )
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)


def _write_support_files(root: Path, *, note_count: int, resource_count: int) -> None:
    save_yaml(root / "graph" / "edges.yaml", {"edges": []})
    save_yaml(root / "graph" / "interaction_log.yaml", {"events": []})
    save_yaml(root / "graph" / "graph_views.yaml", {"views": []})

    for index in range(resource_count):
        path = root / _resource_path(index)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n".join(
                [
                    f"# Synthetic Search Resource {index:04d}",
                    "",
                    "This deterministic resource is used for dashboard build profiling.",
                    f"resource_index: {index}",
                    "topic: synthetic performance fixture",
                    "",
                ]
            ),
            encoding="utf-8",
        )

    notes_dir = root / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    for index in range(note_count):
        (notes_dir / f"note_{index:04d}.md").write_text(
            "\n".join(
                [
                    f"# Synthetic Note {index:04d}",
                    "",
                    "This note adds deterministic Markdown content to the search index.",
                    f"note_index: {index}",
                    "topic: synthetic performance fixture",
                    "",
                ]
            ),
            encoding="utf-8",
        )


def _link_notes_to_nodes(nodes: dict[str, dict[str, Any]], host_ids: list[str], *, note_count: int) -> None:
    if note_count > len(host_ids):
        raise ValueError("--note-count cannot exceed generated node count because each note is linked from one node")
    for note_index in range(note_count):
        host = nodes[host_ids[note_index]]
        links = dict(host.get("links") or {})
        links["notes"] = f"notes/note_{note_index:04d}.md"
        host["links"] = links


def _write_artifact_records(
    root: Path,
    experiment_ids: list[str],
    *,
    artifact_record_count: int,
    resource_count: int,
) -> int:
    if artifact_record_count <= 0:
        return 0
    if not experiment_ids:
        raise ValueError("Cannot create artifact records without experiment nodes")

    records_by_experiment: dict[str, dict[str, Any]] = {}
    for index in range(artifact_record_count):
        experiment_id = experiment_ids[index % len(experiment_ids)]
        run_id = f"run_perf_{index:04d}"
        record_id = f"artifact_record_perf_{index:04d}"
        records_by_experiment.setdefault(experiment_id, {})[record_id] = {
            "record_id": record_id,
            "run_id": run_id,
            "title": f"Synthetic artifact record {index:04d}",
            "status": "available",
            "path": f"artifacts/record_outputs/{experiment_id}/{run_id}",
            "links": {
                "metrics": _resource_path(index % resource_count),
            },
            "artifact_kind": "run_output",
            "retention": {
                "class": "reproducible_output",
                "reason": "Synthetic record for benchmark fixture growth.",
            },
            "created_at": FIXTURE_DATE,
            "updated_at": FIXTURE_DATE,
            "promoted_artifact_id": None,
        }

    for experiment_id, records in sorted(records_by_experiment.items()):
        save_yaml(
            root / "artifact_records" / f"{experiment_id}.yaml",
            {
                "schema_version": "artifact_records_v1",
                "experiment_id": experiment_id,
                "records": records,
            },
        )
    return len(records_by_experiment)


def _write_marker(root: Path, payload: dict[str, Any]) -> None:
    marker_payload = {
        "kind": MARKER_KIND,
        "schema_version": 1,
        "generated_at": FIXTURE_DATE,
        "node_count": payload["node_count"],
        "note_count": payload["note_count"],
        "resource_count": payload["resource_count"],
        "linked_resource_count": payload["linked_resource_count"],
        "artifact_record_count": payload.get("artifact_record_count", 0),
        "artifact_record_file_count": payload.get("artifact_record_file_count", 0),
    }
    (root / MARKER_FILE).write_text(json.dumps(marker_payload, indent=2, ensure_ascii=False), encoding="utf-8")


def generate_fixture(
    root: Path,
    *,
    node_count: int,
    links_per_node: int,
    note_count: int,
    resource_count: int,
    artifact_record_count: int = 0,
    force: bool = False,
) -> dict[str, Any]:
    if node_count < 5:
        raise ValueError("--nodes must be at least 5")
    if links_per_node < 0:
        raise ValueError("--links-per-node must be non-negative")
    if note_count < 0:
        raise ValueError("--note-count must be non-negative")
    if resource_count < 1:
        raise ValueError("--resource-count must be at least 1")
    if artifact_record_count < 0:
        raise ValueError("--artifacts must be non-negative")

    level_count, artifact_count, experiment_count = _layout_counts(node_count, resource_count)
    if note_count > node_count:
        raise ValueError("--note-count must be less than or equal to --nodes")
    _prepare_root(root, force=force)
    _write_support_files(root, note_count=note_count, resource_count=resource_count)

    nodes: dict[str, dict[str, Any]] = {}
    node_index = 0
    stage_id = "stage_perf_0000"
    problem_ids: list[str] = []
    option_ids: list[str] = []
    artifact_ids: list[str] = []
    experiment_ids: list[str] = []
    nodes[stage_id] = _node(
        stage_id,
        "stage",
        "Synthetic performance stage",
        "active",
        summary="Root stage for deterministic dashboard build profiling.",
        links=_base_links(node_index, links_per_node=links_per_node, resource_count=resource_count),
    )
    node_index += 1

    for level in range(level_count):
        problem_id = f"problem_perf_{level:04d}"
        option_id = f"option_perf_{level:04d}"
        problem_ids.append(problem_id)
        option_ids.append(option_id)
        problem_parent = stage_id if level == 0 else f"option_perf_{level - 1:04d}"
        nodes[problem_id] = _node(
            problem_id,
            "problem",
            f"Synthetic performance problem {level:04d}",
            "active" if level == 0 else "open",
            parent=problem_parent,
            summary="Nested problem node used to exercise parent chains.",
            links=_base_links(node_index, links_per_node=links_per_node, resource_count=resource_count),
        )
        node_index += 1
        nodes[option_id] = _node(
            option_id,
            "option",
            f"Synthetic performance option {level:04d}",
            "active" if level == 0 else "open",
            parent=problem_id,
            summary="Nested option branch for deterministic build profiling.",
            links=_base_links(node_index, links_per_node=links_per_node, resource_count=resource_count),
            extra={
                "hypothesis": "Deterministic fixture branches make build regressions easier to profile.",
            },
        )
        node_index += 1
        _add_child(nodes, problem_parent, problem_id)
        _add_child(nodes, problem_id, option_id)

    for artifact_index in range(artifact_count):
        artifact_id = f"artifact_perf_{artifact_index:04d}"
        artifact_ids.append(artifact_id)
        artifact_links = _base_links(node_index, links_per_node=links_per_node, resource_count=resource_count)
        artifact_links.update(
            _artifact_resource_links(artifact_index, artifact_count=artifact_count, resource_count=resource_count)
        )
        nodes[artifact_id] = _node(
            artifact_id,
            "artifact",
            f"Synthetic artifact bundle {artifact_index:04d}",
            "done",
            summary="Artifact node with deterministic searchable resources.",
            links=artifact_links,
            extra={"path": _resource_path(artifact_index % resource_count)},
        )
        node_index += 1

    for experiment_index in range(experiment_count):
        option_id = f"option_perf_{experiment_index % level_count:04d}"
        artifact_id = f"artifact_perf_{experiment_index % artifact_count:04d}" if artifact_count else ""
        experiment_id = f"experiment_perf_{experiment_index:04d}"
        experiment_ids.append(experiment_id)
        links = _base_links(node_index, links_per_node=links_per_node, resource_count=resource_count)
        extra: dict[str, Any] = {
            "metrics": ["synthetic_score", "build_latency_ms"],
            "success_criteria": ["Fixture remains valid after build and smoke checks."],
            "ready_for_agent": experiment_index % 3 == 0,
            "owner": f"agent_perf_{experiment_index % 4}",
        }
        if artifact_id:
            extra["linked_artifacts"] = [artifact_id]
        nodes[experiment_id] = _node(
            experiment_id,
            "experiment",
            f"Synthetic experiment {experiment_index:04d}",
            "queued" if experiment_index % 5 == 0 else "planned",
            parent=option_id,
            summary="Generated experiment node for dashboard performance profiling.",
            links=links,
            extra=extra,
        )
        node_index += 1
        _add_child(nodes, option_id, experiment_id)

    _link_notes_to_nodes(
        nodes,
        [*experiment_ids, *option_ids, *problem_ids, stage_id, *artifact_ids],
        note_count=note_count,
    )
    artifact_record_file_count = _write_artifact_records(
        root,
        experiment_ids,
        artifact_record_count=artifact_record_count,
        resource_count=resource_count,
    )

    for node in nodes.values():
        save_yaml(root / "graph" / "nodes" / f"{node['id']}.yaml", node)

    focus_experiment = "experiment_perf_0000"
    save_yaml(
        root / "current_state.yaml",
        {
            "current_stage": stage_id,
            "current_problem": "problem_perf_0000",
            "current_option": "option_perf_0000",
            "current_focus_node": focus_experiment,
            "current_focus_path": [stage_id, "problem_perf_0000", "option_perf_0000", focus_experiment],
            "current_hypothesis": "Synthetic fixture is large enough to expose dashboard build bottlenecks.",
            "next_actions": ["Profile dashboard build stages on this synthetic fixture."],
            "open_risks": [],
            "updated_at": FIXTURE_DATE,
        },
    )

    loaded_nodes = load_nodes(root)
    payload = {
        "ok": True,
        "root": str(root),
        "node_count": len(loaded_nodes),
        "level_count": level_count,
        "experiment_count": experiment_count,
        "artifact_count": artifact_count,
        "note_count": note_count,
        "resource_count": resource_count,
        "links_per_node": links_per_node,
        "artifact_record_count": artifact_record_count,
        "artifact_record_file_count": artifact_record_file_count,
        "linked_resource_count": len(build_link_rows(root, loaded_nodes)),
    }
    _write_marker(root, payload)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a deterministic large Research Cockpit fixture.")
    parser.add_argument("--root", type=Path, required=True, help="Target research_cockpit data root.")
    parser.add_argument("--nodes", type=int, default=1000, help="Total graph node count to generate.")
    parser.add_argument("--links-per-node", type=int, default=2, help="Search resource links attached to each node.")
    parser.add_argument("--note-count", type=int, default=25, help="Markdown notes to create and link to generated nodes.")
    parser.add_argument("--resource-count", type=int, default=100, help="Search resource files to create.")
    parser.add_argument(
        "--artifacts",
        "--artifact-records",
        dest="artifact_record_count",
        type=int,
        default=0,
        help="Artifact-like sidecar records to create outside graph/nodes.",
    )
    parser.add_argument("--force", action="store_true", help="Replace an existing synthetic fixture root with a valid marker.")
    parser.add_argument("--json", action="store_true", help="Print a machine-readable summary.")
    args = parser.parse_args()

    try:
        payload = generate_fixture(
            args.root,
            node_count=args.nodes,
            links_per_node=args.links_per_node,
            note_count=args.note_count,
            resource_count=args.resource_count,
            artifact_record_count=args.artifact_record_count,
            force=args.force,
        )
    except (ValueError, FileExistsError) as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2, ensure_ascii=False))
        else:
            print(str(exc))
        raise SystemExit(1) from None

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    print(f"Generated {payload['node_count']} nodes at {payload['root']}")
    print(f"Resources: {payload['resource_count']} files, notes: {payload['note_count']}")
    print(f"Artifact records: {payload['artifact_record_count']} records in {payload['artifact_record_file_count']} files")


if __name__ == "__main__":
    main()
