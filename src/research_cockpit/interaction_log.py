from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
from typing import Any, Iterator
import uuid

import yaml

from research_cockpit.mutation_lock import mutation_lock
from research_cockpit.storage import load_yaml


EVENT_DIR_NAME = "interaction_events"
MANIFEST_NAME = "manifest.json"
MANIFEST_SCHEMA = "interaction_event_manifest_v1"
ACTIVE_FORMAT = "jsonl_v1"
SEGMENT_MAX_BYTES = 8 * 1024 * 1024
SEGMENT_PATTERN = re.compile(r"^events-(\d{6})\.jsonl$")


class InteractionLogError(ValueError):
    pass


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _legacy_path(root: Path) -> Path:
    return root / "graph" / "interaction_log.yaml"


def interaction_event_dir(root: Path) -> Path:
    return root / "graph" / EVENT_DIR_NAME


def _manifest_path(root: Path) -> Path:
    return interaction_event_dir(root) / MANIFEST_NAME


def _backend_files_exist(root: Path) -> bool:
    event_dir = interaction_event_dir(root)
    if not event_dir.exists():
        return False
    if (event_dir / MANIFEST_NAME).exists():
        return True
    return bool(_segment_paths(event_dir))


def _active_segment_dir(root: Path, manifest: dict[str, Any]) -> Path:
    generation = manifest.get("generation")
    if not generation:
        return interaction_event_dir(root)
    relative = PurePosixPath(str(generation))
    if relative.is_absolute() or ".." in relative.parts or not relative.parts or relative.parts[0] != "generations":
        raise InteractionLogError(
            f"graph/{EVENT_DIR_NAME}/{MANIFEST_NAME}: invalid generation path"
        )
    candidate = interaction_event_dir(root).joinpath(*relative.parts)
    if not candidate.is_dir():
        raise InteractionLogError(
            f"graph/{EVENT_DIR_NAME}/{MANIFEST_NAME}: active generation does not exist: {generation}"
        )
    return candidate


def _warning(message: str) -> str:
    return f"graph/interaction_log.yaml: {message}"


def _segment_warning(path: Path, message: str) -> str:
    return f"graph/{EVENT_DIR_NAME}/{path.name}: {message}"


def _legacy_events(root: Path, *, strict: bool = False) -> tuple[list[dict[str, Any]], list[str]]:
    path = _legacy_path(root)
    warnings: list[str] = []
    if not path.exists():
        return [], warnings
    try:
        data = load_yaml(path)
    except yaml.YAMLError as exc:
        message = _warning(f"YAML parse error: {exc}")
        if strict:
            raise InteractionLogError(message) from exc
        return [], [message]
    if not isinstance(data, dict):
        message = _warning("top-level document must be a mapping")
        if strict:
            raise InteractionLogError(message)
        return [], [message]

    events = data.get("events", [])
    if events is None:
        events = []
    if not isinstance(events, list):
        message = _warning("events must be a list")
        if strict:
            raise InteractionLogError(message)
        return [], [message]

    valid_events: list[dict[str, Any]] = []
    for index, event in enumerate(events, start=1):
        if isinstance(event, dict):
            valid_events.append(event)
            continue
        message = _warning(f"events[{index}] must be a mapping; got {type(event).__name__}")
        if strict:
            raise InteractionLogError(message)
        warnings.append(message)
    return valid_events, warnings


def _load_manifest(root: Path, *, strict: bool) -> tuple[dict[str, Any] | None, list[str]]:
    path = _manifest_path(root)
    if not path.exists():
        return None, []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        message = f"graph/{EVENT_DIR_NAME}/{MANIFEST_NAME}: JSON parse error: {exc}"
        if strict:
            raise InteractionLogError(message) from exc
        return None, [message]
    errors: list[str] = []
    if not isinstance(data, dict):
        errors.append(f"graph/{EVENT_DIR_NAME}/{MANIFEST_NAME}: document must be a mapping")
    else:
        if data.get("schema_version") != MANIFEST_SCHEMA:
            errors.append(f"graph/{EVENT_DIR_NAME}/{MANIFEST_NAME}: unsupported schema_version")
        if data.get("active_format") != ACTIVE_FORMAT:
            errors.append(f"graph/{EVENT_DIR_NAME}/{MANIFEST_NAME}: unsupported active_format")
        if data.get("legacy_mode") not in {"prefix", "migrated"}:
            errors.append(f"graph/{EVENT_DIR_NAME}/{MANIFEST_NAME}: legacy_mode must be 'prefix' or 'migrated'")
        if not errors and data.get("generation"):
            try:
                _active_segment_dir(root, data)
            except InteractionLogError as exc:
                errors.append(str(exc))
    if errors and strict:
        raise InteractionLogError(errors[0])
    return (data if isinstance(data, dict) and not errors else None), errors


