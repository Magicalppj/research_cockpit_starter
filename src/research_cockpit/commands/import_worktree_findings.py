from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

from research_cockpit.commands._runtime import (
    dry_run_preflight_result,
    emit_json,
    finish_mutation,
    load_validated_state,
    safe_print,
    yaml_change_diff,
)
from research_cockpit.graph_core import unique_strings
from research_cockpit.model import ResearchNode, ValidationError, load_nodes, load_yaml, script_command, validate_cockpit
from research_cockpit.mutation_lock import MutationError
from research_cockpit.option_workstreams import experiment_ids_for_option
from research_cockpit.paths import default_data_root
from research_cockpit.storage import find_node_file

ROOT = default_data_root()

EVIDENCE_EXPERIMENT_FIELDS = ("findings", "result_summary", "next_actions", "linked_artifacts")
EVIDENCE_OPTION_FIELDS = ("linked_artifacts", "workstream_report")
RECOVERY_OPTION_METADATA_FIELDS = ("agent_workstream", "updated_at")


def _finding_key(finding: dict[str, Any]) -> str:
    return str(finding.get("id") or finding.get("statement") or "")


def _artifact_ids_from_node(data: dict[str, Any]) -> list[str]:
    ids: list[str] = [str(item) for item in data.get("linked_artifacts", []) or [] if str(item).strip()]
    findings = data.get("findings", []) or []
    if isinstance(findings, list):
        for finding in findings:
            if isinstance(finding, dict):
                ids.extend(str(item) for item in finding.get("linked_artifacts", []) or [] if str(item).strip())
    return unique_strings(ids)


def _reject_unsafe_source_changes(
    canonical_current: dict[str, Any],
    source_current: dict[str, Any],
    canonical_nodes: dict[str, ResearchNode],
    source_nodes: dict[str, ResearchNode],
) -> None:
    for field in ("current_stage", "current_problem", "current_option", "current_focus_node", "current_focus_path"):
        if canonical_current.get(field) != source_current.get(field):
            raise ValueError(f"Refusing to import worktree global focus change: current_state.{field}")
    for node_id, source_node in source_nodes.items():
        if node_id not in canonical_nodes and source_node.type != "artifact":
            raise ValueError(f"Refusing structural graph change from worktree: new non-artifact node {node_id}")
        canonical_node = canonical_nodes.get(node_id)
        if canonical_node and source_node.type == "experiment":
            source_structural = {key: value for key, value in source_node.raw.items() if key not in EVIDENCE_EXPERIMENT_FIELDS}
            canonical_structural = {
                key: value for key, value in canonical_node.raw.items() if key not in EVIDENCE_EXPERIMENT_FIELDS
            }
            if source_structural != canonical_structural:
                raise ValueError(f"Refusing structural experiment change from worktree: {node_id}")
        if canonical_node and source_node.type == "option":
            ignored = (*EVIDENCE_OPTION_FIELDS, *RECOVERY_OPTION_METADATA_FIELDS)
            source_structural = {key: value for key, value in source_node.raw.items() if key not in ignored}
            canonical_structural = {key: value for key, value in canonical_node.raw.items() if key not in ignored}
            if source_structural != canonical_structural:
                raise ValueError(f"Refusing structural option change from worktree: {node_id}")
        if source_node.type == "decision":
            if canonical_node and source_node.status == "accepted" and canonical_node.status != "accepted":
                raise ValueError(f"Refusing decision acceptance from worktree: {node_id}")


