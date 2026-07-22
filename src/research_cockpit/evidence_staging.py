from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import stat
from typing import Any

from research_cockpit.operation_receipts import normalized_request_hash
from research_cockpit.retention import validate_retention
from research_cockpit.runtime_ids import generate_runtime_id
from research_cockpit.storage_layout import StorageLayout, resolve_storage_layout


MANIFEST_NAME = "_research_cockpit_ingest.json"
MAX_LINKS = 20
MAX_LINK_KEY_CHARS = 100
MAX_LINK_PATH_CHARS = 1000
HASH_CHUNK_BYTES = 1024 * 1024
MAX_INVENTORY_FILES = 1000
MAX_INVENTORY_BYTES = 100 * 1024 * 1024
MAX_MANAGED_TREE_ENTRIES = 10_000
_DECLARED_DIGEST = re.compile(r"^(sha256|manifest-sha256):[a-f0-9]{64}$")


_COPY_CONTEXT: ContextVar[dict[str, Any] | None] = ContextVar("evidence_copy_context", default=None)


@dataclass(frozen=True)
class StagedEvidence:
    source: Path
    record_spec: dict[str, Any]
    manifest: dict[str, Any]
    mode: str
    snapshot_revision: str
    staging_dir: Path | None = None
    target_dir: Path | None = None
    move_root: Path | None = None

    @property
    def staged_move(self) -> tuple[Path, Path] | None:
        if self.staging_dir is None or self.target_dir is None:
            return None
        return self.staging_dir, self.target_dir

    @property
    def content_sha256(self) -> str:
        digest = str(self.record_spec.get("integrity", {}).get("digest") or "")
        return digest.removeprefix("sha256:")

    def cleanup(self) -> None:
        if self.staging_dir is None:
            return
        shutil.rmtree(self.staging_dir, ignore_errors=True)
        parent = self.staging_dir.parent
        try:
            parent.rmdir()
        except OSError:
            pass


