from __future__ import annotations

import difflib
import json
from pathlib import Path
import sys
from typing import Any
import yaml

from research_cockpit.mutation_lock import MutationError
from research_cockpit.mutation_runtime import CommandState, finish_mutation, load_validated_state, preflight_mutation


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


def safe_print(text: object = "", *, end: str = "\n") -> None:
    payload = f"{text}{end}"
    try:
        print(text, end=end)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "utf-8"
        if hasattr(sys.stdout, "buffer"):
            sys.stdout.buffer.write(payload.encode(encoding, errors="replace"))
            sys.stdout.buffer.flush()
        else:
            sys.stdout.write(payload.encode(encoding, errors="replace").decode(encoding, errors="replace"))


def _stdout_supports_unicode() -> bool:
    return "utf" in (sys.stdout.encoding or "").lower()


def emit_json(payload: Any) -> None:
    safe_print(json.dumps(payload, ensure_ascii=not _stdout_supports_unicode(), indent=2))


def compact_mutation_result(
    result: dict[str, Any],
    *,
    command: str,
    target: str | dict[str, Any],
    root: Path,
    created: list[str] | None = None,
    updated: list[str] | None = None,
) -> dict[str, Any]:
    changed_files = result.get("changed_files")
    if changed_files is None and result.get("path"):
        changed_files = [result["path"]]
    payload: dict[str, Any] = {
        "ok": True,
        "command": f"research-cockpit {command}",
        "target": target,
        "dry_run": bool(result.get("dry_run")),
        "changed": bool(result.get("changed")),
        "would_change": bool(result.get("would_change")),
        "created": created if created is not None else result.get("created_nodes", []),
        "updated": updated if updated is not None else result.get("updated_nodes", []),
        "changed_files_count": len(changed_files or []),
        "verify_commands": [
            f"research-cockpit validate --root {root} --json",
            f"research-cockpit build --root {root}",
        ],
    }
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
    return payload


def dry_run_preflight_result(root: Path, result: dict[str, Any]) -> dict[str, Any]:
    result.update(preflight_mutation(root))
    return result
