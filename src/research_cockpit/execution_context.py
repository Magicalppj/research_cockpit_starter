from __future__ import annotations

from pathlib import Path
from typing import Any

from research_cockpit.agent_state import AssignmentRecord, load_assignments
from research_cockpit.baselines import resolve_effective_baseline
from research_cockpit.commands._runtime import stable_payload_revision
from research_cockpit.gate_result_records import build_experiment_gate_context
from research_cockpit.model import ACTIVE_ASSIGNMENT_STATUSES, ValidationError
from research_cockpit.root_snapshot import load_root_snapshot
from research_cockpit.run_summaries import build_experiment_run_context


EXECUTION_CONTEXT_SCHEMA_VERSION = "execution_context_v1"
_TEXT_LIMIT = 256
_LIST_LIMIT = 5


def _text(value: Any, *, limit: int = _TEXT_LIMIT) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _bounded_strings(values: Any, *, limit: int = _LIST_LIMIT) -> list[str]:
    if not isinstance(values, list):
        return []
    return [_text(value) for value in values[:limit] if _text(value)]


def _first_action(values: Any) -> str:
    if not isinstance(values, list):
        return ""
    for value in values:
        if isinstance(value, dict):
            for key in ("action", "text", "title"):
                action = _text(value.get(key))
                if action:
                    return action
            continue
        action = _text(value)
        if action:
            return action
    return ""


def _assignment_boundary(root: Path, node_id: str) -> dict[str, Any] | None:
    assignments = [
        assignment
        for assignment in load_assignments(root).values()
        if assignment.status in ACTIVE_ASSIGNMENT_STATUSES
        and node_id in {assignment.current_node, assignment.root_node}
    ]
    if not assignments:
        return None

    def rank(assignment: AssignmentRecord) -> tuple[int, str, str]:
        return (
            1 if assignment.current_node == node_id else 0,
            str(assignment.updated_at or ""),
            assignment.assignment_id,
        )

    assignment = sorted(assignments, key=rank, reverse=True)[0]
    return {
        "assignment_id": assignment.assignment_id,
        "agent_id": assignment.agent_id,
        "status": assignment.status,
        "root_node": assignment.root_node,
        "current_node": assignment.current_node,
        "allowed_subtree": assignment.allowed_subtree,
        "objective": _text(assignment.objective),
        "next_action": _first_action(assignment.next_actions),
    }


def _node_payload(node: Any) -> dict[str, Any]:
    return {
        "id": node.id,
        "type": node.type,
        "title": _text(node.title),
        "status": node.status,
        "next_action": _first_action(node.raw.get("next_actions")),
    }


def _compact_progress(progress: Any) -> dict[str, Any] | None:
    if not isinstance(progress, dict):
        return None
    out: dict[str, Any] = {}
    for key in (
        "percent_complete",
        "completed_steps",
        "total_steps",
        "last_update",
        "current_stage",
        "latest_artifact",
        "possibly_stale",
    ):
        value = progress.get(key)
        if value not in (None, "", []):
            out[key] = _text(value) if isinstance(value, str) else value
    warnings = _bounded_strings(progress.get("warnings"))
    if warnings:
        out["warnings"] = warnings
    return out or None


def _compact_run(run: Any) -> dict[str, Any] | None:
    if not isinstance(run, dict):
        return None
    out = {
        key: run[key]
        for key in ("run_id", "status", "started_at", "possibly_stale")
        if run.get(key) not in (None, "", [])
    }
    progress = _compact_progress(run.get("progress"))
    if progress:
        out["progress"] = progress
    stale_reasons = _bounded_strings(run.get("stale_reasons"))
    if stale_reasons:
        out["stale_reasons"] = stale_reasons
    return out or None


def _compact_gate(gate: Any) -> dict[str, Any] | None:
    if not isinstance(gate, dict):
        return None
    out = {
        key: gate[key]
        for key in (
            "gate_id",
            "gate_type",
            "passed",
            "blocks_next_action",
            "next_allowed_action",
            "run_id",
            "recorded_at",
        )
        if gate.get(key) not in (None, "", [])
    }
    for key in ("fatal_failures", "warnings", "schema_warnings"):
        values = _bounded_strings(gate.get(key), limit=3)
        if values:
            out[key] = values
    return out or None


