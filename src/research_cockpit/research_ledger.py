from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import subprocess
from typing import Any

from research_cockpit.artifact_records import list_artifact_records
from research_cockpit.baselines import (
    compact_effective_baseline,
    resolve_current_effective_baseline,
    resolve_effective_baseline,
)
from research_cockpit.gate_result_records import build_gate_summaries
from research_cockpit.storage import save_yaml


RESEARCH_LEDGER_SCHEMA_VERSION = "research_ledger_v1"
LEDGER_DIRECTORY = "research-ledger"
LEDGER_COLLECTION_LIMIT = 50
LEDGER_BYTE_LIMIT = 48 * 1024
_SAFE_FILE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_LOCAL_PATH_IN_TEXT = re.compile(r"(?:^|[\s\"'(=])(?:~[\\/]|/[A-Za-z0-9._-])|[A-Za-z]:[\\/]")
_REVISION_FIELDS = (
    "code_revision",
    "config_revision",
    "data_revision",
    "git_revision",
    "dataset_revision",
    "model_revision",
)


def git_toplevel(path: Path) -> Path | None:
    completed = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    value = completed.stdout.strip()
    return Path(value).resolve() if value else None


def ledger_file_id(operation_id: str) -> str:
    value = str(operation_id or "").strip()
    if _SAFE_FILE_ID.fullmatch(value):
        return value
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")[:96] or "handoff"
    return f"{stem}-{digest}"


def ledger_relative_path(operation_id: str) -> str:
    return f"{LEDGER_DIRECTORY}/{ledger_file_id(operation_id)}.yaml"


def ledger_path(repo: Path, operation_id: str) -> Path:
    return repo / ledger_relative_path(operation_id)


def _has_local_path(value: str) -> bool:
    text = value.strip()
    if not text:
        return False
    if text.startswith("file://"):
        return True
    if re.match(r"^[A-Za-z]:[\\/]", text):
        return True
    uri = re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", text)
    if uri is not None:
        return bool(_LOCAL_PATH_IN_TEXT.search(text[uri.end() :]))
    path = PurePosixPath(text)
    windows = PureWindowsPath(text)
    return bool(
        path.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or _LOCAL_PATH_IN_TEXT.search(text)
    )


def _safe_text(value: Any, *, limit: int = 600) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or _has_local_path(text):
        return None
    return text[:limit]


def _portable_uri(value: Any) -> str | None:
    text = _safe_text(value, limit=512)
    if text is None or "://" not in text:
        return None
    scheme = text.split("://", 1)[0].lower()
    if not scheme or scheme == "file" or not re.fullmatch(r"[a-z][a-z0-9+.-]*", scheme):
        return None
    return text


def _bounded_strings(values: Any, *, limit: int = LEDGER_COLLECTION_LIMIT) -> list[str]:
    if not isinstance(values, list):
        return []
    return sorted(
        {
            text
            for item in values
            if (text := _safe_text(item, limit=200)) is not None
        }
    )[:limit]


def _safe_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 3:
        return None
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _safe_text(value, limit=200)
    if isinstance(value, list):
        items = [_safe_value(item, depth=depth + 1) for item in value[:20]]
        return [item for item in items if item is not None]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key in sorted(value, key=str):
            safe_key = _safe_text(str(key), limit=80)
            safe_item = _safe_value(value[key], depth=depth + 1)
            if safe_key is not None and safe_item is not None:
                out[safe_key] = safe_item
        return out
    return None


def _safe_mapping(value: Any, *, limit: int = 16) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, Any] = {}
    for key in sorted(value, key=str)[:limit]:
        safe_key = _safe_text(str(key), limit=80)
        safe_item = _safe_value(value[key])
        if safe_key is not None and safe_item is not None:
            out[safe_key] = safe_item
    return out


def _option_id_for_node(nodes: dict[str, Any], node_id: str) -> str | None:
    current_id = node_id
    seen: set[str] = set()
    while current_id and current_id in nodes and current_id not in seen:
        node = nodes[current_id]
        if getattr(node, "type", "") == "option":
            return str(getattr(node, "id", current_id))
        seen.add(current_id)
        current_id = str(getattr(node, "parent", "") or "")
    return None


