from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import stat
import tempfile
from typing import Any
import yaml

from research_cockpit.cli_progress import progress_phase, progress_traced
from research_cockpit.interaction_log import (
    _append_interaction_log_unlocked,
    interaction_append_checkpoint,
    restore_interaction_append_checkpoint,
    validate_interaction_append_target,
)
from research_cockpit.mutation_lock import MutationError, mutation_lock
from research_cockpit.operation_receipts import (
    OperationIdConflict,
    operation_source_signature,
    patch_operation_index,
    replay_or_conflict,
)
from research_cockpit.storage import load_yaml, save_text


@dataclass(frozen=True)
class CommandState:
    nodes: dict[str, Any]
    current: dict[str, Any]
    explicit_edges: list[dict[str, Any]]
    runs: dict[str, Any] | None = None
    artifact_records: list[dict[str, Any]] | None = None
    validation_index: dict[str, Any] | None = None
    targeted: bool = False


YamlChange = tuple[Path, dict[str, Any] | None, dict[str, Any]]
TextChange = tuple[Path, str | None, str]
ReadDependency = tuple[Path, bytes | None]
ReadBefore = Callable[[Path], Any]
CommitValidator = Callable[[], None]


def patch_validation_index(root: Path, changed_paths: list[Path]) -> dict[str, Any]:
    from research_cockpit.validation_index import patch_validation_index as patch

    with progress_phase("index_update"):
        return patch(root, changed_paths)


def mark_validation_index_stale(root: Path, *, reason: str, detail: str = "") -> None:
    from research_cockpit.validation_index import mark_validation_index_stale as mark_stale

    mark_stale(root, reason=reason, detail=detail)


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
    errors = validate_interaction_append_target(root)
    if errors:
        message = errors[0]
        raise MutationError(message, _interaction_log_error_payload(root, message))


@progress_traced("load_and_validate")
def load_validated_state(root: Path) -> CommandState:
    from research_cockpit.model import load_explicit_edges, load_nodes, load_yaml as model_load_yaml, validate_cockpit

    nodes = load_nodes(root)
    current = model_load_yaml(root / "current_state.yaml")
    explicit_edges = load_explicit_edges(root)
    validate_cockpit(root, nodes, current, explicit_edges, raise_on_error=True)
    ensure_interaction_log_valid(root)
    return CommandState(nodes=nodes, current=current, explicit_edges=explicit_edges)


def _indexed_stub_node(node_id: str, row: dict[str, Any]) -> Any:
    from research_cockpit.model import ResearchNode

    raw: dict[str, Any] = {
        "id": node_id,
        "type": row.get("type") or "node",
        "title": row.get("title") or node_id,
        "status": row.get("status") or "open",
    }
    if row.get("parent"):
        raw["parent"] = row["parent"]
    if row.get("children"):
        raw["children"] = list(row.get("children") or [])
    return ResearchNode.from_dict(raw)


def _targeted_node_ids(index: dict[str, Any], seeds: list[str]) -> list[str]:
    rows = index.get("nodes", {}) or {}
    reverse_refs = index.get("reverse_refs", {}) or {}
    edge_neighbors = index.get("edge_neighbors", {}) or {}
    selected: list[str] = []
    seen: set[str] = set()

    def add(node_id: str) -> None:
        if node_id in rows and node_id not in seen:
            selected.append(node_id)
            seen.add(node_id)

    for seed in seeds:
        add(seed)
        current_id = seed
        while current_id in rows:
            parent_id = str(rows[current_id].get("parent") or "")
            if not parent_id or parent_id in seen:
                break
            add(parent_id)
            current_id = parent_id
        row = rows.get(seed, {})
        for candidate in [
            *list(row.get("children", []) or []),
            *list(row.get("references", []) or []),
            *list(reverse_refs.get(seed, []) or []),
            *list(edge_neighbors.get(seed, []) or []),
        ]:
            add(str(candidate))
    return selected