def _is_link_like(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", None)
    return path.is_symlink() or bool(is_junction and is_junction())


def _reject_symlink_components(path: Path) -> None:
    for candidate in (path, *path.parents):
        if _is_link_like(candidate):
            raise ValueError(
                f"evidence source path contains a symlink or junction: {candidate}"
            )


def evidence_source_locator(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("evidence_inputs.source must be a non-empty path")
    path = Path(text).expanduser()
    absolute = Path(os.path.abspath(path))
    return absolute.as_uri()


def _source_directory(value: Any) -> Path:
    raw_source = Path(str(value or "")).expanduser()
    if raw_source.is_symlink():
        raise ValueError("evidence_inputs.source must not be a symlink")
    source = raw_source.resolve(strict=False)
    if not source.exists():
        raise FileNotFoundError(source)
    if not source.is_dir():
        raise ValueError(f"evidence_inputs.source must be a directory: {source}")
    _reject_symlink_components(raw_source.absolute())
    return source


def _file_identity(info: os.stat_result) -> tuple[int, int]:
    return int(info.st_dev), int(info.st_ino)


def _file_snapshot(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        int(info.st_dev),
        int(info.st_ino),
        int(info.st_size),
        int(info.st_mtime_ns),
        int(info.st_ctime_ns),
    )


def _copy_regular_file(source: Path, target: Path) -> None:
    if _is_link_like(source):
        raise ValueError(f"evidence_inputs contains a symlink or junction: {source}")
    before = os.lstat(source)
    if not stat.S_ISREG(before.st_mode):
        raise ValueError(f"evidence_inputs contains a non-regular file: {source}")
    flags = os.O_RDONLY | int(getattr(os, "O_BINARY", 0))
    flags |= int(getattr(os, "O_NOFOLLOW", 0))
    try:
        descriptor = os.open(source, flags)
    except OSError as exc:
        raise ValueError(
            f"evidence source file changed or became a symlink: {source}"
        ) from exc
    try:
        opened_before = os.fstat(descriptor)
        path_after_open = os.lstat(source)
        if (
            not stat.S_ISREG(path_after_open.st_mode)
            or _file_identity(before) != _file_identity(opened_before)
            or _file_identity(before) != _file_identity(path_after_open)
        ):
            raise ValueError(f"evidence source file changed while opening: {source}")
        copy_context = _COPY_CONTEXT.get()
        if copy_context is not None and copy_context.get(
            "enforce_admission_limits"
        ):
            prospective_count = int(copy_context["file_count"]) + 1
            prospective_size = int(copy_context["size_bytes"]) + int(
                opened_before.st_size
            )
            if (
                prospective_count > MAX_INVENTORY_FILES
                or prospective_size > MAX_INVENTORY_BYTES
            ):
                raise ValueError(
                    "managed evidence above admission limits requires explicit retention"
                )
        target.parent.mkdir(parents=True, exist_ok=True)
        if copy_context is not None:
            relative = source.relative_to(copy_context["source_root"]).as_posix()
            encoded_relative = relative.encode("utf-8")
            digest = copy_context["digest"]
            digest.update(len(encoded_relative).to_bytes(8, "big"))
            digest.update(encoded_relative)
            digest.update(int(opened_before.st_size).to_bytes(8, "big"))
        with os.fdopen(descriptor, "rb", closefd=False) as source_handle, target.open(
            "xb"
        ) as target_handle:
            while True:
                chunk = source_handle.read(HASH_CHUNK_BYTES)
                if not chunk:
                    break
                target_handle.write(chunk)
                if copy_context is not None:
                    copy_context["digest"].update(chunk)
        opened_after = os.fstat(descriptor)
        path_after_copy = os.lstat(source)
        if (
            not stat.S_ISREG(path_after_copy.st_mode)
            or _file_snapshot(opened_before) != _file_snapshot(opened_after)
            or _file_identity(opened_before) != _file_identity(path_after_copy)
        ):
            raise ValueError(f"evidence source file changed while copying: {source}")
        if copy_context is not None:
            copy_context["file_count"] += 1
            copy_context["size_bytes"] += int(opened_after.st_size)
    finally:
        os.close(descriptor)


def _copy_source_tree(source: Path, target: Path) -> None:
    before = os.lstat(source)
    if _is_link_like(source):
        raise ValueError(f"evidence_inputs contains a symlink or junction: {source}")
    if not stat.S_ISDIR(before.st_mode):
        raise ValueError(f"evidence source directory changed: {source}")
    target.mkdir(parents=True, exist_ok=False)
    with os.scandir(source) as entries:
        rows = sorted(entries, key=lambda item: item.name)
    for entry in rows:
        source_path = source / entry.name
        target_path = target / entry.name
        info = os.lstat(source_path)
        if stat.S_ISLNK(info.st_mode) or _is_link_like(source_path):
            raise ValueError(
                f"evidence_inputs contains a symlink: {source_path.relative_to(source)}"
            )
        copy_context = _COPY_CONTEXT.get()
        if copy_context is not None:
            tree_entries = int(copy_context["tree_entries"]) + 1
            copy_context["tree_entries"] = tree_entries
            if tree_entries > MAX_MANAGED_TREE_ENTRIES:
                raise ValueError("managed evidence exceeds hard directory entry limit")
        if stat.S_ISDIR(info.st_mode):
            _copy_source_tree(source_path, target_path)
        elif stat.S_ISREG(info.st_mode):
            _copy_regular_file(source_path, target_path)
        else:
            raise ValueError(
                f"evidence_inputs contains an unsupported file type: {source_path}"
            )
    after = os.lstat(source)
    if (
        not stat.S_ISDIR(after.st_mode)
        or _file_snapshot(before) != _file_snapshot(after)
    ):
        raise ValueError(f"evidence source directory changed while copying: {source}")


def _copy_source_tree_hashed(
    source: Path,
    target: Path,
    *,
    enforce_admission_limits: bool = False,
) -> tuple[str, int, int]:
    context: dict[str, Any] = {
        "source_root": source,
        "digest": hashlib.sha256(),
        "file_count": 0,
        "enforce_admission_limits": enforce_admission_limits,
        "size_bytes": 0,
        "tree_entries": 0,
    }
    token = _COPY_CONTEXT.set(context)
    try:
        _copy_source_tree(source, target)
    finally:
        _COPY_CONTEXT.reset(token)
    return (
        context["digest"].hexdigest(),
        int(context["file_count"]),
        int(context["size_bytes"]),
    )


def _relative_link(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    relative = PurePosixPath(text)
    if not text or relative.is_absolute() or ".." in relative.parts:
        raise ValueError("evidence_inputs.links must use source-relative paths")
    if len(text) > MAX_LINK_PATH_CHARS:
        raise ValueError("evidence_inputs.links path exceeds 1000 characters")
    return relative.as_posix()


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _normalized_links(spec: dict[str, Any]) -> dict[str, str]:
    links = spec.get("links", {})
    if not isinstance(links, dict):
        raise ValueError("evidence_inputs.links must be a mapping")
    if len(links) > MAX_LINKS:
        raise ValueError("evidence_inputs.links supports at most 20 entries")
    relative_links: dict[str, str] = {}
    for key, value in links.items():
        normalized_key = str(key).strip()
        if not normalized_key:
            raise ValueError("evidence_inputs.links keys must be non-empty")
        if len(normalized_key) > MAX_LINK_KEY_CHARS:
            raise ValueError("evidence_inputs.links key exceeds 100 characters")
        relative_links[normalized_key] = _relative_link(value)
    return relative_links


def _reference_inventory(source: Path) -> tuple[dict[str, Any], str]:
    rows: list[tuple[str, int, int]] = []
    stack = [source]
    file_count = 0
    size_bytes = 0
    entries_scanned = 0
    complete = True
    max_entries = max(MAX_INVENTORY_FILES * 2, MAX_INVENTORY_FILES + 1)
    while stack and complete:
        directory = stack.pop()
        with os.scandir(directory) as stream:
            for entry in stream:
                entries_scanned += 1
                path = directory / entry.name
                if entry.is_symlink() or _is_link_like(path):
                    raise ValueError(f"evidence_inputs contains a symlink or junction: {path}")
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    stack.append(path)
                elif stat.S_ISREG(info.st_mode):
                    file_count += 1
                    size_bytes += int(info.st_size)
                    rows.append(
                        (
                            path.relative_to(source).as_posix(),
                            int(info.st_size),
                            int(info.st_mtime_ns),
                        )
                    )
                else:
                    raise ValueError(f"evidence_inputs contains an unsupported file type: {path}")
                if (
                    file_count > MAX_INVENTORY_FILES
                    or size_bytes > MAX_INVENTORY_BYTES
                    or entries_scanned > max_entries
                ):
                    complete = False
                    break
    digest = hashlib.sha256()
    digest.update(str(source.stat().st_mtime_ns).encode("ascii"))
    for relative, size, modified in sorted(rows):
        encoded = relative.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        digest.update(size.to_bytes(8, "big"))
        digest.update(modified.to_bytes(8, "big"))
    digest.update(b"complete" if complete else b"truncated")
    inventory = {
        "size_bytes": size_bytes,
        "file_count": file_count,
        "complete": complete,
        "entries_scanned": entries_scanned,
    }
    return inventory, f"inventory-sha256:{digest.hexdigest()}"


def _reference_links(source: Path, relative_links: dict[str, str]) -> dict[str, str]:
    links: dict[str, str] = {}
    for key, relative in relative_links.items():
        candidate = source.joinpath(*PurePosixPath(relative).parts)
        current = source
        for part in PurePosixPath(relative).parts:
            current /= part
            if _is_link_like(current):
                raise ValueError(f"evidence_inputs link contains a symlink or junction: {relative}")
        resolved = candidate.resolve(strict=True)
        try:
            resolved.relative_to(source)
        except ValueError as exc:
            raise ValueError(f"evidence_inputs link escapes source: {relative}") from exc
        if not resolved.is_file():
            raise FileNotFoundError(resolved)
        links[key] = resolved.as_uri()
    return links


def _declared_integrity(value: Any) -> dict[str, Any] | None:
    if value in (None, ""):
        return None
    digest = str(value).strip().lower()
    match = _DECLARED_DIGEST.fullmatch(digest)
    if match is None:
        raise ValueError(
            "evidence_inputs.content_digest must be sha256:<64 hex> or "
            "manifest-sha256:<64 hex>"
        )
    return {
        "level": "content" if match.group(1) == "sha256" else "manifest",
        "algorithm": "sha256",
        "digest": digest,
    }


def _retention_policy(value: Any) -> tuple[dict[str, Any], bool]:
    if value is None:
        return {"class": "reproducible_output"}, False
    retention = validate_retention(value, "evidence_inputs.retention")
    retention_class = str(retention.get("class") or "").strip()
    if not retention_class:
        raise ValueError(
            "evidence_inputs.retention.class is required when retention is explicit"
        )
    return retention, True


def _evidence_request_fingerprint(
    payload: dict[str, Any],
    request_fingerprint: str | None,
) -> str:
    return request_fingerprint or normalized_request_hash(payload)


def _reference_evidence(
    *,
    assignment_id: str,
    experiment_id: str,
    run_id: str,
    agent_id: str,
    spec: dict[str, Any],
    record_id: str | None,
) -> StagedEvidence:
    source = _source_directory(spec.get("source"))
    relative_links = _normalized_links(spec)
    inventory, inventory_digest = _reference_inventory(source)
    explicit_retention = spec.get("retention")
    if not inventory["complete"] and explicit_retention is None:
        raise ValueError(
            "truncated evidence inventory requires an explicit retention mapping"
        )
    retention, _ = _retention_policy(explicit_retention)
    declared = _declared_integrity(spec.get("content_digest"))
    integrity = declared or {
        "level": "inventory",
        "algorithm": "sha256",
        "digest": inventory_digest,
    }
    current_record_id = record_id or generate_runtime_id(
        "record",
        scope_hint=assignment_id,
        slug_hint=run_id,
    )
    source_uri = source.as_uri()
    links = _reference_links(source, relative_links)
    verified_at = _utc_timestamp()
    record_spec: dict[str, Any] = {
        "record_id": current_record_id,
        "artifact_kind": "run_output",
        "status": "recorded",
        "title": str(spec.get("title") or f"Evidence for {run_id}"),
        "summary": str(spec.get("summary") or ""),
        "links": links,
        "stable_path": source_uri,
        "source_file_count": inventory["file_count"],
        "storage": {
            "mode": "reference",
            "ownership": "external",
            "uri": source_uri,
            "managed_key": None,
        },
        "integrity": integrity,
        "inventory": inventory,
        "retention": retention,
        "availability": {"status": "available", "last_verified_at": verified_at},
        "lifecycle": {"supersedes": [], "superseded_by": None},
        "agent": agent_id,
    }
    if integrity["level"] == "content":
        record_spec["content_sha256"] = str(integrity["digest"]).removeprefix("sha256:")
    manifest = {
        "schema_version": "evidence_reference_v1",
        "assignment_id": assignment_id,
        "experiment_id": experiment_id,
        "run_id": run_id,
        "record_id": current_record_id,
        "storage": record_spec["storage"],
        "integrity": integrity,
        "inventory": inventory,
    }
    return StagedEvidence(
        source=source,
        record_spec=record_spec,
        manifest=manifest,
        mode="reference",
        snapshot_revision=str(integrity["digest"]),
    )


def _hash_regular_file(source: Path, source_root: Path, digest: Any) -> int:
    if _is_link_like(source):
        raise ValueError(f"evidence_inputs contains a symlink or junction: {source}")
    before = os.lstat(source)
    if not stat.S_ISREG(before.st_mode):
        raise ValueError(f"evidence_inputs contains a non-regular file: {source}")
    flags = os.O_RDONLY | int(getattr(os, "O_BINARY", 0))
    flags |= int(getattr(os, "O_NOFOLLOW", 0))
    try:
        descriptor = os.open(source, flags)
    except OSError as exc:
        raise ValueError(
            f"evidence source file changed or became a symlink: {source}"
        ) from exc
    try:
        opened_before = os.fstat(descriptor)
        path_after_open = os.lstat(source)
        if (
            not stat.S_ISREG(path_after_open.st_mode)
            or _file_identity(before) != _file_identity(opened_before)
            or _file_identity(before) != _file_identity(path_after_open)
        ):
            raise ValueError(f"evidence source file changed while opening: {source}")
        relative = source.relative_to(source_root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(int(opened_before.st_size).to_bytes(8, "big"))
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            while True:
                chunk = handle.read(HASH_CHUNK_BYTES)
                if not chunk:
                    break
                digest.update(chunk)
        opened_after = os.fstat(descriptor)
        path_after_read = os.lstat(source)
        if (
            not stat.S_ISREG(path_after_read.st_mode)
            or _file_snapshot(opened_before) != _file_snapshot(opened_after)
            or _file_identity(opened_before) != _file_identity(path_after_read)
        ):
            raise ValueError(f"evidence source file changed while hashing: {source}")
        return int(opened_after.st_size)
    finally:
        os.close(descriptor)


def _hash_source_tree(
    source: Path,
    source_root: Path,
    digest: Any,
    *,
    excluded_paths: set[str] | None = None,
) -> tuple[int, int]:
    before = os.lstat(source)
    if _is_link_like(source) or not stat.S_ISDIR(before.st_mode):
        raise ValueError(f"evidence source directory changed: {source}")
    with os.scandir(source) as entries:
        rows = sorted(entries, key=lambda item: item.name)
    file_count = 0
    size_bytes = 0
    for entry in rows:
        path = source / entry.name
        relative = path.relative_to(source_root).as_posix()
        if excluded_paths is not None and relative in excluded_paths:
            continue
        info = os.lstat(path)
        if stat.S_ISLNK(info.st_mode) or _is_link_like(path):
            raise ValueError(f"evidence_inputs contains a symlink or junction: {path}")
        if stat.S_ISDIR(info.st_mode):
            child_count, child_size = _hash_source_tree(
                path,
                source_root,
                digest,
                excluded_paths=excluded_paths,
            )
            file_count += child_count
            size_bytes += child_size
        elif stat.S_ISREG(info.st_mode):
            size_bytes += _hash_regular_file(path, source_root, digest)
            file_count += 1
        else:
            raise ValueError(f"evidence_inputs contains an unsupported file type: {path}")
    after = os.lstat(source)
    if (
        not stat.S_ISDIR(after.st_mode)
        or _file_snapshot(before) != _file_snapshot(after)
    ):
        raise ValueError(f"evidence source directory changed while hashing: {source}")
    return file_count, size_bytes


def _content_digest(
    source: Path,
    *,
    excluded_paths: set[str] | None = None,
) -> tuple[str, int, int]:
    digest = hashlib.sha256()
    file_count, size_bytes = _hash_source_tree(
        source,
        source,
        digest,
        excluded_paths=excluded_paths,
    )
    return digest.hexdigest(), file_count, size_bytes


def _has_git_worktree_marker(directory: Path) -> bool:
    marker = directory / ".git"
    if marker.is_dir():
        return (marker / "HEAD").is_file()
    if not marker.is_file():
        return False
    try:
        first_line = marker.read_text(encoding="utf-8").splitlines()[0].strip()
    except (OSError, UnicodeDecodeError, IndexError):
        return False
    prefix = "gitdir:"
    if not first_line.startswith(prefix):
        return False
    git_dir = Path(first_line.removeprefix(prefix).strip()).expanduser()
    if not git_dir.is_absolute():
        git_dir = marker.parent / git_dir
    return (git_dir / "HEAD").is_file()


def _inside_git_worktree(path: Path) -> bool:
    current = path
    while not current.exists() and current != current.parent:
        current = current.parent
    for candidate in (current, *current.parents):
        if _has_git_worktree_marker(candidate):
            return True
    return False


def stage_final_evidence(
    root: Path,
    *,
    assignment_id: str,
    experiment_id: str,
    run_id: str,
    agent_id: str,
    spec: dict[str, Any],
    record_id: str | None = None,
    storage_layout: StorageLayout | None = None,
) -> StagedEvidence:
    allowed = {
        "source",
        "title",
        "summary",
        "links",
        "mode",
        "content_digest",
        "retention",
    }
    unknown = sorted(set(spec) - allowed)
    if unknown:
        raise ValueError("evidence_inputs does not support: " + ", ".join(unknown))
    mode = str(spec.get("mode") or "managed").strip()
    if mode != "managed":
        raise ValueError("stage_final_evidence requires evidence_inputs.mode: managed")
    source = _source_directory(spec.get("source"))
    layout = storage_layout or resolve_storage_layout(root)
    artifact_root = layout.require_managed_artifact_root()
    if _inside_git_worktree(artifact_root):
        raise ValueError(
            "managed artifact root must be outside a Git worktree; "
            "configure RESEARCH_COCKPIT_ARTIFACT_ROOT or storage.yaml"
        )
    staging_root = artifact_root / ".staging"
    current_record_id = record_id or generate_runtime_id(
        "record",
        scope_hint=assignment_id,
        slug_hint=run_id,
    )
    managed_key = PurePosixPath(
        experiment_id,
        run_id,
        current_record_id,
    ).as_posix()
    target_dir = artifact_root.joinpath(*PurePosixPath(managed_key).parts).resolve(
        strict=False
    )
    if target_dir == artifact_root or not target_dir.is_relative_to(artifact_root):
        raise ValueError("evidence target must remain inside the artifact store")
    if (
        staging_root.is_relative_to(source)
        or target_dir.is_relative_to(source)
        or source.is_relative_to(staging_root)
        or source.is_relative_to(target_dir)
        or source.is_relative_to(artifact_root)
        or artifact_root.is_relative_to(source)
    ):
        raise ValueError(
            "evidence_inputs.source must not contain or reuse managed staging/artifact paths"
        )
    relative_links = _normalized_links(spec)
    retention, _ = _retention_policy(spec.get("retention"))
    staging_dir = staging_root / current_record_id
    stable_links = {
        key: target_dir.joinpath(*PurePosixPath(relative).parts).as_uri()
        for key, relative in relative_links.items()
    }
    if target_dir.exists():
        if _is_link_like(target_dir) or not target_dir.is_dir():
            raise FileExistsError(target_dir)
        manifest_path = target_dir / MANIFEST_NAME
        if _is_link_like(manifest_path) or not manifest_path.is_file():
            raise FileExistsError(
                f"managed evidence target has no trusted manifest: {target_dir}"
            )
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise FileExistsError(
                f"managed evidence target has an unreadable manifest: {target_dir}"
            ) from exc
        if not isinstance(manifest, dict):
            raise FileExistsError(
                f"managed evidence target has an invalid manifest: {target_dir}"
            )
        expected_identity = {
            "schema_version": "evidence_ingest_v1",
            "assignment_id": assignment_id,
            "experiment_id": experiment_id,
            "run_id": run_id,
            "record_id": current_record_id,
        }
        for field_name, expected_value in expected_identity.items():
            if manifest.get(field_name) != expected_value:
                raise FileExistsError(
                    f"managed evidence target belongs to another operation: {target_dir}"
                )
        storage = manifest.get("storage")
        integrity = manifest.get("integrity")
        inventory = manifest.get("inventory")
        if (
            not isinstance(storage, dict)
            or storage.get("mode") != "managed"
            or storage.get("ownership") != "cockpit_managed"
            or storage.get("managed_key") != managed_key
            or storage.get("uri") != target_dir.as_uri()
            or not isinstance(integrity, dict)
            or integrity.get("level") != "content"
            or not isinstance(inventory, dict)
            or inventory.get("complete") is not True
        ):
            raise FileExistsError(
                f"managed evidence target manifest does not match storage contract: {target_dir}"
            )
        if spec.get("retention") is None and (
            int(inventory.get("file_count") or 0) > MAX_INVENTORY_FILES
            or int(inventory.get("size_bytes") or 0) > MAX_INVENTORY_BYTES
        ):
            raise ValueError(
                "managed evidence above admission limits requires explicit retention"
            )
        target_sha256, target_file_count, target_size_bytes = _content_digest(
            target_dir,
            excluded_paths={MANIFEST_NAME},
        )
        expected_digest = f"sha256:{target_sha256}"
        if integrity.get("digest") != expected_digest:
            raise FileExistsError(
                f"managed evidence target content does not match manifest: {target_dir}"
            )
        if (
            inventory.get("file_count") != target_file_count
            or inventory.get("size_bytes") != target_size_bytes
        ):
            raise FileExistsError(
                f"managed evidence target inventory does not match manifest: {target_dir}"
            )
        content_sha256, file_count, size_bytes = _content_digest(source)
        expected_digest = f"sha256:{content_sha256}"
        if integrity.get("digest") != expected_digest:
            raise FileExistsError(
                f"managed evidence target content differs from retry source: {target_dir}"
            )
        if (
            inventory.get("file_count") != file_count
            or inventory.get("size_bytes") != size_bytes
        ):
            raise FileExistsError(
                f"managed evidence target inventory differs from retry source: {target_dir}"
            )
        for relative in relative_links.values():
            linked = target_dir.joinpath(*PurePosixPath(relative).parts)
            if not linked.is_file() or _is_link_like(linked):
                raise FileNotFoundError(linked)
        verified_at = _utc_timestamp()
        record_spec = {
            "record_id": current_record_id,
            "artifact_kind": "run_output",
            "status": "recorded",
            "title": str(spec.get("title") or f"Evidence for {run_id}"),
            "summary": str(spec.get("summary") or ""),
            "links": stable_links,
            "stable_path": target_dir.as_uri(),
            "manifest_path": manifest_path.as_uri(),
            "source_file_count": file_count,
            "content_sha256": content_sha256,
            "storage": storage,
            "integrity": integrity,
            "inventory": inventory,
            "retention": retention,
            "availability": {
                "status": "available",
                "last_verified_at": verified_at,
            },
            "lifecycle": {"supersedes": [], "superseded_by": None},
            "agent": agent_id,
        }
        return StagedEvidence(
            source=source,
            record_spec=record_spec,
            manifest=manifest,
            mode="managed",
            snapshot_revision=expected_digest,
            target_dir=target_dir,
        )
    if staging_dir.exists():
        raise FileExistsError(staging_dir)
    try:
        staging_dir.parent.mkdir(parents=True, exist_ok=True)
        content_sha256, file_count, size_bytes = _copy_source_tree_hashed(
            source,
            staging_dir,
            enforce_admission_limits=spec.get("retention") is None,
        )
        for relative in relative_links.values():
            linked = staging_dir.joinpath(*PurePosixPath(relative).parts)
            if not linked.is_file() or _is_link_like(linked):
                raise FileNotFoundError(linked)
        integrity = {
            "level": "content",
            "algorithm": "sha256",
            "digest": f"sha256:{content_sha256}",
        }
        inventory = {
            "size_bytes": size_bytes,
            "file_count": file_count,
            "complete": True,
            "entries_scanned": file_count,
        }
        storage = {
            "mode": "managed",
            "ownership": "cockpit_managed",
            "uri": target_dir.as_uri(),
            "managed_key": managed_key,
        }
        verified_at = _utc_timestamp()
        manifest = {
            "schema_version": "evidence_ingest_v1",
            "assignment_id": assignment_id,
            "experiment_id": experiment_id,
            "run_id": run_id,
            "record_id": current_record_id,
            "storage": storage,
            "links": stable_links,
            "inventory": inventory,
            "integrity": integrity,
        }
        (staging_dir / MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except BaseException:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise
    record_spec = {
        "record_id": current_record_id,
        "artifact_kind": "run_output",
        "status": "recorded",
        "title": str(spec.get("title") or f"Evidence for {run_id}"),
        "summary": str(spec.get("summary") or ""),
        "links": stable_links,
        "stable_path": target_dir.as_uri(),
        "manifest_path": (target_dir / MANIFEST_NAME).as_uri(),
        "source_file_count": file_count,
        "content_sha256": content_sha256,
        "storage": storage,
        "integrity": integrity,
        "inventory": inventory,
        "retention": retention,
        "availability": {"status": "available", "last_verified_at": verified_at},
        "lifecycle": {"supersedes": [], "superseded_by": None},
        "agent": agent_id,
    }
    return StagedEvidence(
        source=source,
        record_spec=record_spec,
        manifest=manifest,
        mode="managed",
        snapshot_revision=str(integrity["digest"]),
        staging_dir=staging_dir,
        target_dir=target_dir,
        move_root=artifact_root,
    )


def prepare_final_evidence(
    root: Path,
    *,
    assignment_id: str,
    experiment_id: str,
    run_id: str,
    agent_id: str,
    spec: dict[str, Any],
    record_id: str | None = None,
) -> StagedEvidence:
    if not isinstance(spec, dict):
        raise ValueError("evidence_inputs must be a mapping")
    allowed = {
        "source",
        "title",
        "summary",
        "links",
        "mode",
        "content_digest",
        "retention",
    }
    unknown = sorted(set(spec) - allowed)
    if unknown:
        raise ValueError("evidence_inputs does not support: " + ", ".join(unknown))
    mode = str(spec.get("mode") or "reference").strip()
    if mode == "reference":
        return _reference_evidence(
            assignment_id=assignment_id,
            experiment_id=experiment_id,
            run_id=run_id,
            agent_id=agent_id,
            spec=spec,
            record_id=record_id,
        )
    if mode == "managed":
        return stage_final_evidence(
            root,
            assignment_id=assignment_id,
            experiment_id=experiment_id,
            run_id=run_id,
            agent_id=agent_id,
            spec=spec,
            record_id=record_id,
        )
    raise ValueError("evidence_inputs.mode must be reference or managed")