def _segment_paths(event_dir: Path) -> list[Path]:
    if not event_dir.exists():
        return []
    paths = [
        path
        for path in event_dir.iterdir()
        if path.is_file() and SEGMENT_PATTERN.fullmatch(path.name)
    ]
    return sorted(paths, key=lambda path: path.name)


def _read_segment(
    path: Path,
    *,
    strict: bool,
    warnings: list[str] | None = None,
) -> list[dict[str, Any]]:
    warnings = warnings if warnings is not None else []
    events: list[dict[str, Any]] = []
    try:
        stream = path.open("r", encoding="utf-8", newline="")
    except (OSError, UnicodeError) as exc:
        message = _segment_warning(path, f"read error: {exc}")
        if strict:
            raise InteractionLogError(message) from exc
        warnings.append(message)
        return events
    with stream:
        for line_number, line in enumerate(stream, start=1):
            raw = line.rstrip("\r\n")
            if not raw:
                message = _segment_warning(path, f"line {line_number} is blank")
                if strict:
                    raise InteractionLogError(message)
                warnings.append(message)
                continue
            try:
                event = json.loads(raw)
            except json.JSONDecodeError as exc:
                message = _segment_warning(path, f"line {line_number} JSON parse error: {exc}")
                if strict:
                    raise InteractionLogError(message) from exc
                warnings.append(message)
                continue
            if not isinstance(event, dict):
                message = _segment_warning(path, f"line {line_number} must be a mapping")
                if strict:
                    raise InteractionLogError(message)
                warnings.append(message)
                continue
            events.append(event)
    return events


def iter_interaction_events(root: Path, *, strict: bool = False) -> Iterator[dict[str, Any]]:
    manifest, manifest_warnings = _load_manifest(root, strict=strict)
    if manifest is None:
        if manifest_warnings or _backend_files_exist(root):
            message = manifest_warnings[0] if manifest_warnings else (
                f"graph/{EVENT_DIR_NAME}: backend files exist without a valid {MANIFEST_NAME}"
            )
            raise InteractionLogError(message)
        events, _ = _legacy_events(root, strict=strict)
        yield from events
        return
    if manifest.get("legacy_mode") == "prefix":
        events, _ = _legacy_events(root, strict=strict)
        yield from events
    warnings: list[str] = []
    for path in _segment_paths(_active_segment_dir(root, manifest)):
        yield from _read_segment(path, strict=strict, warnings=warnings)


def load_interaction_log(root: Path, *, strict: bool = False) -> dict[str, Any]:
    manifest, warnings = _load_manifest(root, strict=strict)
    events: list[dict[str, Any]] = []
    if manifest is None:
        if warnings or _backend_files_exist(root):
            message = warnings[0] if warnings else (
                f"graph/{EVENT_DIR_NAME}: backend files exist without a valid {MANIFEST_NAME}"
            )
            if strict:
                raise InteractionLogError(message)
            return {"events": [], "warnings": [message], "backend": "invalid"}
        legacy_events, legacy_warnings = _legacy_events(root, strict=strict)
        return {"events": legacy_events, "warnings": legacy_warnings, "backend": "legacy_yaml"}

    if manifest.get("legacy_mode") == "prefix":
        legacy_events, legacy_warnings = _legacy_events(root, strict=strict)
        events.extend(legacy_events)
        warnings.extend(legacy_warnings)
    for path in _segment_paths(_active_segment_dir(root, manifest)):
        events.extend(_read_segment(path, strict=strict, warnings=warnings))
    return {"events": events, "warnings": warnings, "backend": ACTIVE_FORMAT}

def validate_interaction_log(root: Path) -> list[str]:
    try:
        for _ in iter_interaction_events(root, strict=True):
            pass
        manifest, _ = _load_manifest(root, strict=True)
        if manifest is not None:
            _validate_sealed_segments(root, manifest, verify_checksums=True)
    except (InteractionLogError, OSError) as exc:
        return [str(exc)]
    return []


def _file_checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _legacy_signature(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"size": 0, "mtime_ns": None, "sha256": hashlib.sha256(b"").hexdigest()}
    stat = path.stat()
    return {
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": _file_checksum(path),
    }

