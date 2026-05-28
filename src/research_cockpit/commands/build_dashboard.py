from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import time
from typing import Any

from research_cockpit.paths import default_data_root
import json

ROOT = default_data_root()
PROFILE_SCHEMA_VERSION = "build_profile_v1"

from research_cockpit.context_packs import (
    DashboardReadModels,
    build_agent_context,
    build_current_state_payload,
    build_focus_context,
    write_dashboard_markdown,
)
from research_cockpit.assignment_view import build_assignment_view
from research_cockpit.graph_core import GraphTopology
from research_cockpit.model import (
    build_experiment_matrix,
    build_search_index,
    build_search_index_summary,
    graph_to_json,
    load_explicit_edges,
    load_nodes,
    load_yaml,
    validate_cockpit,
)
from research_cockpit.resources import build_link_rows
from research_cockpit.decisions import build_decision_acceptance_checklists
from research_cockpit.mutation_lock import mutation_lock
from research_cockpit.option_workstreams import build_option_workstream_rows
from research_cockpit.gate_result_records import gate_result_signature
from research_cockpit.run_summaries import run_progress_signature, run_staleness_signature
from research_cockpit.suggestions import build_action_suggestions
from research_cockpit.storage import save_text


def _truth_source_files(root: Path) -> list[Path]:
    files: list[Path] = []
    current = root / "current_state.yaml"
    if current.exists():
        files.append(current)
    graph = root / "graph"
    if graph.exists():
        files.extend(path for path in graph.rglob("*.yaml") if path.is_file())
    notes = root / "notes"
    if notes.exists():
        files.extend(path for path in notes.rglob("*.md") if path.is_file())
    runs = root / "runs"
    if runs.exists():
        files.extend(path for path in runs.rglob("*.yaml") if path.is_file())
    gate_results = root / "gate_results"
    if gate_results.exists():
        files.extend(path for path in gate_results.rglob("*.yaml") if path.is_file())
    return sorted(files)


def truth_source_signature(root: Path) -> tuple[tuple[str, int, int], ...]:
    items: list[tuple[str, int, int]] = []
    for path in _truth_source_files(root):
        stat = path.stat()
        try:
            name = path.relative_to(root).as_posix()
        except ValueError:
            name = str(path)
        items.append((name, stat.st_mtime_ns, stat.st_size))
    return tuple(items)


def dashboard_watch_signature(root: Path, *, now: Any | None = None) -> tuple[object, object, object, object]:
    return (
        truth_source_signature(root),
        run_staleness_signature(root, now=now),
        run_progress_signature(root, now=now),
        gate_result_signature(root),
    )


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _duration_ms(started_at: float) -> float:
    return round(max(0.0, time.perf_counter() - started_at) * 1000, 3)


class _BuildProfiler:
    def __init__(self) -> None:
        self._started_at = time.perf_counter()
        self.stages: list[dict[str, object]] = []

    def run(self, name: str, callback: Any) -> Any:
        started_at = time.perf_counter()
        try:
            return callback()
        finally:
            self.stages.append({"name": name, "duration_ms": _duration_ms(started_at)})

    def payload(self, *, root: Path, counts: dict[str, int], outputs: list[Path]) -> dict[str, object]:
        return {
            "schema_version": PROFILE_SCHEMA_VERSION,
            "generated_at": _utc_timestamp(),
            "total_duration_ms": _duration_ms(self._started_at),
            "stages": self.stages,
            "counts": counts,
            "output_files": _output_file_metrics(root, outputs),
        }


def _profiled(profiler: _BuildProfiler | None, name: str, callback: Any) -> Any:
    if profiler is None:
        return callback()
    return profiler.run(name, callback)


def _dashboard_outputs(root: Path) -> list[Path]:
    dash = root / "dashboards"
    return [
        dash / "graph_view.json",
        dash / "agent_context_pack.json",
        dash / "focus_context_pack.json",
        dash / "current_state.md",
        dash / "current_state.json",
        dash / "experiment_matrix.json",
        dash / "linked_resources.json",
        dash / "next_action_suggestions.json",
        dash / "search_index.json",
        dash / "decision_acceptance_checklists.json",
        dash / "option_workstreams.json",
        dash / "assignment_view.json",
    ]


