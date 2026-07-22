from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "src"))

from research_cockpit.cli import init_command
from research_cockpit.paths import default_data_root, load_project_locator
from research_cockpit.storage import load_yaml


class ExternalInitializationTests(unittest.TestCase):
    def setUp(self) -> None:
        parent = ROOT_DIR / ".test_tmp"
        parent.mkdir(exist_ok=True)
        self.base = parent / f"external_init_{uuid.uuid4().hex}"
        self.repo = self.base / "repo"
        self.repo.mkdir(parents=True)
        self._git("init")
        self._git("config", "user.name", "Research Cockpit Test")
        self._git("config", "user.email", "test@example.invalid")
        (self.repo / "tracked.txt").write_text("tracked\n", encoding="utf-8")
        self._git("add", "tracked.txt")
        self._git("commit", "-m", "initial")
        self.worktree = self.base / "worktree"
        self._git("worktree", "add", "-b", "agent/external-init", str(self.worktree))
        self.state_home = self.base / "state_home"

    def tearDown(self) -> None:
        shutil.rmtree(self.base, ignore_errors=True)

    def _git(self, *args: str) -> None:
        self._git_at(self.repo, *args)

    def _git_at(self, path: Path, *args: str) -> None:
        completed = subprocess.run(
            ["git", "-C", str(path), *args],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def _branch_name(self) -> str:
        completed = subprocess.run(
            ["git", "-C", str(self.repo), "branch", "--show-current"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        return completed.stdout.strip()

    def test_external_init_writes_portable_locator_and_all_worktrees_share_state(self) -> None:
        with (
            patch.dict(os.environ, {"RESEARCH_COCKPIT_STATE_HOME": str(self.state_home)}, clear=False),
            patch("research_cockpit.cli.emit_json") as emit,
            patch("research_cockpit.cli.Path.cwd", return_value=self.repo),
        ):
            init_command(
                [
                    "--project-id",
                    "project_external_test",
                    "--json",
                ]
            )
            self._git("add", ".research-cockpit.yaml")
            self._git("commit", "-m", "add research cockpit locator")
            self._git_at(self.worktree, "merge", self._branch_name())
            main_root = default_data_root(self.repo)
            worktree_root = default_data_root(self.worktree)

        locator_path = self.repo / ".research-cockpit.yaml"
        locator = load_yaml(locator_path)
        result = emit.call_args.args[0]

        self.assertEqual(main_root, self.state_home / "project_external_test")
        self.assertEqual(worktree_root, main_root)
        self.assertTrue((main_root / "current_state.yaml").exists())
        self.assertEqual(locator, load_project_locator(locator_path))
        self.assertEqual(locator["schema_version"], "research_cockpit_locator_v1")
        self.assertEqual(locator["project_id"], "project_external_test")
        self.assertNotIn(str(self.state_home), locator_path.read_text(encoding="utf-8"))
        self.assertEqual(result["root"], str(main_root))
        self.assertTrue(result["external"])
        for worktree in (self.repo, self.worktree):
            with self.assertRaises(ValueError):
                main_root.relative_to(worktree)

    def test_explicit_root_preserves_legacy_in_repository_discovery(self) -> None:
        legacy_root = self.repo / "research_cockpit"
        with patch("research_cockpit.cli.emit_json"):
            init_command(["--root", str(legacy_root), "--json"])

        self.assertTrue((legacy_root / "current_state.yaml").exists())
        self.assertFalse((self.repo / ".research-cockpit.yaml").exists())
        self.assertEqual(default_data_root(self.repo), legacy_root)

    def test_no_git_default_preserves_legacy_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            plain_project = Path(temporary_directory) / "plain_project"
            plain_project.mkdir()
            with (
                patch("research_cockpit.cli.Path.cwd", return_value=plain_project),
                patch("research_cockpit.cli.emit_json") as emit,
            ):
                init_command(["--json"])

            legacy_root = plain_project / "research_cockpit"
            self.assertTrue((legacy_root / "current_state.yaml").exists())
            self.assertFalse(emit.call_args.args[0]["external"])
            self.assertEqual(emit.call_args.args[0]["root"], str(legacy_root))


if __name__ == "__main__":
    unittest.main()
