from __future__ import annotations

from pathlib import Path
import shutil
import sys
import unittest
import uuid
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "src"))

from research_cockpit.cli_progress import progress_phase, progress_session
from research_cockpit.mutation_lock import mutation_lock


class _BrokenProgressStream:
    def isatty(self) -> bool:
        return False

    def write(self, text: str) -> int:
        raise BrokenPipeError("progress consumer closed")

    def flush(self) -> None:
        raise BrokenPipeError("progress consumer closed")


class _IsattyFailureStream(_BrokenProgressStream):
    def isatty(self) -> bool:
        raise OSError("tty probe failed")


class ProgressIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        parent = ROOT_DIR / ".test_tmp"
        parent.mkdir(exist_ok=True)
        self.root = parent / f"progress_isolation_{uuid.uuid4().hex}"
        (self.root / "graph").mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_broken_progress_stream_does_not_change_body_semantics(self) -> None:
        body_ran = False

        with patch(
            "research_cockpit.cli_progress.sys.stderr",
            _BrokenProgressStream(),
        ):
            with progress_session("test", explicit=True):
                with progress_phase("commit"):
                    body_ran = True

        self.assertTrue(body_ran)

    def test_isatty_failure_does_not_prevent_command_body(self) -> None:
        body_ran = False

        with patch(
            "research_cockpit.cli_progress.sys.stderr",
            _IsattyFailureStream(),
        ):
            with progress_session("test"):
                body_ran = True

        self.assertTrue(body_ran)

    def test_lock_metadata_write_failure_removes_open_lock(self) -> None:
        lock_path = self.root / "graph" / ".mutation.lock"

        with patch(
            "research_cockpit.mutation_lock.os.write",
            side_effect=OSError("metadata write failed"),
        ):
            with self.assertRaisesRegex(OSError, "metadata write failed"):
                with mutation_lock(self.root):
                    self.fail("lock body must not run")

        self.assertFalse(lock_path.exists())

    def test_broken_progress_stream_does_not_leave_or_break_lock(self) -> None:
        lock_path = self.root / "graph" / ".mutation.lock"

        with patch(
            "research_cockpit.cli_progress.sys.stderr",
            _BrokenProgressStream(),
        ):
            with progress_session("test", explicit=True):
                with mutation_lock(self.root):
                    self.assertTrue(lock_path.exists())

        self.assertFalse(lock_path.exists())


if __name__ == "__main__":
    unittest.main()
