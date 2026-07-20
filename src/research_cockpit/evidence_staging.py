from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
from typing import Any

from research_cockpit.runtime_ids import generate_runtime_id


MANIFEST_NAME = "_research_cockpit_ingest.json"
MAX_LINKS = 20
MAX_LINK_KEY_CHARS = 100
MAX_LINK_PATH_CHARS = 1000
HASH_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True)
class StagedEvidence:
    source: Path
    staging_dir: Path
    target_dir: Path
    record_spec: dict[str, Any]
    manifest: dict[str, Any]

    @property
    def staged_move(self) -> tuple[Path, Path]:
        return self.staging_dir, self.target_dir

    @property
    def content_sha256(self) -> str:
        return str(self.manifest["content_sha256"])

    def cleanup(self) -> None:
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
        target.parent.mkdir(parents=True, exist_ok=True)
        with os.fdopen(descriptor, "rb", closefd=False) as source_handle, target.open(
            "xb"
        ) as target_handle:
            while True:
                chunk = source_handle.read(HASH_CHUNK_BYTES)
                if not chunk:
                    break
                target_handle.write(chunk)
        opened_after = os.fstat(descriptor)
        path_after_copy = os.lstat(source)
        if (
            not stat.S_ISREG(path_after_copy.st_mode)
            or _file_snapshot(opened_before) != _file_snapshot(opened_after)
            or _file_identity(opened_before) != _file_identity(path_after_copy)
        ):
            raise ValueError(f"evidence source file changed while copying: {source}")
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


def _relative_link(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    relative = PurePosixPath(text)
    if not text or relative.is_absolute() or ".." in relative.parts:
        raise ValueError("evidence_inputs.links must use source-relative paths")
    if len(text) > MAX_LINK_PATH_CHARS:
        raise ValueError("evidence_inputs.links path exceeds 1000 characters")
    return relative.as_posix()


def _content_digest(source: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    count = 0
    for path in sorted(item for item in source.rglob("*") if item.is_file()):
        relative = path.relative_to(source).as_posix().encode("utf-8")
        size = path.stat().st_size
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(size.to_bytes(8, "big"))
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(HASH_CHUNK_BYTES)
                if not chunk:
                    break
                digest.update(chunk)
        count += 1
    return digest.hexdigest(), count


def stage_final_evidence(
    root: Path,
    *,
    assignment_id: str,
    experiment_id: str,
    run_id: str,
    agent_id: str,
    spec: dict[str, Any],
) -> StagedEvidence:
    allowed = {"source", "title", "summary", "links"}
    unknown = sorted(set(spec) - allowed)
    if unknown:
        raise ValueError("evidence_inputs does not support: " + ", ".join(unknown))
    source = _source_directory(spec.get("source"))
    resolved_root = root.resolve(strict=False)
    artifact_root = (resolved_root / "artifacts").resolve(strict=False)
    staging_root = (resolved_root / ".staging").resolve(strict=False)
    if not artifact_root.is_relative_to(resolved_root) or not staging_root.is_relative_to(
        resolved_root
    ):
        raise ValueError("managed evidence paths must remain inside the data root")
    target_dir = (artifact_root / experiment_id / run_id).resolve(strict=False)
    if target_dir == artifact_root or not target_dir.is_relative_to(artifact_root):
        raise ValueError("evidence target must remain inside the artifact store")
    stable_path = target_dir.relative_to(resolved_root).as_posix()
    if (
        staging_root.is_relative_to(source)
        or target_dir.is_relative_to(source)
        or source.is_relative_to(staging_root)
        or source.is_relative_to(target_dir)
    ):
        raise ValueError("evidence_inputs.source must not contain or reuse managed staging/artifact paths")
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
    record_id = generate_runtime_id(
        "record",
        scope_hint=assignment_id,
        slug_hint=run_id,
    )
    staging_dir = staging_root / record_id
    if staging_dir.exists():
        raise FileExistsError(staging_dir)
    stable_links = {
        key: PurePosixPath(stable_path, relative).as_posix()
        for key, relative in relative_links.items()
    }
    try:
        staging_dir.parent.mkdir(parents=True, exist_ok=True)
        _copy_source_tree(source, staging_dir)
        for relative in relative_links.values():
            linked = staging_dir.joinpath(*PurePosixPath(relative).parts)
            if not linked.is_file() or _is_link_like(linked):
                raise FileNotFoundError(linked)
        content_sha256, file_count = _content_digest(staging_dir)
        manifest = {
            "schema_version": "evidence_ingest_v1",
            "assignment_id": assignment_id,
            "experiment_id": experiment_id,
            "run_id": run_id,
            "record_id": record_id,
            "stable_path": stable_path,
            "links": stable_links,
            "source_file_count": file_count,
            "content_sha256": content_sha256,
        }
        (staging_dir / MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise
    record_spec = {
        "record_id": record_id,
        "artifact_kind": "run_output",
        "status": "recorded",
        "title": str(spec.get("title") or f"Evidence for {run_id}"),
        "summary": str(spec.get("summary") or ""),
        "links": stable_links,
        "stable_path": stable_path,
        "manifest_path": PurePosixPath(stable_path, MANIFEST_NAME).as_posix(),
        "source_file_count": file_count,
        "content_sha256": content_sha256,
        "agent": agent_id,
    }
    return StagedEvidence(
        source=source,
        staging_dir=staging_dir,
        target_dir=target_dir,
        record_spec=record_spec,
        manifest=manifest,
    )