def _targeted_artifact_records(
    root: Path,
    index: dict[str, Any],
    experiment_ids: list[str],
) -> list[dict[str, Any]]:
    indexed_records = index.get("artifact_records", {}) or {}
    records: dict[str, dict[str, Any]] = {
        str(record_id): {
            "record_id": str(record_id),
            "experiment_id": str(row.get("experiment_id") or ""),
            **({"run_id": str(row["run_id"])} if row.get("run_id") else {}),
        }
        for record_id, row in indexed_records.items()
        if isinstance(row, dict)
    }
    file_rows = index.get("artifact_record_files", {}) or {}
    for rel_path, file_row in file_rows.items():
        if (
            not isinstance(file_row, dict)
            or str(file_row.get("experiment_id") or "") not in experiment_ids
        ):
            continue
        for record_id in file_row.get("record_ids", []) or []:
            records.pop(str(record_id), None)
        data = load_yaml(root / str(rel_path))
        raw_records = data.get("records", {}) if isinstance(data, dict) else {}
        if not isinstance(raw_records, dict):
            raise ValueError(f"{rel_path}: records must be a mapping")
        experiment_id = str(data.get("experiment_id") or Path(rel_path).stem)
        for record_id, record in raw_records.items():
            if not isinstance(record, dict):
                raise ValueError(f"artifact record {record_id!r}: record must be a mapping")
            payload = dict(record)
            payload.setdefault("record_id", str(record_id))
            payload.setdefault("experiment_id", experiment_id)
            records[str(record_id)] = payload
    return list(records.values())

@progress_traced("targeted_preflight")
def load_targeted_state(
    root: Path,
    *,
    node_ids: list[str] | None = None,
    run_ids: list[str] | None = None,
    include_artifact_records: bool = False,
) -> CommandState:
    from research_cockpit.commands.validate_cockpit import validation_payload
    from research_cockpit.model import (
        ResearchNode,
        RunRecord,
        load_explicit_edges,
        load_yaml as model_load_yaml,
    )
    from research_cockpit.validation_index import is_index_schema_compatible, load_validation_index

    node_ids = [str(node_id) for node_id in node_ids or [] if str(node_id).strip()]
    run_ids = [str(run_id) for run_id in run_ids or [] if str(run_id).strip()]
    index = load_validation_index(root)
    if not is_index_schema_compatible(index):
        return load_validated_state(root)
    assert index is not None

    indexed_runs = index.get("runs", {}) or {}
    seed_node_ids = list(node_ids)
    changed_files: list[str] = []
    for run_id in run_ids:
        row = indexed_runs.get(run_id)
        if not isinstance(row, dict):
            return load_validated_state(root)
        experiment_id = str(row.get("experiment_id") or "")
        if experiment_id:
            seed_node_ids.append(experiment_id)
        if row.get("file"):
            changed_files.append(str(row["file"]))
    seed_node_ids = list(dict.fromkeys(seed_node_ids))
    if not seed_node_ids:
        return load_validated_state(root)
    if include_artifact_records:
        for rel_path, row in (index.get("artifact_record_files", {}) or {}).items():
            if (
                isinstance(row, dict)
                and str(row.get("experiment_id") or "") in seed_node_ids
            ):
                changed_files.append(str(rel_path))

    validation = validation_payload(
        root,
        changed_nodes=node_ids,
        changed_files=changed_files,
        validation_index=index,
    )
    if validation.get("fallback", {}).get("used_full_validation"):
        return load_validated_state(root)
    if not validation.get("ok"):
        from research_cockpit.types import ValidationError

        raise ValidationError(list(validation.get("errors", []) or []))

    rows = index.get("nodes", {}) or {}
    nodes = {
        str(current_id): _indexed_stub_node(str(current_id), row)
        for current_id, row in rows.items()
        if isinstance(row, dict)
    }
    for current_id in _targeted_node_ids(index, seed_node_ids):
        row = rows.get(current_id, {})
        rel_path = str(row.get("file") or "")
        if not rel_path:
            continue
        data = model_load_yaml(root / rel_path)
        if isinstance(data, dict):
            nodes[current_id] = ResearchNode.from_dict(data)

    runs: dict[str, RunRecord] = {}
    for run_id in run_ids:
        row = indexed_runs[run_id]
        data = model_load_yaml(root / str(row["file"]))
        runs[run_id] = RunRecord.from_dict(data)

    artifact_records = (
        _targeted_artifact_records(root, index, seed_node_ids)
        if include_artifact_records
        else None
    )
    ensure_interaction_log_valid(root)
    return CommandState(
        nodes=nodes,
        current=model_load_yaml(root / "current_state.yaml"),
        explicit_edges=load_explicit_edges(root),
        runs=runs,
        artifact_records=artifact_records,
        validation_index=index,
        targeted=True,
    )


