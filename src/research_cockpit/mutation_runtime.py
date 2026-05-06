from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import os
from pathlib import Path
import tempfile
from typing import Any
import yaml

from research_cockpit.interaction_log import append_interaction_log, load_interaction_log
from research_cockpit.mutation_lock import MutationError, mutation_lock
from research_cockpit.storage import load_yaml, save_text


@dataclass(frozen=True)
class CommandState:
    nodes: dict[str, Any]
    current: dict[str, Any]
    explicit_edges: list[dict[str, Any]]


YamlChange = tuple[Path, dict[str, Any] | None, dict[str, Any]]
TextChange = tuple[Path, str | None, str]
ReadBefore = Callable[[Path], Any]


def _interaction_log_error_payload(root: Path, message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "partial_success": False,
        "rolled_back": False,
        "written_files": [],
        "error": message,
        "recovery_commands": [f"research-cockpit validate --root {root} --json"],
    }


def ensure_interaction_log_valid(root: Path) -> None:
    try:
        load_interaction_log(root, strict=True)
    except ValueError as exc:
        raise MutationError(str(exc), _interaction_log_error_payload(root, str(exc))) from exc


def load_validated_state(root: Path) -> CommandState:
    from research_cockpit.model import load_explicit_edges, load_nodes, load_yaml as model_load_yaml, validate_cockpit

    nodes = load_nodes(root)
    current = model_load_yaml(root / "current_state.yaml")
    explicit_edges = load_explicit_edges(root)
    validate_cockpit(root, nodes, current, explicit_edges, raise_on_error=True)
    ensure_interaction_log_valid(root)
    return CommandState(nodes=nodes, current=current, explicit_edges=explicit_edges)


def preflight_mutation(root: Path) -> dict[str, bool]:
    ensure_interaction_log_valid(root)
    return {"preflight_ok": True}


def _atomic_save_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".tmp-", suffix=".yaml", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
        temp_path.replace(path)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def _restore_files(backups: dict[Path, bytes | None]) -> list[str]:
    errors: list[str] = []
    for path, content in reversed(list(backups.items())):
        try:
            if content is None:
                path.unlink(missing_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
        except OSError as exc:
            errors.append(f"{path}: {exc}")
    return errors


def _read_yaml_before(path: Path) -> dict[str, Any] | None:
    return load_yaml(path) if path.exists() else None


def _read_text_before(path: Path) -> str | None:
    return path.read_text(encoding="utf-8") if path.exists() else None


def _coerce_changes(changes: list[tuple] | None, read_before: ReadBefore, label: str) -> list[tuple]:
    out: list[tuple] = []
    for change in changes or []:
        if len(change) == 3:
            path, before, after = change
        elif len(change) == 2:
            path, after = change
            before = read_before(path)
        else:
            raise ValueError(f"{label} mutation changes must be (path, before, after)")
        out.append((Path(path), before, after))
    return out


def _coerce_yaml_changes(changes: list[tuple]) -> list[YamlChange]:
    return _coerce_changes(changes, _read_yaml_before, "YAML")


def _coerce_text_changes(changes: list[tuple] | None) -> list[TextChange]:
    return _coerce_changes(changes, _read_text_before, "Text")


def _conflicting_files(changes: list[tuple], read_before: ReadBefore) -> list[str]:
    conflicts: list[str] = []
    for path, before, _ in changes:
        if read_before(path) != before:
            conflicts.append(str(path))
    return conflicts


def finish_mutation(
    root: Path,
    yaml_changes: list[tuple],
    *,
    interaction: dict[str, Any],
    rebuild_dashboard: bool,
    text_changes: list[tuple] | None = None,
) -> None:
    planned_yaml = _coerce_yaml_changes(yaml_changes)
    planned_text = _coerce_text_changes(text_changes)
    written_files: list[str] = []
    backups: dict[Path, bytes | None] = {}

    with mutation_lock(root):
        ensure_interaction_log_valid(root)

        conflict_files = [
            *_conflicting_files(planned_yaml, _read_yaml_before),
            *_conflicting_files(planned_text, _read_text_before),
        ]
        if conflict_files:
            error = "Mutation conflict: truth-source file changed after command planning"
            raise MutationError(
                error,
                {
                    "ok": False,
                    "partial_success": False,
                    "rolled_back": False,
                    "written_files": [],
                    "error": error,
                    "conflict_files": conflict_files,
                    "recovery_commands": [
                        f"research-cockpit context --root {root} --id <node_id> --compact --json",
                        f"research-cockpit validate --root {root} --json",
                    ],
                },
            )

        for path, _, _ in [*planned_yaml, *planned_text]:
            backups[path] = path.read_bytes() if path.exists() else None
        try:
            for path, _, after in planned_yaml:
                _atomic_save_yaml(path, after)
                written_files.append(str(path))
            for path, _, after_text in planned_text:
                save_text(path, after_text)
                written_files.append(str(path))
            append_interaction_log(root, **interaction)
        except Exception as exc:
            rollback_errors = _restore_files(backups)
            rolled_back = not rollback_errors
            payload = {
                "ok": False,
                "partial_success": bool(written_files) and not rolled_back,
                "rolled_back": rolled_back,
                "written_files": written_files,
                "error": str(exc),
                "recovery_commands": [
                    f"research-cockpit validate --root {root} --json",
                    f"research-cockpit build --root {root}",
                ],
            }
            if rollback_errors:
                payload["rollback_errors"] = rollback_errors
            raise MutationError(f"Mutation failed while writing audit log; rolled_back={rolled_back}: {exc}", payload) from exc

        if rebuild_dashboard:
            try:
                from research_cockpit.commands.build_dashboard import build_dashboard

                build_dashboard(root)
            except Exception as exc:
                raise MutationError(
                    f"Mutation succeeded but dashboard build failed: {exc}",
                    {
                        "ok": False,
                        "partial_success": True,
                        "rolled_back": False,
                        "written_files": written_files,
                        "error": str(exc),
                        "recovery_commands": [f"research-cockpit build --root {root}"],
                    },
                ) from exc
