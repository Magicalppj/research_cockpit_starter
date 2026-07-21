from __future__ import annotations

import errno
import os
from pathlib import Path
import time
from typing import Any

from research_cockpit.cli_progress import emit_progress_event


class MutationError(RuntimeError):
    def __init__(self, message: str, payload: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.payload = payload or {}


def _process_is_alive(pid: int) -> bool | None:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            open_process = kernel32.OpenProcess
            open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            open_process.restype = wintypes.HANDLE
            handle = open_process(0x1000, False, pid)
            if not handle:
                error = ctypes.get_last_error()
                if error == 5:
                    return True
                if error == 87:
                    return False
                return None
            try:
                exit_code = wintypes.DWORD()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return None
                return exit_code.value == 259
            finally:
                kernel32.CloseHandle(handle)
        except (AttributeError, OSError):
            return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return False
        if exc.errno == errno.EPERM:
            return True
        return None
    return True


class mutation_lock:
    def __init__(
        self,
        root: Path,
        *,
        timeout_seconds: float = 30.0,
        lock_name: str = ".mutation.lock",
        stale_after_seconds: float = 30.0,
    ) -> None:
        if Path(lock_name).name != lock_name:
            raise ValueError("lock_name must be a file name")
        if stale_after_seconds < 0:
            raise ValueError("stale_after_seconds must be non-negative")
        self.root = root
        self.timeout_seconds = timeout_seconds
        self.stale_after_seconds = stale_after_seconds
        self.path = root / "graph" / lock_name
        self.fd: int | None = None
        self.acquired_at: float | None = None

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

    def _owner_state(self, *, inspect_young: bool = False) -> dict[str, Any]:
        try:
            stat_before = self.path.stat()
        except OSError:
            return {
                "metadata": {},
                "owner_pid": None,
                "owner_alive": None,
                "lock_age_seconds": None,
                "stale": False,
                "identity": None,
            }
        file_age = max(0.0, time.time() - stat_before.st_mtime)
        if not inspect_young and file_age < self.stale_after_seconds:
            return {
                "metadata": {},
                "owner_pid": None,
                "owner_alive": None,
                "lock_age_seconds": round(file_age, 3),
                "stale": False,
                "identity": None,
            }
        metadata = self._metadata()
        try:
            owner_pid = int(str(metadata.get("pid") or ""))
            created_at = float(str(metadata.get("created_at") or ""))
        except (TypeError, ValueError):
            return {
                "metadata": metadata,
                "owner_pid": metadata.get("pid"),
                "owner_alive": None,
                "lock_age_seconds": None,
                "stale": False,
                "identity": None,
            }
        age = max(0.0, time.time() - created_at)
        owner_alive = _process_is_alive(owner_pid)
        return {
            "metadata": metadata,
            "owner_pid": str(owner_pid),
            "owner_alive": owner_alive,
            "lock_age_seconds": round(age, 3),
            "stale": age >= self.stale_after_seconds and owner_alive is False,
            "identity": (
                stat_before.st_ino,
                stat_before.st_size,
                stat_before.st_mtime_ns,
            ),
        }

    def _recover_stale_lock(self, owner: dict[str, Any]) -> bool:
        if owner.get("stale") is not True:
            return False
        try:
            stat_now = self.path.stat()
        except FileNotFoundError:
            return True
        except OSError:
            return False
        identity = (stat_now.st_ino, stat_now.st_size, stat_now.st_mtime_ns)
        if identity != owner.get("identity") or self._metadata() != owner.get("metadata"):
            return False
        try:
            self.path.unlink()
        except FileNotFoundError:
            return True
        except OSError:
            return False
        return True

    def _release_file(self) -> None:
        close_error: OSError | None = None
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError as exc:
                close_error = exc
            finally:
                self.fd = None
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        if close_error is not None:
            raise close_error

    def __enter__(self) -> "mutation_lock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        start = time.monotonic()
        emit_progress_event("lock_wait", event="phase_start")
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            owner: dict[str, Any] | None = None
            try:
                self.fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                try:
                    os.write(self.fd, f"pid: {os.getpid()}\ncreated_at: {time.time()}\n".encode("utf-8"))
                    emit_progress_event(
                        "lock_wait",
                        event="phase_end",
                        duration_ms=(time.monotonic() - start) * 1000,
                        status="completed",
                    )
                    self.acquired_at = time.monotonic()
                    emit_progress_event("lock_hold", event="phase_start")
                    return self
                except BaseException:
                    try:
                        self._release_file()
                    except OSError:
                        pass
                    raise
            except (FileExistsError, PermissionError) as exc:
                if isinstance(exc, PermissionError) and not self.path.exists():
                    raise
                owner = self._owner_state()
                if self._recover_stale_lock(owner):
                    continue
                if time.monotonic() >= deadline:
                    owner = self._owner_state(inspect_young=True)
                    metadata = owner.get("metadata", {}) if owner else self._metadata()
                    message = f"Timed out waiting for mutation lock: {self.path}"
                    payload = {
                        "ok": False,
                        "partial_success": False,
                        "rolled_back": False,
                        "written_files": [],
                        "error": message,
                        "recovery_commands": [],
                        "lock_path": str(self.path),
                        "owner_pid": (
                            owner.get("owner_pid") if owner else metadata.get("pid")
                        ),
                        "created_at": metadata.get("created_at"),
                        "owner_alive": owner.get("owner_alive") if owner else None,
                        "lock_age_seconds": owner.get("lock_age_seconds") if owner else None,
                        "waited_seconds": round(time.monotonic() - start, 3),
                        "retryable": True,
                        "recovery": "retry_same_operation",
                    }
                    raise MutationError(message, payload) from exc
                time.sleep(0.1)

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self._release_file()
        if self.acquired_at is not None:
            emit_progress_event(
                "lock_hold",
                event="phase_end",
                duration_ms=(time.monotonic() - self.acquired_at) * 1000,
                status="failed" if exc_type is not None else "completed",
            )
            self.acquired_at = None