def validate_mutation_candidate(
    root: Path,
    state: CommandState,
    *,
    nodes: dict[str, Any] | None = None,
    runs: dict[str, Any] | None = None,
    artifact_records: list[dict[str, Any]] | None = None,
) -> None:
    from research_cockpit.model import (
        validate_artifact_records,
        validate_cockpit,
        validate_current_state,
        validate_explicit_edges,
        validate_nodes,
        validate_runs,
    )
    from research_cockpit.types import ValidationError

    candidate_nodes = nodes if nodes is not None else state.nodes
    if not state.targeted:
        validate_cockpit(
            root,
            candidate_nodes,
            state.current,
            state.explicit_edges,
            runs=runs,
            artifact_records=artifact_records,
            raise_on_error=True,
        )
        return

    errors = validate_nodes(candidate_nodes)
    errors.extend(validate_explicit_edges(candidate_nodes, state.explicit_edges))
    errors.extend(validate_current_state(state.current, candidate_nodes, state.explicit_edges))
    if runs is not None:
        errors.extend(validate_runs(runs, candidate_nodes))
    if artifact_records is not None:
        errors.extend(validate_artifact_records(root, candidate_nodes, artifact_records))
    if errors:
        raise ValidationError(errors)


def indexed_artifact_record_stubs(state: CommandState) -> list[dict[str, Any]]:
    if not state.targeted or not isinstance(state.validation_index, dict):
        return []
    if state.artifact_records is not None:
        return [dict(record) for record in state.artifact_records]
    return [
        {
            "record_id": str(record_id),
            "experiment_id": str(row.get("experiment_id") or ""),
            **({"run_id": str(row["run_id"])} if row.get("run_id") else {}),
        }
        for record_id, row in (state.validation_index.get("artifact_records", {}) or {}).items()
        if isinstance(row, dict)
    ]

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


def _read_bytes_before(path: Path) -> bytes | None:
    return path.read_bytes() if path.exists() else None


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


def _coerce_read_dependencies(
    dependencies: list[tuple[Path, bytes | None]] | None,
) -> list[ReadDependency]:
    planned: list[ReadDependency] = []
    for dependency in dependencies or []:
        if len(dependency) != 2:
            raise ValueError("Read dependencies must be (path, before_bytes)")
        path, before = dependency
        if before is not None and not isinstance(before, bytes):
            raise ValueError("Read dependency signatures must be bytes or None")
        planned.append((Path(path), before))
    return planned


def _conflicting_read_dependencies(dependencies: list[ReadDependency]) -> list[str]:
    return [
        str(path)
        for path, before in dependencies
        if _read_bytes_before(path) != before
    ]


def _path_key(path: Path) -> str:
    return path.resolve(strict=False).as_posix().casefold()


def _validate_unique_transaction_targets(paths: list[Path]) -> None:
    seen: dict[str, Path] = {}
    for path in paths:
        key = _path_key(path)
        if key in seen:
            raise ValueError(
                f"Mutation transaction has duplicate target paths: {seen[key]} and {path}"
            )
        seen[key] = path


def _remove_staged_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _lexists(path: Path) -> bool:
    return os.path.lexists(path)


def _is_link_like(path: Path, info: os.stat_result | None = None) -> bool:
    is_junction = getattr(path, "is_junction", None)
    return stat.S_ISLNK((info or os.lstat(path)).st_mode) or bool(
        is_junction and is_junction()
    )


def _staged_path_issues(root: Path, path: Path) -> list[str]:
    root_path = root.resolve(strict=True)
    candidate = path.absolute()
    try:
        relative = candidate.relative_to(root_path)
    except ValueError:
        return [f"staged move path escapes data root: {candidate}"]
    issues: list[str] = []
    current = root_path
    for part in relative.parts:
        current = current / part
        if not _lexists(current):
            continue
        info = os.lstat(current)
        if _is_link_like(current, info):
            issues.append(f"staged move path contains a symlink or junction: {current}")
            break
    return issues


def _staged_move_conflicts(
    root: Path,
    source: Path,
    target: Path,
    target_existed: bool,
) -> list[str]:
    conflicts = [
        *_staged_path_issues(root, source),
        *_staged_path_issues(root, target),
    ]
    if not _lexists(source):
        conflicts.append(str(source))
    else:
        source_info = os.lstat(source)
        if not stat.S_ISDIR(source_info.st_mode) or _is_link_like(source, source_info):
            conflicts.append(f"staged move source is not a directory: {source}")
    if _lexists(target) != target_existed:
        conflicts.append(str(target))
    return conflicts