def _validate_prefix_signature(root: Path, manifest: dict[str, Any]) -> None:
    expected = manifest.get("legacy_signature")
    if not isinstance(expected, dict):
        raise InteractionLogError(f"graph/{EVENT_DIR_NAME}/{MANIFEST_NAME}: missing legacy_signature")
    if _legacy_signature(_legacy_path(root)) != expected:
        raise InteractionLogError(
            "graph/interaction_log.yaml changed after the JSONL backend was activated; "
            "run full validate and migrate-interaction-log before appending"
        )


def _segment_metadata(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "path": path.name,
        "bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "checksum": _file_checksum(path),
    }


def _validate_sealed_segments(
    root: Path,
    manifest: dict[str, Any],
    *,
    verify_checksums: bool,
) -> None:
    active_dir = _active_segment_dir(root, manifest)
    sealed = manifest.get("sealed_segments", []) or []
    if not isinstance(sealed, list):
        raise InteractionLogError(f"graph/{EVENT_DIR_NAME}/{MANIFEST_NAME}: sealed_segments must be a list")
    for item in sealed:
        if not isinstance(item, dict) or not item.get("path"):
            raise InteractionLogError(f"graph/{EVENT_DIR_NAME}/{MANIFEST_NAME}: invalid sealed segment metadata")
        path = active_dir / str(item["path"])
        if not path.is_file():
            raise InteractionLogError(_segment_warning(path, "sealed segment is missing"))
        stat = path.stat()
        if stat.st_size != item.get("bytes") or stat.st_mtime_ns != item.get("mtime_ns"):
            raise InteractionLogError(_segment_warning(path, "sealed segment changed after sealing"))
        if verify_checksums and _file_checksum(path) != item.get("checksum"):
            raise InteractionLogError(_segment_warning(path, "sealed segment checksum mismatch"))


def validate_interaction_append_target(root: Path) -> list[str]:
    try:
        manifest, _ = _load_manifest(root, strict=True)
        if manifest is None:
            if _backend_files_exist(root):
                raise InteractionLogError(
                    f"graph/{EVENT_DIR_NAME}: backend files exist without a valid {MANIFEST_NAME}"
                )
            _legacy_events(root, strict=True)
            return []
        if manifest.get("legacy_mode") == "prefix":
            _validate_prefix_signature(root, manifest)
        _validate_sealed_segments(root, manifest, verify_checksums=False)
        paths = _segment_paths(_active_segment_dir(root, manifest))
        sealed_names = {
            str(item.get("path"))
            for item in manifest.get("sealed_segments", []) or []
            if isinstance(item, dict)
        }
        if paths and paths[-1].name not in sealed_names:
            _read_segment(paths[-1], strict=True)
    except (InteractionLogError, OSError) as exc:
        return [str(exc)]
    return []


def interaction_log_warnings(root: Path) -> list[str]:
    return validate_interaction_append_target(root)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def _ensure_segment_backend(root: Path) -> dict[str, Any]:
    manifest, _ = _load_manifest(root, strict=True)
    if manifest is not None:
        return manifest
    if _backend_files_exist(root):
        raise InteractionLogError(
            f"graph/{EVENT_DIR_NAME}: backend files exist without a valid {MANIFEST_NAME}"
        )
    legacy_events, _ = _legacy_events(root, strict=True)
    manifest = {
        "schema_version": MANIFEST_SCHEMA,
        "active_format": ACTIVE_FORMAT,
        "legacy_mode": "prefix",
        "legacy_event_count": len(legacy_events),
        "legacy_signature": _legacy_signature(_legacy_path(root)),
        "sealed_segments": [],
        "activated_at": utc_timestamp(),
    }
    _atomic_write_json(_manifest_path(root), manifest)
    return manifest


def _seal_segment(root: Path, manifest: dict[str, Any], path: Path) -> dict[str, Any]:
    sealed = [
        dict(item)
        for item in manifest.get("sealed_segments", []) or []
        if isinstance(item, dict)
    ]
    if path.name in {str(item.get("path")) for item in sealed}:
        return manifest
    next_manifest = {**manifest, "sealed_segments": [*sealed, _segment_metadata(path)]}
    _atomic_write_json(_manifest_path(root), next_manifest)
    return next_manifest

