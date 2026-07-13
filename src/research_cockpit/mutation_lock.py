from __future__ import annotations

import os
from pathlib import Path
import time
from typing import Any

from research_cockpit.cli_progress import emit_progress_event


class MutationError(RuntimeError):
    def __init__(self, message: str, payload: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.payload = payload or {}


class mutation_lock:
    def __init__(
        self,
        root: Path,
        *,
        timeout_seconds: float = 30.0,
        lock_name: str = ".mutation.lock",
    ) -> None:
        if Path(lock_name).name != lock_name:
            raise ValueError("lock_name must be a file name")
        self.root = root
        self.timeout_seconds = timeout_seconds
        self.path = root / "graph" / lock_name
        self.fd: int | None = None

    def _metadata(self) -> dict[str, Any]:
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError:
            return {}
        metadata: dict[str, Any] = {}
        for line in text.splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            metadata[key.strip()] = value.strip()
        return metadata

    def __enter__(self) -> "mutation_lock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        start = time.monotonic()
        emit_progress_event("lock_wait", event="phase_start")
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            try:
                self.fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self.fd, f"pid: {os.getpid()}\ncreated_at: {time.time()}\n".encode("utf-8"))
                emit_progress_event(
                    "lock_wait",
                    event="phase_end",
                    duration_ms=(time.monotonic() - start) * 1000,
                    status="completed",
                )
                return self
            except FileExistsError as exc:
                if time.monotonic() >= deadline:
                    metadata = self._metadata()
                    message = f"Timed out waiting for mutation lock: {self.path}"
                    payload = {
                        "ok": False,
                        "partial_success": False,
                        "rolled_back": False,
                        "written_files": [],
                        "error": message,
                        "recovery_commands": [],
                        "lock_path": str(self.path),
                        "owner_pid": metadata.get("pid"),
                        "created_at": metadata.get("created_at"),
                        "waited_seconds": round(time.monotonic() - start, 3),
                    }
                    raise MutationError(message, payload) from exc
                time.sleep(0.1)

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