def _accepted_decisions(nodes: dict[str, Any]) -> tuple[list[dict[str, Any]], set[str], set[str]]:
    rows: list[dict[str, Any]] = []
    artifact_ids: set[str] = set()
    artifact_record_ids: set[str] = set()
    for node in sorted(nodes.values(), key=lambda item: str(getattr(item, "id", ""))):
        if (
            getattr(node, "type", "") != "decision"
            or getattr(node, "status", "") != "accepted"
        ):
            continue
        raw = getattr(node, "raw", {}) or {}
        linked_artifacts = _bounded_strings(raw.get("linked_artifacts"))
        linked_records = _bounded_strings(raw.get("linked_artifact_records"))
        artifact_ids.update(linked_artifacts)
        artifact_record_ids.update(linked_records)
        row: dict[str, Any] = {"id": str(node.id)}
        option_id = _option_id_for_node(nodes, str(node.id))
        if option_id:
            row["option_id"] = option_id
        if linked_artifacts:
            row["artifact_ids"] = linked_artifacts
        if linked_records:
            row["artifact_record_ids"] = linked_records
        rows.append(row)
    return rows[:LEDGER_COLLECTION_LIMIT], artifact_ids, artifact_record_ids


def _final_findings(nodes: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], set[str], set[str]]:
    findings: list[dict[str, Any]] = []
    metrics: list[dict[str, Any]] = []
    artifact_ids: set[str] = set()
    artifact_record_ids: set[str] = set()
    terminal = {"done", "failed", "cancelled"}
    for node in sorted(nodes.values(), key=lambda item: str(getattr(item, "id", ""))):
        if (
            getattr(node, "type", "") != "experiment"
            or getattr(node, "status", "") not in terminal
        ):
            continue
        raw_findings = (getattr(node, "raw", {}) or {}).get("findings") or []
        if not isinstance(raw_findings, list):
            continue
        for index, finding in enumerate(raw_findings[:LEDGER_COLLECTION_LIMIT], start=1):
            if not isinstance(finding, dict):
                continue
            row: dict[str, Any] = {
                "experiment_id": str(node.id),
                "finding_index": index,
            }
            statement = _safe_text(finding.get("statement"))
            if statement is not None:
                row["statement"] = statement
            for field in ("confidence", "outcome"):
                value = _safe_text(finding.get(field), limit=80)
                if value is not None:
                    row[field] = value
            linked_artifacts = _bounded_strings(finding.get("linked_artifacts"))
            linked_records = _bounded_strings(finding.get("linked_artifact_records"))
            artifact_ids.update(linked_artifacts)
            artifact_record_ids.update(linked_records)
            if linked_artifacts:
                row["artifact_ids"] = linked_artifacts
            if linked_records:
                row["artifact_record_ids"] = linked_records
            safe_metrics = _safe_mapping(finding.get("metrics"))
            if safe_metrics:
                row["metrics"] = safe_metrics
                metrics.append(
                    {
                        "experiment_id": str(node.id),
                        "finding_index": index,
                        "metrics": safe_metrics,
                    }
                )
            findings.append(row)
    return (
        findings[:LEDGER_COLLECTION_LIMIT],
        metrics[:LEDGER_COLLECTION_LIMIT],
        artifact_ids,
        artifact_record_ids,
    )


def _effective_baselines(nodes: dict[str, Any], current: dict[str, Any]) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    seen: set[str] = set()
    candidate_ids = [
        str(node.id)
        for node in sorted(nodes.values(), key=lambda item: item.id)
        if isinstance((getattr(node, "raw", {}) or {}).get("baseline"), dict)
    ]
    current_baseline = resolve_current_effective_baseline(nodes, current)
    if current_baseline.get("option"):
        candidate_ids.append(str(current_baseline.get("source_node_id") or "current_state"))
    for node_id in candidate_ids:
        if node_id == "current_state":
            effective = current_baseline
        elif node_id in nodes:
            effective = resolve_effective_baseline(nodes, node_id, current)
        else:
            continue
        compact = compact_effective_baseline(effective)
        if compact.get("reason") and _has_local_path(str(compact["reason"])):
            compact["reason"] = ""
        key = json.dumps(compact, sort_keys=True, separators=(",", ":"))
        if key not in seen:
            seen.add(key)
            values.append(compact)
    return values[:LEDGER_COLLECTION_LIMIT]