def _append_event_line(path: Path, line: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        original_size = stream.tell()
        try:
            stream.write(line)
            stream.flush()
            os.fsync(stream.fileno())
        except Exception:
            stream.seek(original_size)
            stream.truncate()
            stream.flush()
            os.fsync(stream.fileno())
            raise


def interaction_append_checkpoint(root: Path) -> dict[str, Any]:
    manifest, _ = _load_manifest(root, strict=True)
    active_dir = (
        _active_segment_dir(root, manifest)
        if manifest is not None
        else interaction_event_dir(root)
    )
    manifest_path = _manifest_path(root)
    return {
        "manifest_bytes": manifest_path.read_bytes() if manifest_path.exists() else None,
        "active_dir": active_dir,
        "segment_sizes": {
            path: path.stat().st_size
            for path in _segment_paths(active_dir)
        },
    }


def restore_interaction_append_checkpoint(
    root: Path,
    checkpoint: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    active_dir = Path(checkpoint["active_dir"])
    segment_sizes = dict(checkpoint.get("segment_sizes", {}))
    current_paths = set(_segment_paths(active_dir))
    for path in current_paths - set(segment_sizes):
        try:
            path.unlink()
        except OSError as exc:
            errors.append(f"{path}: {exc}")
    for path, size in segment_sizes.items():
        try:
            with Path(path).open("r+b") as stream:
                stream.truncate(int(size))
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as exc:
            errors.append(f"{path}: {exc}")

    manifest_path = _manifest_path(root)
    manifest_bytes = checkpoint.get("manifest_bytes")
    try:
        if manifest_bytes is None:
            manifest_path.unlink(missing_ok=True)
        else:
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(
                prefix=".tmp-",
                suffix=".json",
                dir=manifest_path.parent,
            )
            temp_path = Path(temp_name)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(manifest_bytes)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp_path, manifest_path)
            finally:
                temp_path.unlink(missing_ok=True)
    except OSError as exc:
        errors.append(f"{manifest_path}: {exc}")
    return errors

def _append_interaction_log_unlocked(
    root: Path,
    *,
    kind: str,
    actor: str = "researcher",
    node_id: str | None = None,
    command: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
    prevalidated: bool = False,
) -> dict[str, Any]:
    if not prevalidated:
        errors = validate_interaction_append_target(root)
        if errors:
            raise InteractionLogError(errors[0])
    created_at = utc_timestamp()
    raw_id = "_".join(str(part) for part in (created_at, kind, node_id or "event") if part)
    event_id = f"{re.sub(r'[^A-Za-z0-9_.-]+', '_', raw_id)}_{uuid.uuid4().hex[:12]}"
    event: dict[str, Any] = {
        "id": event_id,
        "kind": str(kind),
        "actor": str(actor),
        "created_at": created_at,
    }
    if node_id:
        event["node_id"] = str(node_id)
    if command:
        event["command"] = str(command)
    if before:
        event["before"] = before
    if after:
        event["after"] = after
    if extra:
        event.update(extra)

    manifest = _ensure_segment_backend(root)
    if manifest.get("legacy_mode") == "prefix":
        _validate_prefix_signature(root, manifest)
    active_dir = _active_segment_dir(root, manifest)
    paths = _segment_paths(active_dir)
    sealed_names = {
        str(item.get("path"))
        for item in manifest.get("sealed_segments", []) or []
        if isinstance(item, dict)
    }
    line = (
        json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")

    segment_path: Path
    if paths:
        latest = paths[-1]
        latest_is_sealed = latest.name in sealed_names
        if not latest_is_sealed:
            _read_segment(latest, strict=True)
        if not latest_is_sealed and latest.stat().st_size + len(line) <= SEGMENT_MAX_BYTES:
            segment_path = latest
        else:
            if not latest_is_sealed:
                manifest = _seal_segment(root, manifest, latest)
            match = SEGMENT_PATTERN.fullmatch(latest.name)
            next_index = int(match.group(1)) + 1 if match else len(paths) + 1
            segment_path = active_dir / f"events-{next_index:06d}.jsonl"
    else:
        segment_path = active_dir / "events-000001.jsonl"
    _append_event_line(segment_path, line)
    return event


def append_interaction_log(
    root: Path,
    *,
    kind: str,
    actor: str = "researcher",
    node_id: str | None = None,
    command: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    with mutation_lock(root):
        return _append_interaction_log_unlocked(
            root,
            kind=kind,
            actor=actor,
            node_id=node_id,
            command=command,
            before=before,
            after=after,
            extra=extra,
        )

def recent_interactions_with_warnings(
    root: Path,
    limit: int = 5,
) -> tuple[list[dict[str, Any]], list[str]]:
    if limit <= 0:
        return [], []
    manifest, warnings = _load_manifest(root, strict=False)
    if manifest is None:
        if warnings or _backend_files_exist(root):
            message = warnings[0] if warnings else (
                f"graph/{EVENT_DIR_NAME}: backend files exist without a valid {MANIFEST_NAME}"
            )
            return [], [message]
        events, legacy_warnings = _legacy_events(root, strict=False)
        return list(reversed(events[-limit:])), legacy_warnings

    if manifest.get("legacy_mode") == "prefix":
        try:
            _validate_prefix_signature(root, manifest)
        except InteractionLogError as exc:
            warnings.append(str(exc))

    newest: list[dict[str, Any]] = []
    for path in reversed(_segment_paths(_active_segment_dir(root, manifest))):
        events = _read_segment(path, strict=False, warnings=warnings)
        for event in reversed(events):
            newest.append(event)
            if len(newest) >= limit:
                return newest, warnings
    if manifest.get("legacy_mode") == "prefix" and len(newest) < limit:
        legacy_events, legacy_warnings = _legacy_events(root, strict=False)
        warnings.extend(legacy_warnings)
        for event in reversed(legacy_events):
            newest.append(event)
            if len(newest) >= limit:
                break
    return newest, warnings


def recent_interactions(root: Path, limit: int = 5) -> list[dict[str, Any]]:
    events, _ = recent_interactions_with_warnings(root, limit=limit)
    return events

def event_content_checksum(events: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for event in events:
        digest.update(json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def write_segment_snapshot(
    event_dir: Path,
    events: list[dict[str, Any]],
    *,
    source: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    event_dir.mkdir(parents=True, exist_ok=False)
    segments: list[dict[str, Any]] = []
    current_lines: list[bytes] = []
    current_size = 0

    def flush_segment() -> None:
        nonlocal current_lines, current_size
        if not current_lines:
            return
        index = len(segments) + 1
        path = event_dir / f"events-{index:06d}.jsonl"
        payload = b"".join(current_lines)
        with path.open("wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        segment_events = [json.loads(line) for line in payload.decode("utf-8").splitlines()]
        stat = path.stat()
        segments.append({
            "path": path.name,
            "event_count": len(segment_events),
            "checksum": event_content_checksum(segment_events),
            "bytes": len(payload),
            "mtime_ns": stat.st_mtime_ns,
        })
        current_lines = []
        current_size = 0

    for event in events:
        line = (
            json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        if current_lines and current_size + len(line) > SEGMENT_MAX_BYTES:
            flush_segment()
        current_lines.append(line)
        current_size += len(line)
    flush_segment()

    manifest = {
        "schema_version": MANIFEST_SCHEMA,
        "active_format": ACTIVE_FORMAT,
        "legacy_mode": "migrated",
        "legacy_event_count": int(source.get("legacy_event_count", 0)),
        "legacy_signature": source.get("legacy_signature", {"size": 0, "mtime_ns": None}),
        "activated_at": utc_timestamp(),
        "snapshot_event_count": len(events),
        "snapshot_checksum": event_content_checksum(events),
        "snapshot_segments": segments,
    }
    _atomic_write_json(event_dir / MANIFEST_NAME, manifest)
    return manifest, segments


def activate_segment_generation(
    root: Path,
    generation_dir: Path,
    snapshot_manifest: dict[str, Any],
    segments: list[dict[str, Any]],
) -> dict[str, Any]:
    event_dir = interaction_event_dir(root)
    try:
        relative = generation_dir.resolve(strict=True).relative_to(event_dir.resolve(strict=True)).as_posix()
    except ValueError as exc:
        raise InteractionLogError("interaction generation must stay inside graph/interaction_events") from exc
    sealed_segments = [
        {
            "path": item["path"],
            "bytes": item["bytes"],
            "mtime_ns": item["mtime_ns"],
            "checksum": item["checksum"],
        }
        for item in segments[:-1]
    ]
    active_manifest = {
        **snapshot_manifest,
        "generation": relative,
        "sealed_segments": sealed_segments,
    }
    _atomic_write_json(_manifest_path(root), active_manifest)
    return active_manifest

def read_segment_snapshot(event_dir: Path) -> list[dict[str, Any]]:
    manifest_path = event_dir / MANIFEST_NAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InteractionLogError(f"{manifest_path}: invalid snapshot manifest: {exc}") from exc
    if manifest.get("legacy_mode") != "migrated":
        raise InteractionLogError(f"{manifest_path}: snapshot must use migrated legacy_mode")
    events: list[dict[str, Any]] = []
    for path in _segment_paths(event_dir):
        events.extend(_read_segment(path, strict=True))
    return events