def _commit_staged_move(
    root: Path,
    source: Path,
    target: Path,
    target_existed: bool,
) -> None:
    conflicts = _staged_move_conflicts(root, source, target, target_existed)
    if conflicts:
        raise OSError("Unsafe staged move: " + "; ".join(conflicts))
    target.parent.mkdir(parents=True, exist_ok=True)
    conflicts = _staged_move_conflicts(root, source, target, target_existed)
    if conflicts:
        raise OSError(
            "Unsafe staged move after parent creation: " + "; ".join(conflicts)
        )
    source.replace(target)


@progress_traced("apply_transaction")
def execute_mutation_transaction(
    root: Path,
    yaml_changes: list[tuple],
    *,
    interactions: list[dict[str, Any]],
    rebuild_dashboard: bool,
    text_changes: list[tuple] | None = None,
    staged_moves: list[tuple[Path, Path]] | None = None,
    read_dependencies: list[tuple[Path, bytes | None]] | None = None,
    operation_request: dict[str, Any] | None = None,
    commit_validators: list[CommitValidator] | None = None,
) -> dict[str, Any]:
    if not interactions:
        raise ValueError("mutation transaction requires at least one interaction event")
    planned_yaml = _coerce_yaml_changes(yaml_changes)
    planned_text = _coerce_text_changes(text_changes)
    planned_reads = _coerce_read_dependencies(read_dependencies)
    planned_commit_validators = list(commit_validators or [])
    planned_moves = [
        (Path(source), Path(target), _lexists(Path(target)))
        for source, target in staged_moves or []
    ]
    planned_interactions = deepcopy(interactions)
    if operation_request is not None:
        required = {
            "scope",
            "operation_id",
            "request_hash",
            "receipt",
            "operation",
            "assignment_id",
        }
        missing = sorted(required - set(operation_request))
        if missing:
            raise ValueError(
                "operation_request is missing fields: " + ", ".join(missing)
            )
        if not isinstance(operation_request["receipt"], dict):
            raise ValueError("operation_request.receipt must be a mapping")
        first = planned_interactions[0]
        extra = dict(first.get("extra") or {})
        extra.update(
            {
                "operation_scope": str(operation_request["scope"]),
                "operation_id": str(operation_request["operation_id"]),
                "operation_request_hash": str(operation_request["request_hash"]),
                "operation_receipt": deepcopy(operation_request["receipt"]),
            }
        )
        first["extra"] = extra
    existing_move_targets = [str(target) for _, target, existed in planned_moves if existed]
    if existing_move_targets:
        raise ValueError(
            "Staged move targets must not already exist: "
            + ", ".join(existing_move_targets)
        )
    _validate_unique_transaction_targets([
        *[path for path, _, _ in planned_yaml],
        *[path for path, _, _ in planned_text],
        *[target for _, target, _ in planned_moves],
    ])
    written_files: list[str] = []
    backups: dict[Path, bytes | None] = {}
    operation_event: dict[str, Any] | None = None
    operation_signature_before: str | None = None
    operation_signature_after: str | None = None

    with mutation_lock(root):
        ensure_interaction_log_valid(root)

        if operation_request is not None:
            try:
                replay = replay_or_conflict(
                    root,
                    scope=str(operation_request["scope"]),
                    operation_id=str(operation_request["operation_id"]),
                    request_hash=str(operation_request["request_hash"]),
                    operation=str(operation_request["operation"]),
                    assignment_id=operation_request["assignment_id"],
                )
            except OperationIdConflict as exc:
                raise MutationError(
                    str(exc),
                    {
                        "ok": False,
                        "status": "idempotency_conflict",
                        "partial_success": False,
                        "rolled_back": False,
                        "written_files": [],
                        "operation_receipt": exc.receipt,
                    },
                ) from exc
            if replay is not None:
                return {
                    "ok": True,
                    "status": "replayed",
                    "partial_success": False,
                    "rolled_back": False,
                    "written_files": [],
                    "interaction_count": 0,
                    "replayed": True,
                    "operation_receipt": replay,
                }

        conflict_files = [
            *_conflicting_files(planned_yaml, _read_yaml_before),
            *_conflicting_files(planned_text, _read_text_before),
            *_conflicting_read_dependencies(planned_reads),
        ]
        for source, target, existed in planned_moves:
            conflict_files.extend(
                _staged_move_conflicts(root, source, target, existed)
            )
        if conflict_files:
            error = "Mutation conflict: truth-source file changed after command planning"
            raise MutationError(
                error,
                {
                    "ok": False,
                    "status": "conflict",
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

        for validator in planned_commit_validators:
            validator()

        for path, _, _ in [*planned_yaml, *planned_text]:
            backups[path] = path.read_bytes() if path.exists() else None
        event_checkpoint = interaction_append_checkpoint(root)
        if operation_request is not None:
            operation_signature_before = operation_source_signature(root)
        try:
            with progress_phase("commit"):
                for path, _, after in planned_yaml:
                    _atomic_save_yaml(path, after)
                    written_files.append(str(path))
                for path, _, after_text in planned_text:
                    save_text(path, after_text)
                    written_files.append(str(path))
                for source, target, existed in planned_moves:
                    _commit_staged_move(root, source, target, existed)
                    written_files.append(str(target))
                for interaction in planned_interactions:
                    appended = _append_interaction_log_unlocked(
                        root, prevalidated=True, **interaction
                    )
                    operation_event = operation_event or appended
        except Exception as exc:
            rollback_errors = restore_interaction_append_checkpoint(root, event_checkpoint)
            rollback_errors.extend(_restore_files(backups))
            for source, target, _ in planned_moves:
                for staged_path in (target, source):
                    try:
                        _remove_staged_path(staged_path)
                    except OSError as cleanup_exc:
                        rollback_errors.append(f"{staged_path}: {cleanup_exc}")
            rolled_back = not rollback_errors
            status = "rolled_back" if rolled_back else "partial_success"
            payload = {
                "ok": False,
                "status": status,
                "partial_success": not rolled_back,
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
            raise MutationError(f"Mutation transaction failed; status={status}: {exc}", payload) from exc

        if operation_request is not None:
            operation_signature_after = operation_source_signature(root)
        if rebuild_dashboard:
            try:
                from research_cockpit.commands.build_dashboard import build_dashboard

                build_dashboard(root)
            except Exception as exc:
                raise MutationError(
                    f"Mutation succeeded but dashboard build failed: {exc}",
                    {
                        "ok": False,
                        "status": "partial_success",
                        "partial_success": True,
                        "rolled_back": False,
                        "written_files": written_files,
                        "error": str(exc),
                        "recovery_commands": [f"research-cockpit build --root {root}"],
                    },
                ) from exc

    operation_index_warning = ""
    if operation_request is not None:
        try:
            if (
                operation_event is None
                or operation_signature_before is None
                or operation_signature_after is None
            ):
                raise RuntimeError("operation index patch metadata was not captured")
            patch_operation_index(
                root,
                event=operation_event,
                source_signature_before=operation_signature_before,
                source_signature_after=operation_signature_after,
            )
        except Exception as exc:
            operation_index_warning = str(exc)

    if not rebuild_dashboard:
        changed_paths = [path for path, _, _ in [*planned_yaml, *planned_text]]
        changed_paths.extend(target for _, target, _ in planned_moves)
        try:
            patch_validation_index(root, changed_paths)
        except Exception as exc:
            stale_error = ""
            try:
                mark_validation_index_stale(
                    root,
                    reason="incremental_patch_failed",
                    detail=str(exc),
                )
            except Exception as stale_exc:
                stale_error = f"; stale marker failed: {stale_exc}"
            error = f"Mutation succeeded but validation index update failed: {exc}{stale_error}"
            raise MutationError(
                error,
                {
                    "ok": False,
                    "status": "partial_success",
                    "partial_success": True,
                    "rolled_back": False,
                    "written_files": written_files,
                    "error": error,
                    "recovery_commands": [f"research-cockpit build --root {root} --affected --id <node_id> --json"],
                },
            ) from exc

    result = {
        "ok": True,
        "status": "changed",
        "partial_success": False,
        "rolled_back": False,
        "written_files": written_files,
        "interaction_count": len(planned_interactions),
    }
    if operation_request is not None:
        result["replayed"] = False
        result["operation_receipt"] = deepcopy(operation_request["receipt"])
    if operation_index_warning:
        result["operation_index_warning"] = operation_index_warning
    return result


def finish_mutation(
    root: Path,
    yaml_changes: list[tuple],
    *,
    interaction: dict[str, Any],
    rebuild_dashboard: bool,
    text_changes: list[tuple] | None = None,
) -> None:
    execute_mutation_transaction(
        root,
        yaml_changes,
        interactions=[interaction],
        rebuild_dashboard=rebuild_dashboard,
        text_changes=text_changes,
    )