def _revision_rows(nodes: dict[str, Any], runs: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for kind, values in (("node", nodes.values()), ("run", runs.values())):
        for value in sorted(
            values,
            key=lambda item: str(getattr(item, "id", getattr(item, "run_id", ""))),
        ):
            raw = getattr(value, "raw", {}) or {}
            revisions = {
                field: safe
                for field in _REVISION_FIELDS
                if (safe := _safe_text(raw.get(field), limit=200)) is not None
            }
            if not revisions:
                continue
            identifier = str(getattr(value, "id", getattr(value, "run_id", "")))
            rows.append({"kind": kind, "id": identifier, "revisions": revisions})
    return rows[:LEDGER_COLLECTION_LIMIT]


def _gate_outcomes(root: Path) -> list[dict[str, Any]]:
    summaries, _warnings = build_gate_summaries(root)
    rows: list[dict[str, Any]] = []
    for value in sorted(
        summaries,
        key=lambda item: str(item.get("gate_id") or ""),
    )[:LEDGER_COLLECTION_LIMIT]:
        row = {
            key: value[key]
            for key in (
                "gate_id",
                "gate_type",
                "passed",
                "blocks_next_action",
                "next_allowed_action",
                "experiment_id",
                "run_id",
                "artifact_id",
            )
            if value.get(key) not in (None, "")
            and not (
                isinstance(value.get(key), str)
                and _has_local_path(str(value[key]))
            )
        }
        for key in ("expected", "observed"):
            safe = _safe_mapping(value.get(key))
            if safe:
                row[key] = safe
        if row.get("gate_id"):
            rows.append(row)
    return rows


def _selected_artifacts(root: Path, *, artifact_ids: set[str], artifact_record_ids: set[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in sorted(
        list_artifact_records(root),
        key=lambda item: str(item.get("record_id") or ""),
    ):
        record_id = str(record.get("record_id") or "")
        artifact_id = str(record.get("artifact_id") or "")
        if record_id not in artifact_record_ids and artifact_id not in artifact_ids:
            continue
        storage = record.get("storage") if isinstance(record.get("storage"), dict) else {}
        integrity = record.get("integrity") if isinstance(record.get("integrity"), dict) else {}
        availability = record.get("availability") if isinstance(record.get("availability"), dict) else {}
        row: dict[str, Any] = {
            "record_id": record_id,
            "experiment_id": str(record.get("experiment_id") or ""),
        }
        for key in ("run_id", "artifact_id"):
            value = _safe_text(record.get(key), limit=160)
            if value is not None:
                row[key] = value
        mode = _safe_text(storage.get("mode"), limit=40)
        ownership = _safe_text(storage.get("ownership"), limit=80)
        if mode is not None:
            row["storage_mode"] = mode
        if ownership is not None:
            row["ownership"] = ownership
        uri = _portable_uri(storage.get("uri"))
        if uri is not None:
            row["uri"] = uri
        managed_key = _safe_text(storage.get("managed_key"), limit=256)
        if managed_key is not None and ".." not in PurePosixPath(managed_key).parts:
            row["managed_key"] = managed_key
        for source, target in ((integrity, "integrity"), (availability, "availability")):
            safe = {
                key: _safe_text(source.get(key), limit=160)
                for key in ("level", "algorithm", "digest", "status")
                if _safe_text(source.get(key), limit=160) is not None
            }
            if safe:
                row[target] = safe
        rows.append(row)
    return rows[:LEDGER_COLLECTION_LIMIT]


def _review_and_provenance(assignments: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    reviews: list[dict[str, Any]] = []
    provenance: list[dict[str, Any]] = []
    for assignment in sorted(
        assignments.values(),
        key=lambda item: str(getattr(item, "assignment_id", "")),
    ):
        assignment_id = str(getattr(assignment, "assignment_id", ""))
        result = getattr(assignment, "result", {}) or {}
        result_revision = _safe_text(result.get("result_revision"), limit=160)
        if result_revision is not None:
            provenance.append({"assignment_id": assignment_id, "result_revision": result_revision})
        review = getattr(assignment, "review", {}) or {}
        status = _safe_text(review.get("status"), limit=80)
        verdict = _safe_text(review.get("verdict"), limit=80)
        if status is None and verdict is None:
            continue
        row: dict[str, Any] = {"assignment_id": assignment_id}
        if status is not None:
            row["status"] = status
        if verdict is not None:
            row["verdict"] = verdict
        producer = _safe_text(review.get("producer_assignment_id"), limit=160)
        if producer is not None:
            row["producer_assignment_id"] = producer
        reviews.append(row)
    return reviews[:LEDGER_COLLECTION_LIMIT], provenance[:LEDGER_COLLECTION_LIMIT]


def _bound_ledger(payload: dict[str, Any]) -> dict[str, Any]:
    bounded = deepcopy(payload)
    collections = (
        "accepted_decisions",
        "final_findings",
        "effective_baselines",
        "code_config_data_revisions",
        "primary_metrics",
        "gate_outcomes",
        "reviewed_artifacts",
        "reviews",
        "provenance_references",
    )
    truncated: dict[str, int] = {}

    def exceeds_budget() -> bool:
        encoded = json.dumps(
            bounded,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return len(encoded) > LEDGER_BYTE_LIMIT

    def drop_one_item() -> bool:
        candidates = [
            key
            for key in collections
            if isinstance(bounded.get(key), list) and bounded[key]
        ]
        if not candidates:
            return False
        key = max(candidates, key=lambda name: len(bounded[name]))
        bounded[key].pop()
        truncated[key] = truncated.get(key, 0) + 1
        return True

    while exceeds_budget() and drop_one_item():
        bounded["truncation"] = {"omitted_items": dict(sorted(truncated.items()))}
    return bounded


def build_research_ledger(
    root: Path,
    validation_state: Any,
    *,
    operation_id: str,
    kind: str,
    target_revision: str,
    timestamp: str,
) -> dict[str, Any]:
    nodes = dict(getattr(validation_state, "nodes", {}) or {})
    current = dict(getattr(validation_state, "current", {}) or {})
    runs = dict(getattr(validation_state, "runs", {}) or {})
    assignments = dict(getattr(validation_state, "assignments", {}) or {})
    accepted, accepted_artifacts, accepted_records = _accepted_decisions(nodes)
    findings, metrics, finding_artifacts, finding_records = _final_findings(nodes)
    reviews, provenance = _review_and_provenance(assignments)
    payload: dict[str, Any] = {
        "schema_version": RESEARCH_LEDGER_SCHEMA_VERSION,
        "milestone": {
            "id": str(operation_id),
            "kind": str(kind),
            "timestamp": str(timestamp),
            "state_revision": str(target_revision),
        },
        "accepted_decisions": accepted,
        "final_findings": findings,
        "effective_baselines": _effective_baselines(nodes, current),
        "code_config_data_revisions": _revision_rows(nodes, runs),
        "primary_metrics": metrics,
        "gate_outcomes": _gate_outcomes(root),
        "reviewed_artifacts": _selected_artifacts(
            root,
            artifact_ids=accepted_artifacts | finding_artifacts,
            artifact_record_ids=accepted_records | finding_records,
        ),
        "reviews": reviews,
        "provenance_references": provenance,
    }
    return _bound_ledger(payload)


def write_research_ledger(repo: Path, ledger: dict[str, Any]) -> Path:
    milestone = ledger.get("milestone") if isinstance(ledger.get("milestone"), dict) else {}
    operation_id = str(milestone.get("id") or "")
    if not operation_id:
        raise ValueError("research ledger milestone.id is required")
    path = ledger_path(repo.resolve(), operation_id)
    save_yaml(path, ledger)
    return path