def import_worktree_findings(
    root: Path,
    *,
    from_root: Path,
    agent_id: str,
    option_id: str,
    rebuild_dashboard: bool = True,
    dry_run: bool = False,
    show_diff: bool = False,
) -> dict[str, Any]:
    state = load_validated_state(root)
    canonical_nodes = state.nodes
    if option_id not in canonical_nodes or canonical_nodes[option_id].type != "option":
        raise ValueError(f"Option node does not exist: {option_id}")
    workstream = canonical_nodes[option_id].raw.get("agent_workstream")
    if isinstance(workstream, dict) and workstream.get("owner") and workstream.get("owner") != agent_id:
        raise ValueError(f"{option_id} is owned by {workstream.get('owner')}; refusing import for {agent_id}")

    source_nodes = load_nodes(from_root)
    source_current = load_yaml(from_root / "current_state.yaml")
    _reject_unsafe_source_changes(state.current, source_current, canonical_nodes, source_nodes)
    if option_id not in source_nodes or source_nodes[option_id].type != "option":
        raise ValueError(f"Source worktree is missing option node: {option_id}")

    experiment_ids = [item for item in experiment_ids_for_option(canonical_nodes, option_id) if item in source_nodes]
    artifact_ids: list[str] = []
    changes: list[tuple[Path, dict[str, Any] | None, dict[str, Any]]] = []
    changed_files: list[str] = []
    updated_nodes: list[str] = []
    imported_artifacts: list[str] = []
    candidate_nodes = dict(canonical_nodes)

    for experiment_id in experiment_ids:
        dest_path = find_node_file(root, experiment_id)
        dest = load_yaml(dest_path)
        source = source_nodes[experiment_id].raw
        before = copy.deepcopy(dest)
        existing_finding_keys = {
            _finding_key(finding)
            for finding in dest.get("findings", []) or []
            if isinstance(finding, dict)
        }
        next_findings = list(dest.get("findings", []) or [])
        for finding in source.get("findings", []) or []:
            if not isinstance(finding, dict):
                continue
            key = _finding_key(finding)
            if key and key not in existing_finding_keys:
                next_findings.append(copy.deepcopy(finding))
                existing_finding_keys.add(key)
        if next_findings:
            dest["findings"] = next_findings
        if source.get("result_summary"):
            dest["result_summary"] = source.get("result_summary")
        if source.get("next_actions"):
            dest["next_actions"] = unique_strings([*(dest.get("next_actions", []) or []), *source.get("next_actions", [])])
        if source.get("linked_artifacts"):
            dest["linked_artifacts"] = unique_strings(
                [*(dest.get("linked_artifacts", []) or []), *source.get("linked_artifacts", [])]
            )
        artifact_ids.extend(_artifact_ids_from_node(source))
        if dest != before:
            changes.append((dest_path, before, dest))
            changed_files.append(str(dest_path))
            updated_nodes.append(experiment_id)
            candidate_nodes[experiment_id] = ResearchNode.from_dict(dest)

    source_option = source_nodes[option_id].raw
    option_path = find_node_file(root, option_id)
    option_data = load_yaml(option_path)
    before_option = copy.deepcopy(option_data)
    if source_option.get("linked_artifacts"):
        option_data["linked_artifacts"] = unique_strings(
            [*(option_data.get("linked_artifacts", []) or []), *source_option.get("linked_artifacts", [])]
        )
    if isinstance(source_option.get("workstream_report"), dict):
        option_data["workstream_report"] = copy.deepcopy(source_option["workstream_report"])
    artifact_ids.extend(_artifact_ids_from_node(source_option))
    if option_data != before_option:
        changes.append((option_path, before_option, option_data))
        changed_files.append(str(option_path))
        updated_nodes.append(option_id)
        candidate_nodes[option_id] = ResearchNode.from_dict(option_data)

    for artifact_id in unique_strings(artifact_ids):
        if artifact_id not in source_nodes:
            raise ValueError(f"Source references missing artifact node: {artifact_id}")
        if source_nodes[artifact_id].type != "artifact":
            raise ValueError(f"Source linked artifact {artifact_id} is type {source_nodes[artifact_id].type}, expected artifact")
        if artifact_id in canonical_nodes:
            dest_path = find_node_file(root, artifact_id)
            before_artifact = load_yaml(dest_path)
        else:
            dest_path = root / "graph" / "nodes" / f"{artifact_id}.yaml"
            before_artifact = None
        after_artifact = copy.deepcopy(source_nodes[artifact_id].raw)
        if before_artifact != after_artifact:
            changes.append((dest_path, copy.deepcopy(before_artifact), after_artifact))
            changed_files.append(str(dest_path))
            imported_artifacts.append(artifact_id)
            candidate_nodes[artifact_id] = ResearchNode.from_dict(after_artifact)

    validate_cockpit(root, candidate_nodes, state.current, state.explicit_edges, raise_on_error=True)
    result: dict[str, Any] = {
        "ok": True,
        "dry_run": dry_run,
        "changed": not dry_run and bool(changes),
        "would_change": dry_run and bool(changes),
        "agent_id": agent_id,
        "option_id": option_id,
        "from_root": str(from_root),
        "changed_files": changed_files,
        "updated_nodes": unique_strings(updated_nodes),
        "imported_artifacts": unique_strings(imported_artifacts),
    }
    if show_diff:
        result["diff"] = yaml_change_diff(changes)
    if dry_run:
        result["changed"] = False
        return dry_run_preflight_result(root, result)
    if changes:
        finish_mutation(
            root,
            changes,
            interaction={
                "kind": "import_worktree_findings",
                "actor": agent_id,
                "node_id": option_id,
                "command": script_command(
                    "import_worktree_findings.py",
                    "--from-root",
                    str(from_root),
                    "--agent",
                    agent_id,
                    "--option",
                    option_id,
                ),
                "extra": {
                    "from_root": str(from_root),
                    "option_id": option_id,
                    "agent_id": agent_id,
                    "updated_nodes": unique_strings(updated_nodes),
                    "imported_artifacts": unique_strings(imported_artifacts),
                },
            },
            rebuild_dashboard=rebuild_dashboard,
        )
    else:
        result["changed"] = False
    return result


def main() -> None:
    parser = argparse.ArgumentParser(prog="research-cockpit import-worktree-findings")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--from-root", required=True, type=Path)
    parser.add_argument("--agent", required=True, dest="agent_id")
    parser.add_argument("--option", required=True, dest="option_id")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--show-diff", action="store_true")
    parser.add_argument("--no-build", action="store_true")
    args = parser.parse_args()

    try:
        payload = import_worktree_findings(
            args.root,
            from_root=args.from_root,
            agent_id=args.agent_id,
            option_id=args.option_id,
            rebuild_dashboard=not args.no_build,
            dry_run=args.dry_run,
            show_diff=args.show_diff,
        )
    except MutationError as exc:
        if args.json and exc.payload:
            emit_json(exc.payload)
        else:
            safe_print(str(exc))
        raise SystemExit(1) from exc
    except (ValidationError, ValueError, FileNotFoundError) as exc:
        if args.json:
            emit_json({
                "ok": False,
                "partial_success": False,
                "rolled_back": False,
                "written_files": [],
                "error": str(exc),
            })
        else:
            safe_print(str(exc))
        raise SystemExit(1) from exc

    if args.json:
        emit_json(payload)
        return
    safe_print(f"Imported worktree findings for {args.option_id}: {len(payload['changed_files'])} files changed.")


if __name__ == "__main__":
    main()