def _compact_baseline(baseline: dict[str, Any]) -> dict[str, Any]:
    def node_ref(value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict) or not value.get("id"):
            return None
        return {
            key: value[key]
            for key in ("id", "type", "status")
            if value.get(key) not in (None, "")
        }

    artifacts = [
        ref
        for ref in (node_ref(value) for value in baseline.get("artifacts", [])[:8])
        if ref is not None
    ]
    return {
        "source_node_id": str(baseline.get("source_node_id") or ""),
        "source_kind": str(baseline.get("source_kind") or "none"),
        "option": node_ref(baseline.get("option")),
        "decision": node_ref(baseline.get("decision")),
        "artifacts": artifacts,
        "reason": _text(baseline.get("reason")),
    }


def _required_action(
    *,
    blocking_gate: dict[str, Any] | None,
    active_run: dict[str, Any] | None,
    latest_gate: dict[str, Any] | None,
    assignment: dict[str, Any] | None,
    node: dict[str, Any],
) -> dict[str, Any]:
    if blocking_gate:
        return {
            "kind": "resolve_blocking_gate",
            "source": "gate",
            "action": _text(blocking_gate.get("next_allowed_action"))
            or f"Resolve blocking gate {blocking_gate.get('gate_id', '')}.".strip(),
        }
    if active_run:
        return {
            "kind": "monitor_active_run",
            "source": "run",
            "action": f"Continue or close active run {active_run.get('run_id', '')}.".strip(),
        }
    if latest_gate and latest_gate.get("passed") is True and latest_gate.get("next_allowed_action"):
        return {
            "kind": "execute_next_action",
            "source": "gate",
            "action": _text(latest_gate["next_allowed_action"]),
        }
    if assignment and assignment.get("next_action"):
        return {
            "kind": "execute_next_action",
            "source": "assignment",
            "action": assignment["next_action"],
        }
    if node.get("next_action"):
        return {
            "kind": "execute_next_action",
            "source": "node",
            "action": node["next_action"],
        }
    return {"kind": "none", "source": "none", "action": ""}


def execution_context_payload(
    root: Path,
    *,
    node_id: str,
    since_revision: str | None = None,
) -> dict[str, Any]:
    snapshot = load_root_snapshot(root, node_id=node_id, compact=True)
    if snapshot.validation_errors:
        raise ValidationError(snapshot.validation_errors)
    node = snapshot.nodes.get(node_id)
    if node is None:
        raise ValueError(f"Node does not exist: {node_id}")

    assignment = _assignment_boundary(root, node_id)
    run_context: dict[str, Any] = {"current": [], "warnings": []}
    gate_context: dict[str, Any] = {"latest": None, "blocking": [], "warnings": []}
    if node.type == "experiment":
        run_context = build_experiment_run_context(
            root,
            snapshot.nodes,
            node_id,
            limit=1,
            records=snapshot.run_records,
        )
        gate_context = build_experiment_gate_context(
            root,
            node_id,
            limit=1,
            records=snapshot.gate_records,
        )

    active_run = _compact_run((run_context.get("current") or [None])[0])
    blocking_gate = _compact_gate((gate_context.get("blocking") or [None])[0])
    latest_gate = _compact_gate(gate_context.get("latest"))
    node_projection = _node_payload(node)
    warnings = _bounded_strings(
        [*list(run_context.get("warnings") or []), *list(gate_context.get("warnings") or [])]
    )
    semantic = {
        "schema_version": EXECUTION_CONTEXT_SCHEMA_VERSION,
        "view": "execution",
        "node": node_projection,
        "assignment_boundary": assignment,
        "active_run": active_run,
        "active_run_count": int((run_context.get("summary") or {}).get("active_count") or 0),
        "blocking_gate": blocking_gate,
        "blocking_gate_count": int((gate_context.get("summary") or {}).get("blocking_count") or 0),
        "effective_baseline": _compact_baseline(
            resolve_effective_baseline(snapshot.nodes, node_id, snapshot.current)
        ),
        "warnings": warnings,
        "warnings_count": len(run_context.get("warnings") or []) + len(gate_context.get("warnings") or []),
    }
    semantic["required_action"] = _required_action(
        blocking_gate=blocking_gate,
        active_run=active_run,
        latest_gate=latest_gate,
        assignment=assignment,
        node=node_projection,
    )
    revision = stable_payload_revision(semantic, prefix="exec-v1")
    if since_revision and since_revision == revision:
        return {
            "schema_version": EXECUTION_CONTEXT_SCHEMA_VERSION,
            "changed": False,
            "revision": revision,
        }
    return {
        **semantic,
        "changed": True,
        "revision": revision,
        "scope": {
            "index_fast_path": snapshot.fast_path,
            "used_full_graph": not snapshot.fast_path,
            "nodes_loaded": len(snapshot.loaded_node_ids) if snapshot.loaded_node_ids else len(snapshot.nodes),
            "nodes_total": snapshot.node_count,
        },
    }