def _output_file_metrics(root: Path, outputs: list[Path]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for output in outputs:
        try:
            path = output.relative_to(root).as_posix()
        except ValueError:
            path = str(output)
        rows.append({"path": path, "bytes": output.stat().st_size if output.exists() else 0})
    return rows


def _json_size_bytes(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _graph_view_payload_size_metrics(graph_json: dict[str, Any], nodes: dict[str, Any]) -> dict[str, int]:
    graph_with_raw = dict(graph_json)
    graph_nodes: list[Any] = []
    omitted_raw_count = 0
    for row in graph_json.get("nodes", []):
        if not isinstance(row, dict):
            graph_nodes.append(row)
            continue
        node_id = str(row.get("id") or "")
        node = nodes.get(node_id)
        raw_row = dict(row)
        if "raw" not in row:
            omitted_raw_count += 1
        if node is not None:
            raw_row["raw"] = node.raw
        graph_nodes.append(raw_row)
    graph_with_raw["nodes"] = graph_nodes
    return {
        "graph_view_slim_estimated_bytes": _json_size_bytes(graph_json),
        "graph_view_with_raw_estimated_bytes": _json_size_bytes(graph_with_raw),
        "graph_view_raw_omitted_node_count": omitted_raw_count,
    }


def _search_index_profile_metrics(search_index: list[dict[str, Any]]) -> dict[str, int]:
    summary = build_search_index_summary(search_index, focus_entry_limit=0)
    resource_indexed_count = int(summary.get("resource_count") or 0)
    resource_skipped_count = int(summary.get("resource_skipped_count") or 0)
    return {
        "search_note_count": int(summary.get("note_count") or 0),
        "search_node_count": int(summary.get("node_count") or 0),
        "search_resource_indexed_count": resource_indexed_count,
        "search_resource_unique_indexed_count": int(summary.get("resource_unique_count") or 0),
        "search_resource_skipped_count": resource_skipped_count,
        "search_resource_total_count": resource_indexed_count + resource_skipped_count,
        "search_resource_truncated_count": int(summary.get("resource_truncated_count") or 0),
        "search_resource_disabled_count": int(summary.get("resource_search_disabled_count") or 0),
        "search_resource_bytes_read": int(summary.get("resource_bytes_read") or 0),
        "search_unlinked_note_count": int(summary.get("unlinked_note_count") or 0),
    }


def _build_dashboard_payload(
    root: Path,
    *,
    profiler: _BuildProfiler | None = None,
    include_resource_search: bool = True,
) -> dict[str, object]:
    nodes = _profiled(profiler, "load_nodes", lambda: load_nodes(root))
    current = _profiled(profiler, "load_current_state", lambda: load_yaml(root / "current_state.yaml"))
    explicit_edges = _profiled(profiler, "load_explicit_edges", lambda: load_explicit_edges(root))
    _profiled(profiler, "validate", lambda: validate_cockpit(root, nodes, current, explicit_edges, raise_on_error=True))
    topology = _profiled(profiler, "build_graph_topology", lambda: GraphTopology.from_nodes(nodes))
    graph_json = _profiled(
        profiler,
        "graph_to_json",
        lambda: graph_to_json(
            nodes,
            current.get("current_focus_path", []),
            current,
            explicit_edges,
            topology=topology,
            include_raw=False,
        ),
    )
    linked_resources = _profiled(profiler, "build_link_rows", lambda: build_link_rows(root, nodes))
    action_suggestions = _profiled(
        profiler,
        "build_action_suggestions",
        lambda: build_action_suggestions(root, nodes, current, linked_resources),
    )
    search_index = _profiled(
        profiler,
        "build_search_index",
        lambda: build_search_index(
            root,
            nodes,
            current,
            link_rows=linked_resources,
            topology=topology,
            include_resource_text=include_resource_search,
        ),
    )
    option_workstreams = _profiled(
        profiler,
        "build_option_workstreams",
        lambda: build_option_workstream_rows(nodes, current, topology=topology),
    )
    assignment_view = _profiled(profiler, "build_assignment_view", lambda: build_assignment_view(nodes))
    read_models = DashboardReadModels(
        linked_resources=linked_resources,
        action_suggestions=action_suggestions,
        search_index=search_index,
        option_workstreams=option_workstreams,
        assignment_view=assignment_view,
        topology=topology,
    )
    context = _profiled(
        profiler,
        "build_agent_context",
        lambda: build_agent_context(root, nodes, current=current, read_models=read_models),
    )
    focus_context = _profiled(
        profiler,
        "build_focus_context",
        lambda: build_focus_context(root, nodes, current=current, read_models=read_models),
    )
    current_payload = _profiled(
        profiler,
        "build_current_state_payload",
        lambda: build_current_state_payload(root, nodes, current),
    )
    experiment_matrix = _profiled(profiler, "build_experiment_matrix", lambda: build_experiment_matrix(nodes))
    decision_checklists = _profiled(
        profiler,
        "build_decision_checklists",
        lambda: build_decision_acceptance_checklists(nodes),
    )

    dash = root / "dashboards"
    dash.mkdir(parents=True, exist_ok=True)
    outputs = _dashboard_outputs(root)

    def write_outputs() -> None:
        save_text(outputs[0], json.dumps(graph_json, indent=2, ensure_ascii=False))
        save_text(outputs[1], json.dumps(context, indent=2, ensure_ascii=False))
        save_text(outputs[2], json.dumps(focus_context, indent=2, ensure_ascii=False))
        write_dashboard_markdown(root, context)
        save_text(outputs[4], json.dumps(current_payload, indent=2, ensure_ascii=False))
        save_text(outputs[5], json.dumps(experiment_matrix, indent=2, ensure_ascii=False))
        save_text(outputs[6], json.dumps(linked_resources, indent=2, ensure_ascii=False))
        save_text(outputs[7], json.dumps(action_suggestions, indent=2, ensure_ascii=False))
        save_text(outputs[8], json.dumps(search_index, indent=2, ensure_ascii=False))
        save_text(outputs[9], json.dumps(decision_checklists, indent=2, ensure_ascii=False))
        save_text(outputs[10], json.dumps(option_workstreams, indent=2, ensure_ascii=False))
        save_text(outputs[11], json.dumps(assignment_view, indent=2, ensure_ascii=False))

    _profiled(profiler, "write_outputs", write_outputs)
    counts = {
        "node_count": len(nodes),
        "edge_count": len(graph_json.get("edges", [])),
        "linked_resource_count": len(linked_resources),
        "action_suggestion_count": len(action_suggestions),
        "search_index_entry_count": len(search_index),
        "decision_checklist_count": len(decision_checklists),
        "option_workstream_count": len(option_workstreams),
        "assignment_count": len(assignment_view.get("assignments", [])),
        "search_resource_text_enabled": 1 if include_resource_search else 0,
    }
    if profiler is not None:
        counts.update(_graph_view_payload_size_metrics(graph_json, nodes))
        counts.update(_search_index_profile_metrics(search_index))
    return {"outputs": outputs, "counts": counts}


def build_dashboard(root: Path = ROOT) -> list[Path]:
    return list(_build_dashboard_payload(root)["outputs"])


def _resolve_profile_output(root: Path, profile_output: Path) -> Path:
    root_path = root.resolve(strict=False)
    dashboards_path = (root_path / "dashboards").resolve(strict=False)
    candidate = profile_output if profile_output.is_absolute() else root_path / profile_output
    candidate = candidate.resolve(strict=False)
    try:
        candidate.relative_to(dashboards_path)
    except ValueError as exc:
        raise ValueError("--profile-output must be a path inside <root>/dashboards") from exc
    if candidate.suffix.lower() != ".json":
        raise ValueError("--profile-output must be a .json file")
    standard_outputs = {output.resolve(strict=False) for output in _dashboard_outputs(root_path)}
    if candidate in standard_outputs:
        raise ValueError("--profile-output must not overwrite standard dashboard outputs")
    return candidate


def build_dashboard_once(
    root: Path,
    *,
    json_output: bool = False,
    profile: bool = False,
    profile_output: Path | None = None,
    include_resource_search: bool = True,
) -> dict:
    resolved_profile_output = _resolve_profile_output(root, profile_output) if profile_output else None
    profile_enabled = profile or resolved_profile_output is not None
    with mutation_lock(root):
        profiler = _BuildProfiler() if profile_enabled else None
        build_payload = _build_dashboard_payload(
            root,
            profiler=profiler,
            include_resource_search=include_resource_search,
        )
        outputs = list(build_payload["outputs"])
        counts = dict(build_payload["counts"])
        profile_payload = profiler.payload(root=root, counts=counts, outputs=outputs) if profiler else None
        if resolved_profile_output and profile_payload:
            save_text(resolved_profile_output, json.dumps(profile_payload, indent=2, ensure_ascii=False))

    payload = {
        "ok": True,
        "root": str(root),
        "node_count": counts["node_count"],
        "written_files": [str(output) for output in outputs],
        "json": json_output,
    }
    if profile_payload:
        payload["profile"] = profile_payload
    if resolved_profile_output:
        payload["profile_output"] = str(resolved_profile_output)
        payload["written_files"].append(str(resolved_profile_output))
    return payload


def watch_dashboard(
    root: Path,
    *,
    interval: float,
    max_iterations: int | None,
    json_output: bool,
    profile: bool = False,
    profile_output: Path | None = None,
    include_resource_search: bool = True,
) -> None:
    last_signature: tuple[object, ...] | None = None
    last_build_at: str | None = None
    last_build_status = "never"
    last_build_error = ""
    iteration = 0
    while max_iterations is None or iteration < max_iterations:
        iteration += 1
        try:
            signature = dashboard_watch_signature(root)
        except Exception as exc:
            last_build_at = _utc_timestamp()
            last_build_status = "failed"
            last_build_error = str(exc)
            payload = {
                "ok": False,
                "root": str(root),
                "watch": True,
                "iteration": iteration,
                "truth_source_changed": True,
                "time_sensitive_changed": False,
                "build_attempted": False,
                "written_files": [],
                "error": str(exc),
            }
        else:
            if last_signature is None or signature != last_signature:
                truth_source_changed = last_signature is None or signature[0] != last_signature[0]
                try:
                    payload = build_dashboard_once(
                        root,
                        json_output=json_output,
                        profile=profile,
                        profile_output=profile_output,
                        include_resource_search=include_resource_search,
                    )
                except Exception as exc:
                    last_build_at = _utc_timestamp()
                    last_build_status = "failed"
                    last_build_error = str(exc)
                    payload = {
                        "ok": False,
                        "root": str(root),
                        "watch": True,
                        "iteration": iteration,
                        "truth_source_changed": truth_source_changed,
                        "time_sensitive_changed": not truth_source_changed,
                        "build_attempted": True,
                        "written_files": [],
                        "error": str(exc),
                    }
                else:
                    last_build_at = _utc_timestamp()
                    last_build_status = "success"
                    last_build_error = ""
                    payload.update({
                        "watch": True,
                        "iteration": iteration,
                        "truth_source_changed": truth_source_changed,
                        "time_sensitive_changed": not truth_source_changed,
                        "build_attempted": True,
                    })
                    try:
                        last_signature = dashboard_watch_signature(root)
                    except Exception:
                        last_signature = signature
            else:
                payload = {
                    "ok": True,
                    "root": str(root),
                    "watch": True,
                    "iteration": iteration,
                    "truth_source_changed": False,
                    "time_sensitive_changed": False,
                    "build_attempted": False,
                    "written_files": [],
                }
        payload.update({
            "last_build_at": last_build_at,
            "last_build_status": last_build_status,
            "last_build_error": last_build_error,
        })
        if json_output:
            print(json.dumps(payload, ensure_ascii=False), flush=True)
        else:
            if not payload.get("ok", True):
                print(f"[{iteration}] Build failed: {payload.get('error')}", flush=True)
            elif payload["truth_source_changed"] or payload.get("time_sensitive_changed"):
                print(f"[{iteration}] Built dashboard.", flush=True)
            else:
                print(f"[{iteration}] No truth-source changes.", flush=True)
        if max_iterations is not None and iteration >= max_iterations:
            break
        time.sleep(max(0.0, interval))


def main() -> None:
    parser = argparse.ArgumentParser(prog="research-cockpit build")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--max-iterations", type=int)
    parser.add_argument("--profile", action="store_true", help="Include per-stage build timing in JSON output.")
    parser.add_argument(
        "--profile-output",
        type=Path,
        help="Write profile JSON to this path. Relative paths are resolved under --root.",
    )
    parser.add_argument(
        "--skip-resource-search",
        action="store_true",
        help="Skip reading local linked resource text while keeping node and note search entries.",
    )
    args = parser.parse_args()
    profile_enabled = args.profile or args.profile_output is not None

    if args.watch:
        watch_dashboard(
            args.root,
            interval=args.interval,
            max_iterations=args.max_iterations,
            json_output=args.json,
            profile=profile_enabled,
            profile_output=args.profile_output,
            include_resource_search=not args.skip_resource_search,
        )
        return

    payload = build_dashboard_once(
        args.root,
        json_output=args.json,
        profile=profile_enabled,
        profile_output=args.profile_output,
        include_resource_search=not args.skip_resource_search,
    )
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    print(f"Built dashboard for {payload['node_count']} nodes.")
    for output in payload["written_files"]:
        print(f"Wrote: {output}")
    if payload.get("profile"):
        profile_payload = payload["profile"]
        print(
            f"Profile: {profile_payload['total_duration_ms']} ms "
            f"across {len(profile_payload['stages'])} stages."
        )


if __name__ == "__main__":
    main()
