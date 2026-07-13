from __future__ import annotations

import difflib
import json
from pathlib import Path
import sys
from typing import Any
import yaml

from research_cockpit.mutation_lock import MutationError
from research_cockpit.mutation_runtime import (
    CommandState,
    finish_mutation,
    indexed_artifact_record_stubs,
    load_targeted_state,
    load_validated_state,
    preflight_mutation,
    validate_mutation_candidate,
)


def yaml_preview(data: dict[str, Any] | None) -> str:
    if data is None:
        return ""
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)


def yaml_change_diff(changes: list[tuple[Path, dict[str, Any] | None, dict[str, Any] | None]]) -> str:
    chunks: list[str] = []
    for path, before, after in changes:
        before_text = yaml_preview(before).splitlines(keepends=True)
        after_text = yaml_preview(after).splitlines(keepends=True)
        chunks.extend(
            difflib.unified_diff(
                before_text,
                after_text,
                fromfile=f"{path}:before",
                tofile=f"{path}:after",
            )
        )
    return "".join(chunks)


def text_change_diff(changes: list[tuple[Path, str | None, str | None]]) -> str:
    chunks: list[str] = []
    for path, before, after in changes:
        before_text = (before or "").splitlines(keepends=True)
        after_text = (after or "").splitlines(keepends=True)
        chunks.extend(
            difflib.unified_diff(
                before_text,
                after_text,
                fromfile=f"{path}:before",
                tofile=f"{path}:after",
            )
        )
    return "".join(chunks)


def configure_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding="utf-8", errors="strict")
        except (TypeError, ValueError, OSError):
            continue


def _write_stdout_utf8(text: str) -> None:
    if hasattr(sys.stdout, "buffer"):
        sys.stdout.buffer.write(text.encode("utf-8"))
        sys.stdout.buffer.flush()
        return
    sys.stdout.write(text)
    sys.stdout.flush()


def safe_print(text: object = "", *, end: str = "\n") -> None:
    payload = f"{text}{end}"
    try:
        print(text, end=end)
    except UnicodeEncodeError:
        _write_stdout_utf8(payload)


def emit_json(payload: Any, *, compact: bool = False) -> None:
    if compact:
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    else:
        text = json.dumps(payload, ensure_ascii=False, indent=2)
    _write_stdout_utf8(text + "\n")


def _unique_nonempty(values: list[Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            out.append(text)
            seen.add(text)
    return out


def _command_relative_path(root: Path, value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    path = Path(text)
    if path.is_absolute():
        try:
            return path.resolve(strict=False).relative_to(root.resolve(strict=False)).as_posix()
        except ValueError:
            return path.as_posix()
    normalized = text.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.lstrip("/")


def _node_ids_from_changed_files(root: Path, changed_files: list[str]) -> list[str]:
    node_ids: list[str] = []
    for changed_file in changed_files:
        normalized = _command_relative_path(root, changed_file)
        parts = normalized.replace("\\", "/").split("/")
        if len(parts) >= 3 and parts[-3:-1] == ["graph", "nodes"] and Path(parts[-1]).suffix in {".yaml", ".yml"}:
            node_ids.append(Path(parts[-1]).stem)
    return _unique_nonempty(node_ids)


def _changed_node_ids(root: Path, changed_files: list[str], created: list[str], updated: list[str]) -> list[str]:
    from_file = _node_ids_from_changed_files(root, changed_files)
    out = list(from_file)
    from_file_set = set(from_file)
    for node_id in [*created, *updated]:
        candidate = str(node_id or "").strip()
        if not candidate:
            continue
        if candidate in from_file_set or (root / "graph" / "nodes" / f"{candidate}.yaml").exists():
            out.append(candidate)
    return _unique_nonempty(out)


def _final_handoff_commands(root: Path) -> list[str]:
    return [
        f"research-cockpit validate --root {root} --json",
        f"research-cockpit build --root {root}",
        f"research-cockpit smoke --root {root} --json --progress",
    ]


def _verify_commands(
    root: Path,
    changed_files: list[str],
    changed_nodes: list[str],
    changed_records: list[str],
) -> list[str]:
    record_flags = " ".join(f"--changed-record {record}" for record in changed_records)
    if changed_nodes:
        node_flags = " ".join(f"--changed-node {node_id}" for node_id in changed_nodes)
        validate_flags = " ".join(flag for flag in [node_flags, record_flags] if flag)
        commands = [f"research-cockpit validate --root {root} {validate_flags} --json"]
        commands.extend(
            f"research-cockpit context --root {root} --id {node_id} --with-bootstrap --with-artifacts --compact --json"
            for node_id in changed_nodes
        )
        return commands
    normalized_files = _unique_nonempty([_command_relative_path(root, path) for path in changed_files])
    if normalized_files or changed_records:
        file_flags = " ".join(f"--changed-file {path}" for path in normalized_files)
        validate_flags = " ".join(flag for flag in [file_flags, record_flags] if flag)
        return [f"research-cockpit validate --root {root} {validate_flags} --json"]
    return [f"research-cockpit validate --root {root} --json"]

def compact_mutation_result(
    result: dict[str, Any],
    *,
    command: str,
    target: str | dict[str, Any],
    root: Path,
    created: list[str] | None = None,
    updated: list[str] | None = None,
    records: list[str] | None = None,
) -> dict[str, Any]:
    changed_files = result.get("changed_files")
    if changed_files is None and result.get("path"):
        changed_files = [result["path"]]
    created_ids = created if created is not None else result.get("created_nodes", [])
    updated_ids = updated if updated is not None else result.get("updated_nodes", [])
    changed_file_list = [str(path) for path in changed_files or []]
    changed_nodes = _changed_node_ids(root, changed_file_list, created_ids, updated_ids)
    changed_records = _unique_nonempty(records if records is not None else result.get("changed_records", []))
    post_apply_verify_commands = _verify_commands(root, changed_file_list, changed_nodes, changed_records)
    is_dry_run = bool(result.get("dry_run"))
    payload: dict[str, Any] = {
        "ok": True,
        "command": f"research-cockpit {command}",
        "target": target,
        "dry_run": is_dry_run,
        "changed": bool(result.get("changed")),
        "would_change": bool(result.get("would_change")),
        "created": created_ids,
        "updated": updated_ids,
        "changed_files_count": len(changed_file_list),
        "changed_scope": {
            "nodes": changed_nodes,
            "files": _unique_nonempty([_command_relative_path(root, path) for path in changed_file_list]),
            "records": changed_records,
        },
        "verify_commands": [] if is_dry_run else post_apply_verify_commands,
        "post_apply_verify_commands": post_apply_verify_commands,
        "final_handoff_commands": _final_handoff_commands(root),
    }
    if is_dry_run:
        payload["verification_note"] = "Dry-run did not write; run post_apply_verify_commands after applying without --dry-run."
    if "diff" in result:
        diff = str(result["diff"])
        payload["diff_included"] = True
        payload["diff_line_count"] = len(diff.splitlines())
        payload["diff"] = diff
    if "resolved_inputs" in result:
        payload["resolved_inputs"] = result["resolved_inputs"]
    if "preflight_ok" in result:
        payload["preflight_ok"] = result["preflight_ok"]
    if "normalized_statuses" in result:
        payload["normalized_statuses"] = result["normalized_statuses"]
    if "warnings" in result:
        payload["warnings"] = result["warnings"]
    if "recommended_commands" in result:
        payload["recommended_commands"] = result["recommended_commands"]
    return payload


def dry_run_preflight_result(root: Path, result: dict[str, Any]) -> dict[str, Any]:
    result.update(preflight_mutation(root))
    return result
